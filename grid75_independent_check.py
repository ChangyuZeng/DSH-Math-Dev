#!/usr/bin/env python3
"""Independent offline checker for fixed-baseline swap-neighborhood nonexistence.

Given a no-three-in-line baseline B on an N x N grid (default N = 75), this
checker independently proves or refutes the claim:

    There is no (|B| + 1)-point no-three-in-line set S with
        |S \\ B| <= k + 1   (additions)   and
        |B \\ S| <= k       (deletions).

The checker shares no code with the original Astra search: blockers are rebuilt
from the baseline points with normalized directions, pair compatibility is
checked on demand (forced baseline points on new-point pair lines, exact
determinants for new-point triples), and deletion feasibility is decided by an
independently written edge-branching hitting-set recursion with a
disjoint-edge-packing lower bound.  No pair graph is ever constructed.

Claim scope: fixed-baseline local only.  The result speaks only about sets in
the k-deletion / (k+1)-addition neighborhood of the supplied baseline.
"""

import argparse
import hashlib
import itertools
import json
import math
import os
import sys

CLAIM_SCOPE = "fixed-baseline local only"
DEFAULT_GRID = 75


# --------------------------------------------------------------------------
# loading / hashing
# --------------------------------------------------------------------------

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_sha256():
    return sha256_file(os.path.abspath(__file__))


def load_baseline(path):
    """Load baseline points.  Accepts {"points": [[x, y], ...], "grid": n};
    grid defaults to 75.  Validates range, distinctness, and that no three
    baseline points are collinear (exact integer determinants)."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "points" not in data:
        raise ValueError("baseline JSON must be an object with a 'points' list")
    n = int(data.get("grid", DEFAULT_GRID))
    points = [tuple(p) for p in data["points"]]
    if len(set(points)) != len(points):
        raise ValueError("baseline contains duplicate points")
    for x, y in points:
        if not (isinstance(x, int) and isinstance(y, int)) or not (0 <= x < n and 0 <= y < n):
            raise ValueError("baseline point out of range: %r" % ((x, y),))
    report = verify_set(points, n)
    if report["zero_determinants"] != 0:
        raise ValueError("baseline has collinear triples: %r" % (report["bad_triples"][:3],))
    return points, n


# --------------------------------------------------------------------------
# exact geometry
# --------------------------------------------------------------------------

def collinear(a, b, c):
    """True iff a, b, c are collinear (exact integer arithmetic)."""
    return (b[0] - a[0]) * (c[1] - a[1]) == (b[1] - a[1]) * (c[0] - a[0])


def verify_set(points, n):
    """Check all triples of `points` for collinearity.  Returns a report dict."""
    bad = []
    for a, b, c in itertools.combinations(points, 3):
        if collinear(a, b, c):
            bad.append([a, b, c])
    return {
        "point_count": len(points),
        "triples_checked": math.comb(len(points), 3) if len(points) >= 3 else 0,
        "zero_determinants": len(bad),
        "bad_triples": bad,
    }


def norm_dir(dx, dy):
    """Normalize a direction vector to a canonical signed primitive form."""
    g = math.gcd(dx, dy)
    dx //= g
    dy //= g
    if dx < 0 or (dx == 0 and dy < 0):
        dx = -dx
        dy = -dy
    return dx, dy


def compute_blockers(p, baseline):
    """Independently rebuild the blocker record of an outside point p.

    Groups baseline points by the normalized direction from p; every group has
    at most 2 points (a group of 3 would mean three collinear baseline points
    on one line through p, which this checker's operating assumption forbids),
    and each 2-group is one blocker edge (a pair of baseline points whose line
    with p must lose an endpoint).  Pairs from distinct direction groups are
    automatically disjoint, so the edges form a matching.
    """
    groups = {}
    for i, (a, b) in enumerate(baseline):
        d = norm_dir(a - p[0], b - p[1])
        groups.setdefault(d, []).append(i)
    edges = []
    for idxs in groups.values():
        if len(idxs) > 2:
            raise ValueError(
                "outside point %r sees %d collinear baseline points; "
                "checker assumes direction groups of size <= 2" % (p, len(idxs))
            )
        if len(idxs) == 2:
            edges.append(tuple(sorted(idxs)))
    return tuple(sorted(edges))


def forced_on_line(a, b, base_set, base_index, n):
    """Baseline point indices lying on the full grid line through a and b."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    g = math.gcd(dx, dy)
    dx //= g
    dy //= g
    x, y = a
    while 0 <= x - dx < n and 0 <= y - dy < n:
        x -= dx
        y -= dy
    forced = set()
    while 0 <= x < n and 0 <= y < n:
        if (x, y) in base_set:
            forced.add(base_index[(x, y)])
        x += dx
        y += dy
    return frozenset(forced)


# --------------------------------------------------------------------------
# hitting set (independent implementation)
# --------------------------------------------------------------------------

def _packing_bound(edges):
    """Greedy disjoint-edge-packing lower bound on the cover size of `edges`."""
    used = set()
    count = 0
    for u, v in edges:
        if u not in used and v not in used:
            used.add(u)
            used.add(v)
            count += 1
    return count


def hitting_set(edges, forced, budget):
    """Decide whether the blocker edges can be covered by <= budget deletions.

    `edges` is an iterable of 2-tuples (u, v) of baseline indices; `forced` is
    a set of baseline indices already committed to the deletion set; every edge
    must have at least one endpoint deleted.  Returns a deletion set D with
    forced <= D, |D| <= budget, covering every edge, or None if impossible.

    Method: edge-branching recursion.  An uncovered edge is selected and the
    search branches on deleting either endpoint; vertices already selected
    (forced or chosen earlier) propagate by covering all edges incident to
    them.  A greedy disjoint-edge-packing bound prunes branches that cannot
    meet the budget.
    """
    if len(forced) > budget:
        return None
    remaining = tuple(e for e in edges if e[0] not in forced and e[1] not in forced)

    def recurse(current, chosen):
        if len(chosen) > budget:
            return None
        if not current:
            return chosen
        if _packing_bound(current) + len(chosen) > budget:
            return None
        u, v = current[0]
        # branch 1: delete u (covers every edge incident to u)
        nxt = tuple(e for e in current if e[0] != u and e[1] != u)
        answer = recurse(nxt, chosen | {u})
        if answer is not None:
            return answer
        # branch 2: delete v
        nxt = tuple(e for e in current if e[0] != v and e[1] != v)
        return recurse(nxt, chosen | {v})

    return recurse(remaining, set(forced))


# --------------------------------------------------------------------------
# checker
# --------------------------------------------------------------------------

class IndependentChecker(object):
    """Prove or refute fixed-baseline k-deletion / (k+1)-addition nonexistence."""

    def __init__(self, baseline_path, k, mode, output_path, case_stop=None):
        if mode not in ("anchored", "unanchored"):
            raise ValueError("mode must be 'anchored' or 'unanchored'")
        self.baseline_path = baseline_path
        self.k = int(k)
        self.mode = mode
        self.output_path = output_path
        self.case_stop = case_stop
        self.baseline_sha256 = sha256_file(baseline_path)
        self.code_sha256 = code_sha256()
        self.baseline, self.n = load_baseline(baseline_path)
        self.base_set = set(self.baseline)
        self.base_index = {p: i for i, p in enumerate(self.baseline)}
        # fixed total order on outside points: row-major
        self.outside = [
            (x, y) for x in range(self.n) for y in range(self.n) if (x, y) not in self.base_set
        ]
        self.order = {p: i for i, p in enumerate(self.outside)}
        # per-outside-point blocker records (baseline-index pairs); no graph.
        self.blockers = [compute_blockers(p, self.baseline) for p in self.outside]
        # eligible: a point with more than k blocker edges can never be part of
        # a feasible trade (its edges are disjoint, so each costs one deletion).
        self.eligible = [i for i, e in enumerate(self.blockers) if len(e) <= self.k]
        self.force_cache = {}
        self.counters = self._fresh_counters()
        self.candidate = None

    @staticmethod
    def _fresh_counters():
        return {
            "nodes": 0,
            "triple_pruned": 0,
            "forced_pruned": 0,
            "cover_calls": 0,
            "cover_pruned": 0,
        }

    # -- cached pair forces -------------------------------------------------

    def pair_forced(self, i, j):
        """Forced baseline indices on the line through outside points i, j."""
        key = (i, j) if i < j else (j, i)
        cached = self.force_cache.get(key)
        if cached is None:
            a = self.outside[i]
            b = self.outside[j]
            cached = forced_on_line(a, b, self.base_set, self.base_index, self.n)
            self.force_cache[key] = cached
        return cached

    # -- node classification ------------------------------------------------

    def _classify(self, edges, forced, size):
        """Classify a partial addition set of `size` points.

        Returns ('candidate', D) if the union blocker edges can be hit within
        size - 1 deletions (a >= 149 witness exists), ('prune', None) if they
        cannot be hit within the constant budget k (monotone: supersets only
        add constraints, so the whole branch is dead), else ('expand', None).
        """
        self.counters["cover_calls"] += 1
        if hitting_set(edges, forced, self.k) is None:
            self.counters["cover_pruned"] += 1
            return "prune", None
        self.counters["cover_calls"] += 1
        small = hitting_set(edges, forced, size - 1)
        if small is not None:
            return "candidate", small
        return "expand", None

    # -- candidate reporting ------------------------------------------------

    def _report_candidate(self, chosen, deletions):
        """Extend deletions to exactly s - 1, build the 149-witness, verify it
        with exact determinants, and record it.  Returns True."""
        chosen = list(chosen)
        deletions = set(deletions)
        extra = (len(chosen) - 1) - len(deletions)
        for i in range(len(self.baseline)):
            if extra == 0:
                break
            if i not in deletions:
                deletions.add(i)
                extra -= 1
        if extra != 0:
            raise AssertionError("could not extend deletion set to %d points" % (len(chosen) - 1))
        witness = sorted(
            (set(self.baseline) - {self.baseline[i] for i in deletions})
            | {self.outside[c] for c in chosen}
        )
        check = verify_set(witness, self.n)
        expected = len(self.baseline) + 1
        if len(witness) != expected or check["zero_determinants"] != 0:
            raise AssertionError(
                "candidate failed independent verification: %r" % (check,)
            )
        self.candidate = {
            "added": [list(self.outside[c]) for c in chosen],
            "removed": [list(self.baseline[i]) for i in sorted(deletions)],
            "points": [list(p) for p in witness],
            "check": check,
        }
        return True

    # -- subset enumeration -------------------------------------------------

    def _dfs(self, chosen, edges, forced, allowed):
        """Enumerate extensions of `chosen` from `allowed` in the fixed total
        order, one depth at a time.  Returns True as soon as a candidate is
        found and verified."""
        self.counters["nodes"] += 1
        pts = self.outside
        for pos, v in enumerate(allowed):
            pv = pts[v]
            # on-demand new-point triple determinant checks
            triple = False
            for x in range(len(chosen)):
                px = pts[chosen[x]]
                for y in range(x + 1, len(chosen)):
                    if collinear(px, pts[chosen[y]], pv):
                        self.counters["triple_pruned"] += 1
                        triple = True
                        break
                if triple:
                    break
            if triple:
                continue
            # on-demand pair compatibility: forced baseline points on each
            # line joining v to an already chosen new point
            new_forced = set(forced)
            for u in chosen:
                new_forced |= self.pair_forced(u, v)
            if len(new_forced) > self.k:
                self.counters["forced_pruned"] += 1
                continue
            new_edges = edges | set(self.blockers[v])
            size = len(chosen) + 1
            status, cover = self._classify(new_edges, new_forced, size)
            if status == "candidate":
                return self._report_candidate(chosen + [v], cover)
            if status == "prune":
                continue
            if size < self.k + 1 and self._dfs(chosen + [v], new_edges, new_forced, allowed[pos + 1:]):
                return True
        return False

    def _run_case(self, seed, allowed):
        """Run one root/anchor case.  Returns True if a candidate was found."""
        edges = set()
        forced = frozenset()
        for v in seed:
            for u in seed:
                if u < v:
                    forced = forced | self.pair_forced(u, v)
            edges |= set(self.blockers[v])
        if len(forced) > self.k:
            self.counters["forced_pruned"] += 1
            return False
        status, cover = self._classify(frozenset(edges), forced, len(seed))
        if status == "candidate":
            return self._report_candidate(list(seed), cover)
        if status == "prune":
            return False
        return self._dfs(list(seed), frozenset(edges), forced, allowed)

    # -- anchored mode geometry ---------------------------------------------

    def _border(self):
        """Derive the anchor borders from the baseline: the unique empty row
        and empty column, with every other row and column holding exactly 2
        baseline points."""
        n = self.n
        rows = [0] * n
        cols = [0] * n
        for x, y in self.baseline:
            rows[x] += 1
            cols[y] += 1
        empty_rows = [x for x in range(n) if rows[x] == 0]
        empty_cols = [y for y in range(n) if cols[y] == 0]
        if len(empty_rows) != 1 or len(empty_cols) != 1:
            raise ValueError(
                "anchored mode requires exactly one empty row and one empty column"
            )
        bx, by = empty_rows[0], empty_cols[0]
        if any(rows[x] != 2 for x in range(n) if x != bx) or any(
            cols[y] != 2 for y in range(n) if y != by
        ):
            raise ValueError(
                "anchored mode requires exactly 2 baseline points in every "
                "non-border row and column"
            )
        return bx, by

    def build_cases(self):
        """Build the fixed-ordered list of root/anchor cases.

        Unanchored: one case per eligible point (roots every eligible point).
        Anchored: the corner case first, then every pair (p, q) with p on the
        empty border row, q on the empty border column (corner excluded), in
        fixed total order.  Every 149-trade contains a border point on each
        side (otherwise 74 rows/columns of capacity 2 hold at most 148
        points), so with the corner case and the least-ordered anchor on each
        side this covers every anchored subset exactly once.
        """
        if self.mode == "unanchored":
            return [([v], self.eligible[i + 1:]) for i, v in enumerate(self.eligible)]
        bx, by = self._border()
        eligible_set = set(self.eligible)
        corner_pos = self.order[(bx, by)]
        row_border = [
            self.order[(bx, y)]
            for y in range(self.n)
            if (bx, y) != (bx, by) and self.order[(bx, y)] in eligible_set
        ]
        col_border = [
            self.order[(x, by)]
            for x in range(self.n)
            if (x, by) != (bx, by) and self.order[(x, by)] in eligible_set
        ]
        cases = []
        if corner_pos in eligible_set:
            cases.append(([corner_pos], [v for v in self.eligible if v != corner_pos]))
        for p in row_border:
            for q in col_border:
                # canonical: p / q are the least ordered border points on each
                # side, so later points exclude the corner and earlier border
                # points on their side (eligible indices are order indices)
                allowed = [
                    v
                    for v in self.eligible
                    if v != corner_pos
                    and not (self.outside[v][0] == bx and v <= p)
                    and not (self.outside[v][1] == by and v <= q)
                ]
                cases.append(([p, q], allowed))
        return cases

    # -- checkpointing ------------------------------------------------------

    def _binding(self):
        return {
            "baseline_sha256": self.baseline_sha256,
            "code_sha256": self.code_sha256,
            "grid": self.n,
            "k": self.k,
            "mode": self.mode,
        }

    def _result(self, status, completed, total):
        result = {
            "version": 1,
            "status": status,
            "k": self.k,
            "mode": self.mode,
            "grid": self.n,
            "claim_scope": CLAIM_SCOPE,
            "no_candidate": status == "completed",
            "baseline_sha256": self.baseline_sha256,
            "code_sha256": self.code_sha256,
            "counters": dict(self.counters),
            "candidate": self.candidate,
        }
        if self.mode == "unanchored":
            result["roots_total"] = total
            result["roots_completed"] = completed
        else:
            result["anchors_total"] = total
            result["anchors_completed"] = completed
        return result

    def _atomic_write(self, payload):
        parent = os.path.dirname(os.path.abspath(self.output_path))
        os.makedirs(parent, exist_ok=True)
        tmp = self.output_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
        os.replace(tmp, self.output_path)

    def run(self):
        cases = self.build_cases()
        total = len(cases)
        start = 0
        if os.path.exists(self.output_path):
            with open(self.output_path, "r", encoding="utf-8") as handle:
                prior = json.load(handle)
            if not all(prior.get(key) == value for key, value in self._binding().items()):
                raise ValueError("checkpoint binding mismatch; use a new output path")
            if prior.get("status") in ("completed", "candidate_found"):
                return prior
            if prior.get("status") != "in_progress":
                raise ValueError("invalid checkpoint status")
            prior_done = prior.get("roots_completed") if self.mode == "unanchored" else prior.get("anchors_completed")
            if not isinstance(prior_done, int) or not 0 <= prior_done <= total:
                raise ValueError("invalid checkpoint progress")
            start = prior_done
            for key, value in prior.get("counters", {}).items():
                if key in self.counters:
                    self.counters[key] += value
        limit = total if self.case_stop is None else min(int(self.case_stop), total)
        if limit < start:
            raise ValueError("case_stop precedes checkpoint progress; use a full run or a new output path")
        for index in range(start, limit):
            seed, allowed = cases[index]
            if self._run_case(seed, allowed):
                result = self._result("candidate_found", index + 1, total)
                self._atomic_write(result)
                return result
            # only completed roots/anchors are counted; a partial case is
            # re-run on the next invocation
            self._atomic_write(self._result("in_progress", index + 1, total))
        status = "completed" if limit == total else "in_progress"
        result = self._result(status, limit, total)
        self._atomic_write(result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Independent fixed-baseline swap-neighborhood checker."
    )
    parser.add_argument("--baseline", required=True, help="path to baseline JSON")
    parser.add_argument("--k", required=True, type=int, help="deletion budget")
    parser.add_argument(
        "--mode", required=True, choices=("anchored", "unanchored"), help="search mode"
    )
    parser.add_argument("--output", required=True, help="path to output/checkpoint JSON")
    parser.add_argument(
        "--anchor-stop",
        type=int,
        default=None,
        help="bound the number of roots/anchors (bounded partial testing)",
    )
    args = parser.parse_args(argv)
    checker = IndependentChecker(
        args.baseline, args.k, args.mode, args.output, case_stop=args.anchor_stop
    )
    result = checker.run()
    total = result.get("roots_total", result.get("anchors_total"))
    completed = result.get("roots_completed", result.get("anchors_completed"))
    print(
        "status=%s k=%d mode=%s cases=%s/%s no_candidate=%s"
        % (
            result["status"],
            result["k"],
            result["mode"],
            completed,
            total,
            result["no_candidate"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
