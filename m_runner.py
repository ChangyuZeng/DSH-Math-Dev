"""Explicit DSH-MATH-M-PILOT-v1 manifests; no inherited defaults or authorization."""

from pathlib import Path
import re

from backend import checked_root
from schema import decode, fields, identifier, require, sha
from research_store import read_regular

PROTOCOL_ID = "DSH-MATH-M-PILOT-v1"
FORMATS = {"dsh-math-m-run-v1"}
LIMIT_NAMES = "wall_seconds requests tools cpu_seconds memory_bytes artifact_bytes tool_timeout_seconds cleanup_seconds"


def load_manifest(path):
    source = Path(path).absolute()
    require(all(not p.is_symlink() for p in [source, *source.parents]), "UNSAFE_PATH", "Symlink run manifest")
    raw = read_regular(source)
    manifest = decode(raw)
    protocol = manifest.get("protocol_id")
    directed = protocol in ("DSH-MATH-M-SELF-DIRECTED-v1", "DSH-MATH-M-SELF-DIRECTED-v2")
    fields(manifest, "format protocol_id run_id op_id problem_id project_id session_id actor_id mode model effort checker_id contract materials workspace output_dir limits execution_authorized authorization_reference stop_rules" +
           (" provider requested_model_label code source_refresh review_policy" if directed else ""))
    require(manifest["format"] in FORMATS and (directed or manifest["protocol_id"] == PROTOCOL_ID), "RUN_CONTRACT", "Wrong M run protocol")
    for name in ("run_id", "project_id", "session_id", "actor_id"):
        identifier(manifest[name])
    require(re.fullmatch(r"OP-00000[678]", manifest["op_id"]) is not None, "RUN_CONTRACT", "Unexpected OP id")
    identifier(manifest["problem_id"])
    require(manifest["mode"] in ("zero_model_acceptance", "proposed_m_research", "live_m_research"), "RUN_CONTRACT", "Unknown run mode")
    require(type(manifest["execution_authorized"]) is bool, "RUN_CONTRACT", "Authorization must be explicit")
    require(manifest["model"] is None or type(manifest["model"]) is str, "RUN_CONTRACT", "Invalid model identity")
    require(manifest["effort"] is None or type(manifest["effort"]) is str, "RUN_CONTRACT", "Invalid effort identity")
    if manifest["mode"] == "zero_model_acceptance":
        require(manifest["execution_authorized"] is False and manifest["model"] is None and manifest["effort"] is None
                and manifest["limits"]["requests"] == 0, "RUN_CONTRACT", "Zero-model fixture cannot carry live identity or requests")
    if manifest["execution_authorized"]:
        require(manifest["mode"] == "live_m_research" and manifest["model"] and manifest["effort"]
                and type(manifest["authorization_reference"]) is str and manifest["authorization_reference"].strip(),
                "RUN_CONTRACT", "Live runs require a separate explicit authorization reference")
    else:
        require(manifest["mode"] != "live_m_research" and manifest["authorization_reference"] is None,
                "RUN_CONTRACT", "Unapproved runs remain proposed or zero-model")
    require(manifest["checker_id"] in ("ring15_d20_v1", "q7_e305_v1", "grid75_p149_v1"), "RUN_CONTRACT", "Unexpected checker")
    for name in ("workspace", "output_dir"):
        require(type(manifest[name]) is str and Path(manifest[name]).is_absolute(), "UNSAFE_PATH", "Host paths must be absolute in the manifest")
        checked_root(manifest[name])
    fields(manifest["limits"], LIMIT_NAMES)
    totals = ("wall_seconds", "requests", "tools", "cpu_seconds")
    for name, value in manifest["limits"].items():
        require((directed and name in totals and value is None) or (type(value) is int and value >= 0),
                "RUN_CONTRACT", "Limits must be integers; only new-protocol totals may be null")
    if directed:
        require(all(manifest["limits"][name] is None for name in totals if name != "requests")
                and manifest["limits"]["requests"] == (0 if manifest["mode"] == "zero_model_acceptance" else None),
                "RUN_CONTRACT", "Self-directed run must explicitly declare uncapped totals")
        routes = {"gpt6.1-sol-max": ("gpt-6.1-sol", "max"), "gpt6-astra-max": ("gpt-6-astra", "max"), "gpt6-astra-ultra": ("gpt-6-astra", "ultra")}
        require(manifest["provider"] == "openai-codex" and manifest["requested_model_label"] in routes
                and (protocol != "DSH-MATH-M-SELF-DIRECTED-v1" or manifest["requested_model_label"] == "gpt6.1-sol-max")
                and manifest["review_policy"] == "deferred_needs_review", "RUN_CONTRACT", "Changed user-selected route or review policy")
        if manifest["mode"] == "live_m_research":
            require((manifest["model"], manifest["effort"]) == routes[manifest["requested_model_label"]], "RUN_CONTRACT", "Changed model/effort")
        require(type(manifest["code"]) is list and manifest["code"], "RUN_CONTRACT", "Frozen code descriptors required")
        fields(manifest["source_refresh"], "accessed_at record_sha256")
        sha(manifest["source_refresh"]["record_sha256"])
    else:
        require(all(manifest["limits"][name] > 0 for name in ("wall_seconds", "tools", "cpu_seconds"))
                and manifest["limits"]["wall_seconds"] <= 2700 and manifest["limits"]["requests"] <= 24
                and manifest["limits"]["tools"] <= 80 and manifest["limits"]["cpu_seconds"] <= 1800,
                "UNREVIEWED_BUDGET", "Manifest exceeds original pilot caps")
    require(0 < manifest["limits"]["memory_bytes"] <= 2 * 1024**3
            and 0 < manifest["limits"]["artifact_bytes"] <= 256 * 1024**2
            and 0 < manifest["limits"]["tool_timeout_seconds"] <= 60
            and 0 < manifest["limits"]["cleanup_seconds"] <= 120,
            "RUN_CONTRACT", "Machine protection values outside supported range")
    fields(manifest["contract"], "path sha256 bytes")
    require(type(manifest["materials"]) is list, "RUN_CONTRACT", "Materials must be a list")
    descriptors = [manifest["contract"], *manifest["materials"]]
    names = set()
    for descriptor in descriptors:
        fields(descriptor, "path sha256 bytes" if descriptor is manifest["contract"] else "name path sha256 bytes")
        if "name" in descriptor:
            require(re.fullmatch(r"[A-Za-z0-9_.-]+", descriptor["name"]) is not None, "UNSAFE_PATH", "Material names are fixed basenames")
            names.add(descriptor["name"])
        require(type(descriptor["path"]) is str and Path(descriptor["path"]).is_absolute(), "UNSAFE_PATH", "Material path must be host supplied")
        sha(descriptor["sha256"])
        require(type(descriptor["bytes"]) is int and descriptor["bytes"] >= 0, "RUN_CONTRACT", "Invalid material size")
    require(len(names) == len(manifest["materials"]), "RUN_CONTRACT", "Duplicate or invalid materials")
    require(type(manifest["stop_rules"]) is list and manifest["stop_rules"] and all(type(x) is str and x for x in manifest["stop_rules"]),
            "RUN_CONTRACT", "Stop rules are required")
    return manifest, raw


def verify_sources(manifest):
    import hashlib
    for descriptor in [manifest["contract"], *manifest["materials"], *manifest.get("code", [])]:
        require(type(descriptor) is dict and set(descriptor) in ({"path", "sha256", "bytes"}, {"name", "path", "sha256", "bytes"}),
                "RUN_CONTRACT", "Invalid frozen descriptor")
        path = Path(descriptor["path"]).absolute()
        require(all(not p.is_symlink() for p in [path, *path.parents]), "UNSAFE_PATH", "Material symlink")
        raw = read_regular(path)
        require(len(raw) == descriptor["bytes"] and hashlib.sha256(raw).hexdigest() == descriptor["sha256"],
                "SOURCE_CHANGED", "Frozen run material changed")


def manifest_sha256(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()
