"""Prepare isolated X/M processes. Preparation never contacts a model or gateway.

The caller owns Gateway authorization, StateGuard over the enclosing arm root,
and run_process supervision. Children can read/write only their native subtree;
common tools, host receipts, provider credentials and the ledger stay outside it.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from native import NativeBlocked, PINS, isolated_environment, macos_fixture_profile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
ROLES = ("explorer", "coordinator", "verifier")
MAX_BYTES = 16 * 1024 * 1024


def _require(value, code, message):
    if not value:
        raise NativeBlocked(code, message)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _execution(value, arm):
    keys = {"mode", "arm", "run_id", "campaign_id", "model_requests", "campaign_max_requests"}
    _require(type(value) is dict and set(value) == keys and value["mode"] == "authorized_smoke"
             and value["arm"] == arm and value["model_requests"] == 2 and value["campaign_max_requests"] == 8
             and all(type(value[key]) is str and 0 < len(value[key]) <= 128 for key in ("run_id", "campaign_id")),
             "SMOKE_AUTHORIZATION", "An exact per-arm two-call smoke execution binding is required")
    return dict(value)


def _gateway(url):
    parsed = urlsplit(url)
    _require(parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
             and parsed.port and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
             and not parsed.username and not parsed.password, "GATEWAY_SCOPE", "Explicit loopback gateway origin required")
    return url.rstrip("/")


def _fresh_root(root):
    root = pathlib.Path(root).absolute()
    temporary = pathlib.Path(tempfile.gettempdir()).resolve()
    resolved = root.resolve()
    _require(resolved.is_relative_to(temporary) and resolved != temporary and not root.exists() and not root.is_symlink(),
             "COLD_STATE", "Native root must be a new OS-temporary subtree; resume is disabled")
    _require(root.parent.is_dir() and not root.parent.is_symlink(), "COLD_STATE", "An existing arm parent is required")
    resolved.mkdir(mode=0o700)
    return resolved


def _executable(value):
    path = pathlib.Path(value).resolve()
    _require(path.is_file() and os.access(path, os.X_OK), "RUNTIME", "An explicit installed runtime executable is required")
    return path


def _environment(root, token, executables, execution, offline):
    env = isolated_environment(root, token=token, path_entries=tuple(str(path.parent) for path in executables))
    env["FOUR_HARNESS_MODE"] = "offline_fixture" if offline else "authorized_smoke"
    env["FOUR_HARNESS_EXECUTION"] = json.dumps(execution, sort_keys=True, separators=(",", ":"))
    if not offline:
        env.pop("FOUR_HARNESS_OFFLINE_FIXTURE", None)
    (root / "workspace").mkdir(mode=0o700)
    (root / "state").mkdir(mode=0o700)
    return env


def _command(root, executable, arguments, readable, gateway, denied=()):
    _require(platform.system() == "Darwin" and shutil.which("sandbox-exec"),
             "OS_ISOLATION", "The approved launcher requires macOS Seatbelt")
    _require(all(not pathlib.Path(path).resolve().is_relative_to(root.parent)
                 and not root.parent.is_relative_to(pathlib.Path(path).resolve()) for path in readable),
             "STATE_ISOLATION", "Read-only source/runtime must be separate from the enclosing writable arm root")
    profile = macos_fixture_profile(root, readable=tuple(readable), writable=(root,), gateway_url=gateway)
    profile += "".join(f"\n(deny file-read* (subpath {json.dumps(str(path))}))\n" for path in denied)
    profile_path = root / "native-profile.sb"
    profile_path.write_text(profile, encoding="utf-8")
    return ["/usr/bin/sandbox-exec", "-f", str(profile_path), str(executable), *map(str, arguments)]


def verify_x_build(source_copy):
    """Verify the frozen source and dependency receipt without copying mutable state."""
    source = pathlib.Path(source_copy).resolve()
    receipt_path = source / "four-harness-build.json"
    receipt = json.loads(receipt_path.read_text())
    _require(receipt.get("arm") == "X" and receipt.get("source", {}).get("commit") == PINS["X"]["commit"],
             "BUILD_DRIFT", "Pinned X source build required")
    files = receipt.get("files", {})
    _require(all(name in files for name in ("four-harness-host-entry.ts", "four-harness-host-runtime.ts",
                 "four-harness-host-fixture.ts", "src/four-harness-native.ts")),
             "BUILD_DRIFT", "X build lacks the reviewed host entry and transport")
    actual_files, actual_links = {}, {}
    for path in sorted(source.rglob("*")):
        relative = str(path.relative_to(source))
        _require(path.resolve().is_relative_to(source) and (path.is_dir() or path.is_file()),
                 "BUILD_DRIFT", f"Unsupported or escaping build entry: {relative}")
        if path.is_symlink():
            actual_links[relative] = os.readlink(path)
        if path.is_file() and relative != "four-harness-build.json":
            actual_files[relative] = _digest(path)
    _require(actual_files == {**files, **receipt.get("dependency_files", {})}
             and actual_links == receipt.get("dependency_links", {}),
             "BUILD_DRIFT", "Frozen X inventory has changed, missing, or unexpected source/dependency entries")
    _require(bool(receipt.get("dependency_files")), "BUILD_DRIFT", "Installed dependency hash receipt required")
    return source, receipt, _digest(receipt_path)


def prepare_x_smoke(root, *, source_copy, bun, gateway_url, role_tokens, execution, offline=False):
    execution = _execution(execution, "X")
    gateway = _gateway(gateway_url)
    _require(type(offline) is bool and type(role_tokens) is dict and set(role_tokens) == set(ROLES)
             and all(type(token) is str and token for token in role_tokens.values()),
             "CAPABILITY", "Exactly three X role capability tokens are required")
    source, receipt, receipt_sha = verify_x_build(source_copy)
    binary = _executable(bun)
    root = _fresh_root(root)
    env = _environment(root, "unused-native-root-capability", (binary,), execution, offline)
    env["FOUR_HARNESS_ROUTES"] = json.dumps({role: {"url": gateway + f"/X/{role}", "token": role_tokens[role]} for role in ROLES})
    env["FOUR_HARNESS_MAX_OUTPUT"] = "2048"
    # Disable Bun's optional transpilation cache: its rapidly renamed temporary
    # files cannot provide a complete state scan. Native SQLite state stays counted.
    env["BUN_RUNTIME_TRANSPILER_CACHE_PATH"] = "0"
    env["FOUR_HARNESS_STATE"] = str(root / "state" / "campaign.sqlite")
    evidence = root / "workspace" / "native-evidence.json"
    env["FOUR_HARNESS_EVIDENCE"] = str(evidence)
    command = _command(root, binary, [source / "four-harness-host-entry.ts"], (source, binary), gateway)
    return {"command": command, "env": env, "cwd": str(root / "workspace"), "writable_roots": [str(root)],
            "evidence_path": str(evidence), "metadata": {"arm": "X", "execution_mode": env["FOUR_HARNESS_MODE"],
                "build_receipt_sha256": receipt_sha, "native_pin": receipt["source"], "runtime_sha256": _digest(binary),
                "readable_source": [str(source)], "state_root": str(root), "state_guard_required": True,
                "state_guard_scope": "entire enclosing arm root including common tools", "state_bytes_limit": MAX_BYTES,
                "filesystem_hard_quota": False, "resume_dispatch": False, "parallel_dispatch": False, "compaction": False}}


def _python_runtime(binary):
    result = subprocess.run([str(binary), "-I", "-S", "-B", "-c",
        "import json,sys,sysconfig; print(json.dumps({'roots':[sys.base_prefix,sys.prefix], 'excluded':[sysconfig.get_path('purelib'),sysconfig.get_path('platlib')]}))"],
        capture_output=True, text=True, check=True, timeout=10, env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"})
    paths = json.loads(result.stdout)
    return (tuple(dict.fromkeys(pathlib.Path(value).resolve() for value in paths["roots"])),
            tuple(dict.fromkeys(pathlib.Path(value).resolve() for value in paths["excluded"])))


def prepare_m_smoke(root, **kwargs):
    return _prepare_m(root, **kwargs)


def prepare_m_optimization(root, **kwargs):
    return _prepare_m(root, profile='optimization', **kwargs)


def _prepare_m(root, *, node, python, gateway_url, token, execution, offline=False, profile='smoke', bootstrap=None, context_policy='full'):
    if profile == 'optimization':
        expected = {'mode': 'authorized_optimization', 'arm': 'M', 'run_id': execution.get('run_id'),
                    'campaign_id': execution.get('campaign_id'), 'model_requests': 120, 'campaign_max_requests': 120}
        _require(execution == expected and bool(execution['run_id']) and bool(execution['campaign_id']),
                 'OPTIMIZATION_AUTHORIZATION', 'Exact optimization binding required')
        _require(type(bootstrap) is dict and type(bootstrap.get('problem')) is dict,
                 'COLD_INPUT', 'Frozen optimization bootstrap required')
    else:
        execution = _execution(execution, 'M')
    gateway = _gateway(gateway_url)
    _require(type(offline) is bool and type(token) is str and token, "CAPABILITY", "M worker capability token required")
    node, python = _executable(node), _executable(python)
    runtime, excluded = _python_runtime(python)
    source_files = [HERE / name for name in ("m_adapter.mjs", "m_bridge.mjs", "m_service.py")]
    source_files += [REPO / name for name in ("schema.py", "backend.py", "workflow_runner.mjs", "workflow_store.py", "workflow_context.py",
                                            "workflow_service.py", "workflow_strategy.py")]
    _require(all(path.is_file() for path in source_files), "BUILD_DRIFT", "Current workflow source is incomplete")
    root = _fresh_root(root)
    env = _environment(root, token, (node, python), execution, offline)
    workflow = root / "tmp" / "workflow"
    workflow.mkdir(mode=0o700)
    evidence = root / "workspace" / "native-evidence.json"
    if profile == 'optimization':
        env['FOUR_HARNESS_MODE'] = 'offline_fixture' if offline else 'authorized_optimization'
        env['FOUR_HARNESS_BOOTSTRAP'] = json.dumps(bootstrap, ensure_ascii=False)
        env['FOUR_HARNESS_CONTEXT_POLICY'] = context_policy
    arguments = [HERE / "m_bridge.mjs", '--' + profile, "--gateway", gateway, "--token-env", "FOUR_HARNESS_TOKEN",
                 "--execution-env", "FOUR_HARNESS_EXECUTION", "--root", workflow, "--run-id", execution["run_id"],
                 "--python", python, "--evidence", evidence]
    if offline:
        arguments.append("--offline")
    readable = (*source_files, REPO / "runtime" / "p2c", node, python, *runtime)
    command = _command(root, node, arguments, readable, gateway, denied=excluded)
    return {"command": command, "env": env, "cwd": str(root / "workspace"), "writable_roots": [str(root)],
            "evidence_path": str(evidence), "metadata": {"arm": "M", "execution_mode": env["FOUR_HARNESS_MODE"],
                "source_sha256": {str(path.relative_to(REPO)): _digest(path) for path in source_files},
                "runtime_sha256": {str(node): _digest(node), str(python): _digest(python)},
                "readable_source": [str(path) for path in source_files] + [str(REPO / "runtime" / "p2c")],
                "state_root": str(root), "state_guard_required": True,
                "state_guard_scope": "entire enclosing arm root including common tools", "state_bytes_limit": MAX_BYTES,
                "filesystem_hard_quota": False, "resume_dispatch": False, "parallel_dispatch": False, "compaction": False}}
