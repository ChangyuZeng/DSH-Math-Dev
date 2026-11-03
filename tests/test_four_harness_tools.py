"""Offline toy-only common-tool/security tests. No model or Ring15 search.

Every fixture is new under tempfile, never a research/history directory. Tests
of _run_process execute fixed trusted toy scripts to exercise supervision even
when the actual OS sandbox is unavailable. Sandbox integration tests run only
when the real containment probe passes; fail-closed behavior is always tested.
"""

import base64
from dataclasses import replace
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


TOOLS_PATH = Path(__file__).resolve().parents[1] / "evaluation/four-harness-v1/tools.py"
SPEC = importlib.util.spec_from_file_location("four_harness_tools", TOOLS_PATH)
tools = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tools
SPEC.loader.exec_module(tools)


def candidate():
    return {"pairs": [[0, 0] for _ in range(15)]}


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="four-tools-toy-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.material = self.root / "public-toy.txt"
        self.material.write_bytes("aπz\n".encode())
        self.events = []
        self.limits = tools.ToolLimits(timeout_seconds=3, cpu_seconds=2)
        self.host = self.make_host()

    def make_host(self, name="run-C", **kwargs):
        return tools.CommonTools(name, self.root / name,
                                 {"toy": {"path": self.material, "sha256": digest(self.material.read_bytes())}},
                                 limits=kwargs.pop("limits", self.limits),
                                 accounting=kwargs.pop("accounting", self.events.append), **kwargs)

    def call(self, name, args=None, host=None):
        reply = (host or self.host).call(name, args)
        json.dumps(reply, allow_nan=False)
        return reply

    def okay(self, reply):
        self.assertTrue(reply["ok"], reply)
        return reply["result"]

    def error(self, reply, code):
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(reply["error"]["code"], code, reply)


class ToolTests(Fixture):
    def test_material_ids_frozen_hash_and_byte_windows(self):
        result = self.okay(self.call("materials.list"))
        self.assertEqual(result["materials"], [{"id": "toy", "size": 5, "sha256": digest(self.material.read_bytes())}])
        self.assertNotIn(str(self.root), json.dumps(result))
        one = self.okay(self.call("materials.read", {"id": "toy", "offset": 1, "length": 1}))
        self.assertIsNone(one["text"])
        self.assertEqual(base64.b64decode(one["base64"]), b"\xcf")
        self.assertEqual(one["sha256"], digest(self.material.read_bytes()))
        tail = self.okay(self.call("materials.read", {"id": "toy", "offset": 99, "length": 1}))
        self.assertEqual(tail["length"], 0)
        self.assertTrue(tail["eof"])
        for args in ({"id": "../public-toy.txt"}, {"id": str(self.material)}):
            self.error(self.call("materials.read", args), "unknown_material")

    def test_material_hash_drift_is_rejected_for_list_and_read(self):
        self.material.write_bytes(b"changed")
        self.error(self.call("materials.list"), "material_hash_drift")
        self.error(self.call("materials.read", {"id": "toy"}), "material_hash_drift")

    def test_material_links_rejected_at_open_and_construction(self):
        linked = self.root / "linked.txt"
        linked.symlink_to(self.material)
        with self.assertRaises(tools.ToolError):
            tools.CommonTools("bad", self.root / "bad", {"x": {"path": linked, "sha256": digest(self.material.read_bytes())}})
        hard = self.root / "hard.txt"
        os.link(self.material, hard)
        self.error(self.call("materials.read", {"id": "toy"}), "unsafe_path")

    def test_workspace_roundtrip_utf8_and_binary(self):
        result = self.okay(self.call("workspace.write", {"path": "notes/a.txt", "text": "π"}))
        self.assertEqual(result["size"], 2)
        self.assertEqual(self.okay(self.call("workspace.read", {"path": "notes/a.txt"}))["text"], "π")
        self.okay(self.call("workspace.write", {"path": "binary", "base64": "/wA="}))
        result = self.okay(self.call("workspace.read", {"path": "binary"}))
        self.assertIsNone(result["text"])
        self.assertEqual(base64.b64decode(result["base64"]), b"\xff\x00")

    def test_traversal_absolute_and_cross_run_paths(self):
        other = self.make_host("run-X")
        self.okay(self.call("workspace.write", {"path": "secret", "text": "other"}, host=other))
        paths = ["../run-X/workspace/secret", "../../public-toy.txt", "/etc/passwd", "./x", "a//x", "a/../x", "a\\x", "x\x00y", ""]
        for path in paths:
            for tool, args in (("workspace.read", {"path": path}),
                               ("workspace.write", {"path": path, "text": "bad"})):
                with self.subTest(tool=tool, path=path):
                    self.error(self.call(tool, args), "invalid_path")
        self.error(self.call("workspace.read", {"path": "secret"}), "unsafe_path")
        self.assertEqual(self.okay(self.call("workspace.read", {"path": "secret"}, host=other))["text"], "other")
        self.error(self.call("workspace.read", {"path": "../.host/identity.json"}), "invalid_path")

    def test_symlinked_parent_and_leaf_cannot_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "keep"
        target.write_text("keep")
        for name, destination in (("leaf", target), ("parent", outside)):
            link = self.host.workspace.path / name
            link.symlink_to(destination)
        self.error(self.call("workspace.read", {"path": "leaf"}), "unsafe_path")
        self.error(self.call("workspace.write", {"path": "parent/keep", "text": "bad"}), "unsafe_path")
        self.assertEqual(target.read_text(), "keep")

    def test_hardlink_is_rejected_without_mutating_other_run(self):
        other = self.make_host("run-X")
        self.okay(self.call("workspace.write", {"path": "secret", "text": "keep"}, host=other))
        target = other.workspace.path / "secret"
        os.link(target, self.host.workspace.path / "link")
        self.error(self.call("workspace.read", {"path": "link"}), "unsafe_path")
        self.error(self.call("workspace.write", {"path": "link", "text": "bad"}), "unsafe_path")
        self.assertEqual(target.read_text(), "keep")

    def test_run_directory_identity_and_freshness(self):
        with self.assertRaises(ValueError):
            self.make_host()
        original = self.host.workspace.path
        moved = self.host.run_root / "moved-workspace"
        original.rename(moved)
        original.mkdir()
        self.error(self.call("workspace.write", {"path": "x", "text": "bad"}), "unsafe_path")
        self.assertFalse((original / "x").exists())

    def test_nonregular_files_rejected_without_blocking(self):
        os.mkfifo(self.host.workspace.path / "fifo")
        start = time.monotonic()
        self.error(self.call("workspace.read", {"path": "fifo"}), "unsafe_path")
        self.assertLess(time.monotonic() - start, 1)

    def test_artifact_file_byte_and_entry_limits(self):
        limited = self.make_host("limited", limits=replace(self.limits, max_file_bytes=5, artifact_bytes=8, max_files=3))
        self.okay(self.call("workspace.write", {"path": "a", "text": "12345"}, limited))
        self.error(self.call("workspace.write", {"path": "b", "text": "123456"}, limited), "file_limit")
        self.error(self.call("workspace.write", {"path": "b", "text": "1234"}, limited), "artifact_limit")
        self.okay(self.call("workspace.write", {"path": "a", "text": "1"}, limited))
        self.okay(self.call("workspace.write", {"path": "b", "text": "12345"}, limited))
        self.okay(self.call("workspace.write", {"path": "c", "text": "1"}, limited))
        self.error(self.call("workspace.write", {"path": "d", "text": ""}, limited), "artifact_limit")

    def test_windows_and_payloads_are_strict(self):
        for args in ({"id": "toy", "offset": True}, {"id": "toy", "length": -1},
                     {"id": "toy", "length": self.limits.max_read_bytes + 1},
                     {"id": "toy", "extra": 1}):
            self.error(self.call("materials.read", args), "invalid_arguments")
        for args in ({"path": "x"}, {"path": "x", "text": "", "base64": ""},
                     {"path": "x", "text": []}, {"path": "x", "base64": "bad %"}):
            self.error(self.call("workspace.write", args), "invalid_arguments")
        self.error(self.call("compute", {"code": "pass", "command": "sh"}), "invalid_arguments")
        self.error(self.call("compute", {"code": "x" * (self.limits.max_code_bytes + 1)}), "code_limit")
        self.error(self.call("unknown", {}), "unknown_tool")

    def test_every_call_has_begin_end_cpu_metrics_including_errors(self):
        for name, args in (("materials.list", {}), ("unknown", {}), ("check", {"candidate": candidate()})):
            reply = self.call(name, args)
            metrics = reply["metrics"]
            self.assertGreaterEqual(metrics["cpu_seconds"], 0)
            self.assertGreaterEqual(metrics["wall_seconds"], 0)
            self.assertEqual(metrics["cpu_seconds"], metrics["parent_cpu_seconds"] + metrics["child_cpu_seconds"])
            begin, end = self.events[-2:]
            self.assertEqual((begin["type"], end["type"]), ("tool_started", "tool_finished"))
            self.assertEqual(begin["request_id"], metrics["request_id"])
            self.assertEqual(begin["params_sha256"], metrics["params_sha256"])
            self.assertEqual(end["result_sha256"], metrics["result_sha256"])
        self.assertGreater(reply["metrics"]["parent_cpu_seconds"], 0.01)
        self.assertGreater(reply["result"]["checker_cpu_seconds"], 0.01)

    def test_accounting_rejection_prevents_dispatch_and_end_failure_visible(self):
        def reject_begin(event):
            self.events.append(event)
            if event["type"] == "tool_started":
                raise RuntimeError("ledger budget")
        host = self.make_host("reject-begin", accounting=reject_begin)
        self.error(self.call("workspace.write", {"path": "x", "text": "bad"}, host), "accounting_failed")
        self.assertFalse((host.workspace.path / "x").exists())
        self.assertEqual([e["type"] for e in self.events], ["tool_started", "tool_finished"])
        def reject_end(event):
            if event["type"] == "tool_finished":
                raise RuntimeError("ledger unavailable")
        host = self.make_host("reject-end", accounting=reject_end)
        self.error(self.call("materials.list", host=host), "accounting_failed")

    def test_invalid_nonjson_arguments_still_accounted(self):
        self.error(self.call("check", {"candidate": float("nan")}), "invalid_arguments")
        self.assertEqual([event["type"] for event in self.events], ["tool_started", "tool_finished"])

    def test_precancelled_call_has_no_side_effects(self):
        cancel = threading.Event()
        cancel.set()
        reply = self.host.call("workspace.write", {"path": "x", "text": "bad"}, cancel_event=cancel)
        self.error(reply, "cancelled")
        self.assertFalse((self.host.workspace.path / "x").exists())
        self.assertEqual(len(self.events), 2)

    def test_exact_check_uses_audit_with_cpu_and_no_model_claims(self):
        data = candidate()
        data["pairs"][0] = [2, 2]
        data["pairs"][-1] = [3, 3]
        result = self.okay(self.call("check", {"candidate": data}))
        self.assertEqual(result["H"], 5)
        self.assertTrue(result["legal"])
        self.assertFalse(result["target_met"])
        self.assertGreater(result["checker_cpu_seconds"], 0)
        data["target_met"] = True
        result = self.okay(self.call("check", {"candidate": data}))
        self.assertFalse(result["legal"])
        self.assertFalse(result["target_met"])
        self.error(self.call("submit", {"candidate": candidate(), "independent_verified": True}), "invalid_arguments")

    def test_submit_persists_immutable_hash_receipt_and_independent_rerun(self):
        data = candidate()
        before = datetime.now().astimezone()
        # Deliberately corrupt the host's in-memory result: submit must use the
        # frozen audit file in a fresh independent interpreter instead.
        with patch.object(self.host._audit, "validate_witness", return_value={"H": 99999999, "D": 1048576, "target_met": True}):
            reply = self.call("submit", {"candidate": data})
        result = self.okay(reply)
        self.assertEqual(result["independent_check"]["H"], 0)
        self.assertFalse(result["independent_check"]["target_met"])
        self.assertEqual(result["run_id"], "run-C")
        self.assertEqual(result["verification_basis"], "fresh_process_exact_audit_rerun")
        self.assertGreater(result["checker_cpu_seconds"], 0)
        self.assertGreater(reply["metrics"]["child_cpu_seconds"], 0)
        self.assertGreaterEqual(datetime.fromisoformat(result["submitted_at"].replace("Z", "+00:00")), before)
        saved = self.host.submissions.path / (result["candidate_sha256"] + ".json")
        receipt = self.host.submissions.path / ("receipt-" + result["receipt_id"] + ".json")
        self.assertEqual(digest(saved.read_bytes()), result["candidate_sha256"])
        self.assertEqual(json.loads(receipt.read_text()), result)
        self.error(self.call("workspace.write", {"path": "../.host/submissions/" + saved.name, "text": "bad"}), "invalid_path")
        with self.assertRaises(tools.ToolError):
            self.host.submissions.write(saved.name, b"bad", exclusive=True)
        second = self.okay(self.call("submit", {"candidate": data}))
        self.assertEqual(second["candidate_sha256"], result["candidate_sha256"])
        self.assertNotEqual(second["receipt_id"], result["receipt_id"])
        self.assertEqual(saved.read_bytes(), tools._json_bytes(data))

    def test_invalid_submission_is_preserved_as_invalid(self):
        result = self.okay(self.call("submit", {"candidate": {"pairs": []}}))
        self.assertFalse(result["independent_check"]["legal"])
        self.assertFalse(result["independent_check"]["target_met"])
        self.assertIsNone(result["independent_check"]["H"])

    def test_cancelled_submission_retains_candidate_and_has_no_success(self):
        cancel = threading.Event()
        original_run = self.host._run_process
        def cancel_at_launch(command, cwd, event):
            event.set()
            return original_run(command, cwd, event)
        with patch.object(self.host, "_run_process", side_effect=cancel_at_launch):
            reply = self.host.call("submit", {"candidate": candidate()}, cancel_event=cancel)
        result = self.okay(reply)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["independent_check"]["legal"])
        self.assertFalse(result["independent_check"]["target_met"])
        self.assertTrue((self.host.submissions.path / (result["candidate_sha256"] + ".json")).is_file())
        self.assertTrue((self.host.submissions.path / ("receipt-" + result["receipt_id"] + ".json")).is_file())

    def test_submission_quota_includes_immutable_receipts(self):
        host = self.make_host("tiny", limits=replace(self.limits, artifact_bytes=4096))
        self.error(self.call("submit", {"candidate": candidate()}, host), "artifact_limit")
        self.assertEqual(list(host.submissions.path.iterdir()), [])
        host = self.make_host("tiny-receipt", limits=replace(self.limits, max_file_bytes=256))
        self.error(self.call("submit", {"candidate": candidate()}, host), "file_limit")
        self.assertEqual(list(host.submissions.path.iterdir()), [])

    def test_checker_hash_drift_fails_closed(self):
        with patch.object(self.host, "checker_sha256", "0" * 64):
            self.error(self.call("check", {"candidate": candidate()}), "checker_hash_drift")
            self.error(self.call("submit", {"candidate": candidate()}), "checker_hash_drift")

    def test_missing_or_failed_sandbox_never_falls_back(self):
        with patch.object(self.host, "_sandbox", None), patch.object(self.host, "_run_process") as launch:
            self.error(self.call("compute", {"code": "print('must not execute')"}), "unsupported_sandbox")
            launch.assert_not_called()
        self.host._probe_result = {"ok": False}
        with patch.object(self.host, "_sandbox", "seatbelt"), patch.object(self.host, "_compute") as execute:
            self.error(self.call("compute", {"code": "print('must not execute')"}), "unsupported_sandbox")
            execute.assert_not_called()


class SupervisorTests(Fixture):
    # These fixed local scripts are trusted supervisor fixtures, never sent via
    # the compute API and never advertised as sandbox integration coverage.
    def trusted(self, code, cancel=None):
        return self.host._run_process([str(self.host._python), "-I", "-S", "-B", "-c", code], self.host.control.path, cancel)

    def test_trusted_supervisor_records_cpu(self):
        result = self.trusted("print(sum(i*i for i in range(500000)))")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["returncode"], 0)
        self.assertGreater(result["cpu_seconds"], 0.01)
        self.assertGreater(result["peak_memory_bytes"], 0)

    def test_trusted_supervisor_timeout(self):
        self.host.limits = replace(self.limits, timeout_seconds=0.15)
        result = self.trusted("import time; time.sleep(5)")
        self.assertEqual(result["status"], "timeout")
        self.assertLess(result["wall_seconds"], 2)

    def test_trusted_supervisor_cancellation_and_process_tree(self):
        self.host.limits = replace(self.limits, timeout_seconds=3)
        pid_file = self.host.control.path / "child-pid"
        code = ("import os,time\n"
                "pid=os.fork()\n"
                "if pid == 0:\n"
                "    time.sleep(10)\n"
                "else:\n"
                f"    open({str(pid_file)!r}, 'w').write(str(pid))\n"
                "    time.sleep(10)\n")
        cancel = threading.Event()
        timer = threading.Timer(0.25, cancel.set)
        timer.start()
        try:
            result = self.trusted(code, cancel)
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(result["status"], "cancelled")
        self.assertLess(result["wall_seconds"], 2)
        pid = int(pid_file.read_text())
        # A dead descendant can briefly remain a zombie awaiting OS adoption.
        observation = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        self.assertTrue(observation.returncode == 1 or observation.stdout.strip().startswith("Z"), observation.stdout)

    def test_trusted_supervisor_output_limit(self):
        self.host.limits = replace(self.limits, max_output_bytes=100)
        result = self.trusted("import sys; sys.stdout.write('x' * 100000)")
        self.assertEqual(result["status"], "output_limit")
        self.assertTrue(result["output_truncated"])
        self.assertLessEqual(len(result["stdout"].encode()), 100)

    def test_trusted_supervisor_memory_watchdog(self):
        self.host.limits = replace(self.limits, memory_bytes=64 * 1024 * 1024)
        result = self.trusted("import time; x=bytearray(96*1024*1024); time.sleep(2)")
        self.assertEqual(result["status"], "memory_limit")
        self.assertGreater(result["peak_memory_bytes"], self.host.limits.memory_bytes)


class SandboxTests(Fixture):
    def qualified(self):
        reply = self.call("compute", {"code": "print(2)"})
        if not reply["ok"] and reply["error"]["code"] == "unsupported_sandbox":
            self.skipTest("real sandbox unavailable: " + json.dumps(self.host.capabilities()["probe"]))
        result = self.okay(reply)
        self.assertEqual(result["returncode"], 0, result)
        self.assertEqual(result["stdout"], "2\n")
        self.assertTrue(self.host.capabilities()["sandbox_verified"])

    def test_actual_os_probe_or_explicit_fail_closed(self):
        reply = self.call("compute", {"code": "print(2)"})
        if reply["ok"]:
            self.assertEqual(reply["result"]["stdout"], "2\n")
            self.assertTrue(self.host.capabilities()["sandbox_verified"])
        else:
            self.assertEqual(reply["error"]["code"], "unsupported_sandbox")
            self.assertFalse(self.host.capabilities()["sandbox_verified"])

    def test_sandbox_readonly_snapshots_and_no_environment_credentials(self):
        self.qualified()
        self.okay(self.call("workspace.write", {"path": "toy", "text": "sandbox-data"}))
        with patch.dict(os.environ, {"FOUR_TOOLS_TEST_SECRET": "do-not-copy"}):
            result = self.okay(self.call("compute", {"code": "import os; print(open(os.path.join(WORKSPACE, 'toy')).read()); print(open(MATERIALS['toy']).read()); print(os.getenv('FOUR_TOOLS_TEST_SECRET'))"}))
        self.assertEqual(result["returncode"], 0, result)
        self.assertIn("sandbox-data", result["stdout"])
        self.assertIn("None", result["stdout"])
        self.assertNotIn("do-not-copy", result["stdout"])

    def test_sandbox_denies_cross_run_host_and_hardlinks(self):
        self.qualified()
        other = self.make_host("other")
        self.okay(self.call("workspace.write", {"path": "private", "text": "cross-run-private"}, other))
        code = ("import os,json\nresults=[]\n"
                f"for path in [{str(other.workspace.path / 'private')!r}, {str(self.material)!r}, '/etc/passwd']:\n"
                " try: open(path).read(); results.append(False)\n"
                " except OSError: results.append(True)\n"
                "try: os.link(__file__, os.path.join(WORKSPACE,'link')); results.append(False)\n"
                "except OSError: results.append(True)\n"
                "print(json.dumps(results))")
        result = self.okay(self.call("compute", {"code": code}))
        self.assertEqual(result["returncode"], 0, result)
        self.assertEqual(json.loads(result["stdout"]), [True] * 4)

    def test_sandbox_denies_processes_network_and_reexec(self):
        self.qualified()
        code = ("import os,sys,socket,json\nresults=[]\n"
                "for action in [lambda: os.fork(), lambda: socket.socket(), lambda: os.execv(sys.executable,[sys.executable,'-c','print(99)'])]:\n"
                " try: action(); results.append(False)\n"
                " except OSError: results.append(True)\n"
                "print(json.dumps(results))")
        result = self.okay(self.call("compute", {"code": code}))
        self.assertEqual(result["returncode"], 0, result)
        self.assertEqual(json.loads(result["stdout"]), [True] * 3)

    def test_sandbox_timeout_and_cpu_limit(self):
        self.qualified()
        self.host.limits = replace(self.limits, timeout_seconds=0.2)
        result = self.okay(self.call("compute", {"code": "import time; time.sleep(5)"}))
        self.assertEqual(result["status"], "timeout")
        self.host.limits = replace(self.limits, timeout_seconds=4, cpu_seconds=1)
        result = self.okay(self.call("compute", {"code": "while True: pass"}))
        self.assertEqual(result["status"], "cpu_limit")
        self.assertGreater(result["cpu_seconds"], 0.5)

    def test_sandbox_hard_memory_limit(self):
        self.qualified()
        self.host.limits = replace(self.limits, memory_bytes=256 * 1024 * 1024)
        result = self.okay(self.call("compute", {"code": "x=bytearray(512*1024*1024); print('allocation passed')"}))
        self.assertNotEqual(result["returncode"], 0)
        self.assertNotIn("allocation passed", result["stdout"])
        self.assertTrue("MemoryError" in result["stderr"] or result["status"] == "memory_limit", result)

    def test_sandbox_cancel(self):
        self.qualified()
        cancel = threading.Event()
        timer = threading.Timer(0.25, cancel.set)
        timer.start()
        try:
            reply = self.host.call("compute", {"code": "import time; time.sleep(5)"}, cancel_event=cancel)
        finally:
            timer.cancel()
            timer.join()
        result = self.okay(reply)
        self.assertEqual(result["status"], "cancelled")
        self.assertGreater(reply["metrics"]["child_cpu_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
