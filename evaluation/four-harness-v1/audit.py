"""Offline, standard-library-only audit helpers for four-harness-v1.

No run is executed here. ``validate_witness`` recomputes the mathematical
score; ``summarize_runs`` only summarizes caller-reported records, including
caller attestations of independent verification. Such attestations are not
cryptographic proof, so every summary is explicitly labelled ``reported``.

Run records require arm, repeat, status, wall_seconds, total_tokens,
usage_complete, and verified_h. Optional independent_verified defaults to
False. Unknown fields and duplicate (arm, repeat) identities are rejected.
Null usage is unknown, never zero. wall_seconds may be null only for not_run.
"""

import argparse
import itertools
import json
import statistics
import sys

D = 2**20
TARGET = 1179649
ARMS = ("C", "X", "R", "M")
STATUSES = ("success", "not_solved", "infra_failure", "not_run")


def validate_witness(witness):
    """Return {H, D, target_met}; raise ValueError for malformed witnesses.

    Enumerate all 32768 choices and every prefix 1..15 with exact integers.
    The input must be exactly a dict containing only ``pairs``, a list of 15
    two-element lists of nonnegative integers with each pair's sum <= D.
    """
    if type(witness) is not dict or set(witness) != {"pairs"}:
        raise ValueError("witness must be an object containing exactly 'pairs'")
    pairs = witness["pairs"]
    if type(pairs) is not list or len(pairs) != 15:
        raise ValueError("pairs must be a list of exactly 15 pairs")
    for index, pair in enumerate(pairs):
        if type(pair) is not list or len(pair) != 2:
            raise ValueError(f"pairs[{index}] must be a two-element list")
        if any(type(value) is not int or value < 0 for value in pair):
            raise ValueError(f"pairs[{index}] must contain nonnegative integers (not bool)")
        if sum(pair) > D:
            raise ValueError(f"pairs[{index}] sum exceeds D={D}")

    best = None
    # Deliberately direct enumeration, independent of the historic checker.
    for route in itertools.product(*[(-u, v) for u, v in pairs]):
        total = sum(route)
        prefix = 0
        worst = 0
        for value in route:
            prefix += value
            worst = max(worst, abs(2 * prefix - total))
        best = worst if best is None else min(best, worst)
    return {"H": best, "D": D, "target_met": best >= TARGET}


def _validate_record(record, index):
    required = {"arm", "repeat", "status", "wall_seconds", "total_tokens",
                "usage_complete", "verified_h"}
    if (type(record) is not dict or not required <= set(record)
            or set(record) - required - {"independent_verified"}):
        raise ValueError(f"record {index}: invalid or missing fields")
    arm, repeat, status = record["arm"], record["repeat"], record["status"]
    if type(arm) is not str or arm not in ARMS:
        raise ValueError(f"record {index}: arm must be C, X, R, or M")
    if type(repeat) is not int or not 1 <= repeat <= 5:
        raise ValueError(f"record {index}: repeat must be an integer in 1..5")
    if type(status) is not str or status not in STATUSES:
        raise ValueError(f"record {index}: invalid status")
    wall = record["wall_seconds"]
    if wall is None:
        if status != "not_run":
            raise ValueError(f"record {index}: wall_seconds is required for attempted runs")
    elif type(wall) not in (int, float) or not 0 <= wall < float('inf'):
        # Keep overruns as observations; rejecting them would hide failures.
        raise ValueError(f"record {index}: wall_seconds must be finite and nonnegative")
    tokens, h = record["total_tokens"], record["verified_h"]
    for field, value in (("total_tokens", tokens), ("verified_h", h)):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"record {index}: {field} must be a nonnegative integer or null")
    complete = record["usage_complete"]
    independent = record.get("independent_verified", False)
    if type(complete) is not bool or type(independent) is not bool:
        raise ValueError(f"record {index}: verification and usage flags must be booleans")
    if complete and tokens is None:
        raise ValueError(f"record {index}: complete usage requires total_tokens")
    if independent and h is None:
        raise ValueError(f"record {index}: independent verification requires verified_h")
    if status == "not_run" and (h is not None or independent or complete or tokens is not None
                                or wall not in (None, 0)):
        raise ValueError(f"record {index}: not_run must not claim results or usage")
    return dict(record, independent_verified=independent)


def _median(values):
    return statistics.median(values) if values else None


def _aggregate(records, expected):
    attempted = [r for r in records if r["status"] != "not_run"]
    successes = [r for r in records if r["status"] == "success"
                 and r["independent_verified"] and r["verified_h"] >= TARGET]
    complete_tokens = [r["total_tokens"] for r in attempted if r["usage_complete"]]
    known_tokens = [r["total_tokens"] for r in attempted if r["total_tokens"] is not None]
    walls = [r["wall_seconds"] for r in attempted]
    all_usage_complete = bool(attempted) and all(r["usage_complete"] for r in attempted)
    return {
        "quality": "reported",
        "expected_runs": expected,
        "record_count": len(records),
        "missing_record_count": expected - len(records),
        "attempted_count": len(attempted),
        "status_counts": {s: sum(r["status"] == s for r in records) for s in STATUSES},
        "independently_verified_success_count": len(successes),
        "unverified_reported_success_count": sum(r["status"] == "success" for r in records) - len(successes),
        "verified_success_rate_of_expected": len(successes) / expected if len(attempted) == expected else None,
        "sample_complete": len(attempted) == expected,
        "budget_overrun_count": sum(r["wall_seconds"] > 7200 or
                                    (r["total_tokens"] is not None and r["total_tokens"] > 1000000)
                                    for r in attempted),
        "median_success_wall_seconds": _median([r["wall_seconds"] for r in successes]),
        "median_attempted_wall_seconds": _median(walls),
        "complete_usage_count": len(complete_tokens),
        "incomplete_usage_count": sum(not r["usage_complete"] for r in attempted),
        "missing_token_count": sum(r["total_tokens"] is None for r in attempted),
        "median_complete_total_tokens": _median(complete_tokens),
        "known_total_tokens_sum": sum(known_tokens) if known_tokens else None,
        "total_tokens": sum(complete_tokens) if all_usage_complete else None,
        "usage_complete": all_usage_complete,
    }


def summarize_runs(records):
    """Validate a list of reported run records and return overall/by-arm data.

    A success counts only when status == success, independent_verified is
    explicitly True, and verified_h >= TARGET. This is a caller attestation;
    this function cannot verify a witness or provenance from a score alone.
    Reported successes without those conditions are counted as unverified.
    Missing repeats are listed separately from explicit not_run records.
    Token medians use complete usage only; known_total_tokens_sum may be a
    partial lower bound, whereas total_tokens is null if any attempted run
    lacks complete usage. An empty set of observations has null statistics.
    """
    if type(records) is not list:
        raise ValueError("records must be a list")
    clean, seen = [], set()
    for index, record in enumerate(records):
        checked = _validate_record(record, index)
        key = checked["arm"], checked["repeat"]
        if key in seen:
            raise ValueError(f"duplicate run identity: {key[0]} repeat {key[1]}")
        seen.add(key)
        clean.append(checked)
    return {
        "quality": "reported",
        "verification_basis": "caller_attestation_of_independent_check",
        "target_h": TARGET,
        "overall": _aggregate(clean, 20),
        "arms": {arm: _aggregate([r for r in clean if r["arm"] == arm], 5) for arm in ARMS},
        "missing_runs": [{"arm": arm, "repeat": repeat} for arm in ARMS
                         for repeat in range(1, 6) if (arm, repeat) not in seen],
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv=None):
    """Read one JSON file, print JSON; return 0 or 2 for invalid input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-witness", "summarize"))
    parser.add_argument("path", help="local UTF-8 JSON input file")
    args = parser.parse_args(argv)
    try:
        with open(args.path, encoding="utf-8") as handle:
            payload = json.load(handle, object_pairs_hook=_unique_object)
        result = (validate_witness(payload) if args.command == "check-witness"
                  else summarize_runs(payload))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
