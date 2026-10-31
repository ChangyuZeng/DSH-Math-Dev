"""Host-only synchronous run ledger operations. Never registered as model tools."""

import json
from pathlib import Path
import sys

from backend import Projects
from m_runner import load_manifest, manifest_sha256, verify_sources
from run_ledger import Ledger, Halt
from schema import StoreError, decode, fields, require


def dispatch(request):
    fields(request, "manifest manifest_sha256 action args")
    manifest, raw = load_manifest(request["manifest"])
    require(manifest_sha256(raw) == request["manifest_sha256"], "MANIFEST_CHANGED", "Host manifest anchor changed")
    verify_sources(manifest)
    ledger = Ledger(Path(manifest["output_dir"]) / "ledger", manifest)
    try:
        action, args = request["action"], request["args"]
        if action == "open":
            fields(args, "")
        elif action == "reserve":
            fields(args, "request_id model effort")
            if manifest.get("source_refresh") and not any(row["kind"] == "request.reserved" for row in ledger.rows):
                from datetime import datetime, timezone
                accessed = datetime.fromisoformat(manifest["source_refresh"]["accessed_at"])
                age = (datetime.now(timezone.utc) - accessed).total_seconds()
                require(0 <= age <= 86400, "SOURCE_REFRESH_EXPIRED", "Launch source refresh must be within 24 hours")
                refresh_path = Path(manifest["output_dir"]) / "source-refresh.json"
                import hashlib
                require(hashlib.sha256(refresh_path.read_bytes()).hexdigest() == manifest["source_refresh"]["record_sha256"],
                        "SOURCE_CHANGED", "Source refresh receipt changed")
            ledger.reserve_request(args["request_id"], args["model"], args["effort"])
        elif action == "settle":
            fields(args, "request_id usage error")
            ledger.settle_request(args["request_id"], args["usage"], args["error"])
        elif action == "seal":
            fields(args, "reason")
            ledger.seal(args["reason"])
        else:
            raise StoreError("FORBIDDEN", "Unknown host control")
        return ledger.actual()
    finally:
        ledger.close()


if __name__ == "__main__":
    try:
        value = dispatch(decode(sys.stdin.buffer.read()))
        print(json.dumps({"ok": True, "value": value}))
    except (StoreError, Halt, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", type(error).__name__), "message": str(error)}))
        raise SystemExit(1)
