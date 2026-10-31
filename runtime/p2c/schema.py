"""P2-C JSON contracts. No SDK, model client, or third-party dependency."""

import hashlib
import json
import re

SCHEMA_VERSION = 3
KINDS = {"Problem", "Note", "Obligation", "Evidence", "Review", "Run"}


class StoreError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise StoreError(code, message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "INVALID_JSON", "Duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise StoreError("INVALID_JSON", "Non-finite JSON value: " + value)

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise StoreError("INVALID_JSON", str(error)) from error


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()),
            "INVALID_SCHEMA", "Expected exactly: " + names)


def string(value, empty=False):
    require(type(value) is str and (empty or bool(value.strip())),
            "INVALID_SCHEMA", "Expected a nonempty string")


def nullable_string(value):
    if value is not None:
        string(value)


def strings(values, nonempty=False):
    require(type(values) is list and (values or not nonempty),
            "INVALID_SCHEMA", "Expected string list")
    for value in values:
        string(value)


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum,
            "INVALID_SCHEMA", "Expected integer >= " + str(minimum))


def identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value),
            "INVALID_SCHEMA", "Invalid identifier")


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value),
            "INVALID_SCHEMA", "Expected SHA256")


def choice(value, options):
    require(type(value) is str and value in options.split(),
            "INVALID_SCHEMA", "Expected one of: " + options)


def ref(value):
    fields(value, "id rev")
    identifier(value["id"])
    integer(value["rev"], 1)


def refs(values):
    require(type(values) is list, "INVALID_SCHEMA", "Expected reference list")
    for value in values:
        ref(value)
    require(len({(v['id'], v['rev']) for v in values}) == len(values),
            "INVALID_SCHEMA", "Duplicate reference")


def context(actor, origin):
    fields(actor, "kind id")
    choice(actor["kind"], "human model system")
    identifier(actor["id"])
    fields(origin, "source run_id session_id tool_call_id")
    string(origin["source"])
    for key in ("run_id", "session_id", "tool_call_id"):
        nullable_string(origin[key])


def attachment(value):
    fields(value, "sha256 relative_path bytes logical_name")
    sha(value["sha256"])
    require(value["relative_path"] == "attachments/sha256/" + value["sha256"],
            "INVALID_SCHEMA", "Attachment path must match its hash")
    integer(value["bytes"])
    string(value["logical_name"])


def validate_data(kind, data):
    require(type(kind) is str and kind in KINDS, "INVALID_SCHEMA", "Unknown object kind")
    if kind == "Problem":
        fields(data, "title statement quantifiers domain conventions target_type parent relation completion_criteria sources knowledge_status")
        for key in ("title", "statement", "quantifiers", "domain"):
            string(data[key])
        strings(data["conventions"])
        choice(data["target_type"], "engineering_fixture")
        choice(data["knowledge_status"], "known_result synthetic_fixture")
        strings(data["completion_criteria"], True)
        strings(data["sources"], True)
        string(data["relation"], empty=data["parent"] is None)
        if data["parent"] is not None:
            ref(data["parent"])
    elif kind == "Note":
        fields(data, "note_type title summary body statement assumptions problem_ref route_id uses candidate_for evidence gaps failure")
        choice(data["note_type"], "argument observation computation source failed_attempt")
        for key in ("title", "summary", "body", "statement"):
            string(data[key])
        for key in ("assumptions", "gaps"):
            strings(data[key])
        ref(data["problem_ref"])
        nullable_string(data["route_id"])
        for key in ("uses", "candidate_for", "evidence"):
            refs(data[key])
        if data["note_type"] == "failed_attempt":
            fields(data["failure"], "inputs location observed negated_scope retry_change")
            for value in data["failure"].values():
                string(value)
        else:
            require(data["failure"] is None, "INVALID_SCHEMA", "Unexpected failure metadata")
    elif kind == "Obligation":
        fields(data, "statement scope completion_criteria check_method checker_id problem_ref uses candidates evidence next_action state reason")
        for key in ("statement", "scope", "next_action", "reason"):
            string(data[key])
        strings(data["completion_criteria"], True)
        choice(data["check_method"], "human program")
        nullable_string(data["checker_id"])
        require((data["checker_id"] is None) == (data["check_method"] == "human"),
                "INVALID_SCHEMA", "Checker id must match check method")
        ref(data["problem_ref"])
        for key in ("uses", "candidates", "evidence"):
            refs(data[key])
        choice(data["state"], "open candidate closed needs_recheck")
        if data["state"] == "candidate":
            require(bool(data["candidates"]), "INVALID_SCHEMA", "Candidate requires a Note")
    elif kind == "Evidence":
        fields(data, "evidence_type problem_ref attachments source source_status support_scope limits production citation")
        choice(data["evidence_type"], "proof_text source_excerpt computation witness figure")
        ref(data["problem_ref"])
        require(type(data["attachments"]) is list, "INVALID_SCHEMA", "Expected attachments")
        for value in data["attachments"]:
            attachment(value)
        choice(data["source_status"], "provided unverified_source")
        require(bool(data["attachments"]) or (data["evidence_type"] == "source_excerpt"
                and data["source_status"] == "unverified_source"),
                "INVALID_SCHEMA", "Evidence without an attachment is only a source lead")
        fields(data["source"], "locator version accessed_at conditions")
        string(data["source"]["locator"])
        for key in ("version", "accessed_at"):
            nullable_string(data["source"][key])
        strings(data["source"]["conditions"])
        string(data["support_scope"])
        strings(data["limits"], True)
        fields(data["production"], "method version")
        for value in data["production"].values():
            string(value)
        citation = data["citation"]
        if data["evidence_type"] != "source_excerpt":
            require(citation is None, "INVALID_SCHEMA", "Citation metadata belongs to source excerpts")
        elif citation is None:
            require(data["source_status"] == "unverified_source", "INVALID_SCHEMA", "Provided source needs precise citation metadata")
        else:
            fields(citation, "title page section theorem excerpt_sha256 condition_map")
            string(citation["title"])
            for name in ("page", "section", "theorem"):
                nullable_string(citation[name])
            require(any(citation[name] for name in ("page", "section", "theorem")),
                    "INVALID_SCHEMA", "Source needs a page, section or theorem locator")
            string(data["source"]["version"])
            sha(citation["excerpt_sha256"])
            require(citation["excerpt_sha256"] in {a["sha256"] for a in data["attachments"]},
                    "INVALID_SCHEMA", "Citation must identify an archived excerpt")
            require(type(citation["condition_map"]) is list, "INVALID_SCHEMA", "Expected condition map")
            mapped = []
            for condition in citation["condition_map"]:
                fields(condition, "condition assessment explanation obligation")
                string(condition["condition"])
                choice(condition["assessment"], "claimed_satisfied unresolved not_satisfied")
                string(condition["explanation"])
                if condition["obligation"] is not None:
                    ref(condition["obligation"])
                require(condition["assessment"] == "claimed_satisfied" or condition["obligation"] is not None,
                        "INVALID_SCHEMA", "Unresolved source conditions need an explicit obligation")
                mapped.append(condition["condition"])
            require(len(mapped) == len(set(mapped)) and set(mapped) == set(data["source"]["conditions"]),
                    "INVALID_SCHEMA", "Map every declared source condition exactly once")
    elif kind == "Review":
        fields(data, "target evidence dependencies method verdict error_code scope findings reviewer checker_version candidate basis basis_hash execution")
        require((type(data["execution"]) is dict) if data["method"] == "program" else data["execution"] is None,
                "INVALID_SCHEMA", "Only program reviews carry execution records")
        ref(data["target"])
        refs(data["evidence"])
        refs(data["dependencies"])
        choice(data["method"], "program model human")
        choice(data["verdict"], "pass fail inconclusive error")
        nullable_string(data["error_code"])
        require((data["error_code"] is not None) == (data["verdict"] == "error"),
                "INVALID_SCHEMA", "error_code must match verdict")
        string(data["scope"])
        strings(data["findings"])
        string(data["reviewer"])
        nullable_string(data["checker_version"])
        if data["candidate"] is not None:
            ref(data["candidate"])
        require(type(data["basis"]) is dict, "INVALID_SCHEMA", "Expected review basis")
        sha(data["basis_hash"])
        require(digest(data["basis"]) == data["basis_hash"], "HASH_MISMATCH", "Review basis hash")
    elif kind == "Run":
        fields(data, "mode session_id model reasoning_effort versions input_seq artifacts usage status stop_reason")
        choice(data["mode"], "offline_fixture")
        for key in ("session_id", "model", "reasoning_effort", "stop_reason"):
            nullable_string(data[key])
        require(data["session_id"] is None and data["model"] is None
                and data["reasoning_effort"] is None,
                "SCOPE_VIOLATION", "P2-C cannot record a fabricated live model run")
        require(type(data["versions"]) is dict, "INVALID_SCHEMA", "Expected versions map")
        for key, value in data["versions"].items():
            string(key)
            string(value)
        integer(data["input_seq"])
        strings(data["artifacts"])
        fields(data["usage"], "input_tokens output_tokens total_tokens")
        require(all(v is None for v in data["usage"].values()),
                "SCOPE_VIOLATION", "Offline Run has no provider usage")
        choice(data["status"], "prepared running interrupted completed")


def reference_fields(kind, data):
    """Reference type constraints; only 'uses' participates in proof cycles."""
    result = []
    if kind == "Problem" and data["parent"] is not None:
        result.append((data["parent"], {"Problem"}))
    if "problem_ref" in data:
        result.append((data["problem_ref"], {"Problem"}))
    for name, allowed in (("uses", {"Note", "Obligation"}),
                          ("evidence", {"Evidence"}),
                          ("candidates", {"Note"}),
                          ("candidate_for", {"Obligation"}),
                          ("dependencies", {"Note", "Obligation"})):
        for value in data.get(name, []):
            result.append((value, allowed))
    if kind == "Evidence" and data["citation"]:
        for condition in data["citation"]["condition_map"]:
            if condition["obligation"]:
                result.append((condition["obligation"], {"Obligation"}))
    if kind == "Review":
        result.append((data["target"], {"Note", "Obligation"}))
        if data["candidate"] is not None:
            result.append((data["candidate"], {"Note"}))
    return result
