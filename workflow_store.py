"""Durable workflow sidecar; never executes models, tools, code or research writes.

Host-only dispatch API. Mutations require operation_id and expected_rev; retries
must repeat the entire request. A reservation is a durable claim, not permission
to replay a call. Reconciliation only records an unknown outcome. Authorization
and research-object commits belong to the caller. completed means orchestration
stopped; scientific_status is always candidate_pending_review.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import os
import re
import stat
import uuid

from backend import Projects, checked_root, make_directory, write_new
from research_store import now, read_regular, safe_path, sync_directory
from schema import StoreError, canonical, decode, digest, fields, identifier, integer, ref, refs, require, sha, string

FORMAT = "dsh-math-workflow-v1"
TX_FORMAT = "dsh-math-workflow-transaction-v1"
KINDS = {"derive", "check", "revise", "decompose", "reconstruct", "stop"}
TERMINAL = {"completed", "cancelled", "needs_reconcile"}
MAX_BYTES = 1024 * 1024


def validate_spec(spec):
    require(type(spec) is dict, "INVALID_SCHEMA", "Expected workflow spec")
    fields(spec, "format project_id problem_ref model_policy budget tools context_budget stage_goal" + (" routes" if "routes" in spec else ""))
    routes = spec.get("routes", [])
    require(type(routes) is list and len(routes) <= 3, "INVALID_SCHEMA", "At most three routes")
    route_ids = set()
    for route in routes:
        fields(route, "id title goal stop_condition reopen_condition")
        identifier(route["id"])
        require(route["id"] not in route_ids, "INVALID_SCHEMA", "Duplicate route")
        route_ids.add(route["id"])
        for key in ("title", "goal", "stop_condition", "reopen_condition"):
            string(route[key])
    require(spec["format"] == FORMAT, "UNKNOWN_SCHEMA", "Unsupported workflow format")
    identifier(spec["project_id"])
    ref(spec["problem_ref"])
    fields(spec["model_policy"], "provider model effort")
    for value in spec["model_policy"].values():
        string(value)
    fields(spec["budget"], "requests total_tokens max_output_tokens")
    for value in spec["budget"].values():
        integer(value, 1)
    require(spec["budget"]["max_output_tokens"] <= spec["budget"]["total_tokens"], "INVALID_SCHEMA", "Output cap exceeds total budget")
    fields(spec["context_budget"], "max_tokens")
    integer(spec["context_budget"]["max_tokens"], 1)
    string(spec["stage_goal"])
    require(type(spec["tools"]) is list, "INVALID_SCHEMA", "Expected selected tools")
    names = set()
    for tool in spec["tools"]:
        fields(tool, "name version read_only")
        string(tool["name"])
        string(tool["version"])
        require(tool["read_only"] is True and tool["name"] not in names, "INVALID_SCHEMA", "Tools must be unique and read-only")
        names.add(tool["name"])
    require(len(canonical(spec)) <= MAX_BYTES, "TOO_LARGE", "Workflow spec exceeds limit")


class WorkflowStore:
    def __init__(self, root):
        self.root = checked_root(root)
        self.projects = Projects(self.root)

    @contextmanager
    def _locked(self, project_id, writable):
        directory = safe_path(self.projects.path(project_id), "workflows")
        if not directory.exists() and not writable:
            yield directory, {"seq": 0, "head": "0" * 64, "runs": {}, "operations": {}}
            return
        if writable:
            make_directory(directory)
        lock = safe_path(directory, ".writer.lock")
        try:
            fd = os.open(lock, os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_RDWR | os.O_CREAT if writable else os.O_RDONLY), 0o600)
        except FileNotFoundError:
            require(not writable and not any(directory.iterdir()), "CORRUPT_WORKFLOW", "Missing workflow lock")
            yield directory, {"seq": 0, "head": "0" * 64, "runs": {}, "operations": {}}
            return
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_PATH", "Unsafe workflow lock")
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if writable else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StoreError("LOCKED", "Workflow in use; retry the same operation") from error
            if writable:
                sync_directory(directory)
            yield directory, self._load(directory)
        finally:
            os.close(fd)

    def _load(self, directory):
        state = {"seq": 0, "head": "0" * 64, "runs": {}, "operations": {}}
        for path in sorted(directory.iterdir()):
            if path.name == ".writer.lock" or path.name.startswith(".pending-"):
                continue
            require(re.fullmatch(r"[0-9]{20}\.json", path.name), "CORRUPT_WORKFLOW", "Unexpected workflow entry")
            event = decode(read_regular(safe_path(directory, path.name)))
            fields(event, "format seq previous_hash request run event_hash")
            require(event["format"] == TX_FORMAT and type(event["seq"]) is int
                    and event["seq"] == state["seq"] + 1 and path.name == f'{event["seq"]:020d}.json'
                    and event["previous_hash"] == state["head"]
                    and digest({k: v for k, v in event.items() if k != "event_hash"}) == event["event_hash"],
                    "CORRUPT_WORKFLOW", "Workflow transaction chain mismatch")
            run, request = event["run"], event["request"]
            validate_spec(run["spec"])
            identifier(run["run_id"])
            old = state["runs"].get(run["run_id"])
            require(type(run["rev"]) is int and run["rev"] == (old["rev"] + 1 if old else 1)
                    and run["scientific_status"] == "candidate_pending_review"
                    and request["operation_id"] not in state["operations"], "CORRUPT_WORKFLOW", "Invalid workflow revision")
            state["runs"][run["run_id"]] = run
            state["operations"][request["operation_id"]] = event
            state["seq"], state["head"] = event["seq"], event["event_hash"]
        return state

    def _publish(self, directory, state, request, run):
        event = {"format": TX_FORMAT, "seq": state["seq"] + 1, "previous_hash": state["head"],
                 "request": request, "run": run}
        event["event_hash"] = digest(event)
        require(len(canonical(event)) <= 8 * MAX_BYTES, "TOO_LARGE", "Workflow transaction exceeds limit")
        pending = safe_path(directory, ".pending-" + str(uuid.uuid4()))
        write_new(pending, event)
        destination = safe_path(directory, f'{event["seq"]:020d}.json')
        require(not destination.exists(), "CONFLICT", "Workflow revision already exists")
        os.rename(pending, destination)
        sync_directory(directory)
        return deepcopy(run)

    @staticmethod
    def _resolve(store, references):
        refs(references)
        for value in references:
            store.get(value["id"], value["rev"])

    def dispatch(self, request):
        require(type(request) is dict, "INVALID_SCHEMA", "Expected workflow request")
        require(len(canonical(request)) <= 2 * MAX_BYTES, "TOO_LARGE", "Workflow request exceeds limit")
        request = deepcopy(request)
        action, project_id = request.get("action"), request.get("project_id")
        require(action in {"list", "read", "prepare", "start", "pause", "cancel", "reserve", "queue", "claim", "propose", "commit", "fail", "reconcile", "continue"},
                "INVALID_SCHEMA", "Unknown workflow action")
        identifier(project_id)
        writable = action not in {"list", "read"}
        # This gate also checks campaign identity, actual schema and log integrity.
        with self.projects.open_project(project_id, require_writable=True) as (_, research):
            with self._locked(project_id, writable) as (directory, state):
                if action == "list":
                    fields(request, "action project_id")
                    return [deepcopy(run) for run in state["runs"].values()]
                identifier(request.get("run_id"))
                if action == "read":
                    fields(request, "action project_id run_id")
                    require(request["run_id"] in state["runs"], "NOT_FOUND", "Workflow run not found")
                    return deepcopy(state["runs"][request["run_id"]])
                identifier(request.get("operation_id"))
                integer(request.get("expected_rev"))
                previous = state["operations"].get(request["operation_id"])
                if previous:
                    require(previous["request"] == request, "OPERATION_CONFLICT", "Operation id reused with different request")
                    return deepcopy(previous["run"])
                run = deepcopy(state["runs"].get(request["run_id"]))
                require(request["expected_rev"] == (run["rev"] if run else 0), "CONFLICT", "Workflow revision changed")
                base = "action project_id run_id operation_id expected_rev"
                if action in {"prepare", "continue"}:
                    fields(request, base + (" spec" if action == "prepare" else " spec new_run_id research_head"))
                    validate_spec(request["spec"])
                    spec = request["spec"]
                    require(spec["project_id"] == project_id, "PROJECT_SCOPE", "Spec project mismatch")
                    problem = research.get(spec["problem_ref"]["id"], spec["problem_ref"]["rev"])
                    require(problem["kind"] == "Problem", "INVALID_SCHEMA", "Expected exact Problem reference")
                    parent = None
                    run_id = request["run_id"]
                    if action == "prepare":
                        require(run is None, "CONFLICT", "Run already exists")
                    else:
                        require(run is not None and run["status"] != "running", "INVALID_STATE", "Pause or reconcile parent before continuing")
                        fields(request["research_head"], "seq head_hash")
                        integer(request["research_head"]["seq"])
                        sha(request["research_head"]["head_hash"])
                        snapshot = research.snapshot()["state"]
                        require(request["research_head"] == {"seq": snapshot["seq"], "head_hash": snapshot["head_hash"]}, "CONFLICT", "Research head changed")
                        parent_event = next(event for event in state["operations"].values()
                                            if event["run"]["run_id"] == run["run_id"] and event["run"]["rev"] == run["rev"])
                        parent = {"run_id": run["run_id"], "rev": run["rev"], "workflow_event_hash": parent_event["event_hash"],
                                  "research_head": request["research_head"]}
                        run_id = request["new_run_id"]
                        identifier(run_id)
                        require(run_id not in state["runs"], "CONFLICT", "New run already exists")
                    run = {"format": FORMAT, "run_id": run_id, "project_id": project_id, "rev": 1,
                           "spec": spec, "parent": parent, "status": "prepared", "scientific_status": "candidate_pending_review",
                           "actions": [], "usage": {"requests": 0, "total_tokens": 0, "complete": True},
                           "reason": None, "created_at": now(), "updated_at": now()}
                else:
                    require(run is not None, "NOT_FOUND", "Workflow run not found")
                    self._transition(run, request, base, research)
                    run["rev"] += 1
                    run["updated_at"] = now()
                return self._publish(directory, state, request, run)

    @staticmethod
    def _can_call(run):
        budget = run["spec"]["budget"]
        return (run["usage"]["complete"] and run["usage"]["requests"] < budget["requests"]
                and run["usage"]["total_tokens"] < budget["total_tokens"]
                and all(a["usage"] is None or a["usage"]["output_tokens"] <= a["max_output_tokens"]
                        for a in run["actions"]))

    def _transition(self, run, request, base, research):
        action = request["action"]
        active = next((a for a in run["actions"] if a["status"] in {"queued", "running", "proposed"}), None)
        if action in {"start", "pause", "cancel", "reconcile"}:
            fields(request, base)
            if action == "reconcile":
                require(active is not None and active["status"] == "running", "INVALID_STATE", "Only a running claim can be reconciled")
                active["status"] = "unknown"
                run["status"], run["reason"] = "needs_reconcile", "unknown_action_outcome"
                run["usage"]["complete"] = False
            elif action == "start":
                require(run["status"] in {"prepared", "paused"} and self._can_call(run)
                        and run["reason"] not in {"budget_exhausted", "usage_missing", "budget_exceeded"}, "INVALID_STATE", "Run cannot start")
                run["status"], run["reason"] = "running", None
            else:
                require(run["status"] not in TERMINAL, "INVALID_STATE", "Run is terminal")
                require(not active or active["status"] != "running", "RECONCILE_REQUIRED", "Reconcile outstanding call before changing run")
                run["status"] = "paused" if action == "pause" else "cancelled"
                if action == "cancel" and active:
                    active["status"] = "cancelled"
            return
        if action in {"reserve", "queue"}:
            fields(request, base + " kind context_snapshot max_output_tokens")
            require(run["status"] == "running" and active is None and self._can_call(run), "INVALID_STATE", "One active action per running workflow")
            require(request["kind"] in KINDS, "INVALID_SCHEMA", "Unsupported workflow action kind")
            snapshot = request["context_snapshot"]
            require(type(snapshot) is dict, "INVALID_SCHEMA", "Expected context snapshot")
            fields(snapshot, "format refs research_head text sha256 input_token_bound action" +
                   "".join(" " + key for key in ("token_estimator", "verification_key") if key in snapshot))
            for key in ("token_estimator", "verification_key"):
                if key in snapshot:
                    string(snapshot[key])
            require(snapshot["format"] == "dsh-math-context-v1", "UNKNOWN_SCHEMA", "Unsupported context snapshot")
            self._resolve(research, snapshot["refs"])
            string(snapshot["text"], empty=True)
            sha(snapshot["sha256"])
            raw = snapshot["text"].encode("utf-8")
            require(len(raw) <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == snapshot["sha256"], "INVALID_SCHEMA", "Context content hash mismatch")
            integer(snapshot["input_token_bound"])
            require(snapshot["input_token_bound"] >= len(raw), "CONTEXT_BUDGET", "Input token bound must cover UTF-8 bytes")
            fields(snapshot["research_head"], "seq head_hash")
            integer(snapshot["research_head"]["seq"])
            sha(snapshot["research_head"]["head_hash"])
            current = research.snapshot()["state"]
            require(snapshot["research_head"] == {"seq": current["seq"], "head_hash": current["head_hash"]}, "CONFLICT", "Context research head changed")
            fields(snapshot["action"], "kind target_ref route_id")
            require(snapshot["action"]["kind"] == request["kind"], "INVALID_SCHEMA", "Context action mismatch")
            if snapshot["action"]["target_ref"] is not None:
                self._resolve(research, [snapshot["action"]["target_ref"]])
            if snapshot["action"]["route_id"] is not None:
                require(snapshot["action"]["route_id"] in {r["id"] for r in run["spec"].get("routes", [])}, "INVALID_SCHEMA", "Unknown route")
            require(snapshot["input_token_bound"] <= run["spec"]["context_budget"]["max_tokens"], "CONTEXT_BUDGET", "Context exceeds budget")
            integer(request["max_output_tokens"], 1)
            budget = run["spec"]["budget"]
            require(request["max_output_tokens"] <= budget["max_output_tokens"], "BUDGET", "Output reservation exceeds per-request cap")
            require(run["usage"]["requests"] < budget["requests"] and
                    run["usage"]["total_tokens"] + snapshot["input_token_bound"] + request["max_output_tokens"] <= budget["total_tokens"],
                    "BUDGET", "No budget for another request")
            item = {"action_id": request["operation_id"], "kind": request["kind"], "status": "queued",
                    "context_snapshot": deepcopy(snapshot), "max_output_tokens": request["max_output_tokens"],
                    "proposal": None, "usage": None, "output_refs": []}
            run["actions"].append(item)
            if action == "reserve":
                item["status"] = "running"
                run["usage"]["requests"] += 1
            return
        fields(request, base + {"claim": " action_id", "propose": " action_id proposal usage",
                               "commit": " action_id output_refs", "fail": " action_id reason"}[action]
               + (" accounting_complete" if action == "fail" and "accounting_complete" in request else ""))
        identifier(request["action_id"])
        require(active is not None and active["action_id"] == request["action_id"], "INVALID_STATE", "Action is not active")
        if action == "claim":
            require(run["status"] == "running" and active["status"] == "queued", "INVALID_STATE", "Action cannot be claimed")
            active["status"] = "running"
            run["usage"]["requests"] += 1
        elif action == "fail":
            string(request["reason"])
            if "accounting_complete" in request:
                require(request["accounting_complete"] is False, "INVALID_SCHEMA", "Only incomplete accounting may be flagged")
                run["usage"]["complete"] = False
            require(active["status"] in {"queued", "running", "proposed"}, "INVALID_STATE", "Action cannot fail")
            if active["status"] == "running":
                active["status"], run["status"] = "unknown", "needs_reconcile"
                run["usage"]["complete"] = False
            else:
                active["status"], run["status"] = "failed", "paused"
            run["reason"] = request["reason"]
        elif action == "propose":
            require(active["status"] == "running", "INVALID_STATE", "Proposal needs a claimed action")
            require(type(request["proposal"]) is dict and len(canonical(request["proposal"])) <= MAX_BYTES, "INVALID_SCHEMA", "Expected bounded proposal object")
            usage = request["usage"]
            if usage is not None:
                fields(usage, "total_tokens output_tokens")
                integer(usage["total_tokens"])
                integer(usage["output_tokens"])
                require(usage["output_tokens"] <= usage["total_tokens"], "INVALID_SCHEMA", "Invalid token accounting")
            active["status"], active["proposal"], active["usage"] = "proposed", request["proposal"], usage
            if usage is None:
                run["status"], run["reason"], run["usage"]["complete"] = "paused", "usage_missing", False
            else:
                run["usage"]["total_tokens"] += usage["total_tokens"]
                budget = run["spec"]["budget"]
                if usage["output_tokens"] > active["max_output_tokens"] or run["usage"]["total_tokens"] > budget["total_tokens"]:
                    run["status"], run["reason"] = "paused", "budget_exceeded"
                elif run["usage"]["requests"] >= budget["requests"] or run["usage"]["total_tokens"] >= budget["total_tokens"]:
                    run["status"], run["reason"] = "paused", "budget_exhausted"
        elif action == "commit":
            require(active["status"] == "proposed", "INVALID_STATE", "Commit needs a recorded proposal")
            self._resolve(research, request["output_refs"])
            active["status"], active["output_refs"] = "committed", request["output_refs"]
            if active["kind"] == "stop" and run["usage"]["complete"] and run["reason"] != "budget_exceeded":
                run["status"] = "completed"
