"""Preparation-only sidecars. Immutable transactions, never research objects.

One POSIX flock serializes CAS and durable operation receipts. Each transaction is
published with fsync + atomic rename, so interrupted temporary files are ignored.
Project publication has a durable intent before touching its private staging area;
only that exact operation may recover a pending finalization.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import os
import re
import stat
import uuid

from backend import HERE, Projects, checked_root, make_directory, write_new
from research_store import now, read_regular, safe_path, sync_directory
from schema import (StoreError, canonical, context, decode, digest, fields, identifier,
                    integer, ref, require, sha, validate_data)

MAX_FIELDS_BYTES = 256 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_BODY_BYTES = 256 * 1024
DRAFT_FORMAT = "dsh-math-preparation-v1"
PAD_FORMAT = "dsh-math-scratchpad-v1"
TX_FORMAT = "dsh-math-workbench-transaction-v1"


def descriptors():
    value = decode(read_regular(HERE / "project-fields.json"))
    require(value["format"] == "dsh-math-project-fields-v1", "INVALID_SCHEMA", "Unsupported project field descriptors")
    return value


def utf8(value):
    try:
        return value.encode("utf-8")
    except UnicodeError as error:
        raise StoreError("INVALID_SCHEMA", "Text must be valid UTF-8") from error


def validate_field_shape(value):
    require(type(value) is dict, "INVALID_SCHEMA", "Expected project fields object")
    available = {item["key"]: item for item in descriptors()["fields"]}
    require(set(value) <= set(available), "INVALID_SCHEMA", "Unknown project field")
    for key, content in value.items():
        descriptor = available[key]
        if descriptor["type"] == "string[]":
            require(type(content) is list and len(content) <= 1024 and all(type(item) is str for item in content),
                    "INVALID_SCHEMA", "Expected bounded string array: " + key)
            require(all(len(utf8(item)) <= MAX_TEXT_BYTES for item in content), "TOO_LARGE", "Project field item is too large: " + key)
        else:
            require(type(content) is str, "INVALID_SCHEMA", "Expected string: " + key)
            require(len(utf8(content)) <= MAX_TEXT_BYTES, "TOO_LARGE", "Project field is too large: " + key)
            if key == "title":
                require(len(content) <= 2000, "TOO_LARGE", "Project title exceeds 2000 characters")
    require(len(canonical(value)) <= MAX_FIELDS_BYTES, "TOO_LARGE", "Project fields exceed 256 KiB")


def validate_fields(value):
    """Shared human/model validation; missing facts are never filled in silently."""
    validate_field_shape(value)
    spec = descriptors()
    missing, errors, questions = [], [], []
    enums = {"target_type": spec["target_types"], "knowledge_status": spec["knowledge_statuses"]}
    for item in spec["fields"]:
        key = item["key"]
        content = value.get(key)
        empty = content is None or (type(content) is str and not content.strip()) or (type(content) is list and not any(part.strip() for part in content))
        code = None
        if item["required"] and empty:
            missing.append(key)
            code, message = "REQUIRED", "请补充" + item["label"] + "。"
        elif key in value and item["type"] == "enum" and content not in {option["value"] for option in enums[key]}:
            code, message = "INVALID_ENUM", "请选择有效的" + item["label"] + "。"
        elif type(content) is list and any(not part.strip() for part in content):
            code, message = "EMPTY_ITEM", item["label"] + "不能包含空白条目。"
        if code:
            errors.append({"field": key, "code": code, "message": message})
            question = {"id": key, "question": message + item.get("help", "")}
            if key in enums:
                question["options"] = [{"label": option["label"], **({"description": option["description"]} if "description" in option else {})} for option in enums[key]]
            questions.append(question)
    if not errors:
        try:
            validate_data("Problem", {**value, "parent": None, "relation": ""})
        except StoreError as error:
            errors.append({"field": "problem", "code": error.code, "message": str(error)})
            questions.append({"id": "problem", "question": "请核对完整问题字段：" + str(error)})
    return {"complete": not errors, "missing_fields": missing, "errors": errors, "questions": questions}


def draft_result(draft, pending_finalization=None):
    return {"draft": deepcopy(draft), "validation": validate_fields(draft["fields"]) if draft else None,
            "pending_finalization": deepcopy(pending_finalization)}


def scratch_content(title, body):
    require(type(title) is str and type(body) is str, "INVALID_SCHEMA", "Expected scratchpad title and body strings")
    require(len(title) <= 2000, "TOO_LARGE", "Scratchpad title exceeds 2000 characters")
    utf8(title)
    require(len(utf8(body)) <= MAX_BODY_BYTES, "TOO_LARGE", "Scratchpad body exceeds 256 KiB")


def empty_state():
    return {"seq": 0, "head": "0" * 64, "drafts": {}, "pads": {}, "operations": {}, "pending": {}, "links": []}


class Workbench:
    def __init__(self, root, *, actor=None, origin=None):
        self.root = checked_root(root)
        self.projects = Projects(root)
        self.actor = deepcopy(actor or {"kind": "human", "id": "local-user"})
        self.origin = deepcopy(origin)
        require(self.actor.get("kind") in ("human", "model"), "FORBIDDEN", "Unsupported preparation actor")
        self.directory = safe_path(self.root, "workbench")

    def _identity(self, session_id):
        identifier(session_id)
        origin = self.origin or {"source": "dsh-math-human", "session_id": session_id, "run_id": None, "tool_call_id": None}
        context(self.actor, origin)
        require(origin["session_id"] == session_id, "PROJECT_SCOPE", "Bound session does not match request")
        return deepcopy(self.actor), deepcopy(origin)

    def _request(self, action, session_id, operation_id, arguments):
        identifier(operation_id)
        actor, origin = self._identity(session_id)
        return {"action": action, "session_id": session_id, "operation_id": operation_id,
                "actor": actor, "origin": origin, "arguments": deepcopy(arguments)}

    @staticmethod
    def _key(request):
        return request["session_id"], request["operation_id"]

    @contextmanager
    def _locked(self, writable=False):
        checked_root(self.directory)
        if not writable and not self.directory.exists():
            yield empty_state()
            return
        if writable:
            make_directory(self.directory)
        lock = safe_path(self.directory, ".writer.lock")
        try:
            fd = os.open(lock, os.O_NOFOLLOW | (os.O_RDWR | os.O_CREAT if writable else os.O_RDONLY) | os.O_NONBLOCK, 0o600)
        except FileNotFoundError:
            # A first writer can stop between mkdir and lock creation. Reading
            # that genuinely empty directory remains pure, without creating a lock.
            require(not writable and not any(self.directory.iterdir()), "CORRUPT_SIDECAR", "Nonempty workbench has no writer lock")
            yield empty_state()
            return
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_PATH", "Unsafe sidecar lock inode")
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if writable else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StoreError("LOCKED", "Preparation store is in use; retry the same operation") from error
            if writable:
                sync_directory(self.directory)
            yield self._load()
        finally:
            os.close(fd)

    def _load(self):
        state = empty_state()
        for path in sorted(self.directory.iterdir()):
            # Orphan .pending files are never authoritative and are never rewritten.
            if path.name.startswith(".pending-") or path.name == ".writer.lock":
                continue
            require(re.fullmatch(r"[0-9]{20}\.json", path.name), "CORRUPT_SIDECAR", "Unexpected workbench entry")
            event = decode(read_regular(safe_path(self.directory, path.name)))
            fields(event, "format seq previous_hash kind request data result event_hash")
            require(event["format"] == TX_FORMAT and type(event["seq"]) is int and event["seq"] == state["seq"] + 1
                    and path.name == f'{event["seq"]:020d}.json' and event["previous_hash"] == state["head"]
                    and digest({key: value for key, value in event.items() if key != "event_hash"}) == event["event_hash"],
                    "CORRUPT_SIDECAR", "Workbench transaction chain mismatch")
            self._apply(state, event)
        return state

    def _apply(self, state, event):
        request = event["request"]
        fields(request, "action session_id operation_id actor origin arguments")
        context(request["actor"], request["origin"])
        identifier(request["session_id"])
        identifier(request["operation_id"])
        require(request["origin"]["session_id"] == request["session_id"], "CORRUPT_SIDECAR", "Transaction session mismatch")
        key, kind = self._key(request), event["kind"]
        previous = state["operations"].get(key)
        completing = kind in ("finalize-complete", "import-complete")
        require((not previous and not completing) or (previous and previous["kind"] == kind.replace("complete", "intent") and previous["request"] == request),
                "CORRUPT_SIDECAR", "Duplicate workbench operation")
        if kind in ("draft-save", "finalize-complete"):
            draft = event["result"]["draft"]
            fields(draft, "format id session_id rev fields status committed_project_id actor origin saved_at")
            identifier(draft["id"])
            validate_field_shape(draft["fields"])
            old = state["drafts"].get(draft["id"])
            require(draft["format"] == DRAFT_FORMAT and draft["session_id"] == request["session_id"]
                    and type(draft["rev"]) is int and draft["rev"] == (old["rev"] + 1 if old else 1)
                    and draft["actor"] == request["actor"] and draft["origin"] == request["origin"]
                    and draft["status"] == ("committed" if kind == "finalize-complete" else "draft"),
                    "CORRUPT_SIDECAR", "Invalid draft revision")
            if kind == "finalize-complete":
                require(old is not None and draft["fields"] == old["fields"]
                        and draft["committed_project_id"] == event["result"]["project"]["id"],
                        "CORRUPT_SIDECAR", "Invalid committed draft")
                state["pending"].pop(draft["id"], None)
            else:
                require(draft["committed_project_id"] is None and (not old or old["status"] == "draft")
                        and draft["id"] not in state["pending"], "CORRUPT_SIDECAR", "Changed frozen draft")
            # Insertion order is transaction order, independent of wall-clock jumps.
            state["drafts"].pop(draft["id"], None)
            state["drafts"][draft["id"]] = draft
        elif kind in ("scratchpad-save", "scratchpad-restore"):
            pad = event["result"]["scratchpad"]
            fields(pad, "format id project_id rev title body actor origin saved_at")
            identifier(pad["project_id"])
            scratch_content(pad["title"], pad["body"])
            versions = state["pads"].setdefault(pad["project_id"], [])
            require(pad["format"] == PAD_FORMAT and pad["id"] == "main" and type(pad["rev"]) is int
                    and pad["rev"] == len(versions) + 1 and pad["actor"] == request["actor"] and pad["origin"] == request["origin"],
                    "CORRUPT_SIDECAR", "Invalid scratchpad revision")
            versions.append(pad)
        elif kind == "scratchpad-promote-link":
            state["links"].append(event["data"])
        elif kind == "finalize-intent":
            draft_id = request["arguments"]["draft_id"]
            require(draft_id not in state["pending"], "CORRUPT_SIDECAR", "Duplicate pending finalization")
            state["pending"][draft_id] = key
        else:
            require(kind in ("import-intent", "import-complete"), "CORRUPT_SIDECAR", "Unknown workbench transaction")
        state["operations"][key] = event
        state["seq"], state["head"] = event["seq"], event["event_hash"]

    def _append(self, state, kind, request, *, data=None, result=None):
        event = {"format": TX_FORMAT, "seq": state["seq"] + 1, "previous_hash": state["head"],
                 "kind": kind, "request": request, "data": data, "result": result}
        event["event_hash"] = digest(event)
        candidate = deepcopy(state)
        self._apply(candidate, event)  # Check before any authoritative write.
        temporary = safe_path(self.directory, ".pending-" + str(uuid.uuid4()))
        destination = safe_path(self.directory, f'{event["seq"]:020d}.json')
        require(not destination.exists(), "VERSION_CONFLICT", "Workbench revision already exists")
        write_new(temporary, event)
        # Both paths are confined to the checked sidecar directory, no overwrite.
        os.rename(temporary, destination)
        sync_directory(self.directory)
        state.clear()
        state.update(candidate)
        return deepcopy(result)

    @staticmethod
    def _idempotency_request(request):
        # A lost model response is retried with a fresh host tool-call identity.
        # Keep original provenance in storage/receipts, but compare semantic
        # operations independently of that transport-attempt identifier only.
        return {**request, "origin": {key: value for key, value in request["origin"].items() if key != "tool_call_id"}}

    def _previous(self, state, request):
        previous = state["operations"].get(self._key(request))
        if previous:
            require(self._idempotency_request(previous["request"]) == self._idempotency_request(request),
                    "OPERATION_CONFLICT", "Same operation id with different payload or identity")
        return previous

    def _draft(self, state, session_id, draft_id):
        identifier(draft_id)
        value = state["drafts"].get(draft_id)
        require(value is not None, "NOT_FOUND", "Preparation draft not found")
        require(value["session_id"] == session_id, "PROJECT_SCOPE", "Draft belongs to another session")
        return value

    def draft_read(self, session_id, draft_id=None):
        self._identity(session_id)
        with self._locked() as state:
            if draft_id is not None:
                draft = self._draft(state, session_id, draft_id)
            else:
                # Recovery must remain discoverable even if a later chat created
                # another draft. Pending intent order is durable transaction order.
                draft = next((state["drafts"][draft_id] for draft_id in state["pending"]
                              if state["drafts"][draft_id]["session_id"] == session_id), None)
                if draft is None:
                    draft = next((value for value in reversed(state["drafts"].values())
                                  if value["session_id"] == session_id and value["status"] == "draft"), None)
            pending = None
            if draft is not None and draft["id"] in state["pending"]:
                intent = state["operations"][state["pending"][draft["id"]]]["request"]
                pending = {"operation_id": intent["operation_id"], "expected_rev": intent["arguments"]["expected_rev"]}
            return draft_result(draft, pending)

    def draft_save(self, session_id, expected_rev, operation_id, patch, draft_id=None):
        integer(expected_rev)
        validate_field_shape(patch)
        if draft_id is not None:
            identifier(draft_id)
        request = self._request("draft-save", session_id, operation_id,
                                {"draft_id": draft_id, "expected_rev": expected_rev, "patch": patch})
        with self._locked(True) as state:
            previous = self._previous(state, request)
            if previous:
                return deepcopy(previous["result"])
            if draft_id is None:
                require(expected_rev == 0, "VERSION_CONFLICT", "A new draft requires expected_rev=0")
                draft_id = str(uuid.uuid4())
                value = {item["key"]: item["default"] for item in descriptors()["fields"] if "default" in item}
                value["conventions"] = []
            else:
                old = self._draft(state, session_id, draft_id)
                require(draft_id not in state["pending"], "FINALIZE_PENDING", "This exact draft is frozen by a pending finalization; retry that operation")
                require(old["status"] == "draft", "DRAFT_COMMITTED", "Committed draft is immutable")
                require(old["rev"] == expected_rev, "VERSION_CONFLICT", "Draft revision changed; read before saving")
                value = old["fields"]
            draft = {"format": DRAFT_FORMAT, "id": draft_id, "session_id": session_id, "rev": expected_rev + 1,
                     "fields": {**value, **deepcopy(patch)}, "status": "draft", "committed_project_id": None,
                     "actor": request["actor"], "origin": request["origin"], "saved_at": now()}
            return self._append(state, "draft-save", request, result=draft_result(draft))

    def finalize_draft(self, session_id, draft_id, expected_rev, operation_id):
        require(self.actor["kind"] == "human", "FORBIDDEN", "Only the human entry may finalize a project")
        identifier(draft_id)
        integer(expected_rev, 1)
        request = self._request("finalize", session_id, operation_id, {"draft_id": draft_id, "expected_rev": expected_rev})
        with self._locked(True) as state:
            previous = self._previous(state, request)
            if previous and previous["kind"] == "finalize-complete":
                return deepcopy(previous["result"])
            if previous:
                request = previous["request"]
            draft = self._draft(state, session_id, draft_id)
            require(draft_id not in state["pending"] or state["pending"][draft_id] == self._key(request),
                    "FINALIZE_PENDING", "Another exact operation owns this pending finalization")
            require(draft["status"] == "draft", "DRAFT_COMMITTED", "Committed draft is immutable; retry the original finalization operation")
            require(draft["rev"] == expected_rev, "VERSION_CONFLICT", "Finalization requires the exact saved draft revision")
            validation = validate_fields(draft["fields"])
            require(validation["complete"], "INCOMPLETE_DRAFT", canonical(validation).decode("utf-8"))
            problem = {**draft["fields"], "parent": None, "relation": ""}
            validate_data("Problem", problem)
            if not previous:
                self._append(state, "finalize-intent", request, data={"project_id": str(uuid.uuid4()), "draft": deepcopy(draft)})
                previous = state["operations"][self._key(request)]
            require(previous["data"]["draft"] == draft, "OPERATION_CONFLICT", "Frozen draft changed")
            project = self.projects._create_project(session_id, problem, previous["data"]["project_id"])
            committed = {**draft, "rev": draft["rev"] + 1, "status": "committed", "committed_project_id": project["id"],
                         "actor": request["actor"], "origin": request["origin"], "saved_at": now()}
            return self._append(state, "finalize-complete", request, result={"project": project, "draft": committed})

    @staticmethod
    def _pad_result(versions, rev=None):
        if rev is not None:
            integer(rev, 1)
            require(rev <= len(versions), "NOT_FOUND", "Scratchpad revision not found")
        pad = versions[rev - 1] if rev is not None else versions[-1] if versions else None
        return {"scratchpad": deepcopy(pad), "history": [{key: value[key] for key in ("rev", "title", "saved_at", "actor")} for value in versions]}

    def _project(self, project_id, session_id, writable=False):
        self._identity(session_id)
        return self.projects.open_project(project_id, require_writable=writable, session_id=session_id, model=self.actor["kind"] == "model")

    def scratchpad_read(self, session_id, project_id, rev=None):
        if rev is not None:
            integer(rev, 1)
        with self._project(project_id, session_id):
            with self._locked() as state:
                return self._pad_result(state["pads"].get(project_id, []), rev)

    def _pad_write(self, action, session_id, project_id, expected_rev, operation_id, content):
        integer(expected_rev)
        request = self._request(action, session_id, operation_id, {"project_id": project_id, "expected_rev": expected_rev, **content})
        # Lock order is always research (shared) -> workbench. Finalization only
        # opens its new, not-yet-addressable project; no reverse lock dependency.
        with self._project(project_id, session_id, True):
            with self._locked(True) as state:
                previous = self._previous(state, request)
                if previous:
                    return deepcopy(previous["result"])
                versions = state["pads"].get(project_id, [])
                require(expected_rev == len(versions), "VERSION_CONFLICT", "Scratchpad revision changed; local edits must be retained")
                if action == "scratchpad-restore":
                    source = self._pad_result(versions, content["restore_rev"])["scratchpad"]
                    title, body = source["title"], source["body"]
                else:
                    title, body = content["title"], content["body"]
                pad = {"format": PAD_FORMAT, "id": "main", "project_id": project_id, "rev": expected_rev + 1,
                       "title": title, "body": body, "actor": request["actor"], "origin": request["origin"], "saved_at": now()}
                return self._append(state, action, request, result=self._pad_result([*versions, pad]))

    def scratchpad_save(self, session_id, project_id, expected_rev, operation_id, title, body):
        scratch_content(title, body)
        return self._pad_write("scratchpad-save", session_id, project_id, expected_rev, operation_id, {"title": title, "body": body})

    def scratchpad_restore(self, session_id, project_id, expected_rev, operation_id, restore_rev):
        integer(restore_rev, 1)
        return self._pad_write("scratchpad-restore", session_id, project_id, expected_rev, operation_id, {"restore_rev": restore_rev})

    def scratchpad_promote_link(self, session_id, project_id, scratchpad_rev, target, operation_id):
        require(self.actor["kind"] == "human", "FORBIDDEN", "Only a human may link formal content")
        integer(scratchpad_rev, 1)
        ref(target)
        request = self._request("scratchpad-promote-link", session_id, operation_id,
                                {"project_id": project_id, "scratchpad_rev": scratchpad_rev, "target": target})
        with self._project(project_id, session_id, True) as (_, store):
            formal = store.get(target["id"], target["rev"])
            require(formal["kind"] in ("Note", "Obligation"), "REFERENCE_KIND", "Promotion may link only an exact Note or Obligation revision")
            with self._locked(True) as state:
                previous = self._previous(state, request)
                if previous:
                    return deepcopy(previous["result"])
                source = self._pad_result(state["pads"].get(project_id, []), scratchpad_rev)["scratchpad"]
                provenance = {"project_id": project_id, "scratchpad_rev": scratchpad_rev, "scratchpad_hash": digest(source),
                              "target": deepcopy(target), "actor": request["actor"], "origin": request["origin"], "saved_at": now()}
                return self._append(state, "scratchpad-promote-link", request, data=provenance, result={"linked": True, "target": deepcopy(target)})

    def import_legacy_confirm(self, session_id, campaign, expected_fingerprint, operation_id):
        require(self.actor["kind"] == "human", "FORBIDDEN", "Only a human may import a project")
        sha(expected_fingerprint)
        require(type(campaign) is str and campaign.strip(), "INVALID_SCHEMA", "Expected legacy campaign path")
        source = checked_root(campaign)
        for output in (self.directory, safe_path(self.root, "project-staging"), safe_path(self.root, "projects")):
            require(source != output and source not in output.parents, "UNSAFE_PATH", "Import outputs must be outside the source campaign")
        campaign = str(source)
        request = self._request("import-legacy", session_id, operation_id, {"campaign": campaign, "expected_fingerprint": expected_fingerprint})
        with self._locked(True) as state:
            previous = self._previous(state, request)
            if previous and previous["kind"] == "import-complete":
                return deepcopy(previous["result"])
            if previous:
                request = previous["request"]
            if not previous:
                preview = self.projects.import_legacy_preview(campaign)["preview"]
                require(preview["fingerprint"] == expected_fingerprint, "SOURCE_CHANGED", "Legacy source changed; preview and confirm again")
                self._append(state, "import-intent", request, data={"preview": preview, "project_id": str(uuid.uuid4())})
                previous = state["operations"][self._key(request)]
            project = self.projects._import_legacy_project(session_id, campaign, previous["data"]["preview"], previous["data"]["project_id"])
            return self._append(state, "import-complete", request, result=project)
