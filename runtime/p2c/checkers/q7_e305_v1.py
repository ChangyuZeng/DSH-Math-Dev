"""Exact OP-000007 Q7 C4-free witness checker; stdlib only."""

from itertools import combinations
import hashlib
import json
from pathlib import Path
import sys

CHECKER_ID = "q7_e305_v1"
VERSION = "1"
MAX_INPUT_BYTES = 65536
STATEMENT = "Construct at least 305 distinct lexicographically ordered Q7 edges with no four-cycle, without odd-square, degree, orbit, bipartition, or symmetry restrictions."
FORMAL_SCOPE = "Formal OP-000007 submission: 305..448 lexicographic Q7 edges and all 672 two-dimensional faces; a program pass is a candidate pending scientific review."
RESEARCH_SCOPE = "Research-only Q7 baseline or partial witness: 0..304 lexicographic C4-free edges and all 672 faces; never a formal 305 submission or a global exclusion."
FORMAL_CRITERIA = ["Supply at least 305 valid distinct lexicographically ordered edges and leave every one of the 672 faces with at most three selected edges."]
RESEARCH_CRITERIA = ["Check a baseline or partial C4-free edge set for research feedback only; do not claim the 305 target or nonexistence."]
PROBLEM_FIELDS = {"statement": STATEMENT, "quantifiers": "there exists a C4-free edge subset of Q7 with at least 305 edges",
                  "domain": "the 448 oriented edges of the 128-vertex seven-dimensional hypercube", "conventions": ["u<v and popcount(u xor v)=1; every C4 is one of 672 two-dimensional faces"],
                  "completion_criteria": FORMAL_CRITERIA}
SCOPES = {
    FORMAL_SCOPE: {"id": "formal", "checks": ["edge_domain", "lexicographic", "c4_free", "target_met"], "values": ["valid", "target_met", "edge_count", "faces_checked"], "timeout": 30},
    RESEARCH_SCOPE: {"id": "research", "checks": ["edge_domain", "lexicographic", "c4_free", "target_met"], "values": ["valid", "target_met", "edge_count", "faces_checked"], "timeout": 30},
}


def obligation_fields(scope):
    return {"statement": STATEMENT, "scope": scope,
            "completion_criteria": FORMAL_CRITERIA if scope == FORMAL_SCOPE else RESEARCH_CRITERIA,
            "check_method": "program", "checker_id": CHECKER_ID}


def target_met_for_count(edge_count, c4_free=True):
    return type(edge_count) is int and edge_count >= 305 and c4_free is True


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
        if type(witness) is not dict or set(witness) != {"edges"} or type(witness["edges"]) is not list:
            return result
        edges = witness["edges"]
        if not (305 <= len(edges) <= 448 if formal else 0 <= len(edges) <= 304):
            return result
        normalized = []
        for edge in edges:
            if type(edge) is not list or len(edge) != 2 or any(type(x) is not int or not 0 <= x <= 127 for x in edge):
                return result
            u, v = edge
            if not u < v or (u ^ v).bit_count() != 1:
                return result
            normalized.append((u, v))
        if normalized != sorted(normalized) or len(set(normalized)) != len(normalized):
            return result
    except (ValueError, UnicodeError, RecursionError):
        return result
    selected = set(normalized)
    faces = 0
    c4_free = True
    for i, j in combinations(range(7), 2):
        for base in range(128):
            if base & ((1 << i) | (1 << j)):
                continue
            a, b, c, d = base, base ^ (1 << i), base ^ (1 << i) ^ (1 << j), base ^ (1 << j)
            square = [tuple(sorted(edge)) for edge in ((a, b), (b, c), (c, d), (d, a))]
            if all(edge in selected for edge in square):
                c4_free = False
                break
            faces += 1
        if not c4_free:
            break
    if c4_free:
        assert faces == 672
    met = target_met_for_count(len(edges), c4_free)
    result.update(verdict="pass" if met else "fail", error_code=None,
                  checks={"edge_domain": True, "lexicographic": True, "c4_free": c4_free, "target_met": met},
                  values={"valid": c4_free, "target_met": met, "edge_count": len(edges), "faces_checked": faces})
    return result


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: q7_e305_v1.py INPUT.json [SCOPE]")
    scope = sys.argv[2] if len(sys.argv) == 3 else FORMAL_SCOPE
    result = evaluate(Path(sys.argv[1]).read_bytes(), scope)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return {"pass": 0, "fail": 1, "error": 2}[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
