"""One-shot fake child executable for original Rethlas process-path tests.

This is deliberately not Codex and does not implement an agent loop. Each native
upstream invocation receives exactly one immutable local fixture response.
"""
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, build_opener, ProxyHandler

if os.environ.get("FOUR_HARNESS_OFFLINE_FIXTURE") != "1":
    raise SystemExit("Fake child requires explicit offline fixture mode")
if sys.argv[1:] == ["--version"]:
    print("four-harness fake child (not Codex)")
    raise SystemExit(0)
args = sys.argv[1:]
if "--dangerously-bypass-approvals-and-sandbox" in args or 'web_search="live"' in args:
    raise SystemExit("Unsafe native launch flag")
if "-m" not in args or args[args.index("-m") + 1] != "gpt-6-astra" or not any(value in args for value in ("model_reasoning_effort=max", 'model_reasoning_effort="max"')):
    raise SystemExit("Frozen child model/effort missing")
if 'sandbox_mode="read-only"' not in args:
    raise SystemExit("Native sandbox not frozen")
role = os.environ["FOUR_HARNESS_ROLE"]
route = json.loads(os.environ["FOUR_HARNESS_ROUTES"])[role]
state_path = Path(os.environ["FOUR_HARNESS_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {"calls": []}
payload = {"model": "gpt-6-astra", "reasoning": {"effort": "max"}, "max_output_tokens": int(os.environ["FOUR_HARNESS_MAX_OUTPUT"]),
           "input": [{"role": "user", "content": "Offline native subprocess fixture " + role}], "stream": False}
headers = {"Content-Type": "application/json", "Authorization": "Bearer " + route["token"],
           "X-Four-Thread-Id": "fixture-" + role}
if state.get("last_request_id"):
    headers["X-Four-Parent-Request-Id"] = state["last_request_id"]
request = Request(route["url"] + "/v1/responses", data=json.dumps(payload).encode(), headers=headers)
with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
    receipt = json.load(response)
    request_id = response.headers["X-Four-Request-Id"]
if receipt.get("status") != "completed" or not request_id:
    raise SystemExit("Missing gateway completion receipt")
state["last_request_id"] = request_id
state["calls"].append({"role": role, "argv": args, "request_id": request_id})
state_path.write_text(json.dumps(state))
source = Path(__file__).resolve().parent
if role == "generator":
    print("session id: four-harness-fixture-session")
    if sum(call["role"] == role for call in state["calls"]) == 2:
        result = source / "agents/generation/results/four-harness-fixture/blueprint_verified.md"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("Offline fixture stop marker only; no proof was verified.\n")
elif role == "verifier":
    run_id = re.match(r"Run_id: ([A-Za-z0-9_-]+)\.", args[-1]).group(1)
    result = source / "agents/verification/results" / run_id / "verification.json"
    result.write_text(json.dumps({"fixture_only": True, "critical_errors": [], "gaps": []}))
else:
    raise SystemExit("Unrecognized fixture role")
print("offline fake child completed")
