"""Ordinary chat tests use new synthetic fixtures, with no model/research runs."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Projects
from preparation_tools import execute
from schema import StoreError

PROBLEM = {"title": "合成夹具", "statement": "仅用于存储测试", "quantifiers": "固定字符串", "domain": "测试数据",
           "conventions": [], "target_type": "exploratory", "parent": None, "relation": "",
           "completion_criteria": ["保留字节"], "sources": ["合成数据"], "knowledge_status": "synthetic_fixture"}


def note():
    return {"note_type": "observation", "title": "阶段记录", "summary": "合成摘要", "body": "α + β\n$$x^2$$\r\n",
            "statement": "测试内容", "assumptions": [], "problem_ref": {"id": "P", "rev": 1}, "route_id": None,
            "uses": [], "candidate_for": [], "evidence": [], "gaps": [], "failure": None}


class ChatResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-chat-research-synthetic-")
        self.root = Path(self.temp.name).resolve() / "data"
        self.projects = Projects(self.root)
        self.project = self.projects.create("session-a", deepcopy(PROBLEM))["id"]

    def tearDown(self):
        self.temp.cleanup()

    def call(self, name, args, session="session-a", call="call-1"):
        return execute(self.root, {"session_id": session, "tool_call_id": call, "name": name, "arguments": args})

    def record(self, **changes):
        args = {"project_id": self.project, "id": "N", "expected_rev": 0, "operation_id": "save-1", "data": note()}
        args.update(changes)
        return self.call("math_research_record", args)

    def read(self, ref=None, **changes):
        args = {"project_id": self.project, "ref": ref, "offset": 0, "limit": 12000}
        args.update(changes)
        return self.call("math_research_read", args)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_note_cas_retry_provenance_and_exact_history(self):
        receipt = self.record()
        self.assertEqual(receipt["versions"], [{"id": "N", "rev": 1}])
        retry = self.call("math_research_record", {"project_id": self.project, "id": "N", "expected_rev": 0,
                         "operation_id": "save-1", "data": note()}, call="different-transport-call")
        self.assertEqual(retry, receipt)
        obj = json.loads(self.read({"id": "N", "rev": 1})["text"])
        self.assertEqual(obj["data"], note())
        self.assertEqual(obj["actor"], {"kind": "model", "id": "dsh-math-chat"})
        self.assertEqual(obj["origin"], {"source": "dsh-math-chat", "session_id": "session-a", "run_id": None, "tool_call_id": "call-1"})
        changed = {**note(), "body": "updated"}
        self.error("OPERATION_CONFLICT", self.record, data=changed)
        self.error("VERSION_CONFLICT", self.record, operation_id="save-2", data=changed)
        self.record(expected_rev=1, operation_id="save-2", data=changed)
        self.assertEqual(json.loads(self.read({"id": "N", "rev": 1})["text"])["data"], note())
        self.assertEqual(json.loads(self.read({"id": "N", "rev": 2})["text"])["data"], changed)

    def test_bounded_directory_and_object_reads_are_pure(self):
        self.record(data={**note(), "body": "α" * 18000})
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        directory = json.loads(self.read()["text"])
        self.assertEqual({row["kind"] for row in directory}, {"Problem", "Note"})
        first = self.read({"id": "N", "rev": 1}, limit=37)
        self.assertEqual(len(first["text"]), 37)
        self.assertEqual(first["next_offset"], 37)
        self.assertTrue(first["truncated"])
        rest = self.read({"id": "N", "rev": 1}, offset=37, limit=37)
        self.assertEqual(rest["offset"], 37)
        for changes in ({"limit": 12001}, {"limit": 0}, {"offset": -1}, {"offset": True}, {"ref": {"id": "N", "rev": 0}}):
            self.error("INVALID_SCHEMA", self.read, **changes)
        self.error("NOT_FOUND", self.read, {"id": "N", "rev": 3})
        after = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_session_scope_kind_references_and_size(self):
        args = {"project_id": self.project, "ref": None, "offset": 0, "limit": 100}
        self.error("PROJECT_SCOPE", self.call, "math_research_read", args, session="session-b")
        save = {"project_id": self.project, "id": "N", "expected_rev": 0, "operation_id": "save", "data": note()}
        self.error("PROJECT_SCOPE", self.call, "math_research_record", save, session="session-b")
        self.error("TOO_LARGE", self.record, data={**note(), "body": "x" * (256 * 1024)})
        self.error("INVALID_SCHEMA", self.record, data={**note(), "actor": "human"})
        self.error("INVALID_SCHEMA", self.record, data={**note(), "problem_ref": {"id": "P", "rev": 0}})
        self.error("INVALID_SCHEMA", self.record, data={**note(), "note_type": "failed_attempt"})
        self.error("DANGLING_REFERENCE", self.record, data={**note(), "uses": [{"id": "absent", "rev": 1}]})

    def test_missing_display_title_uses_statement_first_line(self):
        patch = {key: value for key, value in PROBLEM.items() if key not in ("title", "parent", "relation")}
        patch["statement"] = "一" * 80 + "\nsecond line"
        saved = self.call("math_project_prepare", {"draft_id": None, "expected_rev": 0, "operation_id": "draft", "patch": patch})
        self.assertEqual(saved["draft"]["fields"]["title"], "一" * 60)
        self.assertTrue(saved["validation"]["complete"])
        self.assertEqual(saved["draft"]["fields"]["statement"], patch["statement"])

    def test_material_adapter_preserves_exact_contract(self):
        from unittest.mock import patch
        args = {"project_id": self.project, "ref": {"id": "E", "rev": 2}, "offset": 10, "limit": 300}
        # No extraction/import or real external material is needed to check dispatch.
        import types
        adapter = types.ModuleType("material_store")
        with patch.dict(sys.modules, {"material_store": adapter}):
            from unittest.mock import Mock
            adapter.Materials = Mock()
            adapter.Materials.return_value.read.return_value = {"text": "synthetic", "next_offset": None}
            self.assertEqual(self.call("math_material_read", args)["text"], "synthetic")
            adapter.Materials.assert_called_once_with(self.root)
            adapter.Materials.return_value.read.assert_called_once_with(session_id="session-a", **args)


if __name__ == "__main__":
    unittest.main()
