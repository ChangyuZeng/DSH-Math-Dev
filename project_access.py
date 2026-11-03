"""Human-authorized ordinary-chat project associations, outside research/export data."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import os
import re
import stat
import subprocess
import uuid

from backend import Projects, checked_root, make_directory, write_new
from research_store import read_regular, safe_path, sync_directory
from schema import StoreError, decode, digest, fields, identifier, integer, require

FORMAT = "dsh-math-project-access-v1"


def empty_state():
    return {"seq": 0, "head": "0" * 64, "sessions": {}, "operations": {}}


def session_state(session_id):
    return {"session_id": session_id, "rev": 0, "current_project_id": None,
            "auto_record": True, "associated_project_ids": []}


class ProjectAccess:
    def __init__(self, root):
        self.root = checked_root(root)
        self.projects = Projects(root)
        self.directory = safe_path(self.root, "project-access")

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
            fd = os.open(lock, os.O_NOFOLLOW | os.O_NONBLOCK |
                         (os.O_RDWR | os.O_CREAT if writable else os.O_RDONLY), 0o600)
        except FileNotFoundError:
            require(not writable and not any(self.directory.iterdir()), "CORRUPT_SIDECAR", "Nonempty project access store has no lock")
            yield empty_state()
            return
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_PATH", "Unsafe project access lock")
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if writable else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StoreError("LOCKED", "Project access store is in use; retry the same operation") from error
            if writable:
                sync_directory(self.directory)
            yield self._load()
        finally:
            os.close(fd)

    def _load(self):
        state = empty_state()
        for path in sorted(self.directory.iterdir()):
            if path.name == ".writer.lock" or path.name.startswith(".pending-"):
                continue
            require(re.fullmatch(r"[0-9]{20}\.json", path.name), "CORRUPT_SIDECAR", "Unexpected project access entry")
            event = decode(read_regular(safe_path(self.directory, path.name)))
            fields(event, "format seq previous_hash request result event_hash")
            require(event["format"] == FORMAT and type(event["seq"]) is int
                    and event["seq"] == state["seq"] + 1 and path.name == f'{event["seq"]:020d}.json'
                    and event["previous_hash"] == state["head"]
                    and event["event_hash"] == digest({k: v for k, v in event.items() if k != "event_hash"}),
                    "CORRUPT_SIDECAR", "Project access transaction chain mismatch")
            self._apply(state, event)
        return state

    def _apply(self, state, event):
        request = event["request"]
        fields(request, "action session_id expected_rev operation_id project_id auto_record actor")
        identifier(request["session_id"])
        identifier(request["operation_id"])
        integer(request["expected_rev"])
        require(request["actor"] == {"kind": "human", "id": "local-user"}, "CORRUPT_SIDECAR", "Project access requires human authorization")
        key = request["session_id"], request["operation_id"]
        require(key not in state["operations"], "CORRUPT_SIDECAR", "Duplicate project access operation")
        old = state["sessions"].get(request["session_id"], session_state(request["session_id"]))
        require(old["rev"] == request["expected_rev"], "CORRUPT_SIDECAR", "Project access revision mismatch")
        expected = self._updated(old, request)
        require(event["result"] == expected, "CORRUPT_SIDECAR", "Invalid project access result")
        state["sessions"][request["session_id"]] = event["result"]
        state["operations"][key] = event
        state["seq"], state["head"] = event["seq"], event["event_hash"]

    @staticmethod
    def _updated(old, request):
        result = deepcopy(old)
        action, project_id = request["action"], request["project_id"]
        if action in ("associate", "disassociate"):
            identifier(project_id)
            require(request["auto_record"] is None, "INVALID_SCHEMA", "Unexpected recording preference")
            ids = result["associated_project_ids"]
            if action == "associate":
                if project_id not in ids:
                    ids.append(project_id)
                result["current_project_id"] = project_id
            else:
                if project_id in ids:
                    ids.remove(project_id)
                if result["current_project_id"] == project_id:
                    result["current_project_id"] = None
        else:
            require(action == "preferences" and project_id is None and type(request["auto_record"]) is bool,
                    "INVALID_SCHEMA", "Expected boolean recording preference")
            result["auto_record"] = request["auto_record"]
        result["rev"] += 1
        return result

    def change(self, action, session_id, expected_rev, operation_id, project_id=None, auto_record=None):
        identifier(session_id)
        identifier(operation_id)
        integer(expected_rev)
        request = {"action": action, "session_id": session_id, "expected_rev": expected_rev,
                   "operation_id": operation_id, "project_id": project_id, "auto_record": auto_record,
                   "actor": {"kind": "human", "id": "local-user"}}
        self._updated(session_state(session_id), request)
        with self._locked(True) as state:
            previous = state["operations"].get((session_id, operation_id))
            if previous:
                require(previous["request"] == request, "OPERATION_CONFLICT", "Operation id already used with different content")
                return deepcopy(previous["result"])
            old = state["sessions"].get(session_id, session_state(session_id))
            require(old["rev"] == expected_rev, "VERSION_CONFLICT", "Project access revision changed; reload context")
            if action == "associate":
                self.projects.metadata(project_id)
            result = self._updated(old, request)
            event = {"format": FORMAT, "seq": state["seq"] + 1, "previous_hash": state["head"],
                     "request": request, "result": result}
            event["event_hash"] = digest(event)
            candidate = deepcopy(state)
            self._apply(candidate, event)
            temporary = safe_path(self.directory, ".pending-" + str(uuid.uuid4()))
            destination = safe_path(self.directory, f'{event["seq"]:020d}.json')
            require(not destination.exists(), "VERSION_CONFLICT", "Project access transaction already exists")
            write_new(temporary, event)
            os.rename(temporary, destination)
            sync_directory(self.directory)
            return deepcopy(result)

    @staticmethod
    def _allows(metadata, session_id, state):
        session = state["sessions"].get(session_id, session_state(session_id))
        return metadata["session_id"] == session_id or metadata["id"] in session["associated_project_ids"]

    @contextmanager
    def authorized(self, metadata, session_id):
        identifier(session_id)
        with self._locked() as state:
            require(self._allows(metadata, session_id, state), "PROJECT_SCOPE", "Project is not authorized for this chat session")
            yield

    def _visible(self, session_id, state):
        """A broken project cannot hide healthy projects or disclose unknown ids."""
        directory = safe_path(self.root, "projects")
        if not directory.exists():
            return []
        associated = state["sessions"].get(session_id, session_state(session_id))["associated_project_ids"]
        rows = []
        for path in sorted(directory.iterdir()):
            try:
                checked_root(path)
                if not path.is_dir():
                    continue
                metadata = self.projects.metadata(path.name)
            except (StoreError, OSError, ValueError, KeyError, TypeError):
                # A persisted human association is the only surviving authorization
                # when metadata can no longer establish the original owner.
                if path.name in associated:
                    rows.append({"id": path.name, "title": "", "session_id": None,
                                 "schema_version": None, "mode": None})
                continue
            if self._allows(metadata, session_id, state):
                rows.append(metadata)
        return rows

    def context(self, session_id, query="", offset=0, limit=20):
        identifier(session_id)
        require(type(query) is str and len(query) <= 200, "INVALID_SCHEMA", "query must be at most 200 characters")
        integer(offset)
        integer(limit, 1)
        require(limit <= 50, "INVALID_SCHEMA", "limit must be 1..50 projects")
        with self._locked() as state:
            session = deepcopy(state["sessions"].get(session_id, session_state(session_id)))
            from library_store import Library
            library = Library(self.root)
            rows = [metadata for metadata in self._visible(session_id, state)
                    if not library.project_removed(metadata["id"])
                    and query.casefold() in (metadata["id"] + " " + str(metadata.get("title", ""))).casefold()]
            if session["current_project_id"] is not None and library.project_removed(session["current_project_id"]):
                session["current_project_id"] = None
            result = []
            for metadata in rows[offset:offset + limit]:
                row = {**metadata, "current": session["current_project_id"] == metadata["id"],
                       "capability": {"read": False, "record": False}, "root_ref": None}
                try:
                    with self.projects.open_project(metadata["id"]) as (_, store):
                        records = store.records if hasattr(store, "records") else store["state"]["objects"]
                        roots = [versions[-1] for versions in records.values()
                                 if versions[-1]["kind"] == "Problem" and versions[-1]["data"]["parent"] is None]
                        require(len(roots) == 1, "INVALID_SCHEMA", "Project requires exactly one root Problem")
                        row["root_ref"] = {"id": roots[0]["id"], "rev": roots[0]["rev"]}
                    row["capability"] = {"read": True, "record": metadata["schema_version"] == 4 and metadata.get("mode") == "daily_research"}
                except (StoreError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    row["warning"] = {"code": getattr(error, "code", type(error).__name__), "message": str(error)[:1000]}
                result.append(row)
            end = min(offset + limit, len(rows))
            return {**{k: session[k] for k in ("session_id", "rev", "current_project_id", "auto_record")},
                    "projects": result, "offset": offset, "next_offset": end if end < len(rows) else None,
                    "total": len(rows)}


def dispatch_access(root, action, args):
    access = ProjectAccess(root)
    if action == "project-context":
        args = {"query": "", "offset": 0, "limit": 20, **args}
        fields(args, "session_id query offset limit")
        return access.context(**args)
    if action == "project-preferences":
        fields(args, "session_id expected_rev operation_id auto_record")
        return access.change("preferences", **args)
    fields(args, "session_id project_id expected_rev operation_id")
    return access.change({"project-associate": "associate", "project-disassociate": "disassociate"}[action], **args)
