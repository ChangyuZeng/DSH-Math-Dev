"""Offline workflow durability and state-machine regression tests."""
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Projects
from schema import StoreError
from workflow_store import WorkflowStore


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.projects = Projects(self.root)
        problem = {"title": "Fixture", "statement": "Study a candidate", "quantifiers": "all x", "domain": "integers",
                   "conventions": [], "target_type": "exploratory", "parent": None, "relation": "",
                   "completion_criteria": ["human review"], "sources": ["fixture"], "knowledge_status": "unverified_open"}
        self.project = self.projects.create("session", problem)["id"]
        self.store = WorkflowStore(self.root)
        self.spec = {"format": "dsh-math-workflow-v1", "project_id": self.project, "problem_ref": {"id": "P", "rev": 1},
                     "model_policy": {"provider": "fixture", "model": "fixture", "effort": "high"},
                     "budget": {"requests": 3, "total_tokens": 1000, "max_output_tokens": 100},
                     "tools": [], "context_budget": {"max_tokens": 300}, "stage_goal": "Develop candidate"}
        self.counter = 0
        self.before = (self.projects.path(self.project) / "research/events.jsonl").read_bytes()

    def tearDown(self):
        self.assertEqual((self.projects.path(self.project) / "research/events.jsonl").read_bytes(), self.before)

    def request(self, action, run=None, **extra):
        self.counter += 1
        return {"action": action, "project_id": self.project, "run_id": "r1", "operation_id": "op" + str(self.counter),
                "expected_rev": run["rev"] if run else 0, **extra}

    def call(self, action, run=None, **extra):
        return self.store.dispatch(self.request(action, run, **extra))

    def prepare(self):
        return self.call("prepare", spec=self.spec)

    def head(self):
        with self.projects.open_project(self.project) as (_, store):
            state = store.snapshot()["state"]
            return {"seq": state["seq"], "head_hash": state["head_hash"]}

    def context(self, kind="derive"):
        return {"format": "dsh-math-context-v1", "refs": [{"id": "P", "rev": 1}], "research_head": self.head(),
                "text": "fixture", "sha256": hashlib.sha256(b"fixture").hexdigest(), "input_token_bound": 20,
                "action": {"kind": kind, "target_ref": None, "route_id": None}}

    def reserve(self, run, kind="derive", action="reserve"):
        return self.call(action, run, kind=kind, context_snapshot=self.context(kind), max_output_tokens=100)

    def propose(self, run, usage=None):
        return self.call("propose", run, action_id=run["actions"][-1]["action_id"],
                         proposal={"candidate": "Unreviewed argument"}, usage=usage)

    def commit(self, run):
        return self.call("commit", run, action_id=run["actions"][-1]["action_id"], output_refs=[{"id": "P", "rev": 1}])

    def assertCode(self, code, function, *args, **kwargs):
        with self.assertRaises(StoreError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_full_lifecycle_stop_is_not_mathematical_acceptance(self):
        run = self.call("start", self.prepare())
        for kind in ("derive", "check", "stop"):
            run = self.reserve(run, kind)
            run = self.propose(run, {"total_tokens": 50, "output_tokens": 30})
            run = self.commit(run)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["scientific_status"], "candidate_pending_review")
        self.assertEqual(run["usage"], {"requests": 3, "total_tokens": 150, "complete": True})
        self.assertEqual([a["status"] for a in run["actions"]], ["committed"] * 3)
        self.assertCode("INVALID_STATE", self.call, "start", run)

    def test_cas_and_persistent_operation_receipts(self):
        request = self.request("prepare", spec=self.spec)
        run = self.store.dispatch(request)
        started = self.call("start", run)
        self.assertEqual(WorkflowStore(self.root).dispatch(request), run)
        changed = deepcopy(request)
        changed["spec"]["stage_goal"] = "Changed"
        self.assertCode("OPERATION_CONFLICT", self.store.dispatch, changed)
        self.assertCode("CONFLICT", self.call, "pause", run)
        self.assertEqual(self.store.dispatch({"action": "read", "project_id": self.project, "run_id": "r1"}), started)

    def test_running_claim_never_replayed_after_interruption(self):
        run = self.call("start", self.prepare())
        request = self.request("reserve", run, kind="derive", context_snapshot=self.context(), max_output_tokens=100)
        run = self.store.dispatch(request)
        self.assertEqual(WorkflowStore(self.root).dispatch(request), run)
        self.assertCode("INVALID_STATE", self.reserve, run)
        self.assertCode("RECONCILE_REQUIRED", self.call, "pause", run)
        reconciled = self.call("reconcile", run)
        self.assertEqual(reconciled["status"], "needs_reconcile")
        self.assertEqual(reconciled["actions"][0]["status"], "unknown")
        self.assertFalse(reconciled["usage"]["complete"])
        self.assertCode("INVALID_STATE", self.call, "start", reconciled)
        continued = self.call("continue", reconciled, new_run_id="r2", spec=self.spec, research_head=self.head())
        self.assertEqual(continued["usage"]["requests"], 0)
        self.assertEqual(continued["parent"]["run_id"], "r1")
        self.assertEqual(self.store.dispatch({"action": "read", "project_id": self.project, "run_id": "r1"}), reconciled)

    def test_missing_usage_pauses_and_commit_does_not_unblock(self):
        run = self.propose(self.reserve(self.call("start", self.prepare())))
        self.assertEqual(run["reason"], "usage_missing")
        run = self.commit(run)
        self.assertCode("INVALID_STATE", self.call, "start", run)
        self.assertCode("INVALID_STATE", self.reserve, run)

    def test_budget_reservation_and_actual_overage(self):
        self.spec["budget"]["total_tokens"] = 110
        run = self.call("start", self.prepare())
        self.assertCode("BUDGET", self.reserve, run)
        context = self.context()
        run = self.call("reserve", run, kind="derive", context_snapshot=context, max_output_tokens=90)
        run = self.propose(run, {"total_tokens": 111, "output_tokens": 91})
        self.assertEqual(run["reason"], "budget_exceeded")
        run = self.commit(run)
        self.assertCode("INVALID_STATE", self.call, "start", run)

    def test_queue_claim_failure_and_invalid_commit(self):
        run = self.reserve(self.call("start", self.prepare()), action="queue")
        self.assertEqual(run["usage"]["requests"], 0)
        self.assertCode("INVALID_STATE", self.commit, run)
        run = self.call("claim", run, action_id=run["actions"][0]["action_id"])
        self.assertEqual(run["usage"]["requests"], 1)
        run = self.call("fail", run, action_id=run["actions"][0]["action_id"], reason="transport interrupted")
        self.assertEqual(run["actions"][0]["status"], "unknown")
        self.assertEqual(run["status"], "needs_reconcile")

    def test_exact_refs_context_hash_and_unknown_fields(self):
        self.spec["problem_ref"]["rev"] = 2
        self.assertCode("NOT_FOUND", self.prepare)
        self.spec["problem_ref"]["rev"] = 1
        run = self.call("start", self.prepare())
        context = self.context()
        context["sha256"] = "0" * 64
        self.assertCode("INVALID_SCHEMA", self.call, "reserve", run, kind="derive", context_snapshot=context, max_output_tokens=100)
        run = self.propose(self.reserve(run), {"total_tokens": 50, "output_tokens": 10})
        self.assertCode("NOT_FOUND", self.call, "commit", run, action_id=run["actions"][0]["action_id"], output_refs=[{"id": "P", "rev": 2}])
        self.assertCode("INVALID_SCHEMA", self.call, "pause", run, model_override="other")

    def test_read_is_pure_and_unsafe_lock_rejected(self):
        directory = self.projects.path(self.project) / "workflows"
        self.assertEqual(self.store.dispatch({"action": "list", "project_id": self.project}), [])
        self.assertFalse(directory.exists())
        self.prepare()
        fd = os.open(directory / ".writer.lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertCode("LOCKED", self.store.dispatch, {"action": "list", "project_id": self.project})
        finally:
            os.close(fd)
        os.link(directory / ".writer.lock", directory / "hardlink")
        self.assertCode("UNSAFE_PATH", self.store.dispatch, {"action": "list", "project_id": self.project})

    def test_atomic_orphans_ignored_but_committed_corruption_rejected(self):
        run = self.prepare()
        directory = self.projects.path(self.project) / "workflows"
        (directory / ".pending-interrupted").write_text("partial")
        self.assertEqual(self.store.dispatch({"action": "read", "project_id": self.project, "run_id": "r1"}), run)
        event_file = directory / "00000000000000000001.json"
        event = json.loads(event_file.read_text())
        event["run"]["status"] = "completed"
        event_file.write_text(json.dumps(event))
        self.assertCode("CORRUPT_WORKFLOW", self.store.dispatch, {"action": "list", "project_id": self.project})

    def test_output_overage_cannot_be_hidden_by_failure_reason(self):
        run = self.reserve(self.call("start", self.prepare()))
        run = self.propose(run, {"total_tokens": 150, "output_tokens": 101})
        run = self.call("fail", run, action_id=run["actions"][0]["action_id"], reason="invalid proposal")
        self.assertCode("INVALID_STATE", self.call, "start", run)

    def test_interrupted_atomic_publish_retries_without_consuming_revision(self):
        request = self.request("prepare", spec=self.spec)
        with patch("workflow_store.os.rename", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self.store.dispatch(request)
        self.assertEqual(self.store.dispatch({"action": "list", "project_id": self.project}), [])
        run = self.store.dispatch(request)
        self.assertEqual(run["rev"], 1)
        self.assertEqual(WorkflowStore(self.root).dispatch(request), run)

    def test_continue_requires_current_head_and_records_parent_hash(self):
        run = self.prepare()
        head = self.head()
        head["head_hash"] = "0" * 64
        self.assertCode("CONFLICT", self.call, "continue", run, new_run_id="r2", spec=self.spec, research_head=head)
        continued = self.call("continue", run, new_run_id="r2", spec=self.spec, research_head=self.head())
        self.assertRegex(continued["parent"]["workflow_event_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(continued["status"], "prepared")

    def test_workflow_directory_symlink_is_rejected(self):
        directory = self.projects.path(self.project) / "workflows"
        outside = self.root / "outside"
        outside.mkdir()
        directory.symlink_to(outside, target_is_directory=True)
        self.assertCode("UNSAFE_PATH", self.prepare)
        self.assertEqual(list(outside.iterdir()), [])

    def test_project_schema_and_tool_policy_are_strict(self):
        self.spec["tools"] = [{"name": "execute", "version": "v1", "read_only": False}]
        self.assertCode("INVALID_SCHEMA", self.prepare)
        self.spec["tools"] = []
        path = self.projects.path(self.project) / "project.json"
        metadata = json.loads(path.read_text())
        metadata["mode"] = "legacy_readonly_copy"
        path.write_text(json.dumps(metadata))
        self.assertCode("READ_ONLY", self.prepare)


if __name__ == "__main__":
    unittest.main()
