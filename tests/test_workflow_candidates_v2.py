"""Offline candidate/archive boundaries; public invalid baselines, no task solves."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from backend import Projects
from checkers.registry import get as get_checker
from checkers import workflow_certificate_v2 as fixed
from checking import attempts, checker_hash, validate_request
from schema import StoreError, canonical, decode, digest
from workflow_candidates import certificate_contract
from workflow_context import completion_projection
from workflow_service import dispatch_internal
from workflow_store import WorkflowStore
from workflow_v2 import MODEL, TIMEOUTS


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.projects = Projects(self.root)
        self.workflow = WorkflowStore(self.root)
        self.counter = 0
        self.fresh()

    def fresh(self, task="D1", *, checker=None, problem=None, premise=False, start=True):
        self.contract = certificate_contract(task)
        self.project = self.projects.create("candidate-test", problem or self.contract["problem"])["id"]
        self.spec = {"format": "dsh-math-workflow-v2", "project_id": self.project,
                     "problem_ref": {"id": "P", "rev": 1}, "model_policy": deepcopy(MODEL),
                     "budget": {"requests": 120, "total_tokens": None, "max_output_tokens": 128000},
                     "context_budget": {"max_tokens": None}, "stage_goal": "Archive invalid public sanity candidates",
                     "tools": [{"name": name, "version": "v2", "read_only": False,
                                "schema_sha256": "a" * 64, "effect": name} for name in ("check", "submit")],
                     "execution": {"transport": "metered-codex", "accounting_authority": "gateway",
                                   "verification_policy": "model-directed-v1", "max_revisions": 2,
                                   "timeouts": deepcopy(TIMEOUTS), "isolation": "os-process",
                                   "checker": deepcopy(checker or self.contract["checker"])}}
        refs = [{"id": "P", "rev": 1}]
        if premise:
            with self.projects.open_project(self.project, writable=True) as (_, store):
                store.commit("premise", [{"kind": "Note", "id": "N", "expected_rev": 0,
                    "data": {"note_type": "observation", "title": "Premise", "summary": "Unverified premise",
                             "body": "Original body", "statement": "A stated premise", "assumptions": [],
                             "problem_ref": refs[0], "route_id": None, "uses": [], "candidate_for": [],
                             "evidence": [], "gaps": [], "failure": None}}])
            refs.append({"id": "N", "rev": 1})
        self.run = self.call("prepare", spec=self.spec)
        if start:
            self.run = self.call("start")
            self.reserve(refs)
        self.before_problem = self.object({"id": "P", "rev": 1})

    def call(self, action, **extra):
        self.counter += 1
        run = None if action == "prepare" else self.run
        self.run = self.workflow.dispatch({"action": action, "project_id": self.project, "run_id": "r1",
            "operation_id": "op-" + str(self.counter), "expected_rev": run["rev"] if run else 0, **extra})
        return self.run

    def reserve(self, refs=None):
        with self.projects.open_project(self.project) as (_, store):
            state = store.snapshot()["state"]
        context = {"format": "dsh-math-context-v1", "refs": refs or [{"id": "P", "rev": 1}],
                   "research_head": {"seq": state["seq"], "head_hash": state["head_hash"]},
                   "text": "public offline sanity baseline", "sha256": hashlib.sha256(b"public offline sanity baseline").hexdigest(),
                   "input_token_bound": 128, "action": {"kind": "derive", "target_ref": None, "route_id": None}}
        self.call("reserve", kind="derive", context_snapshot=context, max_output_tokens=100)
        self.action_id = self.run["actions"][-1]["action_id"]

    def args(self, call_id="call/with:untrusted..characters"):
        return {"project_id": self.project, "run_id": "r1", "action_id": self.action_id, "call_id": call_id}

    def candidate(self, value=None, *, call_id="call/with:untrusted..characters", **extra):
        return dispatch_internal(self.root, "workflow-candidate",
                                 {**self.args(call_id), "candidate": {"values": []} if value is None else value, **extra})

    def checked(self, receipt, result=None, tool="check", **extra):
        return dispatch_internal(self.root, "workflow-candidate-check", {**self.args(receipt["call_id"]),
            "candidate_receipt": receipt, "tool_name": tool, "result": result or self.result(receipt, tool), **extra})

    def result(self, receipt, tool="check", *, legal=False, target_met=False):
        checked = {"legal": legal, "H": None if legal is not True else 0, "D": 1, "target_met": target_met}
        payload = {"candidate_sha256": receipt["candidate_sha256"], "checker_sha256": receipt["checker"]["code_sha256"]}
        if tool == "submit":
            payload.update(receipt_id="offline-receipt", run_id="r1", independent_check=checked)
        else:
            payload.update(checked)
        return {"ok": True, "result": payload, "metrics": {"run_id": "r1", "tool": tool}}

    def object(self, value):
        with self.projects.open_project(self.project) as (_, store):
            return store.get(value["id"], value["rev"])

    def attachment(self, value, name):
        obj = self.object(value)
        descriptor = next(item for item in obj["data"]["attachments"] if item["logical_name"] == name)
        return (self.projects.path(self.project) / "research" / descriptor["relative_path"]).read_bytes()

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(StoreError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code, str(raised.exception))

    def test_file_source_provenance_and_original_tool_argument_binding(self):
        value = {"values": [2 ** 100 + 1]}
        source = ' { "candidate_file" : {"path":"candidate.json","sha256":"' + 'a' * 64 + '","size":123} } '
        raw = canonical({"candidate": value}).decode()
        receipt = self.candidate(value, raw_arguments=raw, source_arguments=source)
        self.assertEqual(receipt["candidate_sha256"], digest(value))
        self.assertEqual(receipt["source_arguments_sha256"], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(receipt["tool_arguments_sha256"], digest(decode(source.encode())))
        self.assertEqual(self.attachment(receipt["provenance_ref"], "source-arguments.json"), source.encode())
        self.assertEqual(self.attachment(receipt["provenance_ref"], "raw-arguments.json"), raw.encode())
        result = self.result(receipt)
        result["metrics"]["params_sha256"] = receipt["tool_arguments_sha256"]
        self.assertEqual(self.checked(receipt, result)["result_status"], "fail")
        self.error("OPERATION_CONFLICT", self.candidate, value, raw_arguments=raw, source_arguments=source + ' ')

    def test_exact_raw_bytes_bigint_refs_and_model_origin(self):
        number = (1 << 255) + 9007199254740993
        value = {"values": [number]}
        raw = ' { "candidate" : {"values": [' + str(number) + ']} } \n'
        receipt = self.candidate(value, raw_arguments=raw)
        self.assertEqual(receipt["candidate_sha256"], digest(value))
        self.assertEqual(receipt["candidate_raw_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(self.attachment(receipt["evidence_ref"], "candidate.json"), canonical(value))
        self.assertEqual(self.attachment(receipt["provenance_ref"], "raw-arguments.json"), raw.encode())
        for key in ("evidence_ref", "provenance_ref", "note_ref", "obligation_ref"):
            obj = self.object(receipt[key])
            self.assertEqual(obj["rev"], 1)
            self.assertEqual(obj["actor"]["kind"], "model")
            self.assertLessEqual(len(obj["id"]), 128)
            self.assertNotIn("/", obj["id"])
        self.assertEqual(self.object(receipt["note_ref"])["data"]["body"], canonical(value).decode())
        self.assertEqual(self.object(receipt["note_ref"])["data"]["candidate_for"], [receipt["obligation_ref"]])
        self.assertEqual(self.object(receipt["obligation_ref"])["data"]["candidates"], [receipt["note_ref"]])
        self.assertEqual(self.object({"id": "P", "rev": 1}), self.before_problem)
        self.assertFalse(receipt["human_accepted"])
        self.assertFalse(receipt["global_problem_proved"])

    def test_idempotency_is_exact_in_candidate_kind_and_raw_bytes(self):
        raw = '{"candidate":{"values":[]}}'
        first = self.candidate(raw_arguments=raw)
        self.assertEqual(first, self.candidate(raw_arguments=raw))
        self.error("OPERATION_CONFLICT", self.candidate, {"values": [0]}, raw_arguments=None)
        self.error("OPERATION_CONFLICT", self.candidate, raw_arguments=raw + " ")
        self.error("OPERATION_CONFLICT", self.candidate, candidate_kind="proposal", raw_arguments=raw)

    def test_reusing_call_identity_in_a_different_action_conflicts(self):
        receipt = self.candidate()
        self.call("account", action_id=self.action_id, accounting={"format": "dsh-math-physical-accounting-v2", "requests": [
            {"request_id": "offline-row", "logical_request_id": "offline-row", "parent_request_id": None,
             "role": "worker", "status": "finished", "usage_complete": True, "usage": {"input_tokens": 1, "output_tokens": 1,
             "total_tokens": 2, "cached_input_tokens": None, "reasoning_tokens": None}, "raw_usage": {}}]})
        self.call("propose", action_id=self.action_id, proposal={}, usage=None)
        self.call("commit", action_id=self.action_id, output_refs=[receipt["note_ref"]])
        self.reserve()
        self.error("OPERATION_CONFLICT", self.candidate)

    def test_candidate_records_cannot_be_revised_and_reused(self):
        receipt = self.candidate()
        with self.projects.open_project(self.project, writable=True) as (_, store):
            note = store.get(receipt["note_ref"]["id"])
            note["data"]["body"] = "Changed candidate body"
            store.commit("revise-candidate", [{"kind": "Note", "id": note["id"], "expected_rev": 1, "data": note["data"]}])
        self.error("CANDIDATE_BINDING", self.candidate)
        self.error("CANDIDATE_BINDING", self.checked, receipt)

    def test_python_service_roundtrip_preserves_large_integer_and_raw_arguments(self):
        value = {"values": [(1 << 255) + 3]}
        raw = '{ "candidate" : ' + canonical(value).decode() + ' }\n'
        request = {"action": "workflow-candidate", "args": {**self.args(), "candidate": value, "raw_arguments": raw}}
        process = subprocess.run([sys.executable, "-B", str(HERE / "workflow_service.py"), str(self.root)],
                                 input=canonical(request), capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        response = decode(process.stdout)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["value"]["candidate_sha256"], digest(value))
        self.assertEqual(decode(self.attachment(response["value"]["evidence_ref"], "candidate.json")), value)
        self.assertEqual(self.attachment(response["value"]["provenance_ref"], "raw-arguments.json"), raw.encode())

    def test_raw_arguments_mismatch_duplicate_keys_and_numeric_type_rejected(self):
        self.error("CANDIDATE_BINDING", self.candidate, {"values": [1]}, raw_arguments='{"candidate":{"values":[true]}}')
        self.error("CANDIDATE_BINDING", self.candidate, {"values": [9007199254740993]},
                   raw_arguments='{"candidate":{"values":[9007199254740992]}}')
        self.error("INVALID_JSON", self.candidate, raw_arguments='{"candidate":{},"candidate":{"values":[]}}')
        self.error("CANDIDATE_BINDING", self.candidate, raw_arguments='{"candidate":{"values":[]},"extra":true}')
        self.error("INVALID_JSON", self.candidate, raw_arguments=b"\xff")

    def test_call_id_bounds(self):
        for call_id in ("", "x" * 201, "white space", "\x00", "中文"):
            self.error("INVALID_SCHEMA", self.candidate, call_id=call_id)
        self.assertEqual(self.candidate(call_id="/" * 200)["call_id"], "/" * 200)

    def test_registered_wrong_checker_and_task_hashes_rejected(self):
        for name in ("code_sha256", "task_sha256"):
            with self.subTest(name=name):
                checker = dict(self.contract["checker"], **{name: "0" * 64})
                self.fresh(checker=checker)
                self.error("CHECKER_BINDING", self.candidate)

    def test_registered_problem_fields_all_remain_exact(self):
        for key in self.contract["problem_fields"]:
            with self.subTest(field=key):
                problem = deepcopy(self.contract["problem"])
                problem[key] = problem[key] + (["changed"] if type(problem[key]) is list else " changed")
                self.fresh(problem=problem)
                self.error("TARGET_MISMATCH", self.candidate)
        self.fresh(checker=certificate_contract("D2")["checker"])
        self.error("TARGET_MISMATCH", self.candidate)

    def test_native_checker_rejects_altered_obligation_contract(self):
        candidate = self.candidate()
        for key in ("statement", "scope", "completion_criteria"):
            with self.subTest(key=key):
                with self.projects.open_project(self.project, writable=True) as (_, store):
                    data = store.get(candidate["obligation_ref"]["id"])["data"]
                    data[key] = data[key] + (["changed"] if type(data[key]) is list else " changed")
                    target = {"id": "altered-" + key, "rev": 1}
                    store.commit(target["id"], [{"kind": "Obligation", "id": target["id"], "expected_rev": 0, "data": data}])
                    self.error("TARGET_MISMATCH", validate_request, store,
                               {"checker_id": candidate["checker"]["id"], "target": target, "witness": candidate["evidence_ref"]})

    def test_running_action_and_problem_required(self):
        self.error("INVALID_STATE", dispatch_internal, self.root, "workflow-candidate",
                   {**self.args(), "action_id": "missing", "candidate": {"values": []}})
        self.call("fail", action_id=self.action_id, reason="Offline interrupted action")
        self.error("INVALID_STATE", self.candidate)

    def test_watched_premise_and_problem_revisions_rejected(self):
        for kind in ("Note", "Problem"):
            with self.subTest(kind=kind):
                self.fresh(premise=True)
                receipt = self.candidate()
                ident = "N" if kind == "Note" else "P"
                with self.projects.open_project(self.project, writable=True) as (_, store):
                    data = store.get(ident)["data"]
                    data["body" if kind == "Note" else "statement"] += " changed"
                    store.commit("change-premise", [{"kind": kind, "id": ident, "expected_rev": 1, "data": data}])
                self.error("CONTEXT_STALE", self.candidate)
                self.error("CONTEXT_STALE", self.checked, receipt)

    def test_full_proposal_is_archived_without_review(self):
        proposal = {"changes": [{"kind": "Note", "data": {"body": "Full mathematical argument.\nAssumptions and a gap."}}],
                    "next_action": "stop", "large_integer": (1 << 256) - 1}
        receipt = self.candidate(proposal, candidate_kind="proposal", raw_arguments=canonical(proposal).decode())
        self.assertEqual(decode(self.object(receipt["note_ref"])["data"]["body"]), proposal)
        self.assertEqual(self.object(receipt["obligation_ref"])["data"]["check_method"], "human")
        self.assertFalse(receipt["program_eligible"])
        with self.projects.open_project(self.project) as (_, store):
            self.assertFalse(any(rows[0]["kind"] == "Review" for rows in store.records.values()))
        prose = "A full prose argument\nwith a declared unresolved gap."
        other = self.candidate(prose, candidate_kind="proposal", call_id="prose")
        self.assertEqual(self.object(other["note_ref"])["data"]["body"], prose)

    def test_bound_receipt_gets_a_separate_real_program_review(self):
        candidate = self.candidate()
        result = self.result(candidate)
        receipt = self.checked(candidate, result)
        self.assertEqual(receipt["result_status"], "fail")
        self.assertEqual(receipt["authority"], "local_program")
        self.assertFalse(receipt["explicit_submit"])
        self.assertFalse(receipt["human_accepted"])
        self.assertFalse(receipt["global_problem_proved"])
        self.assertEqual(self.attachment(receipt["receipt_evidence_ref"], "common-tools-receipt.json"), canonical(result))
        review = self.object(receipt["review_ref"])
        self.assertEqual(review["data"]["method"], "program")
        self.assertEqual(review["actor"]["kind"], "system")
        self.assertEqual(review["data"]["verdict"], "fail")
        self.assertEqual(review["data"]["target"], candidate["obligation_ref"])
        self.assertEqual(review["data"]["execution"]["input_sha256"], candidate["candidate_sha256"])
        self.assertEqual(review["data"]["execution"]["checker_sha256"], candidate["native_checker_sha256"])
        self.assertNotEqual(candidate["native_checker_sha256"], candidate["checker"]["code_sha256"])
        with patch("checking.subprocess.run", side_effect=AssertionError("must not replay")):
            self.assertEqual(self.checked(candidate, result), receipt)
        with self.projects.open_project(self.project) as (_, store):
            projection = completion_projection(store)
            self.assertTrue(all(not item["human_accepted"] and not item["global_problem_proved"] for item in projection))

    def test_check_and_explicit_submit_never_conflate(self):
        first = self.candidate(call_id="check-call")
        second = self.candidate(call_id="submit-call")
        checked = self.checked(first)
        submitted = self.checked(second, self.result(second, "submit"), "submit")
        self.assertFalse(checked["explicit_submit"])
        self.assertTrue(submitted["explicit_submit"])
        self.assertEqual(submitted["formal_success_authority"], "original_explicit_submit_only")
        self.assertNotEqual(checked["receipt_evidence_ref"], submitted["receipt_evidence_ref"])
        self.error("OPERATION_CONFLICT", self.checked, first, self.result(first, "submit"), "submit")

    def test_external_claimed_pass_cannot_manufacture_program_pass(self):
        candidate = self.candidate()
        receipt = self.checked(candidate, self.result(candidate, legal=True, target_met=True))
        self.assertEqual(receipt["result_status"], "pass")
        self.assertEqual(self.object(receipt["review_ref"])["data"]["verdict"], "fail")
        self.assertFalse(receipt["explicit_submit"])
        self.assertFalse(receipt["global_problem_proved"])

    def test_infra_null_and_tool_error_remain_inconclusive(self):
        for status in ("null", "tool_error"):
            with self.subTest(status=status):
                candidate = self.candidate(call_id=status)
                result = self.result(candidate, "submit", legal=None) if status == "null" else {
                    "ok": False, "error": {"code": "timeout", "message": "Infrastructure unavailable"}, "metrics": {}}
                receipt = self.checked(candidate, result, "submit")
                self.assertEqual(receipt["result_status"], "inconclusive")
                self.assertFalse(receipt["human_accepted"])
                self.assertEqual(self.object(receipt["review_ref"])["data"]["verdict"], "fail")

    def test_result_hash_task_run_and_candidate_receipt_mismatches_rejected(self):
        candidate = self.candidate()
        for key in ("candidate_sha256", "checker_sha256", "task_sha256"):
            with self.subTest(key=key):
                result = self.result(candidate)
                result["result"][key] = "0" * 64
                self.error("CHECKER_BINDING", self.checked, candidate, result)
        result = self.result(candidate, "submit")
        result["result"]["run_id"] = "other-run"
        self.error("CHECKER_BINDING", self.checked, candidate, result, "submit")
        result = self.result(candidate)
        result["metrics"]["params_sha256"] = "0" * 64
        self.error("CHECKER_BINDING", self.checked, candidate, result)
        result = {"ok": False, "error": {"message": "unavailable"}, "metrics": {"checker_sha256": "0" * 64}}
        self.error("CHECKER_BINDING", self.checked, candidate, result)
        for key in ("candidate_sha256", "native_checker_sha256"):
            forged = dict(candidate, **{key: "0" * 64})
            self.error("CANDIDATE_BINDING", self.checked, forged)
        forged = dict(candidate, note_ref={"id": "P", "rev": 1})
        self.error("CANDIDATE_BINDING", self.checked, forged)

    def test_unknown_checker_archives_external_evidence_without_program_review(self):
        self.fresh(checker={"id": "external-checker", "code_sha256": "a" * 64, "task_sha256": "b" * 64})
        candidate = self.candidate()
        with patch("workflow_candidates.run_check", side_effect=AssertionError("unregistered execution")):
            receipt = self.checked(candidate, self.result(candidate, legal=True, target_met=True))
        self.assertEqual(receipt["authority"], "external_inconclusive")
        self.assertEqual(receipt["receipt_authority"], "external")
        self.assertEqual(receipt["program_check"]["status"], "not_run")
        self.assertIsNone(receipt["review_ref"])
        self.assertEqual(self.object(candidate["obligation_ref"])["data"]["check_method"], "human")

    def test_crash_after_native_start_preserves_incomplete_operation_without_replay(self):
        candidate = self.candidate()
        with patch("checking.subprocess.run", side_effect=KeyboardInterrupt("simulated process crash")):
            with self.assertRaises(KeyboardInterrupt):
                self.checked(candidate)
        with self.projects.open_project(self.project) as (_, store):
            self.assertEqual([item["status"] for item in attempts(store)], ["incomplete"])
        with patch("checking.subprocess.run", side_effect=AssertionError("must not replay")):
            receipt = self.checked(candidate)
        self.assertEqual(receipt["program_check"]["status"], "incomplete")
        self.assertIsNone(receipt["review_ref"])
        self.assertEqual(self.attachment(receipt["receipt_evidence_ref"], "common-tools-receipt.json"), canonical(self.result(candidate)))

    def test_crash_between_receipt_and_native_start_is_not_replayed(self):
        candidate = self.candidate()
        with patch("workflow_candidates.run_check", side_effect=KeyboardInterrupt("crash before native start")):
            with self.assertRaises(KeyboardInterrupt):
                self.checked(candidate)
        with patch("workflow_candidates.run_check", side_effect=AssertionError("must not replay")):
            receipt = self.checked(candidate)
        self.assertEqual(receipt["program_check"]["status"], "incomplete")
        self.assertIsNone(receipt["review_ref"])

    def test_original_and_moved_archived_wrapper_recompute_under_isolation(self):
        candidate = self.candidate()
        receipt = self.checked(candidate)
        review = self.object(receipt["review_ref"])["data"]
        original = self.projects.path(self.project) / "research"
        moved = self.root / "moved-research"
        shutil.copytree(original, moved)
        for location in (original, moved):
            with self.subTest(location=location.name):
                attachments = location / "attachments/sha256"
                result = subprocess.run([sys.executable, "-I", "-B", str(attachments / candidate["native_checker_sha256"]),
                                         str(attachments / candidate["candidate_sha256"]), review["scope"]],
                                        cwd=self.root, env={"PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads(result.stdout), review["execution"]["result"])

    def test_real_common_tools_check_and_fresh_process_submit(self):
        spec = importlib.util.spec_from_file_location("workflow_v2_common_tools_test", HERE / "evaluation/four-harness-v1/tools.py")
        tools = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = tools
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(tools)
        path = self.root / "rendered-checker.py"
        _, raw = fixed.source_bytes()
        path.write_bytes(raw.decode().replace("TASK = None", "TASK = " + repr(self.contract["task"]), 1).encode())
        host = tools.CommonTools("r1", self.root / "common-tools", checker_path=path)
        for tool in ("check", "submit"):
            with self.subTest(tool=tool):
                candidate = self.candidate(call_id="actual-" + tool)
                result = host.call(tool, {"candidate": {"values": []}})
                self.assertTrue(result["ok"], result)
                archived = self.checked(candidate, result, tool)
                self.assertEqual(archived["result_status"], "fail")
                self.assertEqual(archived["explicit_submit"], tool == "submit")
                self.assertEqual(self.attachment(archived["receipt_evidence_ref"], "common-tools-receipt.json"), canonical(result))


class FixedCertificateTests(unittest.TestCase):
    def test_nine_contracts_pin_common_source_and_rendered_hash(self):
        _, source = fixed.source_bytes()
        for task_id in fixed.TASK_IDS:
            with self.subTest(task=task_id):
                contract = certificate_contract(task_id)
                rendered = source.decode().replace("TASK = None", "TASK = " + repr(contract["task"]), 1).encode()
                self.assertEqual(contract["checker"]["code_sha256"], hashlib.sha256(rendered).hexdigest())
                self.assertEqual(contract["checker"]["task_sha256"], contract["task"]["sha256"])
                self.assertEqual(contract["native_checker_sha256"], checker_hash(contract["checker"]["id"]))
                self.assertEqual(get_checker(contract["checker"]["id"])["problem_fields"], contract["problem_fields"])
                result = fixed.evaluate(canonical({"values": []}), contract["obligation_fields"]["scope"])
                self.assertEqual(result["verdict"], "fail")
                self.assertFalse(result["values"]["target_met"])
        with self.assertRaises(StoreError):
            certificate_contract("R1")

    def test_changed_dependency_fails_closed_in_fresh_isolated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "runtime/p2c/checkers"
            destination.mkdir(parents=True)
            shutil.copyfile(HERE / "runtime/p2c/checkers/workflow_certificate_v2.py", destination / "workflow_certificate_v2.py")
            source = root / "evaluation/optimization-v1/certificates.py"
            source.parent.mkdir(parents=True)
            source.write_bytes(fixed.source_bytes()[1] + b"\n# changed dependency\n")
            witness = root / "witness.json"
            witness.write_bytes(canonical({"values": []}))
            result = subprocess.run([sys.executable, "-I", "-B", str(destination / "workflow_certificate_v2.py"),
                                     str(witness), fixed.scope_for("D1")], cwd=root, env={}, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads(result.stdout)["error_code"], "execution_error")

    def test_legacy_per_module_problem_contract_still_executes(self):
        from checkers import primitive_pythagorean_v1 as primitive
        from checking import run_check
        with tempfile.TemporaryDirectory() as directory:
            projects = Projects(Path(directory).resolve())
            project = projects.create("legacy-regression", {**primitive.PROBLEM_FIELDS, "title": "Legacy invalid sanity check",
                "parent": None, "relation": "", "target_type": "engineering_fixture", "knowledge_status": "synthetic_fixture",
                "sources": ["Fresh offline test"]})["id"]
            with projects.open_project(project, writable=True) as (_, store):
                descriptor = store._archive_bytes(canonical({"a": 1, "b": 1, "c": 1}), "legacy-invalid.json")
                evidence = {"evidence_type": "witness", "problem_ref": {"id": "P", "rev": 1}, "attachments": [descriptor],
                    "source": {"locator": "offline-test", "version": "1", "accessed_at": None, "conditions": []},
                    "source_status": "provided", "support_scope": "Legacy sanity baseline", "limits": ["Invalid test witness"],
                    "production": {"method": "offline-test", "version": "1"}, "citation": None}
                obligation = {**primitive.OBLIGATION_FIELDS, "problem_ref": {"id": "P", "rev": 1}, "uses": [], "candidates": [],
                    "evidence": [{"id": "E", "rev": 1}], "next_action": "Offline sanity check", "state": "open", "reason": "Invalid baseline"}
                store.commit("legacy-input", [{"kind": "Evidence", "id": "E", "expected_rev": 0, "data": evidence},
                    {"kind": "Obligation", "id": "O", "expected_rev": 0, "data": obligation}])
                result = run_check(store, "legacy-check", primitive.CHECKER_ID, {"id": "O", "rev": 1}, {"id": "E", "rev": 1})
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["review"]["data"]["verdict"], "fail")
                self.assertEqual(result["review"]["data"]["execution"]["result"]["checks"], {"primitive": True, "pythagorean": False})


if __name__ == "__main__":
    unittest.main()
