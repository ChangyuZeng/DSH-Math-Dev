"""Read-only M-run reconciliation; never resumes inference or re-executes tools."""

import argparse
import json
from pathlib import Path
import sys

from backend import Projects, open_store
from checking import attempts
from m_runner import load_manifest, manifest_sha256, verify_sources
from run_ledger import Halt, replay
from schema import StoreError, decode, fields
from research_store import ResearchStore


def recover(root, manifest_path):
    manifest, raw = load_manifest(manifest_path)
    verify_sources(manifest)
    rows = replay(Path(manifest["output_dir"]) / "ledger/events.jsonl")
    started = {row["call_id"] for row in rows if row["kind"] == "tool.started"}
    finished = {row["call_id"] for row in rows if row["kind"] == "tool.finished"}
    reserved = {row["request_id"] for row in rows if row["kind"] == "request.reserved"}
    settled = {row["request_id"] for row in rows if row["kind"] in ("request.finished", "request.settled")}
    projects = Projects(root)
    metadata = projects.metadata(manifest["project_id"])
    if metadata["session_id"] != manifest["session_id"]:
        raise StoreError("RUN_CONTRACT", "Project/session changed")
    with open_store(projects.path(manifest["project_id"]) / "research") as store:
        snapshot = store.snapshot() if isinstance(store, ResearchStore) else store
        if not snapshot["usable"]:
            raise StoreError("ATTACHMENT_INTEGRITY", "Research evidence is damaged")
        check_attempts = attempts(store) if isinstance(store, ResearchStore) else []
    completed_tools = [row for row in rows if row["kind"] == "tool.finished"]
    usages = [row.get("usage") for row in rows if row["kind"] in ("request.finished", "request.settled")]
    known_tokens = sum(value["totalTokens"] for value in usages if type(value) is dict and type(value.get("totalTokens")) is int)
    complete = bool(reserved) and len(reserved) == len(usages) and all(type(value) is dict and type(value.get("totalTokens")) is int for value in usages)
    return {"format": "dsh-math-m-recovery-v1", "run_id": manifest["run_id"], "session_id": manifest["session_id"],
            "project_id": manifest["project_id"], "manifest_sha256": manifest_sha256(raw), "events": len(rows),
            "research_seq": snapshot["state"]["seq"], "research_head": snapshot["state"]["head_hash"],
            "unknown_tool_outcomes": sorted(started - finished), "unknown_request_outcomes": sorted(reserved - settled),
            "incomplete_checks": [row for row in check_attempts if row["status"] == "incomplete"],
            "last_completed_tools": completed_tools[-10:], "requests": len(reserved),
            "known_tokens": known_tokens, "total_tokens": known_tokens if complete else None,
            "usage_complete": complete, "executed": False, "recovery_model_requests": 0,
            "resume_mode": "readonly", "scientific_status": "candidate_pending_review"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    options = parser.parse_args()
    try:
        request = decode(sys.stdin.buffer.read())
        fields(request, "manifest")
        print(json.dumps({"ok": True, "value": recover(options.root, request["manifest"])}, ensure_ascii=False))
    except (StoreError, OSError, ValueError, KeyError, TypeError, Halt) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", type(error).__name__), "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
