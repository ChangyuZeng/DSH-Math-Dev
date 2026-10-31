"""No-inference closeout receipt; admission is sealed by the host before this runs."""

import argparse
import json
import os
from pathlib import Path
import sys

from m_runner import load_manifest, verify_sources
from run_ledger import Halt, Ledger
from schema import StoreError, canonical, decode, fields, require


def closeout(request):
    fields(request, "manifest reason components timed_out")
    manifest, _ = load_manifest(request["manifest"])
    verify_sources(manifest)
    require(type(request["reason"]) is str and request["reason"], "CLOSEOUT_SCHEMA", "Stop reason required")
    require(type(request["components"]) is dict and request["components"], "CLOSEOUT_SCHEMA", "Owned component receipts required")
    require(all(type(value) is dict and type(value.get("confirmed")) is bool for value in request["components"].values()),
            "CLOSEOUT_SCHEMA", "Each cleanup component needs explicit confirmation")
    require(type(request["timed_out"]) is bool, "CLOSEOUT_SCHEMA", "Timeout state required")
    ledger = Ledger(Path(manifest["output_dir"]) / "ledger", manifest)
    try:
        ledger.seal(request["reason"])
        actual = ledger.actual()
    finally:
        ledger.close()
    confirmed = not request["timed_out"] and all(value["confirmed"] for value in request["components"].values())
    receipt = {"format": "dsh-math-m-closeout-v1", "run_id": manifest["run_id"], "reason": request["reason"],
               "mode": "no_inference", "status": "cleanup_confirmed" if confirmed else "cleanup_unverified",
               "timed_out": request["timed_out"], "components": request["components"], "actual": actual,
               "cleanup_model_requests": 0, "new_checks": 0, "scientific_status": "candidate_pending_review",
               "execution_ready": False, "execution_authorized": False}
    destination = Path(manifest["output_dir"]) / "closeout.json"
    with destination.open("xb") as stream:
        stream.write(canonical(receipt) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.parse_args()
    try:
        print(json.dumps({"ok": True, "value": closeout(decode(sys.stdin.buffer.read()))}, ensure_ascii=False))
    except (StoreError, OSError, ValueError, KeyError, TypeError, Halt) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", type(error).__name__), "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
