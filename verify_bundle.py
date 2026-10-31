import hashlib
import json
from pathlib import Path
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _ledger(root):
    previous = "0" * 64
    rows = []
    raw = (root / "run/events.jsonl").read_bytes()
    require(not raw or raw.endswith(b"\n"), "Torn run ledger")
    for line in raw.splitlines():
        row = json.loads(line)
        digest = row.pop("sha256")
        encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        require(row["seq"] == len(rows) + 1 and row["previous"] == previous and _sha(encoded) == digest, "Run ledger chain mismatch")
        row["sha256"] = previous = digest
        rows.append(row)
    return rows


def _verify_run(root, manifest, replay):
    summary = manifest.get("run")
    if summary is None:
        return 0
    run_manifest = json.loads((root / "run/manifest.json").read_bytes())
    require(run_manifest["run_id"] == summary["run_id"] and run_manifest["project_id"] == manifest_project(root), "Run identity mismatch")
    require(any(row["run_id"] == summary["run_id"] for row in replay["runs"]), "Run ledger missing from replay")
    require(_sha((root / "run/manifest.json").read_bytes()) == summary["manifest_sha256"], "Run manifest binding mismatch")
    for name, descriptor in (("run/contract.json", run_manifest["contract"]),
                             *(("run/materials/" + item["name"], item) for item in run_manifest["materials"])):
        raw = (root / name).read_bytes()
        require(len(raw) == descriptor["bytes"] and _sha(raw) == descriptor["sha256"], "Frozen run material mismatch")
    rows = _ledger(root)
    require(rows and all(row["run_id"] == summary["run_id"] and row["project_id"] == summary["project_id"] for row in rows), "Run ledger identity mismatch")
    requests = sum(row["kind"] == "request.reserved" for row in rows)
    if summary["closeout_included"]:
        closeout = json.loads((root / "run/closeout.json").read_bytes())
        require(closeout["run_id"] == summary["run_id"] and closeout["actual"]["requests"] == requests
                and closeout["cleanup_model_requests"] == 0 and closeout["new_checks"] == 0
                and closeout["scientific_status"] == "candidate_pending_review", "Closeout binding mismatch")
    if summary["recovery_included"]:
        recovery = json.loads((root / "run/recovery.json").read_bytes())
        require(recovery["run_id"] == summary["run_id"] and recovery["requests"] == requests
                and recovery["recovery_model_requests"] == 0 and recovery["executed"] is False
                and recovery["manifest_sha256"] == summary["manifest_sha256"], "Recovery binding mismatch")
    return len(rows)


def manifest_project(root):
    return json.loads((root / "project.json").read_bytes())["id"]


def _recompute_checks(root, snapshot):
    checked = 0
    for versions in snapshot["state"]["objects"].values():
        for obj in versions:
            if obj["kind"] != "Review" or obj["data"]["method"] != "program":
                continue
            execution = obj["data"]["execution"]
            result = execution["result"]
            if result["error_code"] == "execution_error":
                continue
            checker = root / "research/attachments/sha256" / execution["checker_sha256"]
            witness = root / "research/attachments/sha256" / execution["input_sha256"]
            command = [sys.executable, "-I", "-B", str(checker), str(witness)]
            if execution["checker_id"] != "primitive_pythagorean_v1":
                command.append(result["scope"])
            process = subprocess.run(command, cwd=root / "research", env={"PYTHONDONTWRITEBYTECODE": "1"},
                                     capture_output=True, text=True, timeout=60)
            require(process.stdout.strip() and json.loads(process.stdout) == result
                    and process.returncode == execution["exit_code"], "Independent checker recomputation differs: " + obj["id"])
            checked += 1
    return checked


def verify(root):
    root = Path(root).absolute()
    require(all(not p.is_symlink() for p in [root, *root.parents]), "Symlink export")
    manifest = json.loads((root / "manifest.json").read_bytes())
    require(manifest["format"] == "dsh-math-export-v1", "Unsupported format")
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if (p.is_file() or p.is_symlink()) and not p.name.startswith("._")}
    require(actual == set(manifest["files"]) | {"manifest.json"}, "Unexpected or missing files")
    for name, expected in manifest["files"].items():
        require(name and not name.startswith("/") and "\\" not in name and all(p not in ("", ".", "..") for p in name.split("/")), "Unsafe manifest path")
        path = root / name
        require(all(not p.is_symlink() for p in [path, *path.parents]) and path.stat().st_nlink == 1, "Unsafe file")
        raw = path.read_bytes()
        require(len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"], "Hash mismatch: " + name)
    if manifest["research_format"] == "math-harness-p2c-export-v1":
        command = [sys.executable, "-B", "runtime/verify_export.py", "."]
        result = subprocess.run(command, cwd=root / "p2c", capture_output=True, text=True, timeout=60)
        snapshot = json.loads((root / "p2c/snapshot.json").read_bytes())
    else:
        require(manifest["research_format"] == "dsh-math-research-v4", "Unknown research schema")
        result = subprocess.run([sys.executable, "-B", "runtime/cli.py", "research", "replay"], cwd=root, capture_output=True, text=True, timeout=60)
        snapshot = json.loads((root / "snapshot.json").read_bytes())
        require(result.returncode == 0 and json.loads(result.stdout) == snapshot, "Replayed research state differs: " + result.stderr)
    require(result.returncode == 0, "Inner verification failed: " + result.stderr)
    require(snapshot["state"]["seq"] == manifest["captured_seq"] and snapshot["state"]["head_hash"] == manifest["head_hash"] and snapshot["state_sha256"] == manifest["state_sha256"], "Inner/outer binding mismatch")
    for index, source in enumerate(manifest["selected_sources"]):
        raw = (root / f"session-evidence/{index:02d}.jsonl").read_bytes()
        require(len(raw) == source["bytes"] and hashlib.sha256(raw).hexdigest() == source["sha256"], "Session evidence binding mismatch")
    replay = json.loads((root / "replay.json").read_bytes())
    require(replay["sources"] == manifest["selected_sources"] and replay["research_head"] == manifest["head_hash"], "Replay/source binding mismatch")
    for checkpoint in replay["checkpoints"]:
        raw = (root / "session-evidence/checkpoints" / (checkpoint["sha256"] + ".tar")).read_bytes()
        require(len(raw) == checkpoint["bytes"] and hashlib.sha256(raw).hexdigest() == checkpoint["sha256"], "Checkpoint binding mismatch")
    review = json.loads((root / "REVIEW-RECORD.json").read_bytes())
    require(review == {"R1": None, "R2": None, "execution_authorized": False}, "R1/R2 records changed; preserve original and add separate human record")
    run_events = _verify_run(root, manifest, replay)
    independent_checks = _recompute_checks(root, snapshot) if manifest["research_format"] == "dsh-math-research-v4" else 0
    return {"status": "passed", "files": len(manifest["files"]), "operation_model_requests": 0,
            "session_model_requests": None, "independent_checks": independent_checks, "run_events": run_events}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(sys.argv[1])))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        sys.exit(1)
