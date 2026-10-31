"""Allowlist-only, fixed-sequence export; originals are never rewritten."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid
import xml.etree.ElementTree as ET

from render import render
from research_store import read_regular, safe_path, sync_directory
from schema import SCHEMA_VERSION, canonical, require

ROOT = Path(__file__).resolve().parent
RUNTIME_FILES = ("schema.py", "research_store.py", "domain.py", "checking.py", "render.py",
                 "exporting.py", "verify_export.py", "cli.py", "checkers/primitive_pythagorean_v1.py",
                 "checkers/registry.py", "checkers/ring15_d20_v1.py", "checkers/q7_e305_v1.py", "checkers/grid75_p149_v1.py")
SECRET_KEYS = {"accesstoken", "refreshtoken", "apikey", "clientsecret", "authorization", "password"}


def guard_content(raw):
    """Reject common credential patterns; do not silently redact authoritative bytes."""
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return  # Binary evidence is byte-preserved, not claimed to be content-sanitized.

    def inspect(value):
        if type(value) is dict:
            for k, v in value.items():
                normalized = re.sub(r"[^a-z]", "", k.lower())
                require(not (normalized in SECRET_KEYS and v), "SENSITIVE_CONTENT", "Credential-like field in export data")
                inspect(v)
        elif type(value) is list:
            for item in value:
                inspect(item)
        elif type(value) is str:
            require(not re.search(r"(?i)(?:access_token|refresh_token|api_key|client_secret|authorization|password)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"',}]+", value)
                    and not re.search(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}", value),
                    "SENSITIVE_CONTENT", "Credential-like text in export data")
    try:
        parsed = json.loads(text)
    except ValueError:
        inspect(text)
    else:
        inspect(parsed)


def safe_svg(raw):
    """Only inert mathematical SVG primitives may be automatically embedded."""
    if b"<!" in raw:
        return False
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False
    tags = {"svg", "g", "path", "line", "polyline", "polygon", "text", "rect", "circle", "ellipse", "title", "desc"}
    attrs = {"viewBox", "width", "height", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
             "fill", "stroke", "stroke-width", "font-size", "font-family", "text-anchor", "transform", "role", "aria-label"}
    for node in root.iter():
        if node.tag.removeprefix("{http://www.w3.org/2000/svg}") not in tags:
            return False
        if any(k not in attrs or "url(" in v.lower() or "http:" in v.lower() or "https:" in v.lower() for k, v in node.attrib.items()):
            return False
    return root.tag in ("svg", "{http://www.w3.org/2000/svg}svg")


def export_bundle(store, destination, expected_seq=None):
    require(store.lock_fd is not None, "READ_ONLY", "Open a locked store before export")
    snapshot = store.snapshot()
    require(snapshot["usable"], "EXPORT_UNUSABLE", "Repair log/attachments before exporting")
    require(expected_seq is None or (type(expected_seq) is int and expected_seq == len(store.events)),
            "VERSION_CONFLICT", "Export sequence changed; inspect current state first")
    destination = Path(destination).absolute()
    for parent in [destination, *destination.parents]:
        require(not parent.is_symlink(), "UNSAFE_PATH", "Symlink export destination")
    require(store.root != destination and store.root not in destination.parents,
            "UNSAFE_PATH", "Export outside the authoritative campaign")
    require(destination.parent.is_dir() and not destination.exists(), "EXPORT_EXISTS", "Destination must be new; parent must exist")
    # Re-encode only complete canonical committed events; fixed by the held read/write lock.
    event_bytes = b"".join(canonical(e) + b"\n" for e in store.events)
    payloads = {"research/events.jsonl": event_bytes, "research/.writer.lock": b"",
                "snapshot.json": canonical(snapshot) + b"\n"}
    # Scan event objects separately because events.jsonl is not a single JSON document.
    for event in store.events:
        guard_content(canonical(event))
    figures, descriptors = {}, {}
    for versions in store.records.values():
        for obj in versions:
            if obj["kind"] == "Evidence":
                for descriptor in obj["data"]["attachments"]:
                    name = descriptor["logical_name"].lower()
                    require(not (name == ".env" or name.startswith(".env.") or name in {"auth.json", "oauth.json", "credentials.json"}),
                            "SENSITIVE_CONTENT", "Credential filename cannot be exported as evidence")
                    descriptors[descriptor["sha256"]] = descriptor
                    if obj["data"]["evidence_type"] == "figure" and name.endswith(".svg"):
                        raw = read_regular(safe_path(store.root, descriptor["relative_path"]))
                        if safe_svg(raw):
                            figures[descriptor["sha256"]] = "figures/" + descriptor["sha256"] + ".svg"
                            payloads[figures[descriptor["sha256"]]] = raw
    checker_sources = {}
    for event in store.events:
        if event["control"].get("action") == "check.started":
            desc = event["control"]["checker_source"]
            descriptors[desc["sha256"]] = desc
            checker_sources[desc["sha256"]] = "checker-sources/" + desc["sha256"] + ".py"
    for sha256, descriptor in descriptors.items():
        raw = read_regular(safe_path(store.root, descriptor["relative_path"]))
        store._verify_attachment(descriptor)
        if sha256 not in checker_sources:
            guard_content(raw)
        payloads["research/" + descriptor["relative_path"]] = raw
        if sha256 in checker_sources:
            payloads[checker_sources[sha256]] = raw
    for name, text in render(store, figures=figures).items():
        payloads[name] = text.encode("utf-8")
    for name in RUNTIME_FILES:
        payloads["runtime/" + name] = read_regular(safe_path(ROOT, name))
    checks = []
    for versions in store.records.values():
        obj = versions[0]
        if obj["kind"] != "Review" or obj["data"]["method"] != "program":
            continue
        execution = obj["data"]["execution"]
        checks.append({"review_id": obj["id"], "checker_id": execution["checker_id"],
                       "input_path": "research/attachments/sha256/" + execution["input_sha256"],
                       "checker_path": checker_sources[execution["checker_sha256"]],
                       "expected_result": execution["result"], "expected_exit_code": execution["exit_code"],
                       "recompute": execution["result"]["error_code"] != "execution_error"})
    manifest = {"format": "math-harness-p2c-export-v1", "schema_version": SCHEMA_VERSION,
                "captured_seq": len(store.events), "head_hash": store.head_hash,
                "state_sha256": snapshot["state_sha256"], "model_requests": 0,
                "environment": {"python": sys.version, "platform": sys.platform}, "checks": checks,
                "omitted": {"dsh_session": "not_collected_offline_stage", "public_stream": "not_collected_offline_stage",
                            "provider_usage": "not_available", "staging_and_unreferenced_files": "excluded",
                            "oauth_environment_other_projects": "excluded", "raw_recovery_backups": "excluded_metadata_retained"},
                "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                          for name, raw in sorted(payloads.items())}}
    payloads["manifest.json"] = canonical(manifest) + b"\n"
    temporary = destination.parent / (".export-" + str(uuid.uuid4()))
    temporary.mkdir(mode=0o700)
    # Failures retain a uniquely named partial export; an existing destination is never overwritten.
    for name, raw in payloads.items():
        path = safe_path(temporary, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    for path in sorted((p for p in temporary.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        sync_directory(path)
    sync_directory(temporary)
    require(not destination.exists(), "EXPORT_EXISTS", "Destination appeared during export")
    os.rename(temporary, destination)
    sync_directory(destination.parent)
    return {"status": "exported", "directory": str(destination), "captured_seq": len(store.events),
            "state_sha256": snapshot["state_sha256"], "files": len(payloads), "model_requests": 0}
