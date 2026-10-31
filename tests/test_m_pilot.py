from copy import deepcopy
from fractions import Fraction
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from backend import Projects, open_store
from bundle import export_project
from m_runner import load_manifest
from model_tools import dispatch as model_dispatch
from research_store import ResearchStore
from run_closeout import closeout
from run_ledger import Halt, Ledger
from run_recovery import recover
from schema import StoreError, canonical
from verify_bundle import verify
from checking import run_check
from checkers import grid75_p149_v1 as grid
from checkers import q7_e305_v1 as q7
from checkers import ring15_d20_v1 as ring
from checkers.registry import get as get_checker
from test_product import note


CARDS = HERE / "contracts/cards"


def evidence_data():
    return {"evidence_type": "witness", "problem_ref": {"id": "P", "rev": 1}, "attachments": [],
            "source": {"locator": "synthetic zero-model fixture", "version": "1", "accessed_at": None, "conditions": []},
            "source_status": "provided", "support_scope": "engineering witness bytes only",
            "limits": ["not novelty, mathematical acceptance, or a target witness"], "production": {"method": "unit fixture", "version": "1"}, "citation": None}


def obligation_data(checker_id, scope, evidence=None):
    data = deepcopy(get_checker(checker_id, scope)["obligation_fields"])
    data.update(problem_ref={"id": "P", "rev": 1}, uses=[], candidates=[], evidence=evidence or [],
                next_action="Preserve exact scope and request fixed technical checking", state="open", reason="unreviewed")
    return data


class CheckerTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("DSH_MATH_RING15_BASELINE"),
                         "Optional public 9/8 reproduction: explicitly provide DSH_MATH_RING15_BASELINE; historical material is not bundled")
    def test_ring_public_object_exact_baseline(self):
        source = Path(os.environ["DSH_MATH_RING15_BASELINE"])
        public = json.loads(source.read_bytes())
        pairs = [[int(Fraction(value) * ring.D) for value in pair] for pair in public["pairs"]]
        result = ring.evaluate(canonical({"pairs": pairs}))
        self.assertEqual(result["values"]["minimum_units"], "1179648")
        self.assertEqual((result["values"]["numerator"], result["values"]["denominator"]), ("9", "8"))
        self.assertFalse(result["values"]["target_met"])
        self.assertEqual(result["values"]["assignments_checked"], 32768)
        self.assertEqual(result["values"]["prefixes_checked"], 491520)

    def test_ring_synthetic_exact_and_differential(self):
        # Fifteen symmetric half-unit pairs: every total has magnitude >= D/2;
        # alternating signs attain D/2 at every prefix. This is an analytic
        # unit fixture, not the historical 9/8 baseline or a research witness.
        pairs = [[ring.D // 2, ring.D // 2] for _ in range(15)]
        result = ring.evaluate(canonical({"pairs": pairs}))
        self.assertTrue(result["values"]["valid"])
        self.assertEqual(result["values"]["minimum_units"], str(ring.D // 2))
        self.assertEqual((result["values"]["numerator"], result["values"]["denominator"]), ("1", "2"))
        self.assertFalse(result["values"]["target_met"])
        self.assertEqual(result["values"]["assignments_checked"], 32768)
        self.assertEqual(result["values"]["prefixes_checked"], 491520)
        rng = random.Random(615)
        for _ in range(3):
            sample = [[rng.randrange(0, 1000), rng.randrange(0, 1000)] for _ in range(15)]
            checked = ring.evaluate(canonical({"pairs": sample}))["values"]["minimum_units"]
            best = None
            for signs in itertools.product((0, 1), repeat=15):
                z = [pair[1] if sign else -pair[0] for pair, sign in zip(sample, signs)]
                total = sum(z)
                peak = max(abs(2 * sum(z[:k]) - total) for k in range(1, 16))
                best = peak if best is None else min(best, peak)
            self.assertEqual(checked, str(best))

    def test_ring_invalid_and_threshold_branch_not_witness(self):
        invalid = [{"pairs": [[0, 0]] * 14}, {"pairs": [[-1, 0]] + [[0, 0]] * 14},
                   {"pairs": [[ring.D, 1]] + [[0, 0]] * 14}, {"pairs": [[True, 0]] + [[0, 0]] * 14},
                   {"pairs": [[0.0, 0]] + [[0, 0]] * 14}]
        for value in invalid:
            self.assertEqual(ring.evaluate(canonical(value))["error_code"], "invalid_input")
        self.assertEqual(ring.evaluate(b'{"pairs":[],"pairs":[]}')["error_code"], "invalid_input")
        self.assertFalse(ring.target_met_for_h(1179648))
        self.assertTrue(ring.target_met_for_h(1179649))

    def test_q7_domain_faces_order_and_threshold_branch_not_witness(self):
        empty = q7.evaluate(canonical({"edges": []}), q7.RESEARCH_SCOPE)
        self.assertTrue(empty["values"]["valid"])
        self.assertEqual(empty["values"]["faces_checked"], 672)
        self.assertFalse(empty["values"]["target_met"])
        cycle = q7.evaluate(canonical({"edges": [[0, 1], [0, 2], [1, 3], [2, 3]]}), q7.RESEARCH_SCOPE)
        self.assertFalse(cycle["values"]["valid"])
        self.assertFalse(cycle["checks"]["c4_free"])
        for edges in ([[0, 1], [0, 1]], [[1, 0]], [[0, 3]], [[0, 2], [0, 1]]):
            self.assertEqual(q7.evaluate(canonical({"edges": edges}), q7.RESEARCH_SCOPE)["error_code"], "invalid_input")
        self.assertEqual(q7.evaluate(canonical({"edges": []}))["error_code"], "invalid_input")
        self.assertFalse(q7.target_met_for_count(304))
        self.assertTrue(q7.target_met_for_count(305))

    def test_grid_domain_order_collinearity_and_threshold_branch_not_witness(self):
        valid = grid.evaluate(canonical({"points": [[0, 0], [1, 1], [2, 3], [4, 2]]}), grid.RESEARCH_SCOPE)
        self.assertTrue(valid["values"]["valid"])
        self.assertEqual(valid["values"]["triples_checked"], 4)
        self.assertFalse(valid["values"]["target_met"])
        for points in ([[0, 0], [0, 0]], [[1, 1], [0, 0]], [[0, 0], [75, 1]], [[0, 0], [1, 1], [2, 2]]):
            result = grid.evaluate(canonical({"points": points}), grid.RESEARCH_SCOPE)
            if points == [[0, 0], [1, 1], [2, 2]]:
                self.assertFalse(result["values"]["valid"])
            else:
                self.assertEqual(result["error_code"], "invalid_input")
        self.assertEqual(grid.evaluate(canonical({"points": [[x, 0] for x in range(148)]}))["error_code"], "invalid_input")
        self.assertFalse(grid.target_met_for_count(148))
        self.assertTrue(grid.target_met_for_count(149))
        self.assertTrue(grid.target_met_for_count(150))


class PilotFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        self.projects = Projects(self.data)
        contract_path = HERE / "contracts/OP-000008.json"
        self.contract_raw = contract_path.read_bytes()
        self.contract = json.loads(self.contract_raw)
        self.project = self.projects.create("session-m-fixture", deepcopy(self.contract["problem"]))
        self.project_id = self.project["id"]
        self.output = self.root / "run"
        self.workspace = self.output / "workspace"
        self.output.mkdir()
        self.workspace.mkdir()
        self.material = self.output / "problem.md"
        shutil.copyfile(CARDS / "OP-000008.md", self.material)
        self.sources = self.output / "sources.md"
        self.sources.write_text("Synthetic acceptance exposure only; launch refresh remains pending.\n")
        self.manifest_path = self.output / "manifest.json"
        self.manifest = {"format": "dsh-math-m-run-v1", "protocol_id": "DSH-MATH-M-PILOT-v1",
                         "run_id": "run-m-fixture", "op_id": "OP-000008", "problem_id": "P", "project_id": self.project_id,
                         "session_id": "session-m-fixture", "actor_id": "fixture-model", "mode": "zero_model_acceptance",
                         "model": None, "effort": None, "checker_id": "grid75_p149_v1",
                         "contract": self.descriptor(contract_path), "materials": [self.descriptor(self.material, "problem.md"), self.descriptor(self.sources, "sources.md")],
                         "workspace": str(self.workspace), "output_dir": str(self.output),
                         "limits": {"wall_seconds": 60, "requests": 0, "tools": 20, "cpu_seconds": 10,
                                    "memory_bytes": 64 * 1024**2, "artifact_bytes": 1024**2, "tool_timeout_seconds": 3, "cleanup_seconds": 3},
                         "execution_authorized": False, "authorization_reference": None,
                         "stop_rules": ["synthetic zero-model acceptance; no live inference"]}
        self.manifest_path.write_bytes(canonical(self.manifest) + b"\n")
        self.calls = itertools.count(1)

    def descriptor(self, path, name=None):
        raw = Path(path).read_bytes()
        value = {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        if name is not None:
            value["name"] = name
        return value

    def tearDown(self):
        self.temp.cleanup()

    def call(self, name, arguments, **identity):
        request = {"project_id": self.project_id, "session_id": "session-m-fixture", "run_id": "run-m-fixture",
                   "tool_call_id": "call-" + str(next(self.calls)), "name": name, "arguments": arguments,
                   "manifest": str(self.manifest_path)}
        request.update(identity)
        return model_dispatch(self.data, request)["result"]

    def save_witness(self, points, scope=grid.RESEARCH_SCOPE):
        evidence = evidence_data()
        receipt = self.call("evidence_register", {"operation_id": "witness-evidence", "id": "E-WITNESS", "expected_rev": 0,
                                                   "data": evidence, "attachment_text": json.dumps({"points": points})})
        self.call("obligation_save", {"operation_id": "obligation-initial", "id": "O-CHECK", "expected_rev": 0,
                                      "data": obligation_data(grid.CHECKER_ID, scope)})
        self.call("obligation_save", {"operation_id": "obligation-linked", "id": "O-CHECK", "expected_rev": 1,
                                      "data": obligation_data(grid.CHECKER_ID, scope, [{"id": "E-WITNESS", "rev": 1}])})
        return receipt

    def test_model_identity_idempotence_permissions_and_exact_check(self):
        self.call("note_save", {"operation_id": "source-note", "id": "N-SOURCE", "expected_rev": 0, "data": note()})
        repeated = self.call("research_read", {"kind": "Note", "state": None, "text": "Candidate", "object_id": None, "rev": None}, tool_call_id="fixed-read")
        again = self.call("research_read", {"kind": "Note", "state": None, "text": "Candidate", "object_id": None, "rev": None}, tool_call_id="fixed-read")
        self.assertEqual(repeated["value"], again["value"])
        self.assertTrue(again["idempotent"])
        self.save_witness([[0, 0], [1, 1], [2, 3]])
        checked = self.call("check_witness", {"operation_id": "check-small", "checker_id": grid.CHECKER_ID,
                                               "target": {"id": "O-CHECK", "rev": 2}, "witness": {"id": "E-WITNESS", "rev": 1}})
        review = checked["value"]["review"]
        self.assertEqual(review["data"]["verdict"], "fail")
        self.assertTrue(review["data"]["execution"]["result"]["values"]["valid"])
        self.assertFalse(review["data"]["execution"]["result"]["values"]["target_met"])
        self.assertEqual(review["origin"]["run_id"], "run-m-fixture")
        self.assertEqual(review["origin"]["session_id"], "session-m-fixture")
        with open_store(self.projects.path(self.project_id) / "research") as store:
            model_events = [event for event in store.events if event["actor"]["kind"] == "model"]
            self.assertTrue(model_events)
            self.assertTrue(all(event["origin"]["source"] == "dsh-math-model" for event in model_events))
            self.assertTrue(all(event["origin"]["run_id"] == "run-m-fixture" for event in model_events))
        for name, arguments in (("review", {}), ("export", {}), ("budget_update", {}), ("spawn_agent", {})):
            with self.assertRaises(StoreError):
                self.call(name, arguments)
        with self.assertRaises(StoreError):
            self.call("research_read", {"kind": None, "state": None, "text": "", "object_id": None, "rev": None}, project_id="other-project")
        with self.assertRaises(StoreError):
            self.call("note_save", {"operation_id": "stale", "id": "N-SOURCE", "expected_rev": 0, "data": note()})
        with self.assertRaises(StoreError):
            self.call("file_read", {"path": "../../outside"})

    def test_formal_partial_is_input_error_not_mathematical_result(self):
        self.save_witness([[0, 0], [1, 1], [2, 3]], grid.FORMAL_SCOPE)
        checked = self.call("check_witness", {"operation_id": "check-partial-formal", "checker_id": grid.CHECKER_ID,
                                               "target": {"id": "O-CHECK", "rev": 2}, "witness": {"id": "E-WITNESS", "rev": 1}})
        result = checked["value"]["review"]["data"]["execution"]["result"]
        self.assertEqual(result["verdict"], "error")
        self.assertEqual(result["error_code"], "invalid_input")
        self.assertIsNone(result["values"])

    def test_isolated_material_files_python_and_idempotent_tools(self):
        material = self.call("material_read", {"name": "problem.md"})
        self.assertIn("149", material["value"]["text"])
        self.call("file_write", {"path": "work/input.txt", "text": "fixture"})
        self.assertEqual(self.call("file_read", {"path": "work/input.txt"})["value"]["text"], "fixture")
        host_only = self.root / "host-only.txt"
        host_only.write_text("Synthetic file outside the sandbox workspace; not a user document.\n")
        self.assertTrue(host_only.is_file())
        code = (
            "import pathlib,socket\npathlib.Path('python.txt').write_text('ok')\n"
            "try:\n socket.create_connection(('127.0.0.1',9),1)\n print('network-unexpected')\n"
            "except OSError:\n print('network-denied')\n"
            f"try:\n pathlib.Path({str(host_only)!r}).read_text()\n print('host-read-unexpected')\n"
            "except PermissionError:\n print('host-read-denied')"
        )
        result = self.call("python_run", {"code": code})
        self.assertEqual(result["value"]["exit_code"], 0)
        self.assertEqual(result["value"]["stdout"], "network-denied\nhost-read-denied\n")
        self.assertEqual(result["value"]["memory_enforcement"], "host_rss_poll_soft_limit")
        timeout = self.call("python_run", {"code": "import time\ntime.sleep(10)"})
        self.assertEqual(timeout["value"]["status"], "timeout")
        with self.assertRaises(StoreError):
            self.call("material_read", {"name": "../../secrets"})

    def test_budget_closeout_readonly_recovery_and_export(self):
        self.save_witness([[0, 0], [1, 1], [2, 3]])
        checked = self.call("check_witness", {"operation_id": "check-recovery", "checker_id": grid.CHECKER_ID,
                                               "target": {"id": "O-CHECK", "rev": 2}, "witness": {"id": "E-WITNESS", "rev": 1}})
        self.assertEqual(checked["value"]["status"], "completed")
        self.call("file_write", {"path": "work/局部.txt", "text": "非 ASCII 账本回归"})
        (Path(self.manifest["workspace"]) / "._AppleDouble").write_bytes(b"transfer metadata")
        ledger = Ledger(self.output / "ledger", self.manifest)
        with self.assertRaises(Halt):
            ledger.reserve_request("unapproved", None, None)
        ledger.close()
        receipt = closeout({"manifest": str(self.manifest_path), "reason": "acceptance_complete",
                            "components": {"model_tools": {"confirmed": True}}, "timed_out": False})
        self.assertEqual(receipt["status"], "cleanup_confirmed")
        self.assertEqual(receipt["actual"]["requests"], 0)
        recovery = recover(self.data, self.manifest_path)
        self.assertEqual(recovery["recovery_model_requests"], 0)
        self.assertEqual(recovery["requests"], 0)
        self.assertIsNone(recovery["total_tokens"])
        (self.output / "recovery.json").write_bytes(canonical(recovery) + b"\n")
        replay_sources = [{"adapter": "ledger-py-v1", "path": str(self.output / "ledger/events.jsonl")}]
        overview = self.projects.overview(self.project_id)
        bundle = export_project(self.projects, self.project_id, replay_sources, overview["snapshot"]["state"]["seq"],
                                run={"manifest": str(self.manifest_path), "closeout": str(self.output / "closeout.json"), "recovery": str(self.output / "recovery.json")})
        moved = self.root / "moved"
        shutil.move(bundle["directory"], moved)
        self.assertFalse((moved / "run/workspace/._AppleDouble").exists())
        command = [sys.executable, "-B", "verify_bundle.py", "."]
        standalone = subprocess.run(command, cwd=moved, capture_output=True, text=True, timeout=60)
        self.assertEqual(standalone.returncode, 0, standalone.stderr)
        verified = json.loads(standalone.stdout)
        self.assertEqual(verified["status"], "passed")
        self.assertEqual(verified["independent_checks"], 1)
        (moved / "run/workspace/._Transferred").write_bytes(b"transfer metadata")
        transferred = subprocess.run(command, cwd=moved, capture_output=True, text=True, timeout=60)
        self.assertEqual(transferred.returncode, 0, transferred.stderr)
        (moved / "run/workspace/unexpected.txt").write_text("not transfer metadata")
        with self.assertRaisesRegex(ValueError, "Unexpected or missing files"):
            verify(moved)
        (moved / "run/workspace/unexpected.txt").unlink()
        replay = json.loads((moved / "replay.json").read_bytes())
        self.assertEqual(replay["runs"][0]["run_id"], "run-m-fixture")
        self.assertIsNone(replay["usage"])
        self.assertEqual(json.loads((moved / "REVIEW-RECORD.json").read_bytes()), {"R1": None, "R2": None, "execution_authorized": False})
        self.sources.write_text("changed")
        with self.assertRaises(StoreError):
            recover(self.data, self.manifest_path)

    def test_incomplete_check_and_cleanup_failure(self):
        self.save_witness([[0, 0], [1, 1], [2, 3]])
        self.call("check_witness", {"operation_id": "check-incomplete", "checker_id": grid.CHECKER_ID,
                                     "target": {"id": "O-CHECK", "rev": 2}, "witness": {"id": "E-WITNESS", "rev": 1}})
        research_log = self.projects.path(self.project_id) / "research/events.jsonl"
        lines = research_log.read_bytes().splitlines(keepends=True)
        research_log.write_bytes(b"".join(lines[:-1]))
        with open_store(self.projects.path(self.project_id) / "research", writable=True) as store:
            control = store.operations["check-incomplete"]["control"]
            store.actor = deepcopy(control["requested_by"])
            store.origin = deepcopy(control["requested_origin"])
            outcome = run_check(store, "check-incomplete", grid.CHECKER_ID, {"id": "O-CHECK", "rev": 2}, {"id": "E-WITNESS", "rev": 1})
            self.assertEqual(outcome["status"], "incomplete")
            self.assertIsNone(outcome["checker_executed"])
        recovery = recover(self.data, self.manifest_path)
        self.assertEqual(len(recovery["incomplete_checks"]), 1)
        failure = closeout({"manifest": str(self.manifest_path), "reason": "injected_cleanup_failure",
                            "components": {"model_tools": {"confirmed": False}}, "timed_out": True})
        self.assertEqual(failure["status"], "cleanup_unverified")

    def test_fake_clock_budget_and_missing_usage(self):
        manifest = deepcopy(self.manifest)
        manifest.update(mode="live_m_research", model="synthetic-no-transport", effort="test", execution_authorized=True,
                        authorization_reference="synthetic unit ledger only; not a run authorization")
        manifest["limits"].update(requests=1, tools=1, wall_seconds=10)
        clock = [0.0]
        wall = [100.0]
        ledger = Ledger(self.output / "budget-ledger", manifest, clock=lambda: clock[0], wall_clock=lambda: wall[0])
        ledger.reserve_request("synthetic-request", "synthetic-no-transport", "test")
        self.assertEqual(ledger.rows[-1]["kind"], "request.reserved")
        ledger.settle_request("synthetic-request", None)
        self.assertIsNone(ledger.actual()["total_tokens"])
        ledger.tool_start("only-tool", "research_read", {})
        with self.assertRaises(Halt):
            ledger.tool_start("over-budget", "research_read", {})
        ledger.close()
        wall[0] += 11
        other = Ledger(self.output / "budget-ledger", manifest, clock=lambda: clock[0], wall_clock=lambda: wall[0])
        with self.assertRaises(Halt):
            other.check()
        other.close()


if __name__ == "__main__":
    unittest.main()
