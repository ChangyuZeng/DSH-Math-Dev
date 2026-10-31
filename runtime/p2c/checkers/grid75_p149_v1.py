"""Exact OP-000008 no-three-in-line witness checker; stdlib only."""

from itertools import combinations
import hashlib
import json
from pathlib import Path
import sys

CHECKER_ID = "grid75_p149_v1"
VERSION = "1"
MAX_INPUT_BYTES = 65536
STATEMENT = "Construct 149 or 150 distinct lexicographically ordered integer points in {0,...,74}^2 with no three collinear."
FORMAL_SCOPE = "Formal OP-000008 submission: exactly 149 or 150 lexicographic points and every unordered triple; a program pass is a candidate pending scientific review."
RESEARCH_SCOPE = "Research-only grid75 baseline or partial witness: 1..148 lexicographic noncollinear points; never a formal 149/150 submission or a global exclusion."
FORMAL_CRITERIA = ["Supply exactly 149 or 150 distinct in-bounds lexicographically ordered integer points and pass every exact integer cross-product triple check."]
RESEARCH_CRITERIA = ["Check a baseline or partial no-three-in-line point set for research feedback only; do not claim the 149/150 target or nonexistence."]
PROBLEM_FIELDS = {"statement": STATEMENT, "quantifiers": "there exists a set of 149 or 150 points in the 75 by 75 grid",
                  "domain": "integer coordinate pairs with 0<=x,y<=74", "conventions": ["lexicographic ordering by x then y; every rational slope and every unordered triple is checked"],
                  "completion_criteria": FORMAL_CRITERIA}
SCOPES = {
    FORMAL_SCOPE: {"id": "formal", "checks": ["point_domain", "lexicographic", "no_three_collinear", "target_met"], "values": ["valid", "target_met", "point_count", "triples_checked"], "timeout": 30},
    RESEARCH_SCOPE: {"id": "research", "checks": ["point_domain", "lexicographic", "no_three_collinear", "target_met"], "values": ["valid", "target_met", "point_count", "triples_checked"], "timeout": 30},
}


def obligation_fields(scope):
    return {"statement": STATEMENT, "scope": scope,
            "completion_criteria": FORMAL_CRITERIA if scope == FORMAL_SCOPE else RESEARCH_CRITERIA,
            "check_method": "program", "checker_id": CHECKER_ID}


def target_met_for_count(point_count, no_three_collinear=True):
    return type(point_count) is int and point_count in (149, 150) and no_three_collinear is True


def parse(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out
    def constant(_):
        raise ValueError("non-finite JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def evaluate(raw, scope=FORMAL_SCOPE):
    result = {"checker_id": CHECKER_ID, "checker_version": VERSION,
              "input_sha256": hashlib.sha256(raw).hexdigest(), "scope": scope,
              "verdict": "error", "error_code": "invalid_input", "checks": None, "values": None}
    if scope not in SCOPES or len(raw) > MAX_INPUT_BYTES:
        result["error_code"] = "unsupported" if len(raw) > MAX_INPUT_BYTES else "invalid_input"
        return result
    formal = scope == FORMAL_SCOPE
    try:
        witness = parse(raw)
        if type(witness) is not dict or set(witness) != {"points"} or type(witness["points"]) is not list:
            return result
        points = witness["points"]
        if not (len(points) in (149, 150) if formal else 1 <= len(points) <= 148):
            return result
        normalized = []
        for point in points:
            if type(point) is not list or len(point) != 2 or any(type(x) is not int or not 0 <= x <= 74 for x in point):
                return result
            normalized.append((point[0], point[1]))
        if normalized != sorted(normalized) or len(set(normalized)) != len(normalized):
            return result
    except (ValueError, UnicodeError, RecursionError):
        return result
    triples = 0
    valid = True
    for a, b, c in combinations(normalized, 3):
        if (b[0] - a[0]) * (c[1] - a[1]) == (c[0] - a[0]) * (b[1] - a[1]):
            valid = False
            break
        triples += 1
    met = target_met_for_count(len(points), valid)
    result.update(verdict="pass" if met else "fail", error_code=None,
                  checks={"point_domain": True, "lexicographic": True, "no_three_collinear": valid, "target_met": met},
                  values={"valid": valid, "target_met": met, "point_count": len(points), "triples_checked": triples})
    return result


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: grid75_p149_v1.py INPUT.json [SCOPE]")
    scope = sys.argv[2] if len(sys.argv) == 3 else FORMAL_SCOPE
    result = evaluate(Path(sys.argv[1]).read_bytes(), scope)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return {"pass": 0, "fail": 1, "error": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
