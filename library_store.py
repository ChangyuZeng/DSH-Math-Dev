"""Project knowledge directory and reversible metadata, never immutable content edits.

Transactions use the workbench's durable publication/locking protocol in a separate
sidecar. Replay reconstructs each result from its request and checks CAS and shape;
a valid hash alone cannot bless an invalid metadata transition.
"""
from copy import deepcopy
import json
import re

from research_store import now, read_regular, safe_path
from schema import canonical, decode, digest, fields, identifier, integer, require
from workbench_store import Workbench, empty_state

FORMAT = "dsh-math-library-transaction-v1"
KINDS = frozenset(("object", "scratchpad", "draft", "project"))
FORMAL = frozenset(("Problem", "Note", "Obligation", "Evidence"))


def record_title(obj):
    """Use authored text for directory labels without exposing internal identifiers."""
    data = obj["data"]
    title = data.get("title")
    if type(title) is str and title.strip():
        return title
    statement = data.get("statement")
    if type(statement) is str:
        statement = " ".join(statement.split())
        if statement and statement != obj["id"]:
            return statement if len(statement) <= 160 else statement[:159] + "…"
    return {"Problem": "研究问题", "Note": "笔记", "Obligation": "待证事项", "Evidence": "参考资料"}[obj["kind"]]


def entry_shape(entry):
    fields(entry, "kind id")
    require(entry["kind"] in KINDS, "INVALID_SCHEMA", "Unknown library entry kind")
    identifier(entry["id"])


def patch_shape(patch):
    require(type(patch) is dict and set(patch) <= {"title", "summary", "tags", "pinned"},
            "INVALID_SCHEMA", "Only title, summary, tags and pinned metadata may change")
    for key, value in patch.items():
        if key == "tags":
            require(type(value) is list and len(value) <= 50 and all(type(tag) is str and 0 < len(tag) <= 100 for tag in value)
                    and len(set(value)) == len(value), "INVALID_SCHEMA", "Expected up to 50 distinct bounded tags")
        elif key == "pinned":
            require(type(value) is bool, "INVALID_SCHEMA", "Expected pinned boolean")
        else:
            require(type(value) is str, "INVALID_SCHEMA", "Expected metadata text")
            require(len(value) <= (2000 if key == "title" else 12000), "TOO_LARGE", "Metadata text exceeds bounds")
    require(len(canonical(patch)) <= 64 * 1024, "TOO_LARGE", "Metadata patch exceeds 64 KiB")


def default_metadata():
    return {"metadata_rev": 0, "title": None, "summary": None, "tags": [], "pinned": False,
            "removed": False, "updated_at": None}


class Library(Workbench):
    def __init__(self, root, *, actor=None, origin=None):
        super().__init__(root, actor=actor, origin=origin)
        self.directory = safe_path(self.root, "library")

    @staticmethod
    def _entry_key(project_id, entry, session_id):
        return json.dumps([project_id, session_id if project_id is None else None, entry["kind"], entry["id"]])

    def _load(self):
        state = empty_state()
        for path in sorted(self.directory.iterdir()):
            if path.name.startswith(".pending-") or path.name == ".writer.lock":
                continue
            require(re.fullmatch(r"[0-9]{20}\.json", path.name), "CORRUPT_SIDECAR", "Unexpected library entry")
            event = decode(read_regular(safe_path(self.directory, path.name)))
            fields(event, "format seq previous_hash kind request data result event_hash")
            require(event["format"] == FORMAT and type(event["seq"]) is int and event["seq"] == state["seq"] + 1
                    and path.name == f'{event["seq"]:020d}.json' and event["previous_hash"] == state["head"]
                    and digest({k: v for k, v in event.items() if k != "event_hash"}) == event["event_hash"],
                    "CORRUPT_SIDECAR", "Library transaction chain mismatch")
            self._apply(state, event)
        return state

    def _apply(self, state, event):
        from schema import context
        request = event["request"]
        fields(request, "action session_id operation_id actor origin arguments")
        identifier(request["session_id"])
        identifier(request["operation_id"])
        context(request["actor"], request["origin"])
        require(request["origin"]["session_id"] == request["session_id"] and request["actor"]["kind"] in ("human", "model"),
                "CORRUPT_SIDECAR", "Invalid library identity")
        args = request["arguments"]
        fields(args, "project_id entry expected_rev patch")
        entry_shape(args["entry"])
        if args["project_id"] is not None:
            identifier(args["project_id"])
        require((args["project_id"] is None) == (args["entry"]["kind"] == "draft"), "CORRUPT_SIDECAR", "Invalid library scope")
        integer(args["expected_rev"])
        patch_shape(args["patch"])
        action = request["action"]
        require(action in ("update", "remove", "restore") and event["kind"] == action and event["data"] is None,
                "CORRUPT_SIDECAR", "Invalid library action")
        require(action == "update" or not args["patch"], "CORRUPT_SIDECAR", "Removal cannot change metadata")
        key = self._entry_key(args["project_id"], args["entry"], request["session_id"])
        old = state["drafts"].get(key, default_metadata())
        require(old["metadata_rev"] == args["expected_rev"] and self._key(request) not in state["operations"],
                "CORRUPT_SIDECAR", "Invalid library CAS or duplicate operation")
        result = event["result"]
        fields(result, "entry project_id metadata")
        require(type(result["metadata"]) is dict and type(result["metadata"].get("updated_at")) is str,
                "CORRUPT_SIDECAR", "Invalid library timestamp")
        expected = {**old, **args["patch"], "metadata_rev": old["metadata_rev"] + 1,
                    "updated_at": result["metadata"]["updated_at"]}
        if action != "update":
            expected["removed"] = action == "remove"
        require(result == {"entry": args["entry"], "project_id": args["project_id"], "metadata": expected},
                "CORRUPT_SIDECAR", "Library result differs from requested transition")
        state["drafts"][key] = deepcopy(expected)
        state["operations"][self._key(request)] = event
        state["seq"], state["head"] = event["seq"], event["event_hash"]

    def _append(self, state, kind, request, *, data=None, result=None):
        # Workbench owns fsync/rename publication; its format constant is local to
        # that module, so wrap the event publication without sharing its journal.
        import os
        import uuid
        from backend import write_new
        from research_store import sync_directory
        event = {"format": FORMAT, "seq": state["seq"] + 1, "previous_hash": state["head"],
                 "kind": kind, "request": request, "data": data, "result": result}
        event["event_hash"] = digest(event)
        candidate = deepcopy(state)
        self._apply(candidate, event)
        temporary = safe_path(self.directory, ".pending-" + str(uuid.uuid4()))
        destination = safe_path(self.directory, f'{event["seq"]:020d}.json')
        require(not destination.exists(), "VERSION_CONFLICT", "Library transaction already exists")
        write_new(temporary, event)
        os.rename(temporary, destination)
        sync_directory(self.directory)
        state.clear()
        state.update(candidate)
        return deepcopy(result)

    def metadata(self, project_id, entry, session_id=None):
        entry_shape(entry)
        if project_id is not None:
            identifier(project_id)
        else:
            identifier(session_id)
        with self._locked() as state:
            return deepcopy(state["drafts"].get(self._entry_key(project_id, entry, session_id), default_metadata()))

    def project_removed(self, project_id):
        return self.metadata(project_id, {"kind": "project", "id": project_id})["removed"]

    def snapshot(self, project_id):
        identifier(project_id)
        with self._locked() as state:
            entries = {}
            for key, metadata in state["drafts"].items():
                scope, _, kind, object_id = json.loads(key)
                if scope == project_id:
                    entries[kind + ":" + object_id] = deepcopy(metadata)
            return {"version": {"seq": state["seq"], "head_hash": state["head"]}, "entries": entries,
                    "project": entries.get("project:" + project_id, default_metadata())}

    def _catalog(self, session_id, project_id, writable=False):
        self._identity(session_id)
        if project_id is None:
            workbench = Workbench(self.root, actor=self.actor, origin=self.origin)
            with workbench._locked() as state:
                created = {}
                for event in state["operations"].values():
                    if event["kind"] == "draft-save":
                        first = event["result"]["draft"]
                        created.setdefault(first["id"], first["saved_at"])
                return [{"entry": {"kind": "draft", "id": draft["id"]}, "kind": "preparation", "ref": None,
                         "title": draft["fields"].get("title", ""), "summary": "", "content_rev": draft["rev"],
                         "saved_at": draft["saved_at"], "created_at": created[draft["id"]], "bytes": len(canonical(draft)),
                         "can_manage": draft["id"] not in state["pending"], "can_edit": draft["status"] == "draft" and draft["id"] not in state["pending"],
                         "content": deepcopy(draft)}
                        for draft in state["drafts"].values() if draft["session_id"] == session_id]
        rows = []
        with self.projects.open_project(project_id, require_writable=writable, session_id=session_id,
                model=self.actor["kind"] == "model", chat=self.actor["kind"] == "model") as (project, store):
            records = store.records if hasattr(store, "records") else store["state"]["objects"]
            rows.append({"entry": {"kind": "project", "id": project_id}, "kind": "project", "ref": None,
                         "title": project["title"], "summary": "", "content_rev": None,
                         "created_at": project.get("created_at"), "saved_at": project.get("created_at"), "content": deepcopy(project)})
            for versions in records.values():
                obj = versions[-1]
                if obj["kind"] not in FORMAL:
                    continue
                row = {"entry": {"kind": "object", "id": obj["id"]}, "kind": obj["kind"],
                       "ref": {"id": obj["id"], "rev": obj["rev"]}, "content_rev": obj["rev"],
                       "title": record_title(obj), "summary": obj["data"].get("summary", ""),
                       "content": deepcopy(obj)}
                data = obj["data"]
                if obj["kind"] == "Evidence" and data.get("production") == {"method": "dsh-math-material-import", "version": "1"}:
                    descriptor = next((a for a in reversed(data["attachments"]) if a["logical_name"] == "material-extraction.json"), None)
                    require(descriptor is not None, "ATTACHMENT_INTEGRITY", "Missing committed material metadata")
                    raw = read_regular(safe_path(store.root, descriptor["relative_path"]))
                    import hashlib
                    require(len(raw) == descriptor["bytes"] and hashlib.sha256(raw).hexdigest() == descriptor["sha256"],
                            "ATTACHMENT_INTEGRITY", "Committed extraction metadata changed")
                    extraction = decode(raw)
                    original = next((a for a in data["attachments"] if a["sha256"] == extraction.get("original_sha256")), None)
                    require(original is not None and data["source"]["locator"] == "material:" + original["sha256"],
                            "ATTACHMENT_INTEGRITY", "Material original differs from committed metadata")
                    row.update(kind="material", title=extraction["name"], material={"name": extraction["name"],
                        "bytes": original["bytes"], "sha256": original["sha256"], "status": extraction["status"],
                        "format": extraction["format"], "truncated": extraction["truncated"], "warnings": extraction["warnings"]})
                # Commit time comes from the authoritative record, never upload staging.
                row["saved_at"] = obj.get("created_at", obj.get("timestamp"))
                row["created_at"] = versions[0].get("created_at", versions[0].get("timestamp"))
                rows.append(row)
            workbench = Workbench(self.root)
            with workbench._locked() as state:
                versions = state["pads"].get(project_id, [])
                if versions:
                    pad = versions[-1]
                    rows.append({"entry": {"kind": "scratchpad", "id": "main"}, "kind": "scratchpad", "ref": None,
                                 "title": pad["title"], "summary": "", "content_rev": pad["rev"],
                                 "saved_at": pad["saved_at"], "created_at": versions[0]["saved_at"], "content": deepcopy(pad)})
        can_manage = project["schema_version"] == 4 and project.get("mode") == "daily_research"
        def references(value):
            if type(value) is dict:
                if set(value) == {"id", "rev"}:
                    yield value
                else:
                    for child in value.values():
                        yield from references(child)
            elif type(value) is list:
                for child in value:
                    yield from references(child)
        inbound = {}
        for source in rows:
            if source["entry"]["kind"] == "object":
                for target in {ref["id"] for ref in references(source["content"].get("data", {}))}:
                    inbound.setdefault(target, []).append({"title": source["title"][:2000], "kind": source["kind"], "ref": source["ref"]})
        for row in rows:
            row["can_manage"] = can_manage
            row["can_edit"] = can_manage and row["entry"]["kind"] != "project"
            row["bytes"] = row.get("material", {}).get("bytes", len(canonical(row["content"])))
            row.setdefault("created_at", row.get("saved_at"))
            row["referenced_by"] = []
            if row["entry"]["kind"] == "object":
                row["referenced_by"] = inbound.get(row["entry"]["id"], [])
        return rows

    def _decorate(self, row, project_id, session_id, state, *, include_removed_refs=False):
        metadata = deepcopy(state["drafts"].get(self._entry_key(project_id, row["entry"], session_id), default_metadata()))
        result = {key: deepcopy(value) for key, value in row.items() if key != "content"}
        result.update({k: v for k, v in metadata.items() if k not in ("title", "summary")})
        for key in ("title", "summary"):
            if metadata[key] is not None:
                result[key] = metadata[key]
        result["title"] = result["title"][:2000]
        result["summary"] = result["summary"][:12000]
        # Backlinks carry source titles and exact refs, so apply source metadata
        # before exposing either the bounded list or its total count.
        backlinks = result.get("referenced_by", [])
        if not include_removed_refs:
            backlinks = [source for source in backlinks if not state["drafts"].get(
                self._entry_key(project_id, {"kind": "object", "id": source["ref"]["id"]}, session_id),
                default_metadata())["removed"]]
        result["referenced_by_total"] = len(backlinks)
        result["referenced_by"] = backlinks[:100]
        result["project_id"] = project_id
        return result

    def listing(self, session_id, project_id, include_removed=False, query="", offset=0, limit=50,
                kind=None, removed_only=False, sort="updated_desc"):
        require(type(include_removed) is bool and type(removed_only) is bool and type(query) is str and len(query) <= 200,
                "INVALID_SCHEMA", "Expected bounded library filters")
        require(kind is None or kind in FORMAL | {"material", "scratchpad", "preparation", "project"},
                "INVALID_SCHEMA", "Unknown library kind filter")
        require(sort in ("updated_desc", "updated_asc", "created_desc", "title_asc"), "INVALID_SCHEMA", "Unknown library sort")
        integer(offset)
        integer(limit, 1)
        require(limit <= 100, "TOO_LARGE", "List at most 100 library entries")
        rows = self._catalog(session_id, project_id)
        with self._locked() as state:
            items = [self._decorate(row, project_id, session_id, state, include_removed_refs=include_removed) for row in rows]
            version = {"seq": state["seq"], "head_hash": state["head"]}
        items = [item for item in items if (item["removed"] if removed_only else include_removed or not item["removed"])
                 and (kind is None or item["kind"] == kind)
                 and query.casefold() in (item["title"] + " " + item["summary"] + " " + " ".join(item["tags"])).casefold()]
        def sort_key(item):
            if sort == "title_asc":
                return item["title"].casefold(), item["entry"]["id"]
            timestamp = item.get("created_at") if sort == "created_desc" else item["updated_at"] or item.get("saved_at")
            return timestamp or "", item["entry"]["id"]
        items.sort(key=sort_key, reverse=sort in ("updated_desc", "created_desc"))
        items.sort(key=lambda item: not item["pinned"])
        end = min(offset + limit, len(items))
        return {"items": items[offset:end], "offset": offset, "next_offset": end if end < len(items) else None, "total": len(items), "version": version}

    def read(self, session_id, project_id, entry, rev=None, offset=0, limit=12000):
        entry_shape(entry)
        integer(offset)
        integer(limit, 1)
        require(limit <= 12000, "TOO_LARGE", "Read at most 12000 characters")
        if rev is not None:
            integer(rev, 1)
        row = next((r for r in self._catalog(session_id, project_id) if r["entry"] == entry), None)
        require(row is not None, "NOT_FOUND", "Library entry not found")
        content = row["content"]
        if rev is not None and entry["kind"] == "object":
            with self.projects.open_project(project_id, session_id=session_id, model=self.actor["kind"] == "model",
                    chat=self.actor["kind"] == "model") as (_, store):
                if hasattr(store, "get") and not isinstance(store, dict):
                    content = deepcopy(store.get(entry["id"], rev))
                else:
                    versions = store["state"]["objects"][entry["id"]]
                    require(rev <= len(versions), "NOT_FOUND", "Exact revision not found")
                    content = deepcopy(versions[rev - 1])
        elif rev is not None and entry["kind"] == "scratchpad":
            content = Workbench(self.root, actor=self.actor, origin=self.origin).scratchpad_read(session_id, project_id, rev)["scratchpad"]
        elif rev is not None and entry["kind"] == "draft":
            with Workbench(self.root, actor=self.actor, origin=self.origin)._locked() as state:
                content = next((deepcopy(event["result"]["draft"]) for event in state["operations"].values()
                                if event["kind"] in ("draft-save", "finalize-complete")
                                and event["result"]["draft"]["id"] == entry["id"]
                                and event["result"]["draft"]["session_id"] == session_id
                                and event["result"]["draft"]["rev"] == rev), None)
                require(content is not None, "NOT_FOUND", "Requested draft revision is not available")
        elif rev is not None:
            require(rev == row["content_rev"], "NOT_FOUND", "Requested revision is not available")
        with self._locked() as state:
            item = self._decorate(row, project_id, session_id, state)
        if entry["kind"] == "object":
            item["ref"] = {"id": content["id"], "rev": content["rev"]}
            item["content_rev"] = content["rev"]
        elif entry["kind"] in ("scratchpad", "draft"):
            item["content_rev"] = content["rev"]
        text = json.dumps({"library": item, "content": content}, ensure_ascii=False, indent=2)
        require(offset <= len(text), "INVALID_SCHEMA", "Read offset exceeds content")
        end = min(offset + limit, len(text))
        return {"item": item, "format": "json", "text": text[offset:end], "offset": offset,
                "next_offset": end if end < len(text) else None, "total_chars": len(text), "truncated": end < len(text)}

    def change(self, action, session_id, project_id, entry, expected_rev, operation_id, patch=None):
        entry_shape(entry)
        if entry["kind"] == "draft":
            # Finalization takes the same workbench lock before checking library
            # visibility, so a draft cannot be hidden while its intent is pending.
            workbench = Workbench(self.root, actor=self.actor, origin=self.origin)
            with workbench._locked() as state:
                workbench._draft(state, session_id, entry["id"])
                request = self._request(action, session_id, operation_id, {"project_id": project_id, "entry": entry,
                                        "expected_rev": expected_rev, "patch": {} if patch is None else patch})
                with self._locked() as metadata_state:
                    previous = self._previous(metadata_state, request)
                    if previous:
                        return deepcopy(previous["result"])
                require(entry["id"] not in state["pending"], "FINALIZE_PENDING", "Draft is frozen by a pending finalization")
                return self._change(action, session_id, project_id, entry, expected_rev, operation_id, patch)
        return self._change(action, session_id, project_id, entry, expected_rev, operation_id, patch)

    def _change(self, action, session_id, project_id, entry, expected_rev, operation_id, patch=None):
        entry_shape(entry)
        integer(expected_rev)
        require(action in ("update", "remove", "restore"), "FORBIDDEN", "Unknown library action")
        patch = {} if patch is None else patch
        patch_shape(patch)
        require(action == "update" or not patch, "INVALID_SCHEMA", "Remove/restore do not accept metadata patches")
        rows = self._catalog(session_id, project_id, writable=True)
        row = next((row for row in rows if row["entry"] == entry), None)
        require(row is not None, "NOT_FOUND", "Library entry not found")
        require(row["can_manage"], "FINALIZE_PENDING", "Draft is frozen by a pending finalization")
        request = self._request(action, session_id, operation_id, {"project_id": project_id, "entry": entry,
                                "expected_rev": expected_rev, "patch": patch})
        with self._locked(True) as state:
            previous = self._previous(state, request)
            if previous:
                return deepcopy(previous["result"])
            old = state["drafts"].get(self._entry_key(project_id, entry, session_id), default_metadata())
            require(old["metadata_rev"] == expected_rev, "VERSION_CONFLICT", "Library metadata changed; read before updating")
            metadata = {**old, **deepcopy(patch), "metadata_rev": expected_rev + 1, "updated_at": now()}
            if action != "update":
                metadata["removed"] = action == "remove"
            return self._append(state, action, request, result={"entry": deepcopy(entry), "project_id": project_id, "metadata": metadata})


def dispatch_library(root, action, args, *, actor=None, origin=None):
    library = Library(root, actor=actor, origin=origin)
    if action == "library-list":
        args = {"include_removed": False, "removed_only": False, "kind": None, "sort": "updated_desc",
                "query": "", "offset": 0, "limit": 50, **args}
        fields(args, "session_id project_id include_removed removed_only kind sort query offset limit")
        return library.listing(**args)
    if action == "library-read":
        args = {"rev": None, "offset": 0, "limit": 12000, **args}
        fields(args, "session_id project_id entry rev offset limit")
        return library.read(**args)
    if action == "library-update":
        fields(args, "session_id project_id entry expected_rev operation_id patch")
    else:
        fields(args, "session_id project_id entry expected_rev operation_id")
    return library.change(action.removeprefix("library-"), **args)
