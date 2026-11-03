"""Exercise pinned Rethlas' real shell and verifier subprocess entry paths.

The child is explicitly fake, one response per invocation; the upstream shell
retains ownership of exec/resume and its stop condition. This does not qualify
native Codex, the R strategy descendants, or proof verification.
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
env = {**os.environ, "FOUR_HARNESS_ROLE": "generator", "PROBLEM_FILE": "data/four-harness-fixture.md",
       "MAX_ITERATIONS": "3", "VERIFY_URL": json.loads(os.environ["FOUR_HARNESS_ROUTES"])["verifier"]["url"] + "/health"}
shell_log = Path(os.environ["FOUR_HARNESS_STATE"]).parent / "upstream-shell.log"
# The original timer's sleep child can retain pipe descriptors after shell exit.
# Use a file, then the outer process-group owner tears down every descendant.
with shell_log.open("w") as output:
    generation = subprocess.run(["/bin/bash", str(source / "agents/generation/tests/run_example.sh")],
                                env=env, stdout=output, stderr=subprocess.STDOUT, text=True, timeout=20)
if generation.returncode:
    logs = source / "agents/generation/logs/four-harness-fixture/iter"
    for log in sorted(logs.glob("*.md")):
        print(log.read_text(), file=sys.stderr)
    raise SystemExit(shell_log.read_text())
os.environ["FOUR_HARNESS_ROLE"] = "verifier"
spec = importlib.util.spec_from_file_location("actual_rethlas_verifier", source / "agents/verification/api/server.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
# This public endpoint function allocates the run, calls the original command
# builder and subprocess runner, then loads the produced JSON object.
result = module.verify(module.VerifyRequest(statement="Fixture: 2 + 3 = 5", proof="Offline transport fixture; not a model proof."))
state = json.loads(Path(os.environ["FOUR_HARNESS_STATE"]).read_text())
if [call["role"] for call in state["calls"]] != ["generator", "generator", "verifier"]:
    raise SystemExit("Native entry invocation sequence changed")
if state["calls"][0]["argv"][:1] != ["exec"] or state["calls"][1]["argv"][:3] != ["exec", "resume", "four-harness-fixture-session"]:
    raise SystemExit("Native session continuation changed")
print(json.dumps({"kind": "actual_rethlas_entry_paths_fake_child", "calls": state["calls"],
                  "verification_fixture": result, "upstream_stdout_sha256": hashlib.sha256(shell_log.read_bytes()).hexdigest(),
                  "native_codex_qualified": False, "native_strategy_qualified": False, "live_ready": False}))
