"""Offline X qualification through the original public host and Pi workflow.

Only the provider transport and evidence observer are injected. The bounded
Responses list contains synthetic arithmetic, never a real research task.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from native import (MAX_OUTPUT, PINS, TOOLS, _fixture_ledger,
                    _fixture_response, _new_file, copied_fixture, require, sha256)

ENTRY = "four-harness-host-fixture.ts"
CORE = ("src/workflow.ts", "src/verification.ts", "src/policy/prompts.ts",
        "src/policy/contracts.ts", "src/state.ts", "src/report.ts",
        "src/host.ts", "src/role-runtime.ts")
INHERITED_ADAPTATIONS = {
    "src/host.ts": "Existing copy-only retry and restart limits; native open/ownership/workflow unchanged",
    "src/role-runtime.ts": "Existing copy-only recovery limits and gateway I/O/common-tool wrappers",
}
STAGES = ("explorer", "explorer", "coordinator", "source", "correctness",
          "requirements", "statement", "proof", "reconstruction")
INTRINSIC = {"submit_notes", "submit_coordination", "submit_verdict",
             "submit_statement", "submit_proof", "read_notes"}


def _tree_manifest(root):
    root = Path(root).resolve()
    files, links = {}, {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        require(path.resolve().is_relative_to(root), "BUILD_LINK", "Build symlink escapes its isolated tree")
        if path.is_symlink():
            links[relative] = str(path.readlink())
        if path.is_file() and relative != "four-harness-build.json":
            files[relative] = sha256(path)
    return files, links


def prepare_host_fixture(source_copy, destination):
    """Clone a verified exported build, including installed dependency hashes.

    The earlier build and pinned original stay read-only. No package manager,
    network, provider, shell scheduler, or source rewrite is involved.
    """
    source, target = Path(source_copy).resolve(), Path(destination).resolve()
    require(source.is_dir() and not (source / ".git").exists(), "COPY_ONLY", "Use an exported native X build")
    require(not target.exists() and not target.is_relative_to(source) and not source.is_relative_to(target),
            "COPY_ONLY", "Host fixture requires a fresh separate copy")
    receipt_path = source / "four-harness-build.json"
    receipt = json.loads(receipt_path.read_text())
    require(receipt.get("arm") == "X" and receipt.get("source", {}).get("commit") == PINS["X"]["commit"],
            "BUILD_PIN", "X host fixture requires the frozen source commit")
    expected = receipt.get("files")
    require(isinstance(expected, dict) and expected, "BUILD_MANIFEST", "Source file manifest is mandatory")
    for relative, digest in expected.items():
        path = source / relative
        require(path.resolve().is_relative_to(source) and path.is_file() and sha256(path) == digest,
                "BUILD_DRIFT", f"Copied native source modified: {relative}")
    require((source / "node_modules/@earendil-works/pi-durable/package.json").is_file(),
            "DEPENDENCIES_MISSING", "Install pinned native dependencies before the host fixture")
    for relative in CORE:
        require(relative in expected, "BUILD_MANIFEST", f"Native core missing from receipt: {relative}")
    files, links = _tree_manifest(source)
    require(ENTRY not in files, "COPY_ONLY", "Host fixture entry must be new")
    shutil.copytree(source, target, symlinks=True, ignore=lambda directory, names:
                    ["four-harness-build.json"] if Path(directory).resolve() == source else [])
    copied_files, copied_links = _tree_manifest(target)
    require(files == copied_files and links == copied_links, "COPY_DRIFT", "Source or dependency bytes changed while copying")
    fixture = Path(__file__).resolve().parent / "patches/xean-host-fixture.ts"
    _new_file(target / ENTRY, fixture.read_text())
    copied_files[ENTRY] = sha256(target / ENTRY)
    inherited = {key: value for key, value in receipt.items() if key not in {"files", "changes", "preserved_core"}}
    dependencies = {name: digest for name, digest in copied_files.items() if name.startswith("node_modules/")}
    dependency_hash = hashlib.sha256(json.dumps(dependencies, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    relation = {}
    for relative in CORE:
        inherited_changes = [change for change in receipt.get("changes", []) if change["path"] == relative]
        relation[relative] = {
            "upstream_relation": "inherited_adapter_patch" if inherited_changes else "byte_identical",
            "upstream_sha256": inherited_changes[0]["before_sha256"] if inherited_changes else copied_files[relative],
            "copy_sha256": copied_files[relative], "adaptation": INHERITED_ADAPTATIONS.get(relative),
        }
    result = {**inherited, "mode": "copy_only_public_host_fixture",
              "parent_receipt_sha256": sha256(receipt_path),
              "changes": [*receipt.get("changes", []), {"path": ENTRY, "before_sha256": None,
                                                        "after_sha256": copied_files[ENTRY]}],
              "preserved_core": {relative: copied_files[relative] for relative in CORE},
              "core_preservation_basis": "Byte-identical to verified parent build; upstream relationship listed separately",
              "native_source_relationship": relation,
              "files": copied_files, "symlinks": copied_links,
              "dependency_file_count": len(dependencies), "dependency_tree_sha256": dependency_hash,
              "live_ready": False}
    _new_file(target / "four-harness-build.json", json.dumps(result, indent=2, sort_keys=True))
    return result


def fixture_responses(scenario="accepted"):
    """Nine bounded receipts drive original submission schemas and full closure."""
    require(scenario in {"accepted", "missing_usage", "failed_response", "request_limit"},
            "SCENARIO", "Unknown offline host fixture scenario")
    note1 = "Synthetic note A: 2 + 2 = 4 by integer addition."
    note2 = "Synthetic note B: using note A, 2 + 3 = (2 + 2) + 1 = 5."
    verdict = lambda note, report: {"note": note, "verdict": "PASS", "report": report}
    calls = [
        ("materials_list", {}),
        ("submit_notes", {"notes": [
            {"text": note1, "support": [], "summary": None, "detailedSummary": None},
            {"text": note2, "support": ["n1"], "summary": None, "detailedSummary": None}]}),
        ("submit_coordination", {"filings": [{"note": "n1", "summary": "Two plus two is four."},
                                             {"note": "n2", "summary": "Two plus three is five."}],
            "verify": [{"note": "n1", "verifiers": ["source", "correctness"]},
                       {"note": "n2", "verifiers": ["source", "correctness", "requirements", "reconstruction"]}]}),
        ("submit_verdict", {"verdicts": [verdict("n1", "Synthetic generated statement; no external source."),
                                          verdict("n2", "Generated support cites n1 exactly.")]}),
        ("submit_verdict", {"verdicts": [verdict("n1", "Addition gives four."),
                                          verdict("n2", "Addition of one gives five.")]}),
        ("submit_verdict", {"verdicts": [verdict("n2", "The requested arithmetic follows from n1.")]}),
        ("submit_statement", {"statements": [{"note": "n1", "statement": "For integers, 2 + 2 = 4."},
                                               {"note": "n2", "statement": "For integers, 2 + 3 = 5, using n1."}]}),
        ("submit_proof", {"proof": "Successor arithmetic proves 2 + 2 = 4. Then 2 + 3 = (2 + 2) + 1 = 4 + 1 = 5."}),
        ("submit_verdict", {"statements": [], "verdicts": [
            verdict("n1", "The independent proof reconstructs n1."),
            verdict("n2", "The independent proof reconstructs n2 and its generated dependency.")]}),
    ]
    responses = [_fixture_response(index, [{"type": "function_call", "id": f"fc_x_host_{index}",
        "call_id": f"call_x_host_{index}", "name": name, "arguments": json.dumps(arguments), "status": "completed"}])
        for index, (name, arguments) in enumerate(calls, 1)]
    if scenario == "missing_usage":
        responses[3]["usage"] = None
    elif scenario == "failed_response":
        responses[3]["status"] = "failed"
        responses[3]["error"] = {"code": "fixture_failure", "message": "Synthetic provider failure"}
    return responses


def xean_full_fixture(root, *, source_copy, bun, max_output_tokens=MAX_OUTPUT, scenario="accepted"):
    """Run the native full host once, behind the exact fake gateway + OS jail."""
    from gateway import FixtureTransport, Gateway, capability
    from tools import CommonTools
    responses = fixture_responses(scenario)
    count = 8 if scenario == "request_limit" else len(responses)
    root, ledger, run_id = _fixture_ledger(root, "X", count, max_output_tokens)
    build = root / "source-copy"
    receipt = prepare_host_fixture(source_copy, build)
    common = CommonTools(run_id=run_id, run_root=root / "tools", materials={},
                         accounting=lambda event: ledger.tool_event(run_id, event))
    tokens = {role: capability() for role in ("explorer", "coordinator", "verifier")}
    transport = FixtureTransport(responses)
    with Gateway(ledger, transport=transport,
                 bindings={token: {"run_id": run_id, "arm": "X", "role": role} for role, token in tokens.items()},
                 tools={run_id: common}, allowed_tools={run_id: set(TOOLS) | INTRINSIC},
                 intrinsic_tools={(run_id, role): INTRINSIC for role in tokens}, inject_output_cap=True) as gateway:
        native = copied_fixture(root / "native", gateway=gateway, arm="X", source_copy=build, executable=bun,
                                entry=[ENTRY], role_tokens=tokens, timeout=60)
        snapshot = ledger.snapshot(run_id)
    artifact = root / "native/workspace/synthetic-host-evidence.json"
    output = None
    if artifact.is_file():
        require(not artifact.is_symlink() and artifact.stat().st_size <= 8 * 1024 * 1024,
                "EVIDENCE_BOUND", "Native evidence must remain a bounded fixture file")
        output = json.loads(artifact.read_text())
        require(output.get("kind") == "actual_native_x_host", "EVIDENCE_KIND", "Native host evidence kind differs")
    physical = output.get("physical_requests", []) if output else []
    stages = [request.get("native", {}).get("stage") for request in physical]
    rows = snapshot["request_states"]
    complete_receipts = (snapshot["requests"] == len(STAGES) and snapshot["accounting_complete"] and
                         snapshot["detail_complete"] and all(row["state"] == "finished" for row in rows) and
                         [row["id"] for row in rows] == [request.get("request_id") for request in physical])
    host_complete = bool(output and native["returncode"] == 0 and complete_receipts and
                         output["report"]["status"]["outcome"] == "accepted" and stages == list(STAGES))
    parent_qualified = bool(host_complete and rows[0]["parent"] is None and
        all(request.get("parent_reason") and not request.get("ancestry_gap") and
            row["parent"] == request["parent_request_id"] == rows[index - 1]["id"] and
            row["thread"] == str(request["native"]["conversation"]["id"])
            for index, (row, request) in enumerate(zip(rows, physical)) if index))
    blockers = ["NATIVE_FORK_RESUME_NOT_EXERCISED", "NATIVE_PARALLEL_DESCENDANTS_NOT_EXERCISED", "NATIVE_COMPACTION_NOT_EXERCISED",
                "NATIVE_CODEX_SOURCE_SEARCH_BRANCH_NOT_EXERCISED", "NATIVE_STATE_ARTIFACT_LIMITS_NOT_QUALIFIED"]
    if not host_complete:
        blockers.insert(0, "FULL_X_HOST_PATH_INCOMPLETE")
    if not parent_qualified:
        blockers.insert(0, "DESCENDANT_PARENT_LINKAGE_NOT_QUALIFIED")
    evidence = {"native": native, "output": output, "snapshot": snapshot,
                "final_snapshot": ledger.snapshot(run_id), "events": ledger.events(), "requests": transport.requests,
                "build": {"parent_receipt_sha256": receipt["parent_receipt_sha256"],
                          "preserved_core": receipt["preserved_core"],
                          "core_preservation_basis": receipt["core_preservation_basis"],
                          "native_source_relationship": receipt["native_source_relationship"],
                          "dependency_file_count": receipt["dependency_file_count"],
                          "dependency_tree_sha256": receipt["dependency_tree_sha256"],
                          "bun_sha256": sha256(Path(bun).resolve()),
                          "fixture_sha256": receipt["files"][ENTRY]},
                "scenario": scenario, "observed_stages": stages,
                "native_host_path_qualified": host_complete, "parent_linkage_qualified": parent_qualified,
                "qualification_scope": "public native X host, Explorer, Coordinator, source/correctness/requirements, joint generated-closure statement/blind-proof/comparison",
                "qualification_blockers": blockers, "native_qualified": False, "live_ready": False}
    _new_file(root / "host/evidence.json", json.dumps(evidence, indent=2, sort_keys=True))
    return evidence
