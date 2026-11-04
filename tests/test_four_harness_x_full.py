"""Synthetic full-host X contract tests; native runs require explicit offline opt-in."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation/four-harness-v1"))
from native import NativeBlocked, PINS, copied_fixture
from x_full import CORE, INTRINSIC, STAGES, expected_stages, fixture_responses, prepare_host_fixture, xean_full_fixture


class XHostBoundaryTests(unittest.TestCase):
    def test_source_drift_is_rejected_before_creating_copy(self):
        with tempfile.TemporaryDirectory(prefix="four-x-manifest-") as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            (source / "core.ts").write_text("changed")
            (source / "four-harness-build.json").write_text(json.dumps({
                "arm": "X", "source": {"commit": PINS["X"]["commit"]},
                "files": {"core.ts": "0" * 64}}))
            with self.assertRaises(NativeBlocked) as raised:
                prepare_host_fixture(source, root / "copy")
            self.assertEqual(raised.exception.code, "BUILD_DRIFT")
            self.assertFalse((root / "copy").exists())
            self.assertEqual((source / "core.ts").read_text(), "changed")

    def test_synthetic_faults_preserve_raw_usage_distinctions(self):
        missing = fixture_responses("missing_usage")
        failed = fixture_responses("failed_response")
        self.assertIsNone(missing[3]["usage"])
        self.assertEqual(failed[3]["status"], "failed")
        self.assertEqual(failed[3]["usage"]["total_tokens"], 120)
        self.assertEqual(fixture_responses()[3]["status"], "completed")
        with self.assertRaises(NativeBlocked):
            fixture_responses("live")


@unittest.skipUnless(os.environ.get("FOUR_HARNESS_NATIVE_FIXTURES") == "1",
                     "Set FOUR_HARNESS_NATIVE_FIXTURES=1 for installed offline native host fixtures")
class XNativeHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ.get("FOUR_HARNESS_XEAN_BUILD", ROOT / "dist/four-harness-builds/xean-native-10"))
        cls.bun = Path(os.environ.get("FOUR_HARNESS_BUN", ROOT / "dist/four-harness-builds/runtime/node_modules/@oven/bun-darwin-aarch64/bin/bun"))
        if not cls.source.is_dir() or not cls.bun.is_file():
            raise RuntimeError("Explicit native qualification requires the installed pinned X build and Bun")

    def fixture(self, scenario="accepted", cap=2048):
        temporary = tempfile.TemporaryDirectory(prefix="four-x-host-test-")
        root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        return xean_full_fixture(root / "run", source_copy=self.source, bun=self.bun,
                                 scenario=scenario, max_output_tokens=cap)

    def smoke_fixture(self, manifest_change=None):
        from cold_start import write_materials
        from gateway import FixtureTransport, Gateway, capability, responses_tools
        from ledger import Ledger, SMOKE_BUDGET
        from tools import CommonTools, ToolLimits
        temporary = tempfile.TemporaryDirectory(prefix="four-x-smoke-tools-")
        root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        ledger = Ledger(root / "ledger.sqlite")
        ledger.create_campaign("smoke", max_requests=2, max_tokens=SMOKE_BUDGET["total_tokens"], scope="smoke")
        ledger.create_run("X-smoke", "smoke", "X", budget=SMOKE_BUDGET)
        common = CommonTools("X-smoke", root / "tools", write_materials(root / "materials", "smoke"),
                             limits=ToolLimits(timeout_seconds=10, artifact_bytes=16 * 1024 * 1024))
        tokens = {role: capability() for role in ("explorer", "coordinator", "verifier")}
        transport = FixtureTransport(fixture_responses()[:2])
        manifests = []

        def declared(scope=None):
            value = responses_tools(scope)
            if manifest_change:
                value = manifest_change(value, len(manifests))
            manifests.append(value)
            return value

        with Gateway(ledger, transport=transport,
                     bindings={token: {"run_id": "X-smoke", "arm": "X", "role": role}
                               for role, token in tokens.items()}, tools={"X-smoke": common},
                     allowed_tools={"X-smoke": {tool["name"] for tool in responses_tools()} | INTRINSIC},
                     intrinsic_tools={("X-smoke", role): INTRINSIC for role in tokens}, inject_output_cap=True) as gateway:
            # Faults live at the HTTP manifest boundary; the native adapter and
            # actual Pi role run unchanged through the real gateway and OS jail.
            with patch("gateway.responses_tools", side_effect=declared):
                native = copied_fixture(root / "native", gateway=gateway, arm="X", source_copy=self.source,
                                        executable=self.bun, entry=["four-harness-fixture.ts"],
                                        role_tokens=tokens, timeout=40)
            snapshot = ledger.snapshot("X-smoke")
        return {"native": native, "snapshot": snapshot, "requests": transport.requests,
                "events": ledger.events(), "manifests": manifests}

    def test_smoke_registers_only_five_manifest_tools_before_native_requests(self):
        result = self.smoke_fixture()
        self.assertEqual(result["native"]["returncode"], 0, result["native"])
        self.assertEqual(result["snapshot"]["requests"], 2)
        self.assertEqual(result["snapshot"]["total_tokens"], 240)
        self.assertEqual(len(result["manifests"]), 6)  # launcher preflight + native module, all three roles
        expected = {"materials_list", "materials_read", "workspace_read", "workspace_write", "compute"}
        for request in result["requests"]:
            names = {tool["name"] for tool in request["payload"]["tools"]}
            self.assertTrue(expected <= names)
            self.assertFalse({"check", "submit"} & names)
            self.assertEqual(names - expected, {"read_notes", "submit_notes"})
        output = json.loads(result["native"]["stdout"])
        self.assertEqual(output["outcome"]["status"], "completed")
        self.assertEqual(output["outcome"]["result"]["value"]["notes"][1]["support"], ["n1"])

    def test_manifest_schema_or_role_disagreement_stops_before_dispatch(self):
        def changed_schema(value, index):
            if index >= 3:
                value[0]["description"] += " synthetic drift"
            return value

        def changed_role(value, index):
            return value[:-1] if index == 5 else value

        def unknown_tool(value, index):
            if index >= 3:
                value[0]["name"] = "synthetic_unknown_tool"
            return value

        def duplicate_tool(value, index):
            return value + value[:1] if index >= 3 else value

        for change in (changed_schema, changed_role, unknown_tool, duplicate_tool):
            with self.subTest(change=change.__name__):
                result = self.smoke_fixture(change)
                self.assertNotEqual(result["native"]["returncode"], 0, result["native"])
                self.assertIn("Native X manifest", result["native"]["stderr"])
                self.assertEqual(result["snapshot"]["requests"], 0)
                self.assertEqual(result["requests"], [])
                self.assertFalse(any(event["type"].startswith("tool_") for event in result["events"]))

    def test_actual_host_verifies_all_stages_and_generated_closure(self):
        for cap in (2048, 16384):
            with self.subTest(max_output_tokens=cap):
                result = self.fixture(cap=cap)
                self.assertEqual(result["native"]["returncode"], 0, result["native"])
                self.assertTrue(result["native_host_path_qualified"], result["qualification_blockers"])
                self.assertTrue(result["parent_linkage_qualified"])
                self.assertEqual(result["observed_stages"], list(STAGES))
                self.assertFalse(result["live_ready"])
                self.assertFalse(result["native_qualified"])
                self.assertIn("ADOPTED_X_PATHS_REQUIRE_COMBINED_OFFLINE_EVIDENCE", result["qualification_blockers"])
                self.assertIn("compaction", result["disabled_paths"])
                self.assertIn("resume_dispatch", result["disabled_paths"])
                snapshot = result["snapshot"]
                self.assertEqual(snapshot["requests"], 9)
                self.assertEqual(snapshot["total_tokens"], 1080)
                self.assertTrue(snapshot["accounting_complete"])
                self.assertTrue(snapshot["detail_complete"])
                states = snapshot["request_states"]
                physical = result["output"]["physical_requests"]
                self.assertEqual([row["role"] for row in states], ["explorer", "explorer", "coordinator"] + ["verifier"] * 6)
                self.assertEqual([row["id"] for row in states], [row["request_id"] for row in physical])
                self.assertIsNone(states[0]["parent"])
                for index, row in enumerate(states):
                    self.assertEqual(row["thread"], str(physical[index]["native"]["conversation"]["id"]))
                    self.assertEqual(row["output_cap"], cap)
                    self.assertEqual(json.loads(row["raw_usage"])["total_tokens"], 120)
                    if index:
                        self.assertEqual(row["parent"], states[index - 1]["id"])
                        self.assertIsNotNone(physical[index]["parent_reason"])
                        self.assertFalse(physical[index]["ancestry_gap"])
                report = result["output"]["report"]
                self.assertEqual(report["status"]["outcome"], "accepted")
                self.assertEqual(report["status"]["accepted"], "n2")
                self.assertEqual(report["notes"][1]["support"], ["n1"])
                self.assertEqual(set(report["reconstructed"]), {"n1", "n2"})
                self.assertEqual(len({check["candidate"] for check in report["checks"]}), 1)
                verdicts = [verdict for check in report["checks"] for verdict in check["values"]]
                self.assertEqual({value["verifier"] for value in verdicts}, {"source", "correctness", "requirements", "reconstruction"})
                self.assertEqual({value["note"] for value in verdicts if value["verifier"] == "reconstruction"}, {"n1", "n2"})
                self.assertEqual(sum(task["kind"] == "xean.campaign" for task in result["output"]["tasks"]), 1)
                self.assertEqual(sum(task["kind"] == "xean.worker" for task in result["output"]["tasks"]), 3)
                proof_payload = json.dumps(result["requests"][7]["payload"])
                self.assertIn("For integers", proof_payload)
                self.assertNotIn("Synthetic note A", proof_payload)
                self.assertNotIn("Synthetic note B", proof_payload)
                for request in result["requests"]:
                    self.assertEqual(request["payload"]["model"], "gpt-6-astra")
                    self.assertEqual(request["payload"]["reasoning"]["effort"], "max")
                    self.assertEqual(request["payload"]["max_output_tokens"], cap)
                    names = {tool["name"] for tool in request["payload"]["tools"]}
                    self.assertTrue({"materials_list", "materials_read", "workspace_read", "workspace_write",
                                     "compute", "check", "submit"} <= names)
                self.assertEqual(set(result["build"]["preserved_core"]), set(CORE))
                self.assertGreater(result["build"]["dependency_file_count"], 0)
                relation = result["build"]["native_source_relationship"]
                for name, value in relation.items():
                    self.assertEqual(value["copy_sha256"], result["build"]["preserved_core"][name])
                    if name in {"src/host.ts", "src/role-runtime.ts"}:
                        self.assertEqual(value["upstream_relation"], "inherited_adapter_patch")
                        self.assertNotEqual(value["upstream_sha256"], value["copy_sha256"])
                    else:
                        self.assertEqual(value["upstream_relation"], "byte_identical")
                        self.assertEqual(value["upstream_sha256"], value["copy_sha256"])
                tool_receipts = [event["tool"] for event in result["events"] if event["type"] == "tool_finished"]
                self.assertEqual(len(tool_receipts), 9)
                self.assertEqual([tool.get("name", tool.get("tool")) for tool in tool_receipts],
                    ["materials.list", "submit_notes", "submit_coordination", "submit_verdict", "submit_verdict",
                     "submit_verdict", "submit_statement", "submit_proof", "submit_verdict"])
                self.assertTrue(all(len(tool["result_sha256"]) == 64 and tool["cpu_seconds"] >= 0 and
                                    tool["wall_seconds"] >= 0 for tool in tool_receipts))
                self.assertIn("## n1", result["output"]["accepted_argument"])
                self.assertIn("## n2", result["output"]["accepted_argument"])

    def assert_adopted_path(self, result, scenario):
        self.assertEqual(result["native"]["returncode"], 0, result["native"])
        self.assertTrue(result["native_host_path_qualified"], result["qualification_blockers"])
        self.assertTrue(result["parent_linkage_qualified"])
        self.assertEqual(result["observed_stages"], list(expected_stages(scenario)))
        self.assertEqual(result["snapshot"]["requests"], len(expected_stages(scenario)))
        self.assertEqual(result["snapshot"]["total_tokens"], 120 * len(expected_stages(scenario)))
        physical = result["output"]["physical_requests"]
        request_ids = {request["request_id"] for request in physical}
        receipts = [event["tool"] for event in result["events"] if event["type"] == "tool_finished"]
        contexts = {event["tool_request_id"]: event["parent_request_id"] for event in result["events"]
                    if event["type"] == "native_tool_context"}
        self.assertTrue(all(tool.get("parent_request_id", contexts.get(tool["request_id"])) in request_ids
                            for tool in receipts), [(tool.get("name", tool.get("tool")), tool["request_id"]) for tool in receipts])
        self.assertEqual(result["output"]["report"]["status"]["accepted"], "n2")
        self.assertFalse(result["output"]["mechanisms"]["compaction"])
        self.assertFalse(result["output"]["mechanisms"]["parallel_dispatch"])
        self.assertFalse(result["output"]["mechanisms"]["resume_dispatch"])
        return physical

    def test_adopted_native_fork_has_durable_prefix_parent(self):
        result = self.fixture("fork")
        physical = self.assert_adopted_path(result, "fork")
        first, second = [request for request in physical if request["native"]["stage"] == "coordinator"]
        self.assertEqual(second["native"]["conversation"]["parent"]["conversationId"], first["native"]["conversation"]["id"])
        self.assertEqual(second["parent_reason"], "native_coordinator_prefix_fork")
        self.assertEqual(second["parent_request_id"], first["request_id"])
        self.assertNotEqual(second["parent_request_id"], second["sequence_previous_request"])
        self.assertEqual(sum(task["kind"] == "xean.worker" for task in result["output"]["tasks"]), 5)

    def test_adopted_native_length_continuation_is_separately_metered(self):
        result = self.fixture("length")
        physical = self.assert_adopted_path(result, "length")
        self.assertEqual(physical[0]["stop_reason"], "length")
        self.assertEqual(physical[1]["parent_reason"], "native_length_continuation")
        self.assertEqual(physical[0]["native"]["conversation"]["id"], physical[1]["native"]["conversation"]["id"])
        self.assertIn("Continue exactly where you left off", json.dumps(result["requests"][1]["payload"]))

    def test_adopted_native_reconstruction_correction_repeats_blind_proof(self):
        result = self.fixture("correction")
        physical = self.assert_adopted_path(result, "correction")
        proofs = [request for request in physical if request["native"]["stage"] == "proof"]
        self.assertEqual(len(proofs), 2)
        self.assertEqual(proofs[1]["parent_reason"], "native_statement_correction_to_blind_proof")
        self.assertIn("Integer successor arithmetic", json.dumps(proofs[1]["request"]))
        self.assertNotIn("Synthetic note A", json.dumps(proofs[1]["request"]))
        self.assertEqual(set(result["output"]["report"]["reconstructed"]), {"n1", "n2"})

    def test_missing_receipt_usage_stops_native_host_without_replay(self):
        result = self.fixture("missing_usage")
        self.assertEqual(result["native"]["returncode"], 1, result["native"])
        self.assertEqual(result["snapshot"]["requests"], 4)
        self.assertEqual(len(result["requests"]), 4)
        self.assertIsNone(result["snapshot"]["total_tokens"])
        self.assertEqual(result["snapshot"]["known_total_tokens"], 360)
        self.assertFalse(result["snapshot"]["accounting_complete"])
        self.assertEqual(result["snapshot"]["request_states"][-1]["state"], "unknown")
        self.assertEqual(result["output"]["report"]["status"]["outcome"], "call-failure")
        self.assertIsNone(result["output"]["accepted_argument"])
        self.assertEqual(result["output"]["report"]["checks"], [])

    def test_failed_provider_receipt_retains_cost_and_never_submits_verdict(self):
        result = self.fixture("failed_response")
        self.assertEqual(result["native"]["returncode"], 1, result["native"])
        self.assertEqual(len(result["requests"]), 4)
        self.assertEqual(result["snapshot"]["known_total_tokens"], 480)
        self.assertEqual(result["output"]["report"]["status"]["outcome"], "call-failure")
        self.assertEqual(result["output"]["report"]["checks"], [])
        self.assertFalse(any(value["tool_name"] == "submit_verdict" for value in result["output"]["submissions"]))

    def test_physical_request_limit_blocks_comparison_before_dispatch(self):
        result = self.fixture("request_limit")
        self.assertEqual(result["native"]["returncode"], 1, result["native"])
        self.assertEqual(result["snapshot"]["requests"], 8)
        self.assertEqual(len(result["requests"]), 8)
        self.assertEqual(result["snapshot"]["total_tokens"], 960)
        self.assertEqual(result["output"]["report"]["status"]["outcome"], "call-failure")
        self.assertEqual(result["output"]["report"]["reconstructed"], {})
        self.assertIsNone(result["output"]["physical_requests"][-1]["request_id"])
        self.assertEqual(result["output"]["physical_requests"][-1]["http_status"], 409)
        self.assertFalse(result["native_host_path_qualified"])


if __name__ == "__main__":
    unittest.main()
