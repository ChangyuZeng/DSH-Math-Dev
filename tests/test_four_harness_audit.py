"""Offline tests for the independent four-harness audit API and JSON CLI."""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import random
import tempfile
import unittest


AUDIT_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "four-harness-v1" / "audit.py"
SPEC = importlib.util.spec_from_file_location("four_harness_audit", AUDIT_PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def witness():
    return {"pairs": [[0, 0] for _ in range(15)]}


def record(**updates):
    result = dict(arm="C", repeat=1, status="not_solved", wall_seconds=12,
                  total_tokens=None, usage_complete=False, verified_h=None)
    result.update(updates)
    return result


def mask_oracle(pairs, prefix_count=15):
    """Separate bit-mask enumeration and direct prefix sums, with no pruning."""
    return min(max(abs(2 * sum(route[:k]) - sum(route))
                   for k in range(1, prefix_count + 1))
               for route in ([pair[1] if mask & (1 << i) else -pair[0]
                              for i, pair in enumerate(pairs)]
                             for mask in range(1 << 15)))


class WitnessTests(unittest.TestCase):
    def test_all_zero(self):
        data = witness()
        original = copy.deepcopy(data)
        self.assertEqual(audit.validate_witness(data),
                         {"H": 0, "D": 1048576, "target_met": False})
        self.assertEqual(data, original)

    def test_pair_sum_boundary(self):
        data = witness()
        data["pairs"][0] = [audit.D // 2, audit.D // 2]
        self.assertEqual(audit.validate_witness(data)["H"], audit.D // 2)
        data["pairs"][0][1] += 1
        with self.assertRaises(ValueError):
            audit.validate_witness(data)

    def test_exact_shapes_and_integer_types(self):
        bad = [None, [], {}, {"pairs": witness()["pairs"], "H": 0},
               {"pairs": tuple(witness()["pairs"])}, {"pairs": [[0, 0]] * 14},
               {"pairs": [[0, 0]] * 16}]
        for pair in ([True, 0], [0, False], [0.0, 0], [-1, 0], [0, -1],
                     [audit.D + 1, 0], (0, 0), [0], [0, 0, 0]):
            data = witness()
            data["pairs"][7] = pair
            bad.append(data)
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                audit.validate_witness(data)

    def test_last_prefix_is_essential(self):
        data = witness()
        data["pairs"][0] = [2, 2]
        data["pairs"][-1] = [3, 3]
        self.assertEqual(mask_oracle(data["pairs"], 14), 1)
        self.assertEqual(mask_oracle(data["pairs"]), 5)
        self.assertEqual(audit.validate_witness(data)["H"], 5)

    def test_exact_enumeration_matches_independent_oracle(self):
        rng = random.Random(729)
        for _ in range(3):
            pairs = [[rng.randrange(8), rng.randrange(8)] for _ in range(15)]
            self.assertEqual(audit.validate_witness({"pairs": pairs})["H"], mask_oracle(pairs))


class SummaryTests(unittest.TestCase):
    def test_empty_has_no_fabricated_observations(self):
        result = audit.summarize_runs([])
        self.assertEqual(result["quality"], "reported")
        self.assertEqual(len(result["missing_runs"]), 20)
        for group in [result["overall"], *result["arms"].values()]:
            self.assertEqual(group["record_count"], 0)
            self.assertIsNone(group["median_success_wall_seconds"])
            self.assertIsNone(group["median_complete_total_tokens"])
            self.assertIsNone(group["known_total_tokens_sum"])
            self.assertIsNone(group["total_tokens"])
            self.assertFalse(group["usage_complete"])

    def test_failures_usage_and_missing_slots_remain_visible(self):
        data = [record(), record(repeat=2, status="infra_failure", total_tokens=9),
                record(repeat=3, status="not_run", wall_seconds=None),
                record(repeat=4, total_tokens=0, usage_complete=True)]
        original = copy.deepcopy(data)
        result = audit.summarize_runs(data)
        group = result["arms"]["C"]
        self.assertEqual(group["status_counts"],
                         {"success": 0, "not_solved": 2, "infra_failure": 1, "not_run": 1})
        self.assertEqual(group["missing_record_count"], 1)
        self.assertEqual(group["attempted_count"], 3)
        self.assertEqual(group["incomplete_usage_count"], 2)
        self.assertEqual(group["missing_token_count"], 1)
        self.assertEqual(group["known_total_tokens_sum"], 9)
        self.assertEqual(group["median_complete_total_tokens"], 0)
        self.assertIsNone(group["total_tokens"])
        self.assertIsNone(group["median_success_wall_seconds"])
        self.assertEqual(data, original)

    def test_success_requires_status_score_and_explicit_attestation(self):
        data = [record(status="success", verified_h=audit.TARGET),
                record(repeat=2, status="success", verified_h=audit.TARGET - 1,
                       independent_verified=True),
                record(repeat=3, status="success", verified_h=audit.TARGET,
                       independent_verified=True, wall_seconds=42),
                record(repeat=4, status="not_solved", verified_h=audit.TARGET,
                       independent_verified=True)]
        result = audit.summarize_runs(data)["overall"]
        self.assertEqual(result["quality"], "reported")
        self.assertEqual(result["independently_verified_success_count"], 1)
        self.assertEqual(result["unverified_reported_success_count"], 2)
        self.assertEqual(result["median_success_wall_seconds"], 42)
        self.assertIsNone(result["verified_success_rate_of_expected"])
        self.assertFalse(result["sample_complete"])

    def test_complete_usage_and_wall_boundaries(self):
        data = [record(total_tokens=0, usage_complete=True, wall_seconds=0),
                record(repeat=2, total_tokens=10, usage_complete=True, wall_seconds=7200)]
        result = audit.summarize_runs(data)["overall"]
        self.assertEqual(result["total_tokens"], 10)
        self.assertEqual(result["median_complete_total_tokens"], 5)
        self.assertEqual(result["median_attempted_wall_seconds"], 3600)
        self.assertTrue(result["usage_complete"])

    def test_overruns_are_retained_and_missing_samples_are_not_failures(self):
        data = [record(repeat=i, total_tokens=1000001 if i == 1 else 0,
                       usage_complete=True, wall_seconds=7201 if i == 1 else 1)
                for i in range(1, 6)]
        result = audit.summarize_runs(data)
        self.assertEqual(result['arms']['C']['budget_overrun_count'], 1)
        self.assertTrue(result['arms']['C']['sample_complete'])
        self.assertEqual(result['arms']['C']['verified_success_rate_of_expected'], 0)
        self.assertIsNone(result['arms']['X']['verified_success_rate_of_expected'])
        self.assertEqual(result['overall']['total_tokens'], 1000001)

    def test_strict_record_validation(self):
        updates = [{"arm": "A"}, {"arm": []}, {"repeat": True}, {"repeat": 0},
                   {"repeat": 6}, {"status": "failed"}, {"status": []},
                   {"wall_seconds": None}, {"wall_seconds": True},
                   {"wall_seconds": -1},
                   {"wall_seconds": float("nan")}, {"wall_seconds": float("inf")},
                   {"total_tokens": True}, {"total_tokens": -1}, {"total_tokens": 1.5},
                   {"verified_h": False}, {"verified_h": -1}, {"verified_h": 1.5},
                   {"usage_complete": 1}, {"usage_complete": True},
                   {"independent_verified": 1}, {"independent_verified": True},
                   {"status": "not_run"}, {"unknown": 2}]
        for update in updates:
            with self.subTest(update=update), self.assertRaises(ValueError):
                audit.summarize_runs([record(**update)])
        missing = record()
        del missing["total_tokens"]
        for data in (None, {}, [missing], [record(), record()]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                audit.summarize_runs(data)


class CliTests(unittest.TestCase):
    def call_cli(self, command, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(text, encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = audit.main([command, str(path)])
            return code, json.loads(stdout.getvalue())

    def test_both_commands_emit_json(self):
        code, result = self.call_cli("check-witness", json.dumps(witness()))
        self.assertEqual(code, 0)
        self.assertEqual(result["H"], 0)
        code, result = self.call_cli("summarize", "[]")
        self.assertEqual(code, 0)
        self.assertEqual(result["overall"]["missing_record_count"], 20)

    def test_bad_json_duplicate_keys_and_invalid_schema(self):
        for text in ("{", '{"pairs": [], "pairs": []}', "null"):
            code, result = self.call_cli("check-witness", text)
            self.assertEqual(code, 2)
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
