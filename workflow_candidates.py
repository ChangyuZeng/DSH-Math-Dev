"""Workflow-v2 candidate evidence and separate, local registered program Reviews.

Callers must already hold workflow_service.transaction(root, project_id). All
identities are host-derived from project/run/call, never from candidate content.
No existing premise is revised. ResearchStore operations provide durable conflict
checks and the existing run_check start/final protocol provides no-replay recovery.

Use certificate_contract('D1') (D1..D6, H1..H3) to prepare the Problem and frozen
execution.checker. ``checker.code_sha256`` is the exact CommonTools rendered task
source hash. ``native_checker_sha256`` is a DIFFERENT hash recorded by run_check;
its source embeds the digest of the imported public certificate dependency.
"""

from copy import deepcopy
import hashlib

from backend import Projects
from checkers.registry import get as get_checker
from checkers import workflow_certificate_v2 as certificates
from checking import checker_hash, run_check, validate_request
from research_store import read_regular, safe_path
from schema import StoreError, canonical, decode, digest, fields, identifier, ref, require, sha
from workflow_context import freshness
from workflow_store import WorkflowStore

CANDIDATE_ACTOR = {"kind": "model", "id": "workflow-v2-candidate"}
RECEIPT_ACTOR = {"kind": "system", "id": "workflow-v2-tool-receipt"}
MAX_BYTES = 2 * 1024 * 1024
LIMITS = ["Model-origin candidate, not human acceptance or a global mathematical proof.",
          "Any program result applies only to the exact archived finite certificate and frozen target."]


def certificate_contract(task_id):
    """Return independent copies of full Problem, fixed Obligation fields and hashes.

    The Obligation fields omit problem_ref/uses/candidates/evidence/state because
    archive_candidate supplies exact newly-created refs. No private answers or
    solve histories are consulted. Reserve tasks are intentionally unregistered.
    """
    try:
        result = certificates.contract(task_id)
    except (OSError, ValueError) as error:
        raise StoreError("CHECKER_BINDING", str(error)) from error
    result["native_checker_sha256"] = checker_hash(result["checker"]["id"])
    return result


def _json_bytes(value):
    def valid(item):
        if type(item) is dict:
            return all(type(key) is str and valid(val) for key, val in item.items())
        if type(item) is list:
            return all(valid(val) for val in item)
        return item is None or type(item) in (bool, int, float, str)
    try:
        require(valid(value), "INVALID_JSON", "Candidate and receipt must contain only JSON values")
        raw = canonical(value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise StoreError("INVALID_JSON", str(error)) from error
    require(len(raw) <= MAX_BYTES, "TOO_LARGE", "Candidate or receipt exceeds archive limit")
    return raw


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _ids(project_id, run_id, call_id):
    require(type(call_id) is str and 0 < len(call_id) <= 200 and
            all(33 <= ord(char) <= 126 for char in call_id), "INVALID_SCHEMA", "Expected a printable ASCII tool call id of at most 200 characters")
    key = digest([project_id, run_id, call_id])
    return {name: prefix + key for name, prefix in {
        "candidate_operation": "wc-candidate-", "evidence": "wc-e-", "provenance": "wc-p-",
        "note": "wc-n-", "obligation": "wc-o-", "receipt_operation": "wc-receipt-",
        "receipt_evidence": "wc-r-", "check_operation": "wc-check-"}.items()}


def _ref(ident):
    return {"id": ident, "rev": 1}


def _context(root, project_id, run_id, action_id, call_id):
    ids = _ids(project_id, run_id, call_id)
    identifier(action_id)
    projects = Projects(root)
    run = WorkflowStore(root).dispatch({"action": "read", "project_id": project_id, "run_id": run_id})
    require(run["spec"]["format"] == "dsh-math-workflow-v2", "UNKNOWN_SCHEMA", "Candidate archival requires workflow-v2")
    action = next((item for item in run["actions"] if item["action_id"] == action_id), None)
    require(run["status"] == "running" and action is not None and action["status"] == "running",
            "INVALID_STATE", "Candidate archival requires an existing running workflow action")
    origin = {"source": "workflow-v2-candidate", "run_id": run_id,
              "session_id": projects.metadata(project_id)["session_id"], "tool_call_id": call_id}
    return projects, run, action, ids, origin


def _target(store, run, action, candidate_kind):
    require(candidate_kind in ("certificate", "proposal"), "INVALID_SCHEMA", "Unknown candidate kind")
    problem_ref = run["spec"]["problem_ref"]
    ref(problem_ref)
    problem = store.get(problem_ref["id"], problem_ref["rev"])
    require(problem["kind"] == "Problem", "REFERENCE_KIND", "Candidate needs an exact Problem revision")
    require(store.get(problem_ref["id"])["rev"] == problem_ref["rev"] and
            not freshness(store.records, action["context_snapshot"]),
            "CONTEXT_STALE", "Frozen problem or watched premise changed; a new action is required")
    frozen = run["spec"]["execution"]["checker"]
    if candidate_kind == "proposal":
        return problem, None
    require(frozen is not None, "CHECKER_BINDING", "A certificate requires a frozen execution checker")
    fields(frozen, "id code_sha256 task_sha256")
    identifier(frozen["id"])
    sha(frozen["code_sha256"])
    sha(frozen["task_sha256"])
    spec = get_checker(frozen["id"])
    if spec is None or "workflow_contract" not in spec:
        return problem, None
    contract = certificate_contract(spec["workflow_contract"]["task"]["id"])
    require(frozen == contract["checker"], "CHECKER_BINDING", "Frozen CommonTools checker/task hash does not match the registered public task")
    require(all(problem["data"].get(key) == value for key, value in contract["problem_fields"].items()),
            "TARGET_MISMATCH", "Problem does not match the exact frozen registered task fields")
    return problem, contract


def _evidence(problem_ref, attachments, evidence_type, locator, support_scope):
    return {"evidence_type": evidence_type, "problem_ref": problem_ref, "attachments": attachments,
            "source": {"locator": locator, "version": "workflow-v2", "accessed_at": None,
                       "conditions": ["Host archived exact supplied content; claims remain scoped to this record."]},
            "source_status": "provided", "support_scope": support_scope, "limits": list(LIMITS),
            "production": {"method": "workflow-v2 immutable archival", "version": "1"}, "citation": None}


def _raw_candidate(candidate, raw_arguments):
    normalized = _json_bytes(candidate)
    if raw_arguments is None:
        return normalized, normalized
    require(type(raw_arguments) in (str, bytes), "INVALID_JSON", "Raw arguments must be UTF-8 JSON text or bytes")
    try:
        raw = raw_arguments.encode("utf-8") if type(raw_arguments) is str else raw_arguments
        raw.decode("utf-8")
    except UnicodeError as error:
        raise StoreError("INVALID_JSON", "Raw arguments are not UTF-8") from error
    require(len(raw) <= MAX_BYTES, "TOO_LARGE", "Raw arguments exceed archive limit")
    try:
        parsed = decode(raw)
    except RecursionError as error:
        raise StoreError("INVALID_JSON", "Raw arguments exceed JSON nesting bounds") from error
    # Tool calls carry exactly {candidate: ...}; proposal outputs may be direct JSON.
    matches = _json_bytes(parsed) == normalized
    if type(parsed) is dict and set(parsed) == {"candidate"}:
        matches = matches or _json_bytes(parsed["candidate"]) == normalized
    require(matches, "CANDIDATE_BINDING", "Raw arguments do not decode to the exact supplied candidate")
    return normalized, raw


def _archive_candidate(store, project_id, run_id, action_id, call_id, run, action, ids,
                       candidate, raw_arguments, candidate_kind):
    problem, contract = _target(store, run, action, candidate_kind)
    normalized, raw = _raw_candidate(candidate, raw_arguments)
    problem_ref = run["spec"]["problem_ref"]
    frozen = run["spec"]["execution"]["checker"]
    scope = contract["obligation_fields"]["scope"] if contract else "External candidate for this exact problem; human review required."
    binding = {"format": "dsh-math-workflow-candidate-v2", "project_id": project_id, "run_id": run_id,
               "action_id": action_id, "call_id": call_id, "candidate_kind": candidate_kind,
               "problem_ref": problem_ref, "checker": frozen, "candidate_sha256": _hash(normalized),
               "candidate_raw_sha256": _hash(raw), "raw_arguments_supplied": raw_arguments is not None,
               "tool_arguments_sha256": digest({"candidate": candidate}),
               "native_checker_sha256": contract["native_checker_sha256"] if contract else None,
               "certificate_source_sha256": contract["certificate_source_sha256"] if contract else None}
    witness = store._archive_bytes(normalized, "candidate.json")
    provenance = [store._archive_bytes(canonical(binding), "candidate-binding.json")]
    if raw_arguments is not None:
        provenance.append(store._archive_bytes(raw, "raw-arguments.json"))
    if contract:
        _, source = certificates.source_bytes()
        provenance.append(store._archive_bytes(source, "certificates.py"))
    evidence_ref, provenance_ref, note_ref, obligation_ref = (_ref(ids[name]) for name in ("evidence", "provenance", "note", "obligation"))
    body = candidate if type(candidate) is str and candidate.strip() else normalized.decode("utf-8")
    note = {"note_type": "computation" if candidate_kind == "certificate" else "argument",
            "title": "Workflow " + candidate_kind + " candidate", "summary": "Exact model-origin candidate retained before verification.",
            "body": body, "statement": problem["data"]["statement"], "assumptions": [],
            "problem_ref": problem_ref, "route_id": action["context_snapshot"]["action"].get("route_id"),
            "uses": [], "candidate_for": [obligation_ref], "evidence": [evidence_ref, provenance_ref],
            "gaps": ["Pending independent review; this candidate has no human or global proof authority."], "failure": None}
    obligation = deepcopy(contract["obligation_fields"]) if contract else {
        "statement": problem["data"]["statement"], "scope": scope,
        "completion_criteria": list(problem["data"]["completion_criteria"]), "check_method": "human", "checker_id": None}
    obligation.update({"problem_ref": problem_ref, "uses": [], "candidates": [note_ref],
                       "evidence": [evidence_ref, provenance_ref], "next_action": "Review the exact retained candidate within the stated local scope.",
                       "state": "candidate", "reason": "Model-origin candidate; no acceptance is implied."})
    changes = [
        {"kind": "Evidence", "id": ids["evidence"], "expected_rev": 0,
         "data": _evidence(problem_ref, [witness], "witness" if candidate_kind == "certificate" else "proof_text",
                           "workflow-candidate:" + ids["candidate_operation"], scope)},
        {"kind": "Evidence", "id": ids["provenance"], "expected_rev": 0,
         "data": _evidence(problem_ref, provenance, "computation", "workflow-candidate-provenance:" + ids["candidate_operation"],
                           "Exact call identity, raw bytes and fixed checker dependency provenance.")},
        {"kind": "Note", "id": ids["note"], "expected_rev": 0, "data": note},
        {"kind": "Obligation", "id": ids["obligation"], "expected_rev": 0, "data": obligation},
    ]
    # Even an idempotent operation must not conceal a later revision of its records.
    require(all(len(store.records.get(change["id"], [])) <= 1 for change in changes),
            "CANDIDATE_BINDING", "Archived candidate records must remain immutable")
    receipt = store.commit(ids["candidate_operation"], changes)
    return {**binding, "evidence_ref": evidence_ref, "provenance_ref": provenance_ref,
            "note_ref": note_ref, "obligation_ref": obligation_ref, "receipt": receipt,
            "authority": "model_candidate", "program_eligible": contract is not None,
            "human_accepted": False, "global_problem_proved": False}


def archive_candidate(root, project_id, run_id, action_id, call_id, candidate,
                      raw_arguments=None, candidate_kind="certificate"):
    """Archive canonical candidate and optional exact raw arguments, idempotently.

    candidate_raw_sha256 hashes raw_arguments when supplied; otherwise it equals
    candidate_sha256. Raw JSON must decode to candidate or exactly {candidate: ...}.
    Python integers are never coerced to floating point. Proposal bodies remain
    complete model-origin Notes with human-check Obligations and no Review.
    """
    projects, run, action, ids, origin = _context(root, project_id, run_id, action_id, call_id)
    with projects.open_project(project_id, writable=True, actor=CANDIDATE_ACTOR, origin=origin) as (_, store):
        return _archive_candidate(store, project_id, run_id, action_id, call_id, run, action, ids,
                                  candidate, raw_arguments, candidate_kind)


def _attachment_bytes(store, obj, name):
    descriptors = [item for item in obj["data"]["attachments"] if item["logical_name"] == name]
    require(len(descriptors) == 1, "CANDIDATE_BINDING", "Missing or ambiguous archived candidate attachment")
    descriptor = descriptors[0]
    store._verify_attachment(descriptor)
    return read_regular(safe_path(store.root, descriptor["relative_path"]))


def _load_candidate(store, project_id, run_id, action_id, call_id, run, action, ids, supplied):
    require(type(supplied) is dict, "CANDIDATE_BINDING", "Expected the exact candidate archive receipt")
    previous = store.operations.get(ids["candidate_operation"])
    require(previous is not None and previous["kind"] == "commit" and previous["actor"] == CANDIDATE_ACTOR
            and previous["origin"] == store.origin, "CANDIDATE_BINDING", "No matching model-origin candidate archive")
    evidence, provenance = (store.get(ids[name], 1) for name in ("evidence", "provenance"))
    binding = decode(_attachment_bytes(store, provenance, "candidate-binding.json"))
    candidate = decode(_attachment_bytes(store, evidence, "candidate.json"))
    raw = _attachment_bytes(store, provenance, "raw-arguments.json") if binding["raw_arguments_supplied"] else None
    expected = _archive_candidate(store, project_id, run_id, action_id, call_id, run, action, ids,
                                  candidate, raw, binding["candidate_kind"])
    require(_json_bytes(supplied) == _json_bytes(expected), "CANDIDATE_BINDING", "Candidate receipt identities, hashes or exact references differ")
    return expected


def _result_binding(result, tool_name, candidate, run_id):
    require(type(result) is dict and type(result.get("ok")) is bool and type(result.get("metrics")) is dict,
            "INVALID_SCHEMA", "Expected a CommonTools {ok,result/error,metrics} receipt")
    fields(result, "ok metrics " + ("result" if result["ok"] else "error"))
    metrics = result["metrics"]
    for key, value in (("run_id", run_id), ("tool", tool_name)):
        require(key not in metrics or metrics[key] == value, "CHECKER_BINDING", "Tool metrics identity differs")
    if "result_sha256" in metrics:
        require(metrics["result_sha256"] == digest({key: value for key, value in result.items() if key != "metrics"}),
                "CHECKER_BINDING", "CommonTools result checksum differs")
    payload = result.get("result", result.get("error"))
    require(type(payload) is dict, "INVALID_SCHEMA", "Expected an object tool result or error")
    checked = payload.get("independent_check") if tool_name == "submit" else payload
    frozen = candidate["checker"]
    expected = {"candidate_sha256": candidate["candidate_sha256"]}
    if frozen:
        expected.update({"checker_sha256": frozen["code_sha256"], "task_sha256": frozen["task_sha256"]})
    if metrics.get("params_sha256") is not None:
        require(metrics["params_sha256"] == candidate["tool_arguments_sha256"],
                "CHECKER_BINDING", "Tool arguments do not match the archived candidate")
    for claimed in (result, payload, checked, metrics):
        if type(claimed) is dict:
            for key, value in expected.items():
                require(key not in claimed or claimed[key] == value, "CHECKER_BINDING", "Tool result " + key + " does not match the archived frozen candidate")
    if not result["ok"]:
        return "inconclusive"
    if tool_name == "submit":
        require(payload.get("run_id") == run_id and type(payload.get("receipt_id")) is str and bool(payload["receipt_id"]),
                "CHECKER_BINDING", "Submission receipt identity differs")
    if candidate["program_eligible"]:
        require(all(payload.get(key) == expected[key] for key in ("candidate_sha256", "checker_sha256")),
                "CHECKER_BINDING", "Registered result must bind candidate and CommonTools checker hashes")
    if type(checked) is not dict or "legal" not in checked:
        require(not candidate["program_eligible"], "INVALID_SCHEMA", "Registered receipt needs an explicit legal status")
        return "inconclusive"
    require(checked["legal"] is None or type(checked["legal"]) is bool, "INVALID_SCHEMA", "Legal status must be bool or null")
    require(type(checked.get("target_met")) is bool and (checked["legal"] is True or checked["target_met"] is False),
            "INVALID_SCHEMA", "Receipt target status is inconsistent")
    if checked["legal"] is None:
        return "inconclusive"
    return "pass" if checked["legal"] and checked["target_met"] else "fail"


def archive_check(root, project_id, run_id, action_id, call_id, candidate_receipt, tool_name, result):
    """Retain the original receipt, then request a separate fixed local Review.

    A check is never a submit. Only the original explicit CommonTools submit can
    determine executor success; neither this return value nor its extra Review
    upgrades external evidence, infra failures, or candidates to formal success.
    A retained incomplete operation is never automatically replayed.
    """
    require(tool_name in ("check", "submit"), "INVALID_SCHEMA", "Only check and submit receipts can be archived")
    result_raw = _json_bytes(result)
    projects, run, action, ids, origin = _context(root, project_id, run_id, action_id, call_id)
    require(tool_name in {tool["name"] for tool in run["spec"]["tools"]},
            "TOOL_CAPABILITY", "Tool was not frozen in this workflow run")
    with projects.open_project(project_id, writable=True, actor=CANDIDATE_ACTOR, origin=origin) as (_, store):
        candidate = _load_candidate(store, project_id, run_id, action_id, call_id, run, action, ids, candidate_receipt)
        result_status = _result_binding(result, tool_name, candidate, run_id)
        if candidate["program_eligible"]:
            validate_request(store, {"checker_id": candidate["checker"]["id"], "target": candidate["obligation_ref"],
                                     "witness": candidate["evidence_ref"]})
        store.actor = deepcopy(RECEIPT_ACTOR)
        store.origin = dict(origin, source="workflow-v2-tool-receipt")
        receipt_ref = _ref(ids["receipt_evidence"])
        binding = {"format": "dsh-math-workflow-check-v2", "candidate": candidate, "tool_name": tool_name,
                   "result_sha256": _hash(result_raw), "result_status": result_status}
        attachments = [store._archive_bytes(result_raw, "common-tools-receipt.json"),
                       store._archive_bytes(canonical(binding), "check-binding.json")]
        already_retained = ids["receipt_operation"] in store.operations
        require(len(store.records.get(ids["receipt_evidence"], [])) <= 1, "CANDIDATE_BINDING", "Archived receipt must remain immutable")
        receipt = store.commit(ids["receipt_operation"], [{"kind": "Evidence", "id": ids["receipt_evidence"], "expected_rev": 0,
            "data": _evidence(run["spec"]["problem_ref"], attachments, "computation", "workflow-tool:" + ids["receipt_operation"],
                              "Original explicit " + tool_name + " receipt; separate from any native local program Review.")}])
        program = {"status": "not_run", "checker_executed": False, "review": None}
        if candidate["program_eligible"]:
            if already_retained and ids["check_operation"] not in store.operations:
                program = {"status": "incomplete", "checker_executed": None, "review": None,
                           "next_action": "Receipt was retained before a native start; a new explicit call id is required. No automatic replay."}
            else:
                program = run_check(store, ids["check_operation"], candidate["checker"]["id"],
                                    candidate["obligation_ref"], candidate["evidence_ref"])
        review = program.get("review")
        return {"candidate_sha256": candidate["candidate_sha256"], "candidate_raw_sha256": candidate["candidate_raw_sha256"],
                "evidence_ref": candidate["evidence_ref"], "provenance_ref": candidate["provenance_ref"],
                "note_ref": candidate["note_ref"], "obligation_ref": candidate["obligation_ref"],
                "receipt_evidence_ref": receipt_ref, "receipt": receipt, "result_sha256": _hash(result_raw),
                "tool_name": tool_name, "explicit_submit": tool_name == "submit", "result_status": result_status,
                "authority": "local_program" if review else "external_inconclusive",
                "receipt_authority": "registered_host_receipt" if candidate["program_eligible"] else "external",
                "program_check": program, "review_ref": {"id": review["id"], "rev": review["rev"]} if review else None,
                "checker": candidate["checker"], "native_checker_sha256": candidate["native_checker_sha256"],
                "formal_success_authority": "original_explicit_submit_only", "human_accepted": False, "global_problem_proved": False}
