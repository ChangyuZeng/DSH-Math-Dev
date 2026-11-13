"""Host-only, opt-in search handoff using existing immutable research evidence.

Callers hold workflow_service.transaction. No execution, strategy, ranking or model
calls. Every material is explicitly authorized by exact record ref and record hash;
attachments are verified and read in full, never recovered from temporary paths.
"""
from copy import deepcopy
import hashlib

from backend import Projects
from checking import ACTOR as CHECK_ACTOR, checker_hash
from research_store import read_regular, safe_path
from schema import canonical, decode, digest, fields, identifier, ref, require, sha
from workflow_candidates import CANDIDATE_ACTOR, _attachment_bytes, _evidence, _ids
from workflow_store import WorkflowStore

ACTOR = {"kind": "system", "id": "workflow-search-checkpoint"}
FORMAT = "dsh-math-search-checkpoint-v1"
MAX_BYTES = 2 * 1024 * 1024


def _record(store, binding, kind=None):
    fields(binding, "ref sha256")
    ref(binding["ref"])
    sha(binding["sha256"])
    obj = store.get(binding["ref"]["id"], binding["ref"]["rev"])
    require(store.get(obj["id"])["rev"] == obj["rev"], "CHECKPOINT_STALE", "Checkpoint material revision changed")
    require(digest(obj) == binding["sha256"], "CHECKPOINT_BINDING", "Authorized record hash differs")
    require(kind is None or obj["kind"] == kind, "REFERENCE_KIND", "Unexpected checkpoint material kind")
    return obj


def _material(store, binding, problem_ref):
    obj = _record(store, binding, "Evidence")
    require(obj["data"]["problem_ref"] == problem_ref, "TARGET_MISMATCH", "Checkpoint material belongs to another problem")
    attachments = []
    for descriptor in obj["data"]["attachments"]:
        store._verify_attachment(descriptor)
        raw = read_regular(safe_path(store.root, descriptor["relative_path"]))
        require(len(raw) <= MAX_BYTES, "TOO_LARGE", "Checkpoint material exceeds limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            require(False, "INVALID_SCHEMA", "Checkpoint context materials must be UTF-8")
        attachments.append({"name": descriptor["logical_name"], "sha256": descriptor["sha256"], "text": text})
    require(attachments, "CHECKPOINT_BINDING", "Checkpoint material has no persistent attachment")
    return {"binding": deepcopy(binding), "record": obj, "attachments": attachments}


def _verification(store, binding, candidate, candidate_binding):
    if binding is None:
        return {"status": "unverified", "authority": "none", "global_problem_proved": False}
    review = _record(store, binding, "Review")
    data = review["data"]
    execution = data.get("execution") or {}
    operation = store.operations.get(execution.get("operation_id"))
    require(review["actor"] == CHECK_ACTOR and data.get("method") == "program" and
            operation is not None and operation["actor"] == CHECK_ACTOR and
            operation["control"].get("action") == "check.started" and
            execution.get("start_event_id") == operation["event_id"],
            "CHECKPOINT_VERIFICATION", "Verification requires an archived native program start and Review")
    control = operation["control"]
    for premise in data["basis"]["objects"]:
        _record(store, premise)
    require(control["request"] == {"checker_id": candidate_binding["checker"]["id"],
            "target": candidate_binding["obligation_ref"], "witness": candidate["ref"]} and
            execution.get("witness") == candidate["ref"] and
            execution.get("input_sha256") == candidate_binding["candidate_sha256"] and
            control.get("input_sha256") == candidate_binding["candidate_sha256"] and
            execution.get("checker_sha256") == candidate_binding["native_checker_sha256"] and
            control.get("checker_sha256") == candidate_binding["native_checker_sha256"] and
            data.get("target") == candidate_binding["obligation_ref"],
            "CHECKPOINT_VERIFICATION", "Review is not bound to this exact candidate and checker")
    # The native start archives the actual checker source. Verify it on every read.
    store._verify_attachment(control["checker_source"])
    require(not any(len(store.records.get(r["id"], [])) != r["rev"] for r in
                    [*data.get("dependencies", []), *data.get("evidence", [])]),
            "CHECKPOINT_STALE", "Review premises changed")
    return {"status": "completed", "verdict": data["verdict"], "error_code": data["error_code"],
            "scope": data["scope"], "execution": execution, "review": deepcopy(binding),
            "authority": "local_program", "global_problem_proved": False}


def _resolve(store, run, authorization):
    fields(authorization, "problem checker candidate provenance code seed search_scope reusable_state review objective")
    problem = _record(store, authorization["problem"], "Problem")
    problem_ref = run["spec"]["problem_ref"]
    require(authorization["problem"]["ref"] == problem_ref, "TARGET_MISMATCH", "Checkpoint target differs")
    require(authorization["checker"] == run["spec"].get("execution", {}).get("checker") and
            authorization["checker"] is not None, "CHECKER_BINDING", "Checkpoint checker differs from frozen run")
    materials = {name: _material(store, authorization[name], problem_ref)
                 for name in ("candidate", "provenance", "code", "seed", "search_scope")}
    require(type(authorization["reusable_state"]) is list and len(authorization["reusable_state"]) <= 16,
            "INVALID_SCHEMA", "Select at most sixteen reusable materials")
    materials["reusable_state"] = [_material(store, value, problem_ref) for value in authorization["reusable_state"]]
    provenance = materials["provenance"]["record"]
    binding = decode(_attachment_bytes(store, provenance, "candidate-binding.json"))
    require(binding["project_id"] == run["project_id"] and binding["run_id"] == run["run_id"] and
            binding["problem_ref"] == problem_ref and binding["checker"] == authorization["checker"],
            "CHECKPOINT_BINDING", "Archived candidate identity differs")
    ids = _ids(run["project_id"], run["run_id"], binding["call_id"])
    require(authorization["candidate"]["ref"] == {"id": ids["evidence"], "rev": 1} and
            authorization["provenance"]["ref"] == {"id": ids["provenance"], "rev": 1} and
            ids["candidate_operation"] in store.operations,
            "CHECKPOINT_BINDING", "Checkpoint needs an existing candidate archive")
    candidate_operation = store.operations[ids["candidate_operation"]]
    require(candidate_operation["actor"] == CANDIDATE_ACTOR and
            candidate_operation["origin"]["source"] == "workflow-v2-candidate" and
            candidate_operation["origin"]["run_id"] == run["run_id"] and
            candidate_operation["origin"]["tool_call_id"] == binding["call_id"] and
            materials["candidate"]["record"]["actor"] == CANDIDATE_ACTOR and
            provenance["actor"] == CANDIDATE_ACTOR,
            "CHECKPOINT_BINDING", "Candidate archive authority or origin differs")
    require(binding["native_checker_sha256"] is None or
            checker_hash(binding["checker"]["id"]) == binding["native_checker_sha256"],
            "CHECKPOINT_STALE", "Native checker source changed since candidate archival")
    candidate_raw = _attachment_bytes(store, materials["candidate"]["record"], "candidate.json")
    require(hashlib.sha256(candidate_raw).hexdigest() == binding["candidate_sha256"],
            "CHECKPOINT_BINDING", "Candidate content hash differs")
    # Obligation identity is derived by the host, never supplied by the model.
    binding["obligation_ref"] = {"id": ids["obligation"], "rev": 1}
    fields(authorization["objective"], "original proxy")
    original = {key: problem["data"][key] for key in
                ("statement", "quantifiers", "domain", "conventions", "target_type", "completion_criteria")}
    require(authorization["objective"]["original"] == original,
            "TARGET_MISMATCH", "Original objective must equal the frozen problem, separate from proxy")
    proxy = authorization["objective"]["proxy"]
    if proxy is not None:
        fields(proxy, "definition direction evaluator_sha256 scope_ref")
        require(type(proxy["definition"]) is str and proxy["direction"] in ("minimize", "maximize"),
                "INVALID_SCHEMA", "Expected an explicit proxy definition and direction")
        sha(proxy["evaluator_sha256"])
        require(proxy["scope_ref"] == authorization["search_scope"]["ref"], "CHECKPOINT_BINDING", "Proxy scope must be authorized")
        require(proxy["evaluator_sha256"] in [a["sha256"] for a in materials["code"]["attachments"]],
                "CHECKPOINT_BINDING", "Proxy evaluator code is not an authorized archived attachment")
    verification = _verification(store, authorization["review"], authorization["candidate"], binding)
    parent_request = next((event.get("parent_request_id") for event in run.get("execution_events", [])
                           if event.get("type") == "tool_started" and event.get("action_id") == binding["action_id"]
                           and event.get("call_id") == binding["call_id"]), None)
    return {"source_action_id": binding["action_id"], "source_call_id": binding["call_id"],
            "source_request_id": parent_request,
            "candidate_sha256": binding["candidate_sha256"], "objective": deepcopy(authorization["objective"]),
            "materials": materials, "verification": verification,
            "notice": "Seed, code, search scope and reusable text are supplied research data. A local program verdict covers only its recorded scope; no proxy ranking, global proof or stagnation conclusion is implied."}


def archive_checkpoint(root, project_id, run_id, action_id, operation_id, authorization):
    """Explicit host authorization is not exposed as a model tool or public control."""
    identifier(operation_id)
    run = WorkflowStore(root).dispatch({"action": "read", "project_id": project_id, "run_id": run_id})
    require(any(a["action_id"] == action_id for a in run["actions"]), "NOT_FOUND", "Source action not found")
    origin = {"source": "workflow-search-checkpoint", "run_id": run_id,
              "session_id": Projects(root).metadata(project_id)["session_id"], "tool_call_id": action_id}
    with Projects(root).open_project(project_id, writable=True, actor=ACTOR, origin=origin) as (_, store):
        resolved = _resolve(store, run, authorization)
        require(resolved["source_action_id"] == action_id, "CHECKPOINT_BINDING", "Candidate belongs to another source action")
        manifest = {"format": FORMAT, "project_id": project_id, "run_id": run_id, "action_id": action_id,
                    "authorization": deepcopy(authorization), "authorization_sha256": digest(authorization)}
        require(len(canonical(manifest)) <= MAX_BYTES, "TOO_LARGE", "Checkpoint manifest exceeds limit")
        ident = "wcp-" + digest([project_id, run_id, operation_id])
        require(len(store.records.get(ident, [])) <= 1, "CHECKPOINT_STALE", "Checkpoint must remain immutable")
        attachment = store._archive_bytes(canonical(manifest), "search-checkpoint.json")
        store.commit(operation_id, [{"kind": "Evidence", "id": ident, "expected_rev": 0,
            "data": _evidence(run["spec"]["problem_ref"], [attachment], "computation",
                               "workflow-search-checkpoint:" + operation_id,
                               "Explicitly authorized search handoff; verification retains its local authority.")}])
        obj = store.get(ident, 1)
        return {"ref": {"id": ident, "rev": 1}, "sha256": digest(obj)}


def read_checkpoints(store, run, bindings):
    """Load full authorized bytes for a *later* action snapshot, with read evidence.

    Actual handoff occurs when this snapshot is reserved/queued as a next action.
    It establishes input inclusion, not model compliance or influence on a decision.
    """
    require(type(bindings) is list and len(bindings) <= 8, "INVALID_SCHEMA", "Select at most eight checkpoints")
    values, watched = [], []
    for binding in bindings:
        obj = _record(store, binding, "Evidence")
        require(obj["actor"] == ACTOR and obj["origin"]["run_id"] == run["run_id"],
                "CHECKPOINT_BINDING", "Checkpoint was not host archived for this run")
        manifest = decode(_attachment_bytes(store, obj, "search-checkpoint.json"))
        require(manifest["format"] == FORMAT and manifest["project_id"] == run["project_id"] and
                manifest["run_id"] == run["run_id"] and
                digest(manifest["authorization"]) == manifest["authorization_sha256"],
                "CHECKPOINT_BINDING", "Checkpoint manifest identity or authorization hash differs")
        source = next((a for a in run["actions"] if a["action_id"] == manifest["action_id"]), None)
        require(source is not None and source["status"] in ("committed", "failed"),
                "CHECKPOINT_ACTION", "Only a finished earlier action can supply a checkpoint")
        resolved = _resolve(store, run, manifest["authorization"])
        require(resolved["source_action_id"] == manifest["action_id"], "CHECKPOINT_BINDING", "Checkpoint action differs")
        values.append({"checkpoint": deepcopy(binding), "authorization_sha256": manifest["authorization_sha256"],
                       **resolved, "read_evidence": {"material_sha256": digest(resolved),
                       "source_action_id": manifest["action_id"], "use": "next_action_input_only",
                       "decision_influence": "not_observed"}})
        auth = manifest["authorization"]
        watched.extend([binding["ref"], *[auth[k]["ref"] for k in ("problem", "candidate", "provenance", "code", "seed", "search_scope")],
                        *[x["ref"] for x in auth["reusable_state"]], *([auth["review"]["ref"]] if auth["review"] else [])])
        if auth["review"]:
            review = store.get(auth["review"]["ref"]["id"], auth["review"]["ref"]["rev"])
            watched.extend(row["ref"] for row in review["data"]["basis"]["objects"])
    require(len(canonical(values)) <= 4 * MAX_BYTES, "TOO_LARGE", "Full checkpoint context exceeds limit")
    return values, watched
