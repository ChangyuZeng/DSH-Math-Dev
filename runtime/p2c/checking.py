"""Host-owned, two-commit checker execution with an explicit incomplete state."""

from copy import deepcopy
import hashlib
from pathlib import Path
import platform
import subprocess
import sys

from checkers.primitive_pythagorean_v1 import (CHECKER_ID, VERSION, SCOPE,
                                              PROBLEM_FIELDS, OBLIGATION_FIELDS)
from checkers.registry import get as get_checker
from domain import ResearchView
from schema import (StoreError, attachment, canonical, context, decode, digest, fields, identifier, ref,
                    require, sha, string)

CHECKER = Path(__file__).resolve().parent / "checkers/primitive_pythagorean_v1.py"
ACTOR = {"kind": "system", "id": "fixed-witness-checker"}
ORIGIN = {"source": "local-fixed-checker", "run_id": None, "session_id": None, "tool_call_id": None}


def checker_hash(checker_id=CHECKER_ID):
    spec = get_checker(checker_id)
    require(spec is not None, "UNSUPPORTED_CHECKER", "Unknown fixed checker")
    return hashlib.sha256((CHECKER.parent / spec["path"]).read_bytes()).hexdigest()


def review_id(operation_id):
    return "check-" + digest(operation_id)[:48]


def validate_request(store, request):
    fields(request, "checker_id target witness")
    identifier(request["checker_id"])
    ref(request["target"])
    ref(request["witness"])
    spec = get_checker(request["checker_id"])
    require(spec is not None, "UNSUPPORTED_CHECKER", "Unknown fixed checker")
    target = store.get(request["target"]["id"], request["target"]["rev"])
    require(target["kind"] == "Obligation", "REFERENCE_KIND", "Expected an obligation")
    data = target["data"]
    scope_spec = get_checker(request["checker_id"], data["scope"])
    require(scope_spec is not None and data["checker_id"] == request["checker_id"],
            "TARGET_MISMATCH", "Obligation scope is not registered for this checker")
    problem = store.get(data["problem_ref"]["id"], data["problem_ref"]["rev"])["data"]
    require(all(data[k] == v for k, v in scope_spec["obligation_fields"].items())
            and all(problem[k] == v for k, v in spec["module"].PROBLEM_FIELDS.items()),
            "TARGET_MISMATCH", "Checker only accepts its exact frozen Problem and Obligation contract")
    require(request["witness"] in data["evidence"], "TARGET_MISMATCH", "Witness must be linked to this obligation revision")
    witness = store.get(request["witness"]["id"], request["witness"]["rev"])
    require(witness["kind"] == "Evidence" and witness["data"]["evidence_type"] == "witness"
            and witness["data"]["problem_ref"] == data["problem_ref"]
            and len(witness["data"]["attachments"]) == 1,
            "TARGET_MISMATCH", "Expected one exact witness attachment for this problem version")
    return witness["data"]["attachments"][0], spec, scope_spec


def validate_start(store, control, actor):
    fields(control, "action request requested_by requested_origin basis basis_hash checker_sha256 input_sha256 checker_source")
    require(actor == ACTOR, "FORBIDDEN", "Only the fixed host executor can start a checker")
    context(control["requested_by"], control["requested_origin"])
    descriptor, spec, scope_spec = validate_request(store, control["request"])
    view = ResearchView(store, check_files=False)
    basis = view.basis(control["request"]["target"])
    require(basis == control["basis"] and digest(basis) == control["basis_hash"], "REVIEW_CONFLICT", "Changed check basis")
    require(not view.blockers(control["request"]["target"]), "REVIEW_BLOCKED", "Check target or premises are stale")
    sha(control["checker_sha256"])
    attachment(control["checker_source"])
    require(control["checker_sha256"] == control["checker_source"]["sha256"], "HASH_MISMATCH", "Checker source differs")
    require(descriptor["sha256"] == control["input_sha256"], "HASH_MISMATCH", "Check input binding differs")
    require(control["checker_sha256"] == checker_hash(control["request"]["checker_id"]),
            "HASH_MISMATCH", "Checker identity differs from the registered source")


def validate_result(result, input_hash):
    fields(result, "checker_id checker_version input_sha256 scope verdict error_code checks values")
    spec = get_checker(result["checker_id"])
    require(spec is not None, "INVALID_CHECK_RESULT", "Unknown checker identity")
    scope_spec = get_checker(result["checker_id"], result["scope"])
    require(scope_spec is not None and result["checker_version"] == spec["module"].VERSION
            and result["input_sha256"] == input_hash,
            "INVALID_CHECK_RESULT", "Checker identity, scope or input mismatch")
    require(result["verdict"] in ("pass", "fail", "error"), "INVALID_CHECK_RESULT", "Unknown checker verdict")
    if result["verdict"] == "error":
        require(result["error_code"] in ("invalid_input", "unsupported", "execution_error")
                and result["checks"] is None and result["values"] is None,
                "INVALID_CHECK_RESULT", "Error must not fabricate constraint results")
        return
    require(result["error_code"] is None, "INVALID_CHECK_RESULT", "Unexpected error code")
    fields(result["checks"], " ".join(scope_spec["checks"]))
    require(all(type(v) is bool for v in result["checks"].values()), "INVALID_CHECK_RESULT", "Expected boolean constraints")
    fields(result["values"], " ".join(scope_spec["values"]))
    require(all(type(v) in (bool, int, str) for v in result["values"].values()),
            "INVALID_CHECK_RESULT", "Expected exact scalar values")
    if result["checker_id"] == CHECKER_ID:
        require(all(type(v) is int for v in result["values"].values()), "INVALID_CHECK_RESULT", "Expected exact integers")
    else:
        require(type(result["values"]["valid"]) is bool and type(result["values"]["target_met"]) is bool
                and result["values"]["target_met"] == result["checks"]["target_met"],
                "INVALID_CHECK_RESULT", "Inconsistent target status")
    require((result["verdict"] == "pass") == all(result["checks"].values()),
            "INVALID_CHECK_RESULT", "Verdict inconsistent with recorded constraints")


def validate_review(store, data, actor):
    execution = data["execution"] if type(data) is dict and type(data.get("execution")) is dict else {}
    checker_id = execution.get("checker_id")
    spec = get_checker(checker_id)
    require(spec is not None and actor == ACTOR and data["reviewer"] == ACTOR["id"]
            and data["checker_version"] == spec["module"].VERSION,
            "FORBIDDEN", "Program review requires the fixed executor")
    fields(execution, "start_event_id operation_id checker_id checker_sha256 input_sha256 witness stdout stderr exit_code result runtime")
    identifier(execution["operation_id"])
    start = store.operations.get(execution["operation_id"])
    require(start is not None and start["control"].get("action") == "check.started"
            and start["event_id"] == execution["start_event_id"], "INVALID_CHECK_RESULT", "No matching committed start")
    require(not any(v[0]["kind"] == "Review" and v[0]["data"]["method"] == "program"
                    and v[0]["data"]["execution"]["start_event_id"] == start["event_id"] for v in store.records.values()),
            "OPERATION_CONFLICT", "Check attempt already has a final review")
    control = start["control"]
    require(execution["checker_id"] == checker_id
            and execution["checker_sha256"] == control["checker_sha256"]
            and execution["input_sha256"] == control["input_sha256"]
            and execution["witness"] == control["request"]["witness"], "HASH_MISMATCH", "Execution binding differs")
    scope_spec = get_checker(checker_id, data["scope"])
    require(scope_spec is not None, "INVALID_CHECK_RESULT", "Unregistered review scope")
    require(data["target"] == control["request"]["target"] and data["candidate"] is None
            and data["basis"] == control["basis"] and data["basis_hash"] == control["basis_hash"]
            and data["basis"] == ResearchView(store, check_files=False).basis(data["target"])
            and data["dependencies"] == data["basis"]["dependencies"]
            and data["evidence"] == data["basis"]["evidence"],
            "REVIEW_CONFLICT", "Program review must match its exact committed basis")
    validate_result(execution["result"], execution["input_sha256"])
    result = execution["result"]
    require(result["scope"] == data["scope"] and data["verdict"] == result["verdict"]
            and data["error_code"] == result["error_code"],
            "INVALID_CHECK_RESULT", "Review verdict differs from actual result")
    string(execution["stdout"], empty=True)
    string(execution["stderr"], empty=True)
    fields(execution["runtime"], "python platform")
    for value in execution["runtime"].values():
        string(value)
    if result["error_code"] != "execution_error":
        require(decode(execution["stdout"]) == result
                and type(execution["exit_code"]) is int
                and execution["exit_code"] == {"pass": 0, "fail": 1, "error": 2}[result["verdict"]],
                "INVALID_CHECK_RESULT", "Actual stdout / exit status do not match")
    else:
        require(execution["exit_code"] is None or type(execution["exit_code"]) is int,
                "INVALID_CHECK_RESULT", "Invalid failed-process exit status")


def attempts(store):
    final = {v[0]["data"]["execution"]["start_event_id"]: v[0] for v in store.records.values()
             if v[0]["kind"] == "Review" and v[0]["data"]["method"] == "program"}
    return [{"operation_id": e["operation_id"], "start_event_id": e["event_id"],
             "request": deepcopy(e["control"]["request"]),
             "status": "completed" if e["event_id"] in final else "incomplete",
             "review_id": final[e["event_id"]]["id"] if e["event_id"] in final else None}
            for e in store.events if e["control"].get("action") == "check.started"]


def outcome(store, operation_id):
    start = store.operations[operation_id]
    obj = store.records.get(review_id(operation_id))
    if obj:
        return {"status": "completed", "attempt_receipt": start["result"],
                "receipt": store.receipt("finish-" + digest(operation_id)[:48]), "review": deepcopy(obj[0]),
                "checker_executed": True}
    return {"status": "incomplete", "attempt_receipt": start["result"], "review": None,
            "checker_executed": None, "next_action": "Inspect preserved attempt; a new explicit operation id is required to execute again."}


def run_check(store, operation_id, checker_id, target, witness):
    store._writer()
    identifier(operation_id)
    identifier(checker_id)
    ref(target)
    ref(witness)
    request = {"checker_id": checker_id, "target": target, "witness": witness}
    previous = store.operations.get(operation_id)
    if previous:
        control = previous["control"]
        require(control.get("action") == "check.started" and control["request"] == request
                and control["requested_by"] == store.actor and control["requested_origin"] == store.origin,
                "OPERATION_CONFLICT", "Checker operation id already used for another request/context")
        return outcome(store, operation_id)
    spec = get_checker(checker_id)
    if spec is None:
        return {"status": "rejected", "verdict": "error", "error_code": "unsupported", "checker_executed": False}
    descriptor, spec, scope_spec = validate_request(store, request)
    scope = scope_spec["obligation_fields"]["scope"]
    executable = CHECKER.parent / spec["path"]
    require(review_id(operation_id) not in store.records
            and "finish-" + digest(operation_id)[:48] not in store.operations,
            "OPERATION_CONFLICT", "Reserved result identity already exists")
    basis = ResearchView(store).basis(target)
    control = {"action": "check.started", "request": request,
               "requested_by": deepcopy(store.actor), "requested_origin": deepcopy(store.origin),
               "basis": basis, "basis_hash": digest(basis), "checker_sha256": checker_hash(checker_id),
               "input_sha256": descriptor["sha256"],
               "checker_source": store._archive_bytes(executable.read_bytes(), spec["path"])}
    actor, origin = store.actor, store.origin
    store.actor = deepcopy(ACTOR)
    store.origin = {"source": ORIGIN["source"], "run_id": origin["run_id"],
                    "session_id": origin["session_id"], "tool_call_id": origin["tool_call_id"]}
    try:
        store.control(operation_id, control)
        start = store.operations[operation_id]
        stdout, stderr, exit_code = "", "", None
        try:
            require(checker_hash(checker_id) == control["checker_sha256"], "CHECKER_CHANGED", "Checker changed after start")
            store._verify_attachment(descriptor)
            command = [sys.executable, "-I", "-B", str(executable), str(store.root / descriptor["relative_path"])]
            if checker_id != CHECKER_ID:
                command.append(scope)
            process = subprocess.run(command, cwd=store.root, env={"PYTHONDONTWRITEBYTECODE": "1"},
                                     capture_output=True, text=True, timeout=scope_spec["timeout"])
            stdout, stderr, exit_code = process.stdout, process.stderr, process.returncode
            result = decode(stdout)
            validate_result(result, descriptor["sha256"])
            require(exit_code == {"pass": 0, "fail": 1, "error": 2}[result["verdict"]],
                    "INVALID_CHECK_RESULT", "Unexpected checker exit")
        except (OSError, ValueError, StoreError, subprocess.TimeoutExpired) as error:
            if isinstance(error, subprocess.TimeoutExpired):
                def as_text(value):
                    return value.decode("utf-8", errors="backslashreplace") if isinstance(value, bytes) else value or ""
                stdout, stderr = as_text(error.output), as_text(error.stderr)
            stderr += "\nHost execution error: " + type(error).__name__ + ": " + str(error)
            result = {"checker_id": checker_id, "checker_version": spec["module"].VERSION,
                      "input_sha256": descriptor["sha256"], "scope": scope, "verdict": "error",
                      "error_code": "execution_error", "checks": None, "values": None}
        execution = {"start_event_id": start["event_id"], "operation_id": operation_id,
                     "checker_id": checker_id, "checker_sha256": control["checker_sha256"],
                     "input_sha256": descriptor["sha256"], "witness": witness,
                     "stdout": stdout, "stderr": stderr, "exit_code": exit_code, "result": result,
                     "runtime": {"python": platform.python_version(), "platform": sys.platform}}
        data = {"target": target, "candidate": None, "evidence": basis["evidence"], "dependencies": basis["dependencies"],
                "method": "program", "verdict": result["verdict"], "error_code": result["error_code"], "scope": scope,
                "findings": ["Fixed technical witness check only; program pass is a candidate pending scientific review, never mathematical acceptance; a failed witness does not refute existence."],
                "reviewer": ACTOR["id"], "checker_version": spec["module"].VERSION, "basis": basis,
                "basis_hash": digest(basis), "execution": execution}
        store._commit("finish-" + digest(operation_id)[:48],
                      [{"kind": "Review", "id": review_id(operation_id), "expected_rev": 0, "data": data}], review=True)
        return outcome(store, operation_id)
    finally:
        store.actor, store.origin = actor, origin
