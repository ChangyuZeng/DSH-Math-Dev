"""Pinned, standalone integer witness checker; stdlib only, no plugins or shell."""

import hashlib
import json
import math
from pathlib import Path
import sys

CHECKER_ID = "primitive_pythagorean_v1"
VERSION = "1"
MAX_INTEGER = 1_000_000
MAX_INPUT_BYTES = 4096
STATEMENT = "There exist positive integers a, b, c such that a^2 + b^2 = c^2 and gcd(a,b,c) = 1."
SCOPE = "One primitive Pythagorean triple; no minimality, classification, or universal claim."
CRITERIA = ["Supply one exact positive-integer witness satisfying the equation and gcd condition."]
PROBLEM_FIELDS = {"statement": STATEMENT, "quantifiers": "there exist a, b, c",
                  "domain": "positive integers", "conventions": ["Exact integer arithmetic"],
                  "completion_criteria": CRITERIA}
OBLIGATION_FIELDS = {"statement": STATEMENT, "scope": SCOPE, "completion_criteria": CRITERIA,
                     "check_method": "program", "checker_id": CHECKER_ID}


def evaluate(raw):
    result = {"checker_id": CHECKER_ID, "checker_version": VERSION,
              "input_sha256": hashlib.sha256(raw).hexdigest(), "scope": SCOPE,
              "verdict": "error", "error_code": "invalid_input", "checks": None, "values": None}
    if len(raw) > MAX_INPUT_BYTES:
        result["error_code"] = "unsupported"
        return result

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate key")
            value[key] = item
        return value

    def bad_constant(_):
        raise ValueError("Non-finite value")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, UnicodeError, RecursionError):
        return result
    if type(value) is not dict or set(value) != {"a", "b", "c"}:
        return result
    if any(type(x) is not int or x < 1 for x in value.values()):
        return result
    if any(x > MAX_INTEGER for x in value.values()):
        result["error_code"] = "unsupported"
        return result
    a, b, c = value["a"], value["b"], value["c"]
    lhs, rhs, gcd = a * a + b * b, c * c, math.gcd(a, b, c)
    checks = {"pythagorean": lhs == rhs, "primitive": gcd == 1}
    result.update(verdict="pass" if all(checks.values()) else "fail", error_code=None,
                  checks=checks, values={**value, "lhs": lhs, "rhs": rhs, "gcd": gcd})
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: primitive_pythagorean_v1.py INPUT.json")
    result = evaluate(Path(sys.argv[1]).read_bytes())
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return {"pass": 0, "fail": 1, "error": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
