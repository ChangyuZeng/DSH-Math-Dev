"""Differential unit tests for grid75_independent_check.

All tests use tiny synthetic grids; nothing here requires a full 75x75 run.
The module under test must be fully independent, so every expected value in
these tests is computed by an explicit brute force written inline below.
"""

import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import grid75_independent_check as gic


def collinear(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) == (b[1] - a[1]) * (c[0] - a[0])


def brute_force_blockers(p, baseline):
    """Every pair of baseline points collinear with p, as sorted index pairs."""
    out = []
    for i, j in itertools.combinations(range(len(baseline)), 2):
        if collinear(p, baseline[i], baseline[j]):
            out.append((i, j))
    return tuple(sorted(out))


def brute_force_feasible(subset_ids, checker, k):
    """True iff the addition set can be completed to a valid witness:
    no collinear new-point triple, and some deletion set D exists with
    F(subset) <= D, |D| <= len(subset) - 1, hitting every union blocker edge.
    """
    pts = checker.outside
    sub = [pts[i] for i in subset_ids]
    for a, b, c in itertools.combinations(sub, 3):
        if collinear(a, b, c):
            return False
    forced = set()
    for a, b in itertools.combinations(subset_ids, 2):
        forced |= checker.pair_forced(a, b)
    edges = set()
    for i in subset_ids:
        edges |= set(checker.blockers[i])
    budget = len(subset_ids) - 1
    base_range = range(len(checker.baseline))
    extras = [i for i in base_range if i not in forced]
    for extra_count in range(0, budget - len(forced) + 1):
        for combo in itertools.combinations(extras, extra_count):
            deletions = set(forced) | set(combo)
            if all(u in deletions or v in deletions for u, v in edges):
                return True
    return False


def make_checker(tmpdir, baseline_points, k, mode, grid, stop=None, name="out.json"):
    path = os.path.join(tmpdir, "baseline.json")
    with open(path, "w") as handle:
        json.dump({"points": [list(p) for p in baseline_points], "grid": grid}, handle)
    return gic.IndependentChecker(path, k, mode, os.path.join(tmpdir, name), case_stop=stop)


# Baselines on a 5x5 grid, verified at import to satisfy the checker
# preconditions (no 3 collinear, every outside point sees groups of size <= 2).
BASELINE_A = [(0, 0), (1, 1), (1, 3), (2, 1), (2, 4), (3, 0), (3, 4), (4, 3)]
BASELINE_BORDER = [(0, 0), (0, 2), (1, 0), (1, 2), (2, 1), (2, 3), (3, 1), (3, 3)]


def valid_baseline(points, grid):
    try:
        checker = gic.IndependentChecker.__new__(gic.IndependentChecker)
        checker.baseline = list(points)
        checker.base_set = set(points)
        checker.base_index = {p: i for i, p in enumerate(points)}
        checker.n = grid
        checker.force_cache = {}
        checker.k = 99
        outside = [
            (x, y) for x in range(grid) for y in range(grid) if (x, y) not in checker.base_set
        ]
        checker.outside = outside
        checker.order = {p: i for i, p in enumerate(outside)}
        checker.blockers = [gic.compute_blockers(p, points) for p in outside]
    except ValueError:
        return None
    if gic.verify_set(list(points), grid)["zero_determinants"] != 0:
        return None
    return checker


class TestBlockersDifferential(unittest.TestCase):
    def test_against_brute_force(self):
        for baseline in (BASELINE_A, BASELINE_BORDER):
            checker = valid_baseline(baseline, 5)
            self.assertIsNotNone(checker, "test baseline invalid")
            for i, p in enumerate(checker.outside):
                self.assertEqual(
                    checker.blockers[i], brute_force_blockers(p, checker.baseline)
                )

    def test_group_size_guard(self):
        # three collinear baseline points on one line through an outside point
        baseline = [(0, 0), (1, 1), (2, 2), (0, 4)]
        with self.assertRaises(ValueError):
            gic.compute_blockers((3, 3), baseline)


class TestHittingSetDifferential(unittest.TestCase):
    def test_random_instances(self):
        rng = random.Random(20261029)
        for _ in range(400):
            nv = rng.randint(1, 8)
            pool = list(range(nv))
            edges = set()
            for u, v in itertools.combinations(pool, 2):
                if rng.random() < 0.4:
                    edges.add((u, v))
            edges = tuple(sorted(edges))
            forced = frozenset(rng.sample(pool, rng.randint(0, nv)))
            budget = rng.randint(0, nv)
            with self.subTest(edges=edges, forced=forced, budget=budget):
                answer = gic.hitting_set(edges, forced, budget)
                brute = None
                rest = [v for v in pool if v not in forced]
                for r in range(0, budget - len(forced) + 1):
                    for combo in itertools.combinations(rest, r):
                        candidate = set(forced) | set(combo)
                        if all(u in candidate or v in candidate for u, v in edges):
                            brute = candidate
                            break
                    if brute is not None:
                        break
                self.assertEqual(answer is not None, brute is not None)
                if answer is not None:
                    self.assertTrue(forced <= answer)
                    self.assertLessEqual(len(answer), budget)
                    self.assertTrue(
                        all(u in answer or v in answer for u, v in edges)
                    )

    def test_forced_exceeds_budget(self):
        self.assertIsNone(gic.hitting_set([(0, 1)], {0, 1}, 1))
        self.assertIsNone(gic.hitting_set([(0, 1)], {0}, 0))
        # forced vertices already cover the edge: no extra deletion needed
        self.assertEqual(gic.hitting_set([(0, 1)], {0, 1}, 5), {0, 1})
        self.assertEqual(gic.hitting_set([(0, 1)], {0}, 5), {0})


class TestUnanchoredDifferential(unittest.TestCase):
    def test_k2_and_k3(self):
        tmpdir = tempfile.mkdtemp()
        for k in (1, 2, 3):
            checker = make_checker(tmpdir, BASELINE_A, k, "unanchored", 5, name="out-k%d.json" % k)
            result = checker.run()
            eligible = checker.eligible
            expect_candidate = False
            for size in range(1, k + 2):
                for combo in itertools.combinations(eligible, size):
                    if brute_force_feasible(combo, checker, k):
                        expect_candidate = True
                        break
                if expect_candidate:
                    break
            self.assertEqual(result["status"] == "candidate_found", expect_candidate)
            if expect_candidate:
                self.assertIsNotNone(result["candidate"])
                self.assertEqual(
                    result["candidate"]["check"]["zero_determinants"], 0
                )
                self.assertEqual(
                    len(result["candidate"]["points"]), len(checker.baseline) + 1
                )
            else:
                self.assertTrue(result["no_candidate"])
                self.assertEqual(result["status"], "completed")

    def test_no_pair_graph_materialized(self):
        tmpdir = tempfile.mkdtemp()
        checker = make_checker(tmpdir, BASELINE_A, 0, "unanchored", 5)
        checker.run()
        # the only cached structures allowed are per-point blocker records and
        # pair forced masks; no adjacency / pair graph is ever built
        self.assertFalse(hasattr(checker, "neighbors"))
        self.assertFalse(hasattr(checker, "adj"))
        self.assertTrue(all(isinstance(e, tuple) for e in checker.blockers))
        for key in checker.force_cache:
            self.assertIsInstance(key, tuple)
            self.assertEqual(len(key), 2)
            self.assertLess(key[0], key[1])


class TestAnchoredMode(unittest.TestCase):
    def test_case_list_covers_every_trade(self):
        tmpdir = tempfile.mkdtemp()
        checker = make_checker(tmpdir, BASELINE_BORDER, 2, "anchored", 5)
        self.assertEqual(checker._border(), (4, 4))
        cases = checker.build_cases()
        bx = by = 4
        corner = checker.order[(4, 4)]
        eligible = set(checker.eligible)
        # rebuild the covered-subset family by brute enumeration
        covered = set()
        for seed, allowed in cases:
            for extra in range(0, 3 - len(seed) + 1):
                for combo in itertools.combinations(allowed, extra):
                    covered.add(tuple(sorted(seed + list(combo))))
        for size in (1, 2, 3):
            for combo in itertools.combinations(sorted(eligible), size):
                pts = [checker.outside[i] for i in combo]
                has_row = any(p[0] == bx for p in pts)
                has_col = any(p[1] == by for p in pts)
                if corner in combo or (has_row and has_col):
                    self.assertIn(combo, covered, "subset not covered: %r" % (combo,))
                else:
                    self.assertNotIn(combo, covered)

    def test_anchored_differential(self):
        tmpdir = tempfile.mkdtemp()
        for k in (1, 2):
            checker = make_checker(tmpdir, BASELINE_BORDER, k, "anchored", 5, name="out-k%d.json" % k)
            result = checker.run()
            bx = by = 4
            corner = checker.order[(4, 4)]
            eligible = checker.eligible
            expect_candidate = False
            for size in range(1, k + 2):
                for combo in itertools.combinations(eligible, size):
                    pts = [checker.outside[i] for i in combo]
                    has_row = any(p[0] == bx for p in pts)
                    has_col = any(p[1] == by for p in pts)
                    if corner in combo or (has_row and has_col):
                        if brute_force_feasible(combo, checker, k):
                            expect_candidate = True
                            break
                if expect_candidate:
                    break
            self.assertEqual(result["status"] == "candidate_found", expect_candidate)

    def test_bad_border_layout_rejected(self):
        tmpdir = tempfile.mkdtemp()
        checker = make_checker(tmpdir, BASELINE_A, 2, "anchored", 5)
        with self.assertRaises(ValueError):
            checker.build_cases()


class TestCheckpointBinding(unittest.TestCase):
    def test_completed_returns_existing_and_binding(self):
        tmpdir = tempfile.mkdtemp()
        checker = make_checker(tmpdir, BASELINE_A, 0, "unanchored", 5)
        first = checker.run()
        self.assertEqual(first["status"], "completed")
        # re-run: completed output is returned as-is, counters not re-executed
        second = make_checker(tmpdir, BASELINE_A, 0, "unanchored", 5).run()
        self.assertEqual(second, first)
        self.assertEqual(second["no_candidate"], first["no_candidate"])
        # tampered baseline (different points) breaks the binding: fresh run
        tampered = os.path.join(tmpdir, "baseline.json")
        with open(tampered, "w") as handle:
            json.dump(
                {"points": [list(p) for p in BASELINE_A[:-1]], "grid": 5}, handle
            )
        with self.assertRaisesRegex(ValueError, "checkpoint binding mismatch"):
            make_checker(tmpdir, BASELINE_A[:-1], 0, "unanchored", 5).run()
        with self.assertRaisesRegex(ValueError, "checkpoint binding mismatch"):
            make_checker(tmpdir, BASELINE_A[:-1], 1, "unanchored", 5).run()

    def test_partial_resume_counts_completed_only(self):
        tmpdir = tempfile.mkdtemp()
        checker = make_checker(tmpdir, BASELINE_BORDER, 1, "unanchored", 5, stop=1)
        partial = checker.run()
        self.assertEqual(partial["status"], "in_progress")
        self.assertEqual(partial["roots_completed"], 1)
        self.assertFalse(partial["no_candidate"])
        with self.assertRaisesRegex(ValueError, "case_stop precedes checkpoint progress"):
            make_checker(tmpdir, BASELINE_BORDER, 1, "unanchored", 5, stop=0).run()
        resumed = make_checker(tmpdir, BASELINE_BORDER, 1, "unanchored", 5).run()
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["roots_total"], len(checker.eligible))
        self.assertEqual(resumed["roots_completed"], resumed["roots_total"])

class TestVerifyAndCLI(unittest.TestCase):
    def test_verify_set_detects_bad_triples(self):
        good = gic.verify_set([(0, 0), (1, 1), (2, 3), (0, 4)], 5)
        self.assertEqual(good["zero_determinants"], 0)
        bad = gic.verify_set([(0, 0), (1, 1), (2, 2)], 5)
        self.assertEqual(bad["zero_determinants"], 1)

    def test_cli_and_anchor_stop(self):
        tmpdir = tempfile.mkdtemp()
        baseline_path = os.path.join(tmpdir, "baseline.json")
        with open(baseline_path, "w") as handle:
            json.dump(
                {"points": [list(p) for p in BASELINE_BORDER], "grid": 5}, handle
            )
        output_path = os.path.join(tmpdir, "out.json")
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run(
            [
                sys.executable, "-B",
                str(HERE / "grid75_independent_check.py"),
                "--baseline", baseline_path,
                "--k", "1",
                "--mode", "anchored",
                "--output", output_path,
                "--anchor-stop", "1",
            ],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("status=in_progress", proc.stdout)
        with open(output_path) as handle:
            payload = json.load(handle)
        self.assertEqual(payload["status"], "in_progress")
        self.assertEqual(payload["anchors_completed"], 1)
        self.assertFalse(payload["no_candidate"])
        self.assertEqual(payload["claim_scope"], "fixed-baseline local only")
        self.assertEqual(len(payload["baseline_sha256"]), 64)
        self.assertEqual(len(payload["code_sha256"]), 64)
        # full run to completion
        proc = subprocess.run(
            [
                sys.executable, "-B",
                str(HERE / "grid75_independent_check.py"),
                "--baseline", baseline_path,
                "--k", "1",
                "--mode", "anchored",
                "--output", output_path,
            ],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(output_path) as handle:
            payload = json.load(handle)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["anchors_completed"], payload["anchors_total"])
        self.assertTrue(payload["no_candidate"])


if __name__ == "__main__":
    unittest.main()
