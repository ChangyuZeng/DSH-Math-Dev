"""Exact OP-000006 Ring15 witness checker; stdlib only."""

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

CHECKER_ID = "ring15_d20_v1"
VERSION = "1"
D = 1048576
TARGET = 1179649
MAX_INPUT_BYTES = 65536
STATEMENT = "Construct ordered integer pairs (U_i,V_i), i=1..15, with U_i,V_i>=0, U_i+V_i<=2^20, and H>=1179649 under the original min-over-routes max-over-prefixes definition."
SCOPE = "Formal OP-000006 submission: all 30 ordered integers, all 32768 routes and all 15 prefixes per route; a program pass is a candidate pending scientific review."
CRITERIA = ["Supply pairs as exactly 15 JSON integer pairs and reach H>=1179649 by exhaustive exact integer recomputation."]
PROBLEM_FIELDS = {"statement": STATEMENT, "quantifiers": "there exists an ordered sequence of 15 pairs",
                  "domain": "nonnegative integers on the fixed D=2^20 grid", "conventions": ["Z_i is V_i or -U_i; minimize the maximum absolute cut over all routes and prefixes including k=15"],
                  "completion_criteria": CRITERIA}
OBLIGATION_FIELDS = {"statement": STATEMENT, "scope": SCOPE, "completion_criteria": CRITERIA,
                     "check_method": "program", "checker_id": CHECKER_ID}
SCOPES = {SCOPE: {"id": "formal", "checks": ["pairs_domain", "all_assignments", "target_met"],
                  "values": ["valid", "target_met", "minimum_units", "minimizing_mask", "assignments_checked", "prefixes_checked", "numerator", "denominator"], "timeout": 30}}


def target_met_for_h(units):
    return type(units) is int and units >= TARGET


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


def evaluate(raw, scope=SCOPE):
    result = {"checker_id": CHECKER_ID, "checker_version": VERSION,
              "input_sha256": hashlib.sha256(raw).hexdigest(), "scope": scope,
              "verdict": "error", "error_code": "invalid_input", "checks": None, "values": None}
    if scope != SCOPE or len(raw) > MAX_INPUT_BYTES:
        result["error_code"] = "unsupported" if len(raw) > MAX_INPUT_BYTES else "invalid_input"
        return result
    try:
        witness = parse(raw)
        if type(witness) is not dict or set(witness) != {"pairs"} or type(witness["pairs"]) is not list or len(witness["pairs"]) != 15:
            return result
        pairs = witness["pairs"]
        for pair in pairs:
            if type(pair) is not list or len(pair) != 2 or any(type(x) is not int or not 0 <= x <= D for x in pair):
                return result
            if sum(pair) > D:
                return result
    except (ValueError, UnicodeError, RecursionError):
        return result
    best, minimizing_mask = None, None
    for mask in range(32768):
        z = [v if (mask >> i) & 1 else -u for i, (u, v) in enumerate(pairs)]
        total, prefix, peak = sum(z), 0, 0
        for number in z:
            prefix += number
            peak = max(peak, abs(2 * prefix - total))
        if best is None or peak < best:
            best, minimizing_mask = peak, mask
    met = target_met_for_h(best)
    fraction = Fraction(best, D)
    result.update(verdict="pass" if met else "fail", error_code=None,
                  checks={"pairs_domain": True, "all_assignments": True, "target_met": met},
                  values={"valid": True, "target_met": met, "minimum_units": str(best),
                          "minimizing_mask": minimizing_mask, "assignments_checked": 32768,
                          "prefixes_checked": 491520, "numerator": str(fraction.numerator),
                          "denominator": str(fraction.denominator)})
    return result


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: ring15_d20_v1.py INPUT.json [SCOPE]")
    scope = sys.argv[2] if len(sys.argv) == 3 else SCOPE
    result = evaluate(Path(sys.argv[1]).read_bytes(), scope)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return {"pass": 0, "fail": 1, "error": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
