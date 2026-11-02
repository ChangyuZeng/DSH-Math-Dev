"""Bounded ordinary-chat research access; no run, review or execution authority."""
from copy import deepcopy
import json

from backend import Projects
from schema import canonical, fields, identifier, integer, ref as exact_ref, require, validate_data

MAX_CHARS = 12000
MAX_NOTE_BYTES = 256 * 1024
READ_KINDS = frozenset(("Problem", "Note", "Obligation", "Evidence"))


def window(offset, limit):
    integer(offset)
    integer(limit, 1)
    require(limit <= MAX_CHARS, "INVALID_SCHEMA", "limit must be 1..12000 characters")


class ChatResearch:
    def __init__(self, root, session_id, tool_call_id):
        identifier(session_id)
        self.projects = Projects(root)
        self.session_id = session_id
        self.actor = {"kind": "model", "id": "dsh-math-chat"}
        self.origin = {"source": "dsh-math-chat", "session_id": session_id,
                       "run_id": None, "tool_call_id": tool_call_id}

    def _open(self, project_id, writable=False):
        return self.projects.open_project(project_id, writable=writable,
                                         session_id=self.session_id, model=True, chat=True,
                                         actor=self.actor, origin=self.origin)

    def read(self, project_id, ref=None, offset=0, limit=12000):
        window(offset, limit)
        if ref is not None:
            exact_ref(ref)
        with self._open(project_id) as (_, store):
            records = store.records if hasattr(store, "records") else store["state"]["objects"]
            if ref is None:
                # Directory text is paginated too: even many titles cannot overflow context.
                value = [{"ref": {"id": versions[-1]["id"], "rev": versions[-1]["rev"]},
                          "kind": versions[-1]["kind"],
                          "title": versions[-1]["data"].get("title", "")[:200]}
                         for _, versions in sorted(records.items()) if versions[-1]["kind"] in READ_KINDS]
            else:
                versions = records.get(ref["id"], [])
                require(ref["rev"] <= len(versions), "NOT_FOUND", "Exact research revision not found")
                value = deepcopy(versions[ref["rev"] - 1])
                require(value["kind"] in READ_KINDS, "REFERENCE_KIND", "Read a Problem, Note, Obligation or Evidence reference")
            text = json.dumps(value, ensure_ascii=False, indent=2)
            end = min(offset + limit, len(text))
            return {"ref": ref, "format": "json", "text": text[offset:end], "offset": offset,
                    "next_offset": end if end < len(text) else None, "total_chars": len(text),
                    "truncated": end < len(text)}

    def record(self, project_id, id, expected_rev, operation_id, data):
        identifier(id)
        identifier(operation_id)
        integer(expected_rev)
        validate_data("Note", data)
        require(len(canonical(data)) <= MAX_NOTE_BYTES, "TOO_LARGE", "Note exceeds 256 KiB")
        change = {"kind": "Note", "id": id, "expected_rev": expected_rev, "data": data}
        with self._open(project_id, True) as (_, store):
            previous = store.operations.get(operation_id)
            if previous:
                # Retried tool calls have new transport ids. Preserve the first call's
                # provenance while still rejecting changed payloads or other actors.
                prior = [{"kind": obj["kind"], "id": obj["id"], "expected_rev": obj["rev"] - 1,
                          "data": obj["data"]} for obj in previous["changes"]]
                origin = previous["origin"]
                require(previous["kind"] == "commit" and previous["actor"] == self.actor
                        and all(origin[key] == self.origin[key] for key in ("source", "session_id", "run_id"))
                        and prior == [change], "OPERATION_CONFLICT", "Operation id already used with different content or identity")
                return deepcopy(previous["result"])
            return store.commit(operation_id, [change])
