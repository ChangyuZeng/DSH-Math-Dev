import hashlib
import json
import os
from pathlib import Path
import subprocess

from schema import canonical, decode, fields, require
from research_store import read_regular, safe_path


def read_source(source):
    fields(source, "adapter path")
    path = Path(source["path"]).absolute()
    require(all(not p.is_symlink() for p in [path, *path.parents]), "UNSAFE_PATH", "Symlink source")
    require(path.suffix == ".jsonl" and not any(word in path.name.lower() for word in ("auth", "credential", "secret")),
            "SENSITIVE_CONTENT", "Replay accepts only explicitly selected journal files")
    raw = read_regular(path)
    require(len(raw) <= 64 * 1024 * 1024, "UNSUPPORTED", "Source exceeds 64 MiB")
    require(not raw or raw.endswith(b"\n"), "INCOMPLETE_TAIL", "Incomplete source: " + str(path))
    adapter = source["adapter"]
    require(adapter in ("native-v4", "audit-js-v1", "ledger-py-v1"), "UNKNOWN_SCHEMA", "Explicit adapter required")
    if adapter == "audit-js-v1":
        node = os.environ.get("DSH_MATH_NODE", "node")
        result = subprocess.run([node, str(Path(__file__).with_name("audit-reader.mjs")), str(path)], capture_output=True, timeout=30)
        require(result.returncode == 0, "HASH_MISMATCH", "JS audit chain verification failed")
        rows = decode(result.stdout)
        require(read_regular(path) == raw, "SOURCE_CHANGED", "Source changed during verification")
    else:
        rows = [decode(line) for line in raw.splitlines()]
        previous = "0" * 64
        if adapter == "ledger-py-v1":
            for seq, row in enumerate(rows, 1):
                data = {k: v for k, v in row.items() if k != "sha256"}
                encoded = canonical(data)
                require(row["seq"] == seq and row["previous"] == previous and hashlib.sha256(encoded).hexdigest() == row["sha256"],
                        "HASH_MISMATCH", "Python ledger chain verification failed")
                previous = row["sha256"]
        else:
            require(rows and rows[0].get("type") == "session" and rows[0].get("version") == 4,
                    "UNKNOWN_SCHEMA", "Expected native session v4")
    descriptor = {"adapter": adapter, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                  "integrity": "byte_hash_only_no_native_chain" if adapter == "native-v4" else "verified_chain"}
    return descriptor, rows, raw


def aggregate(projects, project_id, sources):
    require(type(sources) is list and len(sources) <= 20, "INVALID_SCHEMA", "Select at most 20 source files")
    view = projects.overview(project_id)
    timeline, descriptors, checkpoints = [], [], []
    root = projects.path(project_id)
    research_raw = read_regular(safe_path(root, "research/events.jsonl"))
    research_events = list(map(decode, research_raw.splitlines()))
    require(not research_raw or research_raw.endswith(b"\n"), "INCOMPLETE_TAIL", "Research changed during replay")
    require(len(research_events) == view["snapshot"]["state"]["seq"] and
            (research_events[-1]["event_hash"] if research_events else "0" * 64) == view["snapshot"]["state"]["head_hash"],
            "SOURCE_CHANGED", "Research changed during replay")
    for event in research_events:
        timeline.append({"source": "research/events.jsonl", "seq": event["seq"], "hash": event["event_hash"],
                         "time": event["time"], "kind": "research." + event["kind"], "data": event})
    request_states, tool_states, run_rows = {}, {}, []
    native_present = False
    for source in sources:
        descriptor, rows, _ = read_source(source)
        require(not any(d["path"] == descriptor["path"] for d in descriptors), "INVALID_SCHEMA", "Duplicate source")
        descriptors.append(descriptor)
        native_present |= source["adapter"] == "native-v4"
        for index, row in enumerate(rows):
            kind = row.get("kind", row.get("type", "unknown"))
            timeline.append({"source": descriptor["path"], "seq": row.get("seq", index), "hash": row.get("sha256"),
                             "file_sha256": descriptor["sha256"], "time": row.get("time"), "elapsed": row.get("elapsed"),
                             "kind": kind, "data": row})
            if source["adapter"] == "ledger-py-v1" and "run_id" in row:
                require(row.get("project_id") == project_id and type(row.get("session_id")) is str,
                        "RUN_CONTRACT", "Selected ledger belongs to another project or lacks a session")
                run_rows.append(row)
            identity = row.get("request_id") if source["adapter"] == "ledger-py-v1" else row.get("id")
            key = (descriptor["path"], identity)
            if kind == "request.reserved":
                require(identity is not None, "UNKNOWN_SCHEMA", "Request identity is required")
                request_states[key] = {"request_id": identity, "source": key[0], "outcome": "unknown", "usage": None}
            elif kind in ("request.finished", "request.settled"):
                require(identity is not None, "UNKNOWN_SCHEMA", "Request identity is required")
                request_states[key] = {"request_id": identity, "source": key[0],
                                       "outcome": "finished" if row.get("complete", True) else "unknown",
                                       "usage": row.get("usage")}
            elif kind == "request.error" and key in request_states:
                request_states[key]["error"] = row.get("error")
            if kind in ("tool.started", "tool.finished"):
                tool_states[(descriptor["path"], row.get("call_id"))] = kind
            if kind == "workspace.checkpoint":
                snapshot = row["snapshot"]
                require(snapshot["path"] == "artifacts/" + snapshot["sha256"] + ".tar", "UNSAFE_PATH", "Checkpoint path mismatch")
                content = read_regular(safe_path(Path(descriptor["path"]).parent, snapshot["path"]))
                require(len(content) == snapshot["bytes"] and hashlib.sha256(content).hexdigest() == snapshot["sha256"],
                        "HASH_MISMATCH", "Checkpoint damaged")
                checkpoints.append({"source": descriptor["path"], **snapshot, "extracted": False})
    runs = []
    for run_id in sorted({row["run_id"] for row in run_rows}):
        rows = [row for row in run_rows if row["run_id"] == run_id]
        require(len({row["session_id"] for row in rows}) == 1, "RUN_CONTRACT", "Run/session identity drift")
        runs.append({"run_id": run_id, "session_id": rows[0]["session_id"], "project_id": project_id,
                     "requests": sum(row["kind"] == "request.reserved" for row in rows),
                     "tools": sum(row["kind"] == "tool.started" for row in rows),
                     "tool_call_ids": [row["call_id"] for row in rows if row["kind"] == "tool.started"],
                     "usage_complete": all(row.get("usage") is not None for row in rows if row["kind"] in ("request.finished", "request.settled"))})
    return {"format": "dsh-math-replay-v1", "sources": descriptors, "events": timeline,
            "ordering": "source_order_then_sequence; elapsed and native clocks are not assumed comparable",
            "requests": list(request_states.values()), "runs": runs,
            "unknown_tools": [{"source": k[0], "call_id": k[1]} for k, v in tool_states.items() if v == "tool.started"],
            "checkpoints": checkpoints, "operation_model_requests": 0, "session_model_requests": None,
            "usage": None, "coverage": {"native": "selected" if native_present else "not_selected",
            "transport": "only_explicitly_selected_files", "notice_native_match": "not_established", "provider_usage": "partial_or_unknown"},
            "research_head": view["snapshot"]["state"]["head_hash"]}
