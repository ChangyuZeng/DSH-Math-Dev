#!/usr/bin/env python3
"""Verify a trusted local export and recompute its pinned finite checks offline."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def safe(root, relative):
    require(type(relative) is str and relative and "\\" not in relative, "Invalid manifest path")
    parts = relative.split("/")
    require(all(p not in ("", ".", "..") for p in parts), "Unsafe manifest path")
    path = root
    for part in parts:
        path /= part
        require(not path.is_symlink(), "Symlink in export")
    require(path.is_file() and path.stat().st_nlink == 1, "Not a regular single-link file")
    return path


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads(safe(root, "manifest.json").read_bytes())
    require(manifest["format"] == "math-harness-p2c-export-v1" and manifest["schema_version"] == 3, "Unsupported export format")
    files = manifest["files"]
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if (p.is_file() or p.is_symlink()) and not p.name.startswith("._")}
    require(actual == set(files) | {"manifest.json"}, "Missing or unexpected export files")
    for name, expected in files.items():
        content = safe(root, name).read_bytes()
        require(len(content) == expected["bytes"] and hashlib.sha256(content).hexdigest() == expected["sha256"], "Hash mismatch: " + name)
    snapshot = json.loads(safe(root, "snapshot.json").read_bytes())
    require(hashlib.sha256(canonical(snapshot["state"])).hexdigest() == manifest["state_sha256"] == snapshot["state_sha256"], "Snapshot hash mismatch")
    previous, seq, reviews = "0" * 64, 0, {}
    for line in safe(root, "research/events.jsonl").read_bytes().splitlines():
        event = json.loads(line)
        seq += 1
        require(event["schema_version"] == 3 and event["seq"] == seq and event["previous_hash"] == previous, "Broken event chain")
        require(hashlib.sha256(canonical({k:v for k,v in event.items() if k != "event_hash"})).hexdigest() == event["event_hash"], "Event hash mismatch")
        previous = event["event_hash"]
        for obj in event["changes"]:
            if obj["kind"] == "Review" and obj["data"]["method"] == "program":
                reviews[obj["id"]] = obj["data"]["execution"]
    require(seq == manifest["captured_seq"] and previous == manifest["head_hash"], "Manifest not at the recorded sequence")
    require({r["review_id"] for r in manifest["checks"]} == set(reviews)
            and len(manifest["checks"]) == len(reviews), "Omitted or duplicate checker result")
    outcomes = []
    for check in manifest["checks"]:
        execution = reviews[check["review_id"]]
        require(check["checker_id"] == "primitive_pythagorean_v1" and check["checker_id"] == execution["checker_id"], "Unknown checker")
        require(check["input_path"] == "research/attachments/sha256/" + execution["input_sha256"]
                and check["checker_path"] == "checker-sources/" + execution["checker_sha256"] + ".py", "Checker/input path binding mismatch")
        require(files[check["checker_path"]]["sha256"] == execution["checker_sha256"]
                and files[check["input_path"]]["sha256"] == execution["input_sha256"], "Checker/input SHA mismatch")
        require(check["expected_result"] == execution["result"] and check["expected_exit_code"] == execution["exit_code"], "Expected result was replaced")
        require(type(check["recompute"]) is bool and check["recompute"] == (execution["result"]["error_code"] != "execution_error"), "Invalid recompute flag")
        if not check["recompute"]:
            outcomes.append({"review_id": check["review_id"], "status": "not_recomputed_execution_error"})
            continue
        result = subprocess.run([sys.executable, "-I", "-B", str(safe(root, check["checker_path"])), str(safe(root, check["input_path"]))],
                                cwd=root, env={"PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=5)
        actual_result = json.loads(result.stdout)
        require(result.returncode == check["expected_exit_code"] and actual_result == check["expected_result"], "Recomputed result differs")
        outcomes.append({"review_id": check["review_id"], "status": "matched", "exit_code": result.returncode, "actual": actual_result})
    replay = subprocess.run([sys.executable, "-B", str(safe(root, "runtime/cli.py")), str(root / "research"), "replay"],
                            cwd=root, env={"PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=30)
    require(replay.returncode == 0, "Exported runtime could not replay: " + replay.stderr)
    require(json.loads(replay.stdout) == snapshot, "Replayed state differs from exported snapshot")
    return {"status": "passed", "files_verified": len(files), "captured_seq": seq,
            "checks": outcomes, "replay_matched": True, "model_requests": 0}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(sys.argv[1]), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        sys.exit(1)
