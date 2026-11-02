#!/usr/bin/env python3
"""Dedicated ordinary-chat preparation entry; never a human/research dispatcher.

ROOT/session_id/tool_call_id are trusted host bindings. The model may supply only
an allowlisted tool name and its exact argument shape. Guidance requires
an explicit user request; the host enforces verified plan/read-only/approval policy
before writes. This transport does not attest an individual human gesture.
"""
import argparse
import json
import subprocess
import sys

from workbench_store import Workbench
from schema import StoreError, decode, fields, identifier, require

# Scratch names remain internal compatibility transports, never registered in chat.
TOOLS = frozenset(("math_project_prepare", "math_project_draft_read", "math_scratchpad_read", "math_scratchpad_save",
                   "math_research_read", "math_research_record", "math_material_read"))
MAX_REQUEST_BYTES = 2 * 1024 * 1024


def execute(root, request):
    fields(request, "session_id tool_call_id name arguments")
    session_id, call_id, name, arguments = (request[key] for key in ("session_id", "tool_call_id", "name", "arguments"))
    identifier(session_id)
    require(type(call_id) is str and call_id.strip() and len(call_id) <= 1024, "INVALID_SCHEMA", "Expected host tool-call identity")
    require(type(name) is str and name in TOOLS, "FORBIDDEN", "Tool is not available in preparation context")
    require(type(arguments) is dict, "INVALID_SCHEMA", "Expected direct object tool arguments")
    workbench = Workbench(root, actor={"kind": "model", "id": "dsh-math-chat"},
                          origin={"source": "dsh-math-chat", "session_id": session_id, "run_id": None, "tool_call_id": call_id})
    if name == "math_project_prepare":
        arguments = {"draft_id": None, **arguments}
        fields(arguments, "draft_id expected_rev operation_id patch")
        patch = arguments["patch"]
        if (type(patch) is dict and type(patch.get("statement")) is str
                and type(patch.get("title", "")) is str and not patch.get("title", "").strip()):
            current = workbench.draft_read(session_id, arguments["draft_id"])["draft"] if arguments["draft_id"] else None
            existing_title = current["fields"].get("title", "") if current else ""
            first_line = next((line.strip() for line in patch["statement"].splitlines() if line.strip()), "")
            title = existing_title or first_line[:60]
            if title:
                arguments = {**arguments, "patch": {**patch, "title": title}}
        return workbench.draft_save(session_id=session_id, **arguments)
    if name == "math_project_draft_read":
        arguments = {"draft_id": None, **arguments}
        fields(arguments, "draft_id")
        return workbench.draft_read(session_id=session_id, **arguments)
    if name == "math_scratchpad_read":
        arguments = {"rev": None, **arguments}
        fields(arguments, "project_id rev")
        return workbench.scratchpad_read(session_id=session_id, **arguments)
    if name == "math_scratchpad_save":
        fields(arguments, "project_id expected_rev operation_id title body")
        return workbench.scratchpad_save(session_id=session_id, **arguments)
    if name == "math_material_read":
        from material_store import Materials
        fields(arguments, "project_id ref offset limit")
        return Materials(root).read(session_id=session_id, **arguments)
    from chat_research import ChatResearch
    research = ChatResearch(root, session_id, call_id)
    if name == "math_research_read":
        fields(arguments, "project_id ref offset limit")
        return research.read(**arguments)
    fields(arguments, "project_id id expected_rev operation_id data")
    return research.record(**arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    options = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        require(len(raw) <= MAX_REQUEST_BYTES, "TOO_LARGE", "Preparation request exceeds 2 MiB")
        value = execute(options.root, decode(raw))
        print(json.dumps({"ok": True, "value": value}, ensure_ascii=False))
        return 0
    except (StoreError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", type(error).__name__), "message": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
