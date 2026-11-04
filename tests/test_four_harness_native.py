"""Native adapters: no real model calls, no target-problem solving.

The opt-in CLI test can only construct immutable local fake Responses. Its OS
containment probe must pass before native startup. The default suite checks
rejection and configuration contracts without starting native CLIs.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "evaluation/four-harness-v1"
sys.path.insert(0, str(HERE))
import native
from gateway import FixtureTransport, Gateway, capability
from ledger import Ledger, FORMAL_BUDGET
from tools import CommonTools


def response(index, output):
    return {"id": f"resp_fixture_{index}", "object": "response", "created_at": 1,
            "status": "completed", "model": "gpt-6-astra", "output": output,
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}}}


class NativeContracts(unittest.TestCase):
    def test_endpoint_and_role_cannot_escape_gateway(self):
        for value in ("https://api.openai.com/v1", "http://localhost:8000/C/agent/v1",
                      "http://127.0.0.1/C/agent/v1", "http://u:p@127.0.0.1:8000/C/agent/v1",
                      "http://127.0.0.1:8000/C/agent/v1?redirect=x", "http://127.0.0.1:8000/R/generator/v1"):
            with self.subTest(value=value), self.assertRaises(native.NativeBlocked):
                native.localhost_url(value, arm="C", role="agent")
        self.assertEqual(native.localhost_url("http://127.0.0.1:8000/C/agent/v1", arm="C", role="agent"),
                         "http://127.0.0.1:8000/C/agent/v1")

    def test_native_config_declares_exact_model_and_zero_retries(self):
        config = tomllib.loads(native.codex_config("http://127.0.0.1:8000/C/agent/v1",
            mcp_command=(sys.executable, "-I", str(HERE / "mcp.py")), tools_url="http://127.0.0.1:8000/C/agent/tools"))
        self.assertEqual(config["model"], "gpt-6-astra")
        self.assertEqual(config["model_reasoning_effort"], "max")
        self.assertEqual(config["model_context_window"], 65536)
        self.assertFalse(config["features"]["shell_tool"])
        for feature in ("unbounded_connection_retries", "respect_system_proxy", "system_proxy_fallback"):
            self.assertFalse(config["features"][feature])
        self.assertEqual(config["web_search"], "disabled")
        self.assertEqual(config["cli_auth_credentials_store"], "ephemeral")
        provider = config["model_providers"]["four_harness"]
        self.assertEqual(provider["request_max_retries"], 0)
        self.assertEqual(provider["stream_max_retries"], 0)
        self.assertFalse(provider["requires_openai_auth"])
        self.assertEqual(set(config["mcp_servers"]["common"]["enabled_tools"]), set(native.TOOLS))

    def test_environment_does_not_inherit_daily_credentials_or_proxies(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "OPENAI_API_KEY": "daily-secret", "CODEX_HOME": "/daily", "HOME": "/daily",
            "HTTP_PROXY": "http://external:8080", "NODE_OPTIONS": "--require=evil", "PYTHONPATH": "/evil"}):
            env = native.isolated_environment(directory, token="fixture-token")
            self.assertEqual(set(env) & {"OPENAI_API_KEY", "HTTP_PROXY", "NODE_OPTIONS", "PYTHONPATH"}, set())
            self.assertEqual(env["NO_PROXY"], "127.0.0.1,localhost,::1")
            self.assertEqual(env["no_proxy"], env["NO_PROXY"])
            self.assertTrue(all(env[key].startswith(str(Path(directory).resolve())) for key in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME")))
            with self.assertRaises(FileExistsError):
                native.isolated_environment(directory, token="another-token")

    def test_refuses_arbitrary_localhost_transport_before_creating_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "never-started"
            with self.assertRaises(native.NativeBlocked) as caught:
                native.codex_fixture(root, gateway=object(), token="fixture", mcp_command=())
            self.assertEqual(caught.exception.code, "FAKE_ONLY")
            self.assertFalse(root.exists())

    def test_dependency_snapshot_rejects_symlink_escape_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "isolated-site"
            site.mkdir()
            outside = root / "outside.py"
            outside.write_text("must not enter the dependency snapshot")
            (site / "escape.py").symlink_to(outside)
            with self.assertRaises(native.NativeBlocked) as caught:
                native.rethlas_mcp_dependencies(site)
            self.assertEqual(caught.exception.code, "DEPENDENCIES")
            self.assertIn("symlink escapes", str(caught.exception))

    def test_r_recursive_providers_are_explicit_and_never_retry(self):
        tokens = {role: "fixture-" + role for role in native.ROLES["R"]}
        config = tomllib.loads(native.rethlas_config("http://127.0.0.1:8000", tokens))
        self.assertTrue(config["features"]["multi_agent"])
        self.assertFalse(config["features"]["shell_tool"])
        for role in tokens:
            provider = config["model_providers"]["four_harness_" + role]
            self.assertEqual(provider["base_url"], "http://127.0.0.1:8000/R/" + role + "/v1")
            self.assertEqual(provider["env_key"], "FOUR_HARNESS_" + role.upper() + "_TOKEN")
            self.assertEqual(provider["request_max_retries"], 0)
            self.assertEqual(provider["stream_max_retries"], 0)
        self.assertEqual(config["model_providers"]["four_harness_verifier"]["env_http_headers"],
                         {"X-Four-Parent-Request-Id": "FOUR_HARNESS_PARENT_REQUEST_ID"})
        with self.assertRaises(native.NativeBlocked):
            native.rethlas_config("http://127.0.0.1:8000", {"generator": "fixture"})

    def test_cleanup_preserves_reused_pid(self):
        owned = native.OwnedProcessTree(os.getpid())
        owned.known = {999999: (1, 2)}
        with patch.object(owned, "sample", return_value=[]), patch.object(native, "process_identity", return_value={
                "pid": 999999, "ppid": 1, "pgid": 999999, "birth": (3, 4)}), patch.object(native.os, "kill") as kill:
            result = owned.cleanup()
        kill.assert_not_called()
        self.assertEqual(result["remaining_known_pids"], [])

    @unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "process birth identity reader")
    def test_cleanup_catches_detached_mcp_child_on_exit_and_timeout(self):
        child = "import time; time.sleep(60)"
        for timeout_case in (False, True):
            with self.subTest(timeout=timeout_case), tempfile.TemporaryDirectory() as directory:
                code = ("import subprocess,sys,time; "
                        f"child=subprocess.Popen([sys.executable,'-I','-c',{child!r}],start_new_session=True); "
                        "print(child.pid,flush=True); " + f"time.sleep({60 if timeout_case else 0.8})")
                result = native.run_process([sys.executable, "-I", "-c", code],
                    env={"PATH": "/usr/bin:/bin"}, cwd=directory, timeout=1.5 if timeout_case else 5)
                child_pid = int(result["stdout"].strip())
                self.assertIsNone(native.process_identity(child_pid))
                self.assertEqual(result["timed_out"], timeout_case)
                self.assertTrue(result["cleanup_complete_for_known_processes"])
                self.assertGreaterEqual(result["cleanup"]["known_processes"], 2)
                self.assertFalse(result["cleanup"]["whole_process_tree_complete"])

    def test_cancellation_cleans_detached_child_without_reporting_timeout(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            cancel = threading.Event()
            timer = threading.Timer(0.8, cancel.set)
            timer.start()
            try:
                code = ("import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-I','-c',"
                        "'import time;time.sleep(60)'],start_new_session=True); "
                        "print(child.pid,flush=True);time.sleep(60)")
                result = native.run_process([sys.executable, "-I", "-c", code],
                    env={"PATH": "/usr/bin:/bin"}, cwd=directory, timeout=20, cancel_event=cancel)
            finally:
                timer.cancel()
                timer.join()
            self.assertTrue(result["cancelled"])
            self.assertFalse(result["timed_out"])
            self.assertIsNone(native.process_identity(int(result["stdout"].strip())))
            self.assertTrue(result["cleanup_complete_for_known_processes"])

    def test_x_all_six_native_profiles_have_exact_effort_and_role_route(self):
        settings = native.xean_settings()
        for role in ("coordinator", "explorer", "correctness", "source", "requirements", "reconstruction"):
            self.assertEqual(settings[role]["reasoning"], "max")
            self.assertEqual(settings[role]["model"], "gpt-6-astra")
            self.assertNotEqual(settings[role]["provider"], "codex")


@unittest.skipUnless(os.environ.get("FOUR_HARNESS_NATIVE_FIXTURES") == "1", "explicit offline native fixture opt-in")
class InstalledCodexFixture(unittest.TestCase):
    def test_actual_direct_cli_common_and_intrinsic_continuation(self):
        from codex_bridge import NativeDirectBridge
        from gateway import responses_tools
        with tempfile.TemporaryDirectory(prefix="four-harness-direct-test-") as directory:
            root = Path(directory).resolve()
            ledger = Ledger(root / "host" / "ledger.sqlite")
            ledger.create_campaign("offline-direct", max_requests=2, max_tokens=120000, scope="offline")
            ledger.create_run("C-direct", "offline-direct", "C", budget={**FORMAL_BUDGET,
                "model_requests": 2, "total_tokens": 120000})
            common = CommonTools(run_id="C-direct", run_root=root / "tools", materials={},
                accounting=lambda event: ledger.tool_event("C-direct", event))
            token = capability()
            bridge = NativeDirectBridge("C", "agent", responses_tools(), tool_timeout_seconds=60)
            transport = FixtureTransport([
                response(1, [
                    {"type": "function_call", "id": "fc_materials", "call_id": "call_materials",
                     "namespace": "mcp__common", "name": "materials_list", "arguments": "{}", "status": "completed"},
                    {"type": "function_call", "id": "fc_plan", "call_id": "call_plan", "namespace": "functions",
                     "name": "update_plan", "arguments": json.dumps({"plan": [{"step": "Read frozen fixture materials", "status": "completed"}]}), "status": "completed"}]),
                response(2, [{"type": "message", "id": "msg_done", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "fixture complete", "annotations": []}]}])])
            with Gateway(ledger, transport=transport,
                bindings={token: {"run_id": "C-direct", "arm": "C", "role": "agent", "require_native_metadata": True}},
                tools={"C-direct": common}, allowed_tools={"C-direct": set(native.TOOLS)},
                request_adapters={("C-direct", "agent"): bridge}, inject_output_cap=True) as gateway:
                result = native.codex_fixture(root / "native", gateway=gateway, token=token,
                    mcp_command=(str(Path(sys.executable).resolve()), "-I", str(HERE / "mcp.py")), timeout=35,
                    direct_source=ROOT / "dist/four-harness-sources/codex-0.166.0")
                snapshot = ledger.snapshot("C-direct")
            self.assertEqual(result["returncode"], 0, json.dumps({"native": result, "events": ledger.events()}, indent=2))
            self.assertEqual(snapshot["requests"], 2)
            self.assertTrue(snapshot["accounting_complete"])
            self.assertEqual(len(transport.requests), 2)
            self.assertTrue(all(request["payload"]["reasoning"]["effort"] == "max" for request in transport.requests))
            starts = [event for event in ledger.events() if event["type"] == "tool_started"]
            finishes = [event for event in ledger.events() if event["type"] == "tool_finished"]
            self.assertEqual(len(starts), 2, json.dumps({"native": result, "events": ledger.events()}, indent=2))
            self.assertEqual(len(finishes), 2)
            self.assertEqual(set(bridge.outputs), {"call_materials", "call_plan"})
            self.assertTrue(result["cleanup_complete_for_known_processes"])
            self.assertFalse(result["live_ready"])

    def test_actual_cli_starts_then_rejects_unqualified_code_mode_tools(self):
        with tempfile.TemporaryDirectory(prefix="four-harness-native-test-") as directory:
            root = Path(directory).resolve()
            ledger = Ledger(root / "host" / "ledger.sqlite")
            ledger.create_campaign("offline-native", max_requests=2, max_tokens=120000, scope="offline")
            ledger.create_run("C-fixture", "offline-native", "C",
                              budget={**FORMAL_BUDGET, "model_requests": 2, "total_tokens": 120000})
            common = CommonTools(run_id="C-fixture", run_root=root / "tools", materials={}, accounting=lambda event: ledger.tool_event("C-fixture", event))
            token = capability()
            transport = FixtureTransport([
                response(1, [{"type": "function_call", "id": "fc_fixture_1", "call_id": "call_fixture_1",
                              "name": "mcp__common__materials_list", "arguments": "{}", "status": "completed"}]),
                response(2, [{"type": "message", "id": "msg_fixture_2", "role": "assistant", "status": "completed",
                              "content": [{"type": "output_text", "text": "fixture complete", "annotations": []}]}])])
            with Gateway(ledger, transport=transport, bindings={token: {"run_id": "C-fixture", "arm": "C", "role": "agent", "require_native_metadata": True}},
                         tools={"C-fixture": common}, allowed_tools={"C-fixture": {"mcp__common__" + name for name in native.TOOLS}},
                         inject_output_cap=True) as gateway:
                result = native.codex_fixture(root / "native", gateway=gateway, token=token,
                    mcp_command=(str(Path(sys.executable).resolve()), "-I", str(HERE / "mcp.py")), timeout=35)
            # This is a native startup regression and a fail-closed compatibility
            # result, not a successful common-MCP turn qualification. The pinned
            # Astra catalog forces an unmetered additional_tools code-mode surface.
            self.assertEqual(result["blocker"], "TOOL_NOT_ALLOWED", json.dumps(result, indent=2))
            self.assertTrue(result["native_startup_succeeded"])
            self.assertTrue(result["isolation_probe_passed"])
            self.assertEqual(result["managed_preferences_probe"], {
                "forced": {"config_toml_base64": False, "requirements_toml_base64": False},
                "synchronized": True, "preference_values_read": False})
            self.assertTrue(result["cleanup_complete_for_known_processes"])
            self.assertFalse(result["native_qualified"])
            self.assertFalse(result["live_ready"])
            self.assertEqual(result["returncode"], 1)
            self.assertEqual(len(result["gateway_rejections"]), 1)
            self.assertNotIn("Connection failed", result["stdout"])
            self.assertNotIn("Failed to synchronize managed preferences", result["stderr"])
            self.assertEqual(transport.requests, [], json.dumps(ledger.events(), indent=2))
            self.assertEqual(ledger.snapshot("C-fixture")["requests"], 0)
            self.assertTrue(ledger.snapshot("C-fixture")["accounting_complete"])
            self.assertFalse(any(event["type"] == "tool_started" for event in ledger.events()))
            observations = result["resource_observations"]
            self.assertTrue(observations["observer_stopped"])
            self.assertFalse(observations["whole_process_tree_complete"])
            self.assertFalse(observations["equal_cpu_or_memory_budget"])


@unittest.skipUnless(os.environ.get("FOUR_HARNESS_NATIVE_FIXTURES") == "1", "explicit offline native fixture opt-in")
class CopiedXeanFixture(unittest.TestCase):
    def test_actual_pi_role_common_tool_and_native_submission(self):
        build = Path(os.environ.get("FOUR_HARNESS_XEAN_BUILD", ROOT / "dist/four-harness-builds/xean-native-5"))
        bun = ROOT / "dist/four-harness-builds/runtime/node_modules/@oven/bun-darwin-aarch64/bin/bun"
        self.assertTrue((build / "node_modules").is_dir(), "Prepare isolated Xean build and frozen dependencies first")
        for cap in (2048, 16384):
            with self.subTest(output_cap=cap), tempfile.TemporaryDirectory(prefix="four-harness-x-test-") as directory:
                report = native.xean_fixture(Path(directory) / "evidence", source_copy=build, bun=bun, max_output_tokens=cap)
                result, requests, events = report["native"], report["requests"], report["events"]
                self.assertEqual(result["returncode"], 0, json.dumps(report, indent=2))
                self.assertEqual(len(requests), 2)
                self.assertEqual(report["snapshot"]["requests"], 2)
                self.assertEqual(report["snapshot"]["known_total_tokens"], 240)
                self.assertTrue(report["snapshot"]["accounting_complete"])
                self.assertIn("function_call_output", json.dumps(requests[1]))
                self.assertIn("Fixture arithmetic", result["stdout"])
                self.assertEqual(sum(event["type"] == "tool_started" for event in events), 2)
                self.assertEqual(sum(event["type"] == "tool_finished" for event in events), 2)
                self.assertFalse(report["native_qualified"])
                self.assertIn("FULL_X_HOST_COORDINATOR_VERIFIER_CHAIN_NOT_EXERCISED", report["qualification_blockers"])
                for captured in requests:
                    self.assertEqual(captured["payload"]["reasoning"]["effort"], "max")
                    self.assertEqual(captured["payload"]["max_output_tokens"], cap)


@unittest.skipUnless(os.environ.get("FOUR_HARNESS_NATIVE_FIXTURES") == "1", "explicit offline native fixture opt-in")
class CopiedRethlasFixture(unittest.TestCase):
    def test_actual_codex_generation_and_verifier_startup_reject_unqualified_tools(self):
        source = Path(os.environ.get("FOUR_HARNESS_RETHLAS_SOURCE", "/Users/zcy/Codes/Math Agent/Rethlas-upstream"))
        python_site = ROOT / "dist/four-harness-builds/rethlas-mcp-python"
        with tempfile.TemporaryDirectory(prefix="four-harness-r-codex-test-") as directory:
            report = native.rethlas_codex_fixture(Path(directory) / "evidence", source_root=source,
                python_executable=sys.executable, python_site=python_site)
            self.assertEqual(report["native"]["returncode"], 0, json.dumps(report["native"], indent=2))
            self.assertEqual(report["details"]["runtime_version"], "codex-cli 0.166.0")
            self.assertEqual(report["details"]["generation"]["returncode"], 1, json.dumps(report["details"], indent=2))
            self.assertEqual(report["details"]["verification"]["http_status"], 500)
            self.assertEqual([(item["role"], item["code"]) for item in report["gateway_rejections"]],
                             [("generator", "TOOL_NOT_ALLOWED"), ("verifier", "TOOL_NOT_ALLOWED")],
                             json.dumps(report["details"], indent=2))
            self.assertEqual(report["requests"], [])
            self.assertEqual(report["snapshot"]["requests"], 0)
            self.assertTrue(report["snapshot"]["accounting_complete"])
            self.assertFalse(report["native_qualified"])
            self.assertFalse(report["live_ready"])
            self.assertFalse(report["details"]["continuation_after_rejected_generator"])
            for role in ("generation", "verification"):
                logs = "\n".join(report["details"][role]["logs"])
                self.assertIn("Native additional_tools/code-mode namespaces are not qualified", logs)
                self.assertNotIn("Connection failed", logs)
                self.assertNotIn("Failed to synchronize managed preferences", logs)
            self.assertFalse(any(event["type"] == "tool_started" for event in report["events"]))

    def test_actual_generation_resume_and_verifier_subprocess_paths(self):
        source = Path(os.environ.get("FOUR_HARNESS_RETHLAS_SOURCE", "/Users/zcy/Codes/Math Agent/Rethlas-upstream"))
        python_site = ROOT / "dist/four-harness-builds/rethlas-python"
        self.assertTrue((python_site / "fastapi").is_dir(), "Prepare pinned isolated R Python dependencies first")
        with tempfile.TemporaryDirectory(prefix="four-harness-r-test-") as directory:
            root = Path(directory).resolve() / "evidence"
            evidence = native.rethlas_fixture(root, source_root=source, python_executable=sys.executable, python_site=python_site)
            result = evidence["native"]
            self.assertEqual(result["returncode"], 0, json.dumps(evidence, indent=2))
            build = root / "source-copy"
            receipt = json.loads((build / "four-harness-build.json").read_text())
            self.assertEqual(receipt["source"]["commit"], native.PINS["R"]["commit"])
            child = tomllib.loads((build / "agents/generation/.codex/agents/subgoal-prover.toml").read_text())
            self.assertEqual(child["model"], "gpt-6-astra")
            self.assertEqual(child["model_reasoning_effort"], "max")
            self.assertEqual(child["model_provider"], "four_harness_strategy")
            self.assertEqual((build / "agents/generation/AGENTS.md").read_bytes(), (source / "agents/generation/AGENTS.md").read_bytes())
            report = json.loads(result["stdout"])
            self.assertEqual(report["kind"], "actual_rethlas_entry_paths_fake_child")
            self.assertFalse(report["native_codex_qualified"])
            self.assertFalse(report["native_strategy_qualified"])
            self.assertFalse(report["live_ready"])
            self.assertEqual([call["role"] for call in report["calls"]], ["generator", "generator", "verifier"])
            self.assertEqual(report["calls"][1]["argv"][:3], ["exec", "resume", "four-harness-fixture-session"])
            self.assertTrue(report["verification_fixture"]["fixture_only"])
            self.assertEqual(evidence["snapshot"]["requests"], 3)
            self.assertEqual(evidence["snapshot"]["known_total_tokens"], 360)
            self.assertTrue(evidence["snapshot"]["accounting_complete"])
            self.assertFalse(evidence["native_qualified"])
            reserved = [event for event in evidence["events"] if event["type"] == "request_reserved"]
            self.assertIsNone(reserved[0]["parent_request_id"])
            self.assertEqual(reserved[1]["parent_request_id"], reserved[0]["request_id"])
            self.assertEqual(reserved[2]["parent_request_id"], reserved[1]["request_id"])
            self.assertEqual(native.source_identity(source, "R")["dirty"], False)

    def test_original_mcp_tools_and_verifier_preserve_native_lineage(self):
        source = Path(os.environ.get("FOUR_HARNESS_RETHLAS_SOURCE", "/Users/zcy/Codes/Math Agent/Rethlas-upstream"))
        python_site = ROOT / "dist/four-harness-builds/rethlas-mcp-python"
        self.assertTrue((python_site / "fastmcp").is_dir(), "Prepare pinned isolated R MCP dependencies first")
        with tempfile.TemporaryDirectory(prefix="four-harness-r-mcp-test-") as directory:
            root = Path(directory) / "evidence"
            evidence = native.rethlas_mcp_fixture(root, source_root=source, python_executable=sys.executable, python_site=python_site)
            self.assertEqual(evidence["native"]["returncode"], 0,
                             json.dumps({"native": evidence["native"], "snapshot": evidence["snapshot"]}, indent=2))
            report = json.loads(evidence["native"]["stdout"])
            self.assertFalse(report["native_recursive_spawn_qualified"])
            self.assertEqual(report["tool_timeout_seconds"], 60)
            self.assertFalse(evidence["native_qualified"])
            self.assertEqual(evidence["snapshot"]["requests"], 3)
            self.assertEqual(evidence["snapshot"]["known_total_tokens"], 360)
            self.assertTrue(evidence["snapshot"]["accounting_complete"])
            self.assertEqual(len(evidence["dependencies"]["versions"]), 69)
            self.assertGreater(evidence["dependencies"]["file_count"], 100)
            self.assertRegex(evidence["dependencies"]["file_tree_sha256"], r"^[0-9a-f]{64}$")
            for relative in ("agents/generation/mcp/server.py", "agents/verification/mcp/server.py",
                             "agents/verification/schemas/verification_output.schema.json"):
                self.assertEqual((root / "source-copy" / relative).read_bytes(), (source / relative).read_bytes())
            events = evidence["events"]
            reserved = [event for event in events if event["type"] == "request_reserved"]
            self.assertEqual([event["role"] for event in reserved], ["generator", "strategy", "verifier"])
            self.assertIsNone(reserved[0]["parent_request_id"])
            self.assertEqual(reserved[1]["parent_request_id"], reserved[0]["request_id"])
            self.assertEqual(reserved[2]["parent_request_id"], reserved[0]["request_id"])
            started = [event for event in events if event["type"] == "tool_started"]
            finished = [event for event in events if event["type"] == "tool_finished"]
            self.assertGreaterEqual(len(started), 19)
            self.assertEqual(len(started), len(finished))
            parents = {item["request_id"] for item in reserved}
            contexts = {event["tool_request_id"]: event["parent_request_id"] for event in events if event["type"] == "native_tool_context"}
            for event in started:
                tool = event["tool"]
                self.assertIn(tool.get("parent_request_id", contexts.get(tool["request_id"])), parents)
            self.assertEqual(native.source_identity(source, "R")["dirty"], False)

    def test_mcp_unknown_parent_rejected_before_original_memory_write(self):
        source = Path(os.environ.get("FOUR_HARNESS_RETHLAS_SOURCE", "/Users/zcy/Codes/Math Agent/Rethlas-upstream"))
        python_site = ROOT / "dist/four-harness-builds/rethlas-mcp-python"
        with tempfile.TemporaryDirectory(prefix="four-harness-r-reject-") as directory:
            root = Path(directory) / "evidence"
            evidence = native.rethlas_mcp_fixture(root, source_root=source, python_executable=sys.executable,
                                                 python_site=python_site, scenario="unknown-parent")
            self.assertEqual(evidence["native"]["returncode"], 0, evidence["native"]["stderr"])
            report = json.loads(evidence["native"]["stdout"])
            self.assertTrue(report["rejected_before_original_tool"])
            self.assertEqual(len(evidence["requests"]), 1)
            self.assertFalse(any(event["type"] == "tool_started" for event in evidence["events"]))
            self.assertFalse((root / "source-copy/agents/generation/memory/never-created").exists())


if __name__ == "__main__":
    unittest.main()
