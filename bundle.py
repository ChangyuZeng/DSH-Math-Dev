import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from backend import BASE, HERE, open_store, write_new
from schema import canonical, decode, fields, require
from research_store import ResearchStore, read_regular, safe_path
from exporting import guard_content, RUNTIME_FILES
from render import render
from replay import aggregate, read_source


def _regular(path):
    path = Path(path).absolute()
    require(all(not p.is_symlink() for p in [path, *path.parents]), "UNSAFE_PATH", "Run evidence symlink")
    return read_regular(path)


def _run_payloads(run, project_id, replay):
    fields(run, "manifest closeout recovery")
    from m_runner import load_manifest, manifest_sha256, verify_sources
    manifest, raw = load_manifest(run["manifest"])
    require(manifest["project_id"] == project_id and any(item["run_id"] == manifest["run_id"] for item in replay["runs"]),
            "RUN_CONTRACT", "Run project mismatch or its ledger was not selected for replay")
    verify_sources(manifest)
    payloads = {"run/manifest.json": raw, "run/contract.json": _regular(manifest["contract"]["path"]),
                "run/events.jsonl": _regular(Path(manifest["output_dir"]) / "ledger/events.jsonl")}
    for descriptor in manifest["materials"]:
        payloads["run/materials/" + descriptor["name"]] = _regular(descriptor["path"])
    for key, name in (("closeout", "run/closeout.json"), ("recovery", "run/recovery.json")):
        if run[key] is not None:
            value = decode(_regular(run[key]))
            require(value.get("run_id") == manifest["run_id"], "RUN_CONTRACT", "Run receipt identity mismatch")
            payloads[name] = canonical(value) + b"\n"
    workspace = Path(manifest["workspace"])
    if workspace.exists():
        for path in workspace.rglob("*"):
            require(not path.is_symlink(), "UNSAFE_PATH", "Workspace symlink")
            if path.is_file() and not path.name.startswith("._"):
                payloads["run/workspace/" + path.relative_to(workspace).as_posix()] = _regular(path)
    summary = {"run_id": manifest["run_id"], "session_id": manifest["session_id"], "project_id": project_id,
               "manifest_sha256": manifest_sha256(raw), "closeout_included": run["closeout"] is not None,
               "recovery_included": run["recovery"] is not None, "scientific_status": "candidate_pending_review"}
    return payloads, summary


def export_project(projects, project_id, sources, expected_seq, run=None):
    metadata = projects.metadata(project_id)
    project = projects.path(project_id)
    destination = safe_path(projects.root, "exports/" + str(uuid.uuid4()))
    temporary = destination.with_name(".partial-" + destination.name)
    temporary.mkdir(parents=True, mode=0o700)
    with open_store(project / "research") as store:
        snapshot = store.snapshot() if isinstance(store, ResearchStore) else store
        require(snapshot["usable"] and snapshot["state"]["seq"] == expected_seq,
                "VERSION_CONFLICT", "Inspect current sequence before exporting")
        replay = aggregate(projects, project_id, sources)
        selected = {}
        for index, source in enumerate(sources):
            descriptor, _, raw = read_source(source)
            require(descriptor == replay["sources"][index], "SOURCE_CHANGED", "Selected source changed")
            guard_content(raw)
            selected[f"session-evidence/{index:02d}.jsonl"] = raw
        for checkpoint in replay["checkpoints"]:
            raw = read_regular(safe_path(Path(checkpoint["source"]).parent, checkpoint["path"]))
            require(hashlib.sha256(raw).hexdigest() == checkpoint["sha256"] and len(raw) == checkpoint["bytes"],
                    "SOURCE_CHANGED", "Checkpoint changed")
            selected["session-evidence/checkpoints/" + checkpoint["sha256"] + ".tar"] = raw
        run_summary = None
        if run is not None:
            require(isinstance(store, ResearchStore), "RUN_CONTRACT", "M run export requires schema 4")
            run_payloads, run_summary = _run_payloads(run, project_id, replay)
            selected.update(run_payloads)
        payloads = {"project.json": canonical(metadata) + b"\n", "replay.json": canonical(replay) + b"\n",
                    "REVIEW-RECORD.json": canonical({"R1": None, "R2": None, "execution_authorized": False}) + b"\n",
                    "README.md": ("# DSH Math 审核包\n\nR1/R2 未填写；不代表数学接受或运行授权。程序通过仅为候选待审。\n\n"
                                  "必须在搬移后运行 `python3 -B verify_bundle.py .`，退出码为 0 才是技术验收通过；进程内验证不能替代。\n"
                                  "验证核对外层文件、研究状态、固定 checker 独立复算与所选会话证据。固定 checker 不自动证明 local_claim。\n"
                                  "local_claim 仅限其固定基线和半径，不是全局不存在证明；needs_more_evidence 不是已接受定理。人工 R1/R2 签核只能在包外另存。\n"
                                  "验证器仅忽略 basename 为 `._*` 的 AppleDouble 转移元数据，其他额外文件仍失败。\n"
                                  "研究导出内的零模型标签只描述离线操作，整次会话用量见 replay.json，未知保持 null。\n").encode(),
                    "verify_bundle.py": read_regular(HERE / "verify_bundle.py"), **selected}
        inner_format = "math-harness-p2c-export-v1" if isinstance(store, dict) else "dsh-math-research-v4"
        if isinstance(store, dict):
            result = subprocess.run([sys.executable, "-B", str(BASE / "cli.py"), str(project / "research"), "export", str(temporary / "p2c"), "--expected-seq", str(expected_seq)], capture_output=True, timeout=60)
            require(result.returncode == 0, "EXPORT_FAILED", result.stderr.decode())
            require((temporary / "p2c/snapshot.json").read_bytes() == canonical(snapshot) + b"\n", "SOURCE_CHANGED", "Legacy state changed")
        else:
            for event in store.events:
                guard_content(canonical(event))
            payloads["research/events.jsonl"] = b"".join(canonical(e) + b"\n" for e in store.events)
            payloads["research/.writer.lock"] = b""
            payloads["snapshot.json"] = canonical(snapshot) + b"\n"
            for versions in store.records.values():
                for obj in versions:
                    if obj["kind"] == "Evidence":
                        for attachment in obj["data"]["attachments"]:
                            name = attachment["logical_name"].lower()
                            require(not (name.startswith(".env") or name in ("auth.json", "oauth.json", "credentials.json")),
                                    "SENSITIVE_CONTENT", "Credential filename")
                            raw = read_regular(safe_path(store.root, attachment["relative_path"]))
                            guard_content(raw)
                            payloads["research/" + attachment["relative_path"]] = raw
            for event in store.events:
                if event["control"].get("action") == "check.started":
                    attachment = event["control"]["checker_source"]
                    payloads["research/" + attachment["relative_path"]] = read_regular(safe_path(store.root, attachment["relative_path"]))
            for name, text in render(store).items():
                payloads["readable/" + name] = text.encode()
            for name in RUNTIME_FILES:
                if name in ("schema.py", "cli.py", "verify_export.py"):
                    continue
                payloads["runtime/" + name] = read_regular(safe_path(BASE, name))
            payloads["runtime/p2c_schema.py"] = read_regular(BASE / "schema.py")
            payloads["runtime/schema.py"] = read_regular(HERE / "schema.py")
            payloads["runtime/cli.py"] = read_regular(HERE / "runtime_cli.py")
            from checkers.workflow_certificate_v2 import source_bytes
            payloads['evaluation/optimization-v1/certificates.py'] = source_bytes()[1]
        for name, raw in payloads.items():
            if name not in ("verify_bundle.py",) and not name.startswith("runtime/"):
                guard_content(raw)
            target = safe_path(temporary, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        files = {str(p.relative_to(temporary)): {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in temporary.rglob("*") if p.is_file() and not p.name.startswith("._")}
        manifest = {"format": "dsh-math-export-v1", "research_format": inner_format, "captured_seq": expected_seq,
                    "head_hash": snapshot["state"]["head_hash"], "state_sha256": snapshot["state_sha256"], "files": files,
                    "operation_model_requests": 0, "session_model_requests": None, "R1": None, "R2": None,
                    "run": run_summary, "missing": replay["coverage"], "selected_sources": replay["sources"]}
        write_new(temporary / "manifest.json", manifest)
        result = subprocess.run([sys.executable, "-B", "verify_bundle.py", "."], cwd=temporary,
                                capture_output=True, text=True, timeout=300)
        require(result.returncode == 0 and json.loads(result.stdout).get("status") == "passed",
                "EXPORT_VERIFICATION_FAILED", result.stderr or result.stdout)
    os.rename(temporary, destination)
    return {"directory": str(destination), "format": "dsh-math-export-v1", "captured_seq": expected_seq,
            "operation_model_requests": 0, "session_model_requests": None}
