"""Fixed workflow-v2 certificate checks; no caller-selected source or task data.

The installed package and exported runtime load the public source at one fixed
relative path. An archived checker loads only the sibling named by the embedded
source digest. Both routes verify the bytes before importing them, including in
fresh ``python -I`` processes. No solutions are included in this module.
"""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

VERSION = "workflow-certificate-v2.2"
CERTIFICATE_SOURCE_SHA256 = "239a13acc2ce99834971e0596290f336ec87f22bd4f365bb2525f5dbf48a4271"
TASK_IDS = ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "H1", "H2", "H3", "S1")
PREFIX = "workflow_certificate_v2_"
CHECKS = ["legal", "target_met"]
VALUES = ["valid", "target_met", "H", "D", "task_sha256", "certificate_source_sha256", "detail"]


def source_bytes():
    """Read the pinned dependency from a fixed installation/archive layout."""
    here = Path(__file__).resolve()
    if here.parent.name == "sha256" and here.parent.parent.name == "attachments":
        path = here.parent / CERTIFICATE_SOURCE_SHA256
    elif here.parent.name == "checkers" and here.parent.parent.name == "p2c":
        path = here.parents[3] / "evaluation/optimization-v1/certificates.py"
    elif here.parent.name == "checkers" and here.parent.parent.name == "runtime":
        path = here.parents[2] / "evaluation/optimization-v1/certificates.py"
    else:
        raise ValueError("Unsupported fixed certificate package layout")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CERTIFICATE_SOURCE_SHA256:
        raise ValueError("Pinned public certificate source changed")
    return path, raw


def _source():
    path, raw = source_bytes()
    # Compile the verified bytes, rather than reopening a mutable source path.
    spec = importlib.util.spec_from_loader("_workflow_v2_public_certificates", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module, raw


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def scope_for(task_id):
    return "Exact finite certificate for workflow-v2 task " + task_id + "; local program evidence only."


def _contract(card, raw):
    checker_id = PREFIX + card["id"]
    problem_fields = {
        "statement": card["statement"], "quantifiers": card["quantifiers"], "domain": card["domain"],
        "conventions": [card["certificate"], "Public parameters: " + _canonical(card["parameters"]),
                        "Exact integer verification of the pinned public certificate task."],
        "completion_criteria": ["The exact registered finite certificate check returns target_met=true.",
                                "Program evidence is local and does not constitute human acceptance or a global proof."],
    }
    obligation_fields = {
        "statement": card["statement"], "scope": scope_for(card["id"]),
        "completion_criteria": list(problem_fields["completion_criteria"]),
        "check_method": "program", "checker_id": checker_id,
    }
    # Keep this byte-for-byte aligned with optimization-v1/experiment.task_checker.
    rendered = raw.decode("utf-8").replace("TASK = None", "TASK = " + repr(card), 1).encode("utf-8")
    return {
        "task": deepcopy(card), "problem_fields": problem_fields, "obligation_fields": obligation_fields,
        "problem": {"title": card["id"] + ": " + card["kind"].replace("_", " "), **deepcopy(problem_fields),
                    "target_type": "construct", "parent": None, "relation": "root", "sources": [card["source"]],
                    "knowledge_status": "known_result" if card['id'] in ('D8', 'D9') else "synthetic_fixture"},
        "checker": {"id": checker_id, "code_sha256": hashlib.sha256(rendered).hexdigest(),
                    "task_sha256": card["sha256"]},
        "certificate_source_sha256": CERTIFICATE_SOURCE_SHA256,
    }


def _registered_cards(source):
    # Legacy reserve cards remain unregistered; S1 is acceptance-only.
    return [card for card in source.cards() if card["id"] in TASK_IDS and card["split"] != "reserve"]


def contract(task_id):
    """Return public frozen fields; code_sha256 is the CommonTools rendered hash."""
    if task_id not in TASK_IDS:
        raise ValueError("Unregistered workflow certificate task")
    module, raw = _source()
    return _contract(next(card for card in _registered_cards(module) if card["id"] == task_id), raw)


def registered_specs(module):
    source, raw = _source()
    specs = {}
    for card in _registered_cards(source):
        item = _contract(card, raw)
        specs[item["checker"]["id"]] = {
            "module": module, "path": "workflow_certificate_v2.py", "problem_fields": item["problem_fields"],
            "workflow_contract": item,
            "scopes": {scope_for(card["id"]): {"id": card["id"], "checks": list(CHECKS), "values": list(VALUES),
                                               "timeout": 30, "obligation_fields": item["obligation_fields"]}},
        }
    return specs


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError("Non-finite JSON number")


def evaluate(raw, scope):
    task_id = next((ident for ident in TASK_IDS if scope == scope_for(ident)), None)
    result = {"checker_id": PREFIX + (task_id or "unknown"), "checker_version": VERSION,
              "input_sha256": hashlib.sha256(raw).hexdigest(), "scope": scope,
              "verdict": "error", "error_code": None, "checks": None, "values": None}
    if task_id is None:
        return dict(result, error_code="unsupported")
    try:
        source, _ = _source()
        card = next(card for card in _registered_cards(source) if card["id"] == task_id)
    except (OSError, ValueError, StopIteration):
        return dict(result, error_code="execution_error")
    try:
        candidate = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        return dict(result, error_code="invalid_input")
    detail = "Exact public check(task, candidate)."
    try:
        checked = source.check(card, candidate)
        legal = True
    except (ValueError, TypeError, KeyError, IndexError) as error:
        legal, checked, detail = False, {"H": "unavailable", "D": 1, "target_met": False}, str(error)
    target_met = checked["target_met"] is True
    return dict(result, verdict="pass" if legal and target_met else "fail", error_code=None,
                checks={"legal": legal, "target_met": target_met},
                values={"valid": legal, "target_met": target_met, "H": checked["H"], "D": checked["D"],
                        "task_sha256": card["sha256"], "certificate_source_sha256": CERTIFICATE_SOURCE_SHA256,
                        "detail": detail})


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Expected WITNESS SCOPE")
    result = evaluate(Path(sys.argv[1]).read_bytes(), sys.argv[2])
    print(_canonical(result))
    raise SystemExit({"pass": 0, "fail": 1, "error": 2}[result["verdict"]])
