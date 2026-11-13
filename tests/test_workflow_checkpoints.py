"""Fresh offline handoffs only: no historical experiments or model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workflow_candidates_v2 as fixtures
from schema import StoreError, canonical, digest
from workflow_candidates import _evidence
from workflow_context import freshness
from workflow_service import dispatch_internal
from workflow_store import WorkflowStore


class SearchCheckpointTests(unittest.TestCase):
    def setUp(self, root=None):
        self.f = fixtures.CandidateTests()
        if root is None:
            self.f.setUp()
            self.addCleanup(self.f.doCleanups)
        else:
            from backend import Projects
            self.f.root = Path(root).resolve()
            self.f.projects = Projects(self.f.root)
            self.f.workflow = WorkflowStore(self.f.root)
            self.f.counter = 0
            self.f.fresh()
        self.candidate = self.f.candidate()
        self.checked = self.f.checked(self.candidate)  # Actual fixed subprocess on an invalid fixture.
        self.materials = {}
        for name, text in (("code", "print('public fixture; no search')\n"),
                           ("seed", '{"seed":39,"parameters":{"steps":0}}'),
                           ("search_scope", '{"range":[0,0],"searched":[],"limitations":["no search performed"]}')):
            self.materials[name] = self.save_material(name, text)
        problem = self.f.object({"id": "P", "rev": 1})
        self.authorization = {
            "problem": self.bind({"id": "P", "rev": 1}), "checker": self.candidate["checker"],
            "candidate": self.bind(self.candidate["evidence_ref"]),
            "provenance": self.bind(self.candidate["provenance_ref"]),
            **self.materials, "reusable_state": [], "review": self.bind(self.checked["review_ref"]),
            "objective": {"original": {k: problem["data"][k] for k in
                ("statement", "quantifiers", "domain", "conventions", "target_type", "completion_criteria")},
                "proxy": None}}

    def bind(self, reference):
        return {"ref": reference, "sha256": digest(self.f.object(reference))}

    def save_material(self, name, text):
        with self.f.projects.open_project(self.f.project, writable=True) as (_, store):
            attachment = store._archive_bytes(text.encode(), name + ".txt")
            store.commit("material-" + name, [{"kind": "Evidence", "id": name, "expected_rev": 0,
                "data": _evidence({"id": "P", "rev": 1}, [attachment], "computation", "offline:" + name,
                                  "Synthetic handoff fixture; no search performed.")}])
        return self.bind({"id": name, "rev": 1})

    def archive(self, **changes):
        args = {"project_id": self.f.project, "run_id": "r1", "action_id": self.f.action_id,
                "operation_id": "checkpoint-test", "authorization": self.authorization}
        return dispatch_internal(self.f.root, "workflow-checkpoint", {**args, **changes})

    def finish(self):
        self.f.call("account", action_id=self.f.action_id, accounting={"format": "dsh-math-physical-accounting-v2", "requests": [
            {"request_id": "offline-row", "logical_request_id": "offline-row", "parent_request_id": None,
             "role": "worker", "status": "finished", "usage_complete": True, "usage": {"input_tokens": 1,
             "output_tokens": 1, "total_tokens": 2, "cached_input_tokens": None, "reasoning_tokens": None}, "raw_usage": {}}]})
        self.f.call("propose", action_id=self.f.action_id, proposal={}, usage=None)
        self.f.call("commit", action_id=self.f.action_id, output_refs=[])

    def snapshot(self, checkpoint, **changes):
        args = {"project_id": self.f.project, "run_id": "r1", "kind": "derive", "checkpoint_refs": [checkpoint]}
        return dispatch_internal(self.f.root, "workflow-context", {**args, **changes})

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(StoreError) as raised:
            fn(*args, **kwargs)
        self.assertEqual(raised.exception.code, code, str(raised.exception))

    def test_persistent_exact_cross_action_snapshot_and_no_decision_claim(self):
        checkpoint = self.archive()
        self.assertEqual(checkpoint, self.archive())
        self.finish()
        # A new service/store instance reads durable records and bytes.
        self.f.workflow = WorkflowStore(self.f.root)
        snapshot = self.snapshot(checkpoint)
        loaded = json.loads(snapshot["text"])["search_checkpoints"][0]
        self.assertEqual(loaded["candidate_sha256"], self.candidate["candidate_sha256"])
        self.assertEqual(loaded["verification"]["verdict"], "fail")
        self.assertEqual(loaded["verification"]["authority"], "local_program")
        self.assertEqual(loaded["materials"]["seed"]["attachments"][0]["text"], '{"seed":39,"parameters":{"steps":0}}')
        self.assertEqual(loaded["read_evidence"]["decision_influence"], "not_observed")
        source_action = self.f.action_id
        self.f.call("reserve", kind="derive", context_snapshot=snapshot, max_output_tokens=100)
        self.assertNotEqual(self.f.run["actions"][-1]["action_id"], source_action)
        saved = WorkflowStore(self.f.root).dispatch({"action": "read", "project_id": self.f.project, "run_id": "r1"})
        next_action = saved["actions"][-1]
        self.assertEqual(next_action["context_snapshot"]["sha256"], hashlib.sha256(snapshot["text"].encode()).hexdigest())
        self.assertEqual(json.loads(next_action["context_snapshot"]["text"])["search_checkpoints"][0], loaded)
        self.assertIn(checkpoint["ref"], snapshot["refs"])
        self.assertFalse(loaded["verification"]["global_problem_proved"])

    def test_opt_in_and_no_same_action_or_blind_read(self):
        checkpoint = self.archive()
        self.error("CHECKPOINT_ACTION", self.snapshot, checkpoint)
        self.finish()
        plain = dispatch_internal(self.f.root, "workflow-context", {"project_id": self.f.project, "run_id": "r1", "kind": "derive"})
        self.assertNotIn("search_checkpoints", json.loads(plain["text"]))
        self.error("BLIND_CONTEXT_CONFLICT", self.snapshot, checkpoint, kind="reconstruct", target_ref=self.candidate["note_ref"])

    def test_exact_authorization_hash_seed_code_scope_and_objective(self):
        for key in ("problem", "candidate", "provenance", "code", "seed", "search_scope", "review"):
            authorization = deepcopy(self.authorization)
            authorization[key]["sha256"] = "0" * 64
            self.error("CHECKPOINT_BINDING", self.archive, authorization=authorization)
        authorization = deepcopy(self.authorization)
        authorization["objective"]["original"]["statement"] = "A reduced proxy target"
        self.error("TARGET_MISMATCH", self.archive, authorization=authorization)
        authorization = deepcopy(self.authorization)
        authorization["verified"] = True
        self.error("INVALID_SCHEMA", self.archive, authorization=authorization)

    def test_proxy_is_separate_and_requires_archived_evaluator(self):
        authorization = deepcopy(self.authorization)
        code = self.f.object(self.materials["code"]["ref"])["data"]["attachments"][0]
        proxy = {"definition": "Reduced synthetic score, incomparable with original", "direction": "minimize",
                 "evaluator_sha256": code["sha256"], "scope_ref": self.materials["search_scope"]["ref"]}
        authorization["objective"]["proxy"] = proxy
        checkpoint = self.archive(authorization=authorization)
        self.finish()
        loaded = json.loads(self.snapshot(checkpoint)["text"])["search_checkpoints"][0]
        self.assertEqual(loaded["objective"]["proxy"], proxy)
        self.assertNotEqual(loaded["objective"]["original"], proxy)
        authorization["objective"]["proxy"]["evaluator_sha256"] = "0" * 64
        self.error("CHECKPOINT_BINDING", self.archive, authorization=authorization)

    def test_no_review_cannot_become_verified_and_bad_review_rejected(self):
        authorization = deepcopy(self.authorization)
        authorization["review"] = None
        checkpoint = self.archive(authorization=authorization)
        self.finish()
        loaded = json.loads(self.snapshot(checkpoint)["text"])["search_checkpoints"][0]
        self.assertEqual(loaded["verification"]["status"], "unverified")
        self.assertEqual(loaded["verification"]["authority"], "none")
        authorization["review"] = authorization["candidate"]
        self.error("REFERENCE_KIND", self.archive, authorization=authorization)

    def test_changed_seed_rejects_old_snapshot_and_does_not_inherit_state(self):
        checkpoint = self.archive()
        self.finish()
        snapshot = self.snapshot(checkpoint)
        with self.f.projects.open_project(self.f.project, writable=True) as (_, store):
            obj = store.get("seed", 1)
            obj["data"]["attachments"] = [store._archive_bytes(b'{"seed":40}', "seed.txt")]
            store.commit("new-seed", [{"kind": "Evidence", "id": "seed", "expected_rev": 1, "data": obj["data"]}])
        self.error("CHECKPOINT_STALE", self.snapshot, checkpoint)
        with self.f.projects.open_project(self.f.project) as (_, store):
            self.assertTrue(freshness(store.records, snapshot))

    def test_same_problem_different_checker_rejected(self):
        authorization = deepcopy(self.authorization)
        authorization["checker"]["code_sha256"] = "0" * 64
        self.error("CHECKER_BINDING", self.archive, authorization=authorization)
        checkpoint = self.archive()
        self.finish()
        # Read the real manifest with another frozen checker, same exact Problem.
        from workflow_checkpoints import read_checkpoints
        run = deepcopy(self.f.run)
        run["spec"]["execution"]["checker"]["task_sha256"] = "f" * 64
        with self.f.projects.open_project(self.f.project) as (_, store):
            self.error("CHECKER_BINDING", read_checkpoints, store, run, [checkpoint])

    def test_missing_and_corrupt_persistent_material_fail_closed(self):
        checkpoint = self.archive()
        self.finish()
        descriptor = self.f.object(self.materials["code"]["ref"])["data"]["attachments"][0]
        path = self.f.projects.path(self.f.project) / "research" / descriptor["relative_path"]
        original = path.read_bytes()
        path.write_bytes(b"corrupt")
        self.error("UNUSABLE", self.snapshot, checkpoint)
        path.write_bytes(original)
        # Rename only the explicitly checked fresh temporary fixture file.
        moved = path.with_name(path.name + ".fixture-missing")
        path.rename(moved)
        self.error("UNUSABLE", self.snapshot, checkpoint)
        moved.rename(path)

    def test_checkpoint_hash_and_manifest_corruption_rejected(self):
        checkpoint = self.archive()
        self.finish()
        wrong = dict(checkpoint, sha256="0" * 64)
        self.error("CHECKPOINT_BINDING", self.snapshot, wrong)
        descriptor = self.f.object(checkpoint["ref"])["data"]["attachments"][0]
        path = self.f.projects.path(self.f.project) / "research" / descriptor["relative_path"]
        path.write_bytes(b"{}")
        self.error("UNUSABLE", self.snapshot, checkpoint)

    def test_review_for_another_candidate_cannot_verify_this_one(self):
        other = self.f.candidate({"values": [0]}, call_id="another-candidate")
        checked = self.f.checked(other)
        authorization = deepcopy(self.authorization)
        authorization["review"] = self.bind(checked["review_ref"])
        self.error("CHECKPOINT_VERIFICATION", self.archive, authorization=authorization)

    def test_changed_native_checker_source_rejects_restore(self):
        from unittest.mock import patch
        checkpoint = self.archive()
        self.finish()
        with patch("workflow_checkpoints.checker_hash", return_value="0" * 64):
            self.error("CHECKPOINT_STALE", self.snapshot, checkpoint)

    def test_archived_execution_error_never_becomes_math_conclusion(self):
        from unittest.mock import patch
        import subprocess
        other = self.f.candidate({"values": [1]}, call_id="timeout-candidate")
        with patch("checking.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 1)):
            checked = self.f.checked(other)
        authorization = deepcopy(self.authorization)
        authorization.update(candidate=self.bind(other["evidence_ref"]),
                             provenance=self.bind(other["provenance_ref"]), review=self.bind(checked["review_ref"]))
        checkpoint = self.archive(authorization=authorization)
        self.finish()
        loaded = json.loads(self.snapshot(checkpoint)["text"])["search_checkpoints"][0]
        self.assertEqual(loaded["verification"]["verdict"], "error")
        self.assertEqual(loaded["verification"]["error_code"], "execution_error")
        self.assertFalse(loaded["verification"]["global_problem_proved"])
        self.assertNotIn("stagnation", loaded)

    def test_required_handoff_bytes_are_never_truncated_to_fit_context(self):
        authorization = deepcopy(self.authorization)
        authorization["code"] = self.save_material("large-code", "x" * (1024 * 1024))
        checkpoint = self.archive(authorization=authorization)
        self.finish()
        self.error("CONTEXT_LIMIT", self.snapshot, checkpoint)

    def test_original_candidate_failure_preserved_and_review_not_relabelled(self):
        checkpoint = self.archive()
        self.finish()
        loaded = json.loads(self.snapshot(checkpoint)["text"])["search_checkpoints"][0]
        self.assertEqual(json.loads(loaded["materials"]["candidate"]["attachments"][0]["text"]), {"values": []})
        self.assertEqual(loaded["verification"]["verdict"], self.f.object(self.checked["review_ref"])["data"]["verdict"])
        self.assertNotIn("best", loaded["verification"])
        self.assertNotIn("stagnation", loaded)


if __name__ == "__main__":
    unittest.main()
