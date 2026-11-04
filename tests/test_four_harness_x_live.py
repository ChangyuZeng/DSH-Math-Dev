"""Same-entry X/M smoke and containment, using only the local fake Responses API."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation/four-harness-v1"))
from cold_start import write_materials
from gateway import FixtureTransport, Gateway, capability, responses_tools
from ledger import GateError, Ledger, SMOKE_BUDGET
from native import DEFAULT_NODE, NativeBlocked, _fixture_response, run_process
from state_guard import StateGuard
from tools import CommonTools, ToolLimits
from x_full import INTRINSIC
from x_live import prepare_m_smoke, prepare_x_smoke, verify_x_build


def execution(arm):
    return {"mode": "authorized_smoke", "arm": arm, "run_id": arm + "-smoke", "campaign_id": "fixture-smoke",
            "model_requests": 2, "campaign_max_requests": 8}


def call_response(index, name, arguments):
    return _fixture_response(index, [{"type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
                                      "name": name, "arguments": json.dumps(arguments)}])


class SmokePreparationTests(unittest.TestCase):
    def test_authorization_is_explicit_and_preparation_cannot_create_formal_root(self):
        with tempfile.TemporaryDirectory(prefix="four-plan-boundary-") as directory:
            root = Path(directory).resolve() / "native"
            with self.assertRaises(NativeBlocked) as caught:
                prepare_m_smoke(root, node=DEFAULT_NODE, python=sys.executable, gateway_url="http://127.0.0.1:9",
                                token="offline-test-only", execution={**execution("M"), "model_requests": 120})
            self.assertEqual(caught.exception.code, "SMOKE_AUTHORIZATION")
            self.assertFalse(root.exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS launcher boundary")
    def test_readonly_runtime_cannot_overlap_the_common_tool_parent(self):
        with tempfile.TemporaryDirectory(prefix="four-plan-overlap-") as directory:
            parent = Path(directory).resolve()
            fake_node = parent / "unused-runtime"
            fake_node.write_text("#!/bin/sh\nexit 99\n")
            fake_node.chmod(0o700)
            with self.assertRaises(NativeBlocked) as caught:
                prepare_m_smoke(parent / "native", node=fake_node, python=sys.executable,
                    gateway_url="http://127.0.0.1:9", token="offline-test-only", execution=execution("M"), offline=True)
            self.assertEqual(caught.exception.code, "STATE_ISOLATION")


@unittest.skipUnless(os.environ.get("FOUR_HARNESS_NATIVE_FIXTURES") == "1", "Explicit installed offline native fixture opt-in required")
class NativeSmokePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ.get("FOUR_HARNESS_XEAN_BUILD", ROOT / "dist/four-harness-builds/xean-native-10"))
        cls.bun = Path(os.environ.get("FOUR_HARNESS_BUN", ROOT / "dist/four-harness-builds/runtime/node_modules/@oven/bun-darwin-aarch64/bin/bun"))
        cls.proposal = json.loads(subprocess.run([str(DEFAULT_NODE), "--input-type=module", "-e",
            f"import {{syntheticProposal}} from {json.dumps((ROOT/'evaluation/four-harness-v1/m_bridge.mjs').as_uri())}; console.log(JSON.stringify(syntheticProposal()));"],
            capture_output=True, text=True, check=True).stdout)

    def fixture(self, arm, *, mutate=None, canary=False, oversized_state=False):
        temporary = tempfile.TemporaryDirectory(prefix="four-same-entry-")
        root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        parent = root / "arm"
        parent.mkdir()
        ledger = Ledger(root / "host-ledger.sqlite")
        ledger.create_campaign("fixture-smoke", max_requests=8, max_tokens=4 * SMOKE_BUDGET["total_tokens"], scope="smoke")
        run_id = execution(arm)["run_id"]
        ledger.create_run(run_id, "fixture-smoke", arm, budget=SMOKE_BUDGET)
        common = CommonTools(run_id, parent / "common", write_materials(root / "materials", "smoke"),
                             limits=ToolLimits(timeout_seconds=10, artifact_bytes=16 * 1024 * 1024))
        secret = root / "host-canary"
        secret.write_text("private-host-canary")
        responses = [call_response(1, "compute", {"code": "print(2 + 3)"})]
        if arm == "X":
            responses.append(call_response(2, "submit_notes", {"notes": [{"text": "The common compute returned 5 for 2 + 3.",
                "support": [], "summary": "2 + 3 = 5.", "detailedSummary": None}]}))
        else:
            responses.append(_fixture_response(2, [{"type": "message", "id": "message_2", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(self.proposal)}]}]))
        for response in responses:
            response["reasoning"] = {"effort": "max"}
        if mutate:
            mutate(responses)
        transport = FixtureTransport(responses)
        tokens = {role: capability() for role in (("explorer", "coordinator", "verifier") if arm == "X" else ("worker",))}
        guard_errors = []
        def stop(code):
            error = sys.exception()
            guard_errors.append({"type": type(error).__name__, "message": str(error), "filename": getattr(error, "filename", None)})
            ledger.stop(run_id, code)
        guard = StateGuard([parent], max_bytes=16 * 1024 * 1024, on_stop=stop).start()
        self.addCleanup(guard.stop)
        with Gateway(ledger, transport=transport,
                     bindings={token: {"run_id": run_id, "arm": arm, "role": role} for role, token in tokens.items()},
                     tools={run_id: common}, allowed_tools={run_id: {tool["name"] for tool in responses_tools("smoke")}},
                     intrinsic_tools=__import__('smoke').native_intrinsic_routes(arm,run_id,tokens),
                     state_guards={run_id: guard}, inject_output_cap=True) as gateway:
            if arm == "X":
                plan = prepare_x_smoke(parent / "native", source_copy=self.source, bun=self.bun,
                    gateway_url=gateway.url, role_tokens=tokens, execution=execution(arm), offline=True)
            else:
                plan = prepare_m_smoke(parent / "native", node=DEFAULT_NODE, python=sys.executable,
                    gateway_url=gateway.url, token=tokens["worker"], execution=execution(arm), offline=True)
            self.assertEqual(plan["writable_roots"], [str(parent / "native")])
            if arm == "X":
                self.assertEqual(plan['env']['BUN_RUNTIME_TRANSPILER_CACHE_PATH'], '0')
            if canary:
                # Execute actual OS file operations under the identical generated profile.
                targets = [str(secret), str(parent / "common/.host/identity.json"), str(root / "host-ledger.sqlite")]
                code = "const fs = require('node:fs'); const paths = " + json.dumps(targets) + "; let denied=[]; for(const p of paths){for(const op of ['read','write']){try{if(op==='read')fs.readFileSync(p);else fs.writeFileSync(p,'tampered');denied.push(false)}catch(e){denied.push(e.code==='EPERM'||e.code==='EACCES')}}} console.log(JSON.stringify(denied));"
                binary = str(self.bun if arm == "X" else DEFAULT_NODE)
                command = plan["command"][:3] + [binary, "-e", code]
                denied = run_process(command, env=plan["env"], cwd=plan["cwd"], timeout=15)
                self.assertEqual(denied["returncode"], 0, denied)
                self.assertEqual(json.loads(denied["stdout"]), [True] * 6)
                self.assertEqual(secret.read_text(), "private-host-canary")
            if oversized_state:
                overflow = Path(plan["writable_roots"][0]) / "oversized-native-state.sqlite-wal"
                with overflow.open("xb") as stream:
                    stream.truncate(SMOKE_BUDGET["artifact_bytes"] + 1)
                with self.assertRaises(GateError):
                    guard.check()
            native = run_process(plan["command"], env=plan["env"], cwd=plan["cwd"], timeout=45)
            snapshot = ledger.snapshot(run_id)
            artifact = Path(plan["evidence_path"])
            evidence = json.loads(artifact.read_text()) if artifact.is_file() else None
        return {"native": native, "snapshot": snapshot, "evidence": evidence, "requests": transport.requests,
                "events": ledger.events(), "guard": {**guard.report(), "errors": guard_errors}, "plan": plan}

    def assert_roundtrip(self, result):
        self.assertEqual(result["native"]["returncode"], 0, {"native": result["native"], "guard": result["guard"]})
        self.assertIsNotNone(result["evidence"], result["native"])
        self.assertEqual(result["snapshot"]["requests"], 2, result)
        self.assertEqual(result["snapshot"]["total_tokens"], 240)
        self.assertTrue(result["snapshot"]["accounting_complete"])
        self.assertEqual(len(result["requests"]), 2)
        first, second = (item["payload"] for item in result["requests"])
        for payload in (first, second):
            names = {tool["name"] for tool in payload["tools"]}
            self.assertTrue({"materials_list", "materials_read", "workspace_read", "workspace_write", "compute"} <= names)
            self.assertFalse({"check", "submit"} & names)
            self.assertEqual(payload["model"], "gpt-6-astra")
            self.assertEqual(payload["reasoning"]["effort"], "max")
            self.assertEqual(payload["max_output_tokens"], 2048)
        self.assertNotIn("2 + 3 = 5", json.dumps(first))
        outputs = [item for item in second["input"] if item.get("type") == "function_call_output"]
        self.assertEqual(len(outputs), 1)
        value = json.loads(outputs[0]["output"])
        self.assertEqual(value["result"]["stdout"], "5\n")
        self.assertFalse(result["guard"]["filesystem_hard_quota"])
        self.assertIsNone(result["guard"]["violation"], result["guard"])
        rows = result["snapshot"]["request_states"]
        self.assertEqual(rows[1]["parent"], rows[0]["id"])

    def test_m_same_entry_actual_workflow_compute_and_exact_native_evidence(self):
        result = self.fixture("M", canary=True)
        self.assert_roundtrip(result)
        evidence = result["evidence"]
        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["run"]["actions"][0]["status"], "committed")
        self.assertEqual(evidence["run"]["actions"][0]["proposal"], self.proposal)
        self.assertIn("5", json.dumps(evidence["research"]))
        self.assertFalse(evidence["mechanisms"]["resume_dispatch"])

    def test_x_same_entry_original_host_compute_then_normal_two_request_stop(self):
        result = self.fixture("X", canary=True)
        self.assert_roundtrip(result)
        evidence = result["evidence"]
        self.assertTrue(evidence["normal_request_budget_stop"], evidence)
        self.assertEqual(len(evidence["report"]["notes"]), 1)
        self.assertIn("compute returned 5", evidence["report"]["notes"][0]["text"])
        self.assertIsNone(evidence["accepted_argument"])
        self.assertFalse(evidence["mechanisms"]["resume_dispatch"])
        self.assertEqual(evidence["physical_requests"][1]["parent_reason"], "native_tool_result_continuation")

    def test_x_common_error_receipt_reaches_native_continuation_unchanged(self):
        def missing_workspace(responses):
            responses[0]["output"][0].update(name="workspace_read", arguments=json.dumps({"path": "missing.txt"}))
            responses[1]["output"][0]["arguments"] = json.dumps({"notes": [{
                "text": "The common workspace read reported that missing.txt was not found.",
                "support": [], "summary": "The requested workspace file is missing.", "detailedSummary": None}]})
        result = self.fixture("X", mutate=missing_workspace)
        self.assertEqual(result["native"]["returncode"], 0, {"native": result["native"], "guard": result["guard"]})
        self.assertEqual(result["snapshot"]["requests"], 2)
        self.assertEqual(result["snapshot"]["total_tokens"], 240)
        self.assertTrue(result["snapshot"]["accounting_complete"])
        outputs = [item for item in result["requests"][1]["payload"]["input"] if item.get("type") == "function_call_output"]
        self.assertEqual(len(outputs), 1)
        self.assertTrue(outputs[0]["output"].startswith("{"), outputs[0]["output"])
        receipt = json.loads(outputs[0]["output"])
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["error"]["code"], "unsafe_path")
        self.assertEqual(receipt["error"]["message"], "file is missing, linked, or inaccessible")
        rows = result["snapshot"]["request_states"]
        self.assertEqual(rows[1]["parent"], rows[0]["id"])
        self.assertEqual(receipt["metrics"]["model_request_id"], rows[0]["id"])
        finished = [event["tool"] for event in result["events"]
                    if event["type"] == "tool_finished" and event["tool"].get("tool") == "workspace.read"]
        self.assertEqual(len(finished), 1)
        self.assertFalse(finished[0]["ok"])
        self.assertEqual(receipt["metrics"]["request_id"], finished[0]["request_id"])
        self.assertEqual(receipt["metrics"]["result_sha256"], finished[0]["result_sha256"])
        body = json.dumps({key: value for key, value in receipt.items() if key != "metrics"}, sort_keys=True, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), finished[0]["result_sha256"])
        self.assertEqual(result["evidence"]["physical_requests"][1]["parent_reason"], "native_tool_result_continuation")
        self.assertIn("missing.txt", result["evidence"]["report"]["notes"][0]["text"])
        self.assertTrue(result["evidence"]["normal_request_budget_stop"])

    def test_m_missing_usage_remains_unknown_and_has_no_replay_or_commit(self):
        result = self.fixture("M", mutate=lambda responses: responses[1].update(usage=None))
        self.assertEqual(result["snapshot"]["requests"], 2, {"native": result["native"], "guard": result["guard"]})
        self.assertIsNone(result["snapshot"]["total_tokens"])
        self.assertEqual(result["snapshot"]["known_total_tokens"], 120)
        self.assertFalse(result["evidence"]["ok"], result["native"])
        self.assertFalse(any(action["status"] == "committed" for action in result["evidence"]["run"]["actions"]))
        self.assertEqual(len(result["requests"]), 2)

    def test_x_missing_usage_keeps_the_native_submission_uncommitted(self):
        result = self.fixture("X", mutate=lambda responses: responses[1].update(usage=None))
        self.assertEqual(result["snapshot"]["requests"], 2, {"native": result["native"], "guard": result["guard"]})
        self.assertIsNone(result["snapshot"]["total_tokens"])
        self.assertEqual(result["snapshot"]["known_total_tokens"], 120)
        self.assertIsNotNone(result["evidence"], result["native"])
        self.assertEqual(result["native"]["returncode"], 1)
        self.assertFalse(result["evidence"]["normal_request_budget_stop"])
        self.assertTrue(result["evidence"]["report"]["status"]["error"])
        self.assertEqual(result["evidence"]["report"]["notes"], [])
        self.assertIsNone(result["evidence"]["accepted_argument"])
        self.assertEqual(len(result["requests"]), 2)

    def test_x_failed_provider_receipt_is_counted_without_a_native_submission(self):
        result = self.fixture("X", mutate=lambda responses: responses[1].update(status="failed", error={"code": "fixture_failure"}))
        self.assertEqual(result["snapshot"]["requests"], 2, {"native": result["native"], "guard": result["guard"]})
        self.assertEqual(result["snapshot"]["known_total_tokens"], 240)
        self.assertIsNotNone(result["evidence"], result["native"])
        self.assertEqual(result["native"]["returncode"], 1)
        self.assertFalse(result["evidence"]["normal_request_budget_stop"])
        self.assertTrue(result["evidence"]["report"]["status"]["error"])
        self.assertEqual(result["evidence"]["report"]["notes"], [])
        self.assertIsNone(result["evidence"]["accepted_argument"])
        self.assertEqual(len(result["requests"]), 2)

    def test_native_state_quota_stops_both_entries_before_the_first_model_dispatch(self):
        for arm in ("X", "M"):
            with self.subTest(arm=arm):
                result = self.fixture(arm, oversized_state=True)
                self.assertEqual(result["guard"]["violation"], "ARTIFACT_BUDGET")
                self.assertGreater(result["guard"]["last_bytes"], SMOKE_BUDGET["artifact_bytes"])
                self.assertEqual(result["snapshot"]["requests"], 0)
                self.assertEqual(result["requests"], [])
                self.assertNotEqual(result["native"]["returncode"], 0)

    def test_cold_reuse_is_rejected_before_contacting_a_gateway(self):
        with tempfile.TemporaryDirectory(prefix="four-no-resume-") as directory:
            parent = Path(directory).resolve()
            root = parent / "native"
            root.mkdir()
            (root / "old.sqlite").write_text("old state")
            with self.assertRaises(NativeBlocked) as raised:
                prepare_m_smoke(root, node=DEFAULT_NODE, python=sys.executable, gateway_url="http://127.0.0.1:9",
                                token="offline-only", execution=execution("M"), offline=True)
            self.assertEqual(raised.exception.code, "COLD_STATE")
            self.assertEqual((root / "old.sqlite").read_text(), "old state")


if __name__ == "__main__":
    unittest.main()
