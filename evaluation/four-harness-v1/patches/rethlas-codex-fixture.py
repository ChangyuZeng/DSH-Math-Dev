"""Probe original Rethlas entry paths with installed Codex and fake-only HTTP.

The generation and verifier invocations are separate startup diagnostics. A
rejected generator does not produce a proof or authorize a verifier continuation.
The original generation shell and verifier API still own their native commands.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

if os.environ.get("FOUR_HARNESS_OFFLINE_FIXTURE") != "1":
    raise SystemExit("Offline fixture mode is required")
source = Path(__file__).resolve().parent
sys.path.insert(0, os.environ["FOUR_HARNESS_PYTHON_SITE"])
version = subprocess.run([os.environ["CODEX_BIN"], "--version"], capture_output=True, text=True, timeout=10)
if version.returncode or version.stdout.strip() != "codex-cli 0.166.0":
    raise SystemExit("Actual pinned native Codex is required")

env = {**os.environ, "FOUR_HARNESS_ROLE": "generator", "PROBLEM_FILE": "data/four-harness-fixture.md",
       "MAX_ITERATIONS": "1", "VERIFY_URL": json.loads(os.environ["FOUR_HARNESS_ROUTES"])["verifier"]["url"] + "/health"}
shell_log = Path(os.environ["FOUR_HARNESS_STATE"]).parent / "upstream-native-shell.log"
with shell_log.open("w") as output:
    generation = subprocess.run(["/bin/bash", str(source / "agents/generation/tests/run_example.sh")],
                                env=env, stdout=output, stderr=subprocess.STDOUT, text=True, timeout=25)
generation_logs = [path.read_text() for path in sorted((source / "agents/generation/logs").rglob("*.md"))]

# This independent verifier startup probe imports and invokes the original API.
# No fake report, parent identity, recursive spawn or completed proof is invented.
os.environ["FOUR_HARNESS_ROLE"] = "verifier"
os.environ["CODEX_TIMEOUT_SECONDS"] = "25"
spec = importlib.util.spec_from_file_location("actual_rethlas_native_verifier", source / "agents/verification/api/server.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
try:
    verification = {"returned": module.verify(module.VerifyRequest(
        statement="Offline transport fixture: 2 + 3 = 5", proof="Startup probe only; no model-produced proof."))}
except module.HTTPException as error:
    verification = {"http_status": error.status_code, "detail": error.detail}
verification["logs"] = [path.read_text() for path in sorted((source / "agents/verification/results").rglob("log.md"))]
print(json.dumps({"kind": "actual_rethlas_native_codex_startup", "runtime_version": version.stdout.strip(),
                  "generation": {"returncode": generation.returncode, "logs": generation_logs,
                                 "driver_stdout_sha256": hashlib.sha256(shell_log.read_bytes()).hexdigest()},
                  "verification": verification, "continuation_after_rejected_generator": False,
                  "native_codex_qualified": False, "native_strategy_qualified": False, "live_ready": False}))
