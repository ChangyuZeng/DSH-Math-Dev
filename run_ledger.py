"""Per-run hash-chained reservations; no model transport and no inherited budget."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from schema import canonical, decode, fields, require


class Halt(RuntimeError):
    pass


def replay(path):
    previous = "0" * 64
    rows = []
    raw = Path(path).read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise Halt("torn_event")
    for line in raw.splitlines():
        row = decode(line)
        digest = row.pop("sha256")
        if row["previous"] != previous or row["seq"] != len(rows) + 1 or hashlib.sha256(canonical(row)).hexdigest() != digest:
            raise Halt("event_integrity")
        row["sha256"] = previous = digest
        rows.append(row)
    return rows


class Ledger:
    def __init__(self, root, manifest, *, clock=time.monotonic, wall_clock=time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.manifest = manifest
        self.clock, self.wall_clock = clock, wall_clock
        self.start = clock()
        self.fd = os.open(self.root / "events.jsonl", os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.fd)
            raise Halt("ledger_locked") from error
        self.rows = replay(self.root / "events.jsonl")
        self.seq = len(self.rows)
        self.previous = self.rows[-1]["sha256"] if self.rows else "0" * 64
        identity = {"run_id": manifest["run_id"], "session_id": manifest["session_id"], "project_id": manifest["project_id"]}
        self.identity = identity
        if not self.rows:
            self.started_wall = wall_clock()
            self.event("opened", mode=manifest["mode"], model=manifest["model"], effort=manifest["effort"],
                       execution_authorized=manifest["execution_authorized"], limits=manifest["limits"],
                       started_wall=self.started_wall, real_model_calls=0)
        else:
            first = self.rows[0]
            if first["kind"] != "opened" or any(first[key] != value for key, value in identity.items()) or first["limits"] != manifest["limits"]:
                raise Halt("manifest_identity_mismatch")
            self.started_wall = first["started_wall"]
        self.reason = next((row.get("reason") for row in reversed(self.rows) if row["kind"] in ("stopped", "sealed")), None)

    def event(self, kind, **data):
        row = dict(seq=self.seq + 1, previous=self.previous, elapsed=max(0.0, self.wall_clock() - self.started_wall),
                   kind=kind, **self.identity, **data)
        digest = hashlib.sha256(canonical(row)).hexdigest()
        row["sha256"] = digest
        raw = canonical(row) + b"\n"
        try:
            view = memoryview(raw)
            while view:
                written = os.write(self.fd, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(self.fd)
        except OSError as error:
            self.reason = "audit_failed"
            raise Halt(self.reason) from error
        self.previous, self.seq = digest, self.seq + 1
        self.rows.append(row)
        return row

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def check(self):
        if self.reason:
            raise Halt(self.reason)
        limit = self.manifest["limits"]["wall_seconds"]
        if limit is not None and self.wall_clock() - self.started_wall >= limit:
            self.stop("wall_limit")
        actual = self.actual()
        limit = self.manifest["limits"]["cpu_seconds"]
        if limit is not None and actual["cpu_seconds"] >= limit:
            self.stop("cpu_limit")
        if actual["artifact_bytes"] >= self.manifest["limits"]["artifact_bytes"]:
            self.stop("artifact_limit")

    def stop(self, reason):
        if self.reason is None:
            self.reason = reason
            self.event("stopped", reason=reason, actual=self.actual())
        raise Halt(self.reason)

    def seal(self, reason):
        if self.reason is None:
            self.reason = reason
            self.event("sealed", reason=reason, actual=self.actual())

    def reserve_request(self, identity, model, effort):
        self.check()
        if not self.manifest["execution_authorized"] or self.manifest["mode"] != "live_m_research":
            self.stop("execution_not_authorized")
        if model != self.manifest["model"] or effort != self.manifest["effort"]:
            self.stop("model_identity_mismatch")
        calls = [row for row in self.rows if row["kind"] == "request.reserved"]
        if any(row["request_id"] == identity for row in calls) or any(row["kind"] == "request.reserved" and row["request_id"] not in {r.get("request_id") for r in self.rows if r["kind"] in ("request.finished", "request.settled")} for row in calls):
            self.stop("duplicate_or_parallel_request")
        if self.manifest["limits"]["requests"] is not None and len(calls) >= self.manifest["limits"]["requests"]:
            self.stop("request_limit")
        self.event("request.reserved", request_id=identity, model=model, effort=effort)

    def settle_request(self, identity, usage, error=None):
        pending = [row for row in self.rows if row["kind"] == "request.reserved"]
        settled = {row["request_id"] for row in self.rows if row["kind"] in ("request.finished", "request.settled")}
        if not any(row["request_id"] == identity for row in pending) or identity in settled:
            self.stop("unmatched_response")
        self.event("request.finished", request_id=identity, usage=usage, error=error)
        if error:
            self.stop("technical_failed")
        self.check()

    def tool_start(self, identity, name, arguments):
        prior = [row for row in self.rows if row["kind"] == "tool.started" and row["call_id"] == identity]
        if prior:
            if len(prior) != 1 or prior[0]["name"] != name or prior[0]["arguments"] != arguments:
                self.stop("duplicate_tool_conflict")
            finished = [row for row in self.rows if row["kind"] == "tool.finished" and row["call_id"] == identity]
            if finished:
                return {"status": "completed", "result": finished[-1]["result"], "idempotent": True}
            return {"status": "unknown", "call_id": identity, "idempotent": True, "next_action": "readonly_reconciliation_no_reexecution"}
        self.check()
        used = [row for row in self.rows if row["kind"] == "tool.started"]
        if self.manifest["limits"]["tools"] is not None and len(used) >= self.manifest["limits"]["tools"]:
            self.stop("tool_limit")
        self.event("tool.started", call_id=identity, name=name, arguments=arguments)
        return {"status": "started", "idempotent": False}

    def finish_tool(self, identity, result, *, cpu_seconds=0.0, artifact_bytes=0):
        started = [row for row in self.rows if row["kind"] == "tool.started" and row["call_id"] == identity]
        finished = [row for row in self.rows if row["kind"] == "tool.finished" and row["call_id"] == identity]
        if len(started) != 1 or finished:
            self.stop("unmatched_tool_finish")
        require(type(cpu_seconds) in (int, float) and cpu_seconds >= 0 and type(artifact_bytes) is int and artifact_bytes >= 0,
                "LEDGER_SCHEMA", "Invalid actual resource usage")
        self.event("tool.finished", call_id=identity, result=result, cpu_seconds=cpu_seconds, artifact_bytes=artifact_bytes)
        self.check()

    def actual(self):
        calls = [row for row in self.rows if row["kind"] == "request.reserved"]
        tools = [row for row in self.rows if row["kind"] == "tool.started"]
        finished = [row for row in self.rows if row["kind"] == "tool.finished"]
        usages = [row.get("usage") for row in self.rows if row["kind"] in ("request.finished", "request.settled")]
        known_tokens = sum(row["totalTokens"] for row in usages if type(row) is dict and type(row.get("totalTokens")) is int and row["totalTokens"] >= 0)
        usage_complete = bool(calls) and len(usages) == len(calls) and all(type(row) is dict and type(row.get("totalTokens")) is int for row in usages)
        return {"requests": len(calls), "tools": len(tools), "wall_seconds": max(0.0, self.wall_clock() - self.started_wall),
                "cpu_seconds": sum(row.get("cpu_seconds", 0.0) for row in finished),
                "artifact_bytes": finished[-1].get("artifact_bytes", 0) if finished else 0,
                "known_tokens": known_tokens, "total_tokens": known_tokens if usage_complete else None,
                "usage_complete": usage_complete, "unknown_request_outcomes": len(calls) - len(usages),
                "unknown_tool_outcomes": len(tools) - len(finished)}
