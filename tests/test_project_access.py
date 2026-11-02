"""Persistent chat authorization on fresh synthetic data; no model or research runs."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
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
from backend import Projects, dispatch
from material_store import Materials
from preparation_tools import execute
from project_access import ProjectAccess
from schema import StoreError, canonical

PROBLEM = {"title": "Access synthetic fixture", "statement": "Storage tests only", "quantifiers": "Fixed strings",
           "domain": "Fixture", "conventions": [], "target_type": "exploratory", "parent": None, "relation": "",
           "completion_criteria": ["Preserve bytes"], "sources": ["Temporary synthetic data"], "knowledge_status": "synthetic_fixture"}


def note():
    return {"note_type": "observation", "title": "Synthetic note", "summary": "Fixture", "body": "α",
            "statement": "Fixture", "assumptions": [], "problem_ref": {"id": "P", "rev": 1}, "route_id": None,
            "uses": [], "candidate_for": [], "evidence": [], "gaps": [], "failure": None}


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else {}


class ProjectAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-project-access-synthetic-")
        self.root = Path(self.temp.name).resolve() / "data"
        self.projects = Projects(self.root)
        self.access = ProjectAccess(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def project(self, session="owner", title=None):
        return self.projects.create(session, {**deepcopy(PROBLEM), **({"title": title} if title else {})})["id"]

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(StoreError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def call(self, name, arguments, session="chat"):
        return execute(self.root, {"session_id": session, "tool_call_id": "synthetic-call", "name": name, "arguments": arguments})

    def read(self, project, session="chat"):
        return self.call("math_research_read", {"project_id": project, "ref": {"id": "P", "rev": 1}, "offset": 0, "limit": 100}, session)

    def associate(self, project, rev=0, op="associate", session="chat"):
        return dispatch(self.root, {"action": "project-associate", "args": {
            "session_id": session, "project_id": project, "expected_rev": rev, "operation_id": op}})

    def disassociate(self, project, rev=1, session="chat"):
        return dispatch(self.root, {"action": "project-disassociate", "args": {
            "session_id": session, "project_id": project, "expected_rev": rev, "operation_id": "disassociate"}})

    def test_empty_context_is_pure_and_defaults_recording_without_current(self):
        result = self.call("math_project_context", {"query": "", "offset": 0, "limit": 50})
        self.assertEqual(result, {"session_id": "chat", "rev": 0, "current_project_id": None,
                                 "auto_record": True, "projects": [], "offset": 0, "next_offset": None, "total": 0})
        self.assertFalse(self.root.exists())
        for name in ("project-associate", "project-disassociate", "project-preferences"):
            self.error("FORBIDDEN", self.call, name, {})
        for extra in ("session_id", "actor", "origin", "root"):
            self.error("INVALID_SCHEMA", self.call, "math_project_context", {"query": "", "offset": 0, "limit": 10, extra: "injected"})
        self.assertFalse(self.root.exists())

    def test_association_survives_process_restart_revoke_is_immediate_owner_remains(self):
        project = self.project()
        original = tree_bytes(self.projects.path(project))
        self.error("PROJECT_SCOPE", self.read, project)
        saved = self.associate(project)
        self.assertEqual(saved["current_project_id"], project)
        self.assertEqual(saved["rev"], 1)
        completed = subprocess.run([sys.executable, "-B", str(HERE / "preparation_tools.py"), str(self.root)],
                                   input=canonical({"session_id": "chat", "tool_call_id": "new-call", "name": "math_project_context",
                                                    "arguments": {"query": "", "offset": 0, "limit": 50}}), capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        context = json.loads(completed.stdout)["value"]
        self.assertEqual(context["current_project_id"], project)
        self.assertEqual(context["projects"][0]["root_ref"], {"id": "P", "rev": 1})
        self.assertEqual(context["projects"][0]["capability"], {"read": True, "record": True})
        self.read(project)
        revoked = self.disassociate(project)
        self.assertIsNone(revoked["current_project_id"])
        self.error("PROJECT_SCOPE", self.read, project)
        self.read(project, "owner")
        self.assertEqual(self.access.context("chat")["projects"], [])
        self.assertEqual(tree_bytes(self.projects.path(project)), original)
        # Retrying old successful authorization must return its receipt without regranting access.
        self.assertEqual(self.associate(project), saved)
        self.error("PROJECT_SCOPE", self.read, project)

    def test_owner_context_does_not_guess_current_and_foreign_projects_are_not_discovered(self):
        owner = self.project("chat", "Owner Alpha")
        foreign = self.project("elsewhere", "Foreign Beta")
        before = tree_bytes(self.root)
        context = self.access.context("chat")
        self.assertEqual([p["id"] for p in context["projects"]], [owner])
        self.assertIsNone(context["current_project_id"])
        self.assertFalse(context["projects"][0]["current"])
        self.assertEqual(self.access.context("chat", foreign)["total"], 0)
        self.assertEqual(self.access.context("chat", "Foreign")["total"], 0)
        self.assertEqual(tree_bytes(self.root), before)

    def test_pagination_query_exact_root_and_validation_are_read_only(self):
        ids = [self.project("chat", "Page α " + str(i)) for i in range(4)]
        self.project("other", "Page α hidden")
        before = tree_bytes(self.root)
        first = self.access.context("chat", "PAGE Α", 0, 2)
        second = self.access.context("chat", "PAGE Α", first["next_offset"], 2)
        self.assertEqual(first["total"], 4)
        self.assertEqual({p["id"] for p in first["projects"] + second["projects"]}, set(ids))
        self.assertIsNone(second["next_offset"])
        self.assertEqual(self.access.context("chat", "", 100, 50)["projects"], [])
        for args in (("", -1, 1), ("", True, 1), ("", 0, 0), ("", 0, 51), ("", 0, True), (None, 0, 1), ("x" * 201, 0, 1)):
            self.error("INVALID_SCHEMA", self.access.context, "chat", *args)
        self.assertEqual(tree_bytes(self.root), before)

    def test_cas_idempotency_preferences_and_session_operation_namespaces(self):
        project = self.project()
        first = self.associate(project)
        self.assertEqual(self.associate(project), first)
        self.error("VERSION_CONFLICT", self.associate, project, 0, "stale")
        self.error("OPERATION_CONFLICT", self.associate, project, 1, "associate")
        args = {"session_id": "chat", "expected_rev": 1, "operation_id": "preferences", "auto_record": False}
        saved = dispatch(self.root, {"action": "project-preferences", "args": args})
        self.assertEqual(saved["rev"], 2)
        self.assertFalse(ProjectAccess(self.root).context("chat")["auto_record"])
        self.assertEqual(dispatch(self.root, {"action": "project-preferences", "args": args}), saved)
        self.error("INVALID_SCHEMA", dispatch, self.root, {"action": "project-preferences", "args": {**args, "auto_record": 1}})
        self.associate(project, session="second-chat")
        self.assertTrue(self.access.context("second-chat")["auto_record"])
        self.assertEqual(self.access.context("chat")["rev"], 2)

    def test_competing_writers_have_one_winner_and_failed_publication_retries(self):
        project = self.project()
        def change(index):
            try:
                return ProjectAccess(self.root).change("associate", "chat", 0, "op-" + str(index), project_id=project)
            except StoreError as error:
                self.assertIn(error.code, ("LOCKED", "VERSION_CONFLICT"))
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.access.context("chat")["rev"], 1)
        with patch("project_access.os.rename", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.access.change("preferences", "chat", 1, "retry", auto_record=False)
        self.assertTrue(self.access.context("chat")["auto_record"])
        self.access.change("preferences", "chat", 1, "retry", auto_record=False)
        self.assertFalse(self.access.context("chat")["auto_record"])

    def test_shared_authorization_lock_serializes_revoke_before_further_access(self):
        project = self.project()
        self.associate(project)
        with self.projects.open_project(project, session_id="chat", chat=True):
            self.error("LOCKED", self.disassociate, project)
        self.disassociate(project)
        self.error("PROJECT_SCOPE", self.read, project)

    def test_association_only_expands_chat_and_compatibility_scratchpad(self):
        project = self.project()
        self.associate(project)
        self.call("math_research_record", {"project_id": project, "id": "N", "expected_rev": 0,
                                          "operation_id": "note", "data": note()})
        self.call("math_scratchpad_save", {"project_id": project, "expected_rev": 0, "operation_id": "pad", "title": "Fixture", "body": "α"})
        self.assertEqual(self.call("math_scratchpad_read", {"project_id": project})["scratchpad"]["body"], "α")
        def run_gate():
            with self.projects.open_project(project, session_id="chat", model=True):
                pass
        self.error("PROJECT_SCOPE", run_gate)
        self.disassociate(project)
        self.error("PROJECT_SCOPE", self.call, "math_research_record", {"project_id": project, "id": "N2", "expected_rev": 0,
                                                                        "operation_id": "note2", "data": note()})
        self.error("PROJECT_SCOPE", self.call, "math_scratchpad_read", {"project_id": project})
        self.error("PROJECT_SCOPE", self.call, "math_scratchpad_save", {"project_id": project, "expected_rev": 1, "operation_id": "pad2", "title": "", "body": ""})

    def test_material_project_gate_unified_but_staging_remains_exact_session(self):
        project = self.project()
        self.associate(project)
        materials = Materials(self.root)
        raw = "Synthetic α".encode()
        started = materials.begin("chat", project, "upload", [{"name": "fixture.txt", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}])
        upload = started["upload_id"]
        materials.chunk("chat", upload, "0", 0, base64.b64encode(raw).decode())
        self.associate(project, session="another")
        self.error("PROJECT_SCOPE", materials.preview, "another", upload)
        self.error("PROJECT_SCOPE", materials.chunk, "another", upload, "0", 0, base64.b64encode(raw).decode())
        preview = materials.preview("chat", upload)
        result = materials.confirm("chat", upload, "confirm", preview["preview_hash"], project)
        target = result["materials"][0]["ref"]
        self.assertIsNotNone(target, result)
        self.assertEqual(self.call("math_material_read", {"project_id": project, "ref": target, "offset": 0, "limit": 100})["text"], raw.decode())
        self.assertEqual(base64.b64decode(materials.bytes("chat", project, target)["base64"]), raw)
        self.disassociate(project)
        self.error("PROJECT_SCOPE", materials.preview, "chat", upload)
        self.error("PROJECT_SCOPE", materials.read, "chat", project, target)
        self.error("PROJECT_SCOPE", materials.bytes, "chat", project, target)
        self.error("PROJECT_SCOPE", materials.confirm, "chat", upload, "confirm", preview["preview_hash"], project)
        self.assertEqual(materials.read("owner", project, target)["text"], raw.decode())
        self.assertTrue(materials.cleanup("chat", upload)["cleaned"])

    def test_readonly_project_association_never_grants_record_authority(self):
        project = self.project()
        path = self.projects.path(project) / "project.json"
        metadata = self.projects.metadata(project)
        path.write_bytes(canonical({**metadata, "mode": "legacy_readonly_copy"}))
        before = tree_bytes(self.projects.path(project))
        self.associate(project)
        context = self.access.context("chat")
        self.assertEqual(context["projects"][0]["capability"], {"read": True, "record": False})
        self.read(project)
        self.error("READ_ONLY", self.call, "math_research_record", {"project_id": project, "id": "N", "expected_rev": 0,
                                                                    "operation_id": "note", "data": note()})
        self.error("READ_ONLY", self.call, "math_scratchpad_save", {"project_id": project, "expected_rev": 0,
                                                                  "operation_id": "pad", "title": "", "body": ""})
        self.assertEqual(tree_bytes(self.projects.path(project)), before)

    def test_associations_and_preferences_never_enter_verified_exports(self):
        from bundle import export_project
        project = self.project()
        self.associate(project, session="PRIVATE-ASSOCIATION-SYNTHETIC-MARKER")
        self.access.change("preferences", "PRIVATE-PREFERENCE-SYNTHETIC-MARKER", 0, "preferences", auto_record=False)
        exported = export_project(self.projects, project, [], 1)
        original = Path(exported["directory"])
        copied = Path(self.temp.name).resolve() / "relocated-synthetic-bundle"
        shutil.copytree(original, copied)
        for location in (original, copied):
            completed = subprocess.run([sys.executable, "-B", "verify_bundle.py", "."], cwd=location, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "passed")
            content = tree_bytes(location)
            self.assertFalse(any("project-access" in name for name in content))
            self.assertFalse(any(b"PRIVATE-ASSOCIATION-SYNTHETIC-MARKER" in raw or b"PRIVATE-PREFERENCE-SYNTHETIC-MARKER" in raw
                                 for raw in content.values()))
            self.assertEqual(json.loads(content["REVIEW-RECORD.json"]), {"R1": None, "R2": None, "execution_authorized": False})

    def test_actual_schema3_legacy_association_remains_readonly(self):
        source = Path(self.temp.name).resolve() / "legacy-synthetic"
        request = Path(self.temp.name).resolve() / "synthetic-request.json"
        request.write_bytes(canonical({"operation_id": "create", "changes": [{"kind": "Problem", "id": "P", "expected_rev": 0,
                                    "data": {**deepcopy(PROBLEM), "target_type": "engineering_fixture"}}]}))
        for args in (("init",), ("commit", str(request))):
            completed = subprocess.run([sys.executable, "-B", str(HERE / "runtime/p2c/cli.py"), str(source), *args], capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        source_before = tree_bytes(source)
        project = self.projects.import_legacy("owner", str(source))["id"]
        before = tree_bytes(self.projects.path(project))
        self.associate(project)
        row = self.access.context("chat")["projects"][0]
        self.assertEqual(row["schema_version"], 3)
        self.assertEqual(row["capability"], {"read": True, "record": False})
        self.assertEqual(row["root_ref"], {"id": "P", "rev": 1})
        self.read(project)
        self.error("READ_ONLY", self.call, "math_research_record", {"project_id": project, "id": "N", "expected_rev": 0,
                                                                    "operation_id": "note", "data": note()})
        self.assertEqual(tree_bytes(self.projects.path(project)), before)
        self.assertEqual(tree_bytes(source), source_before)

    def test_corrupt_project_is_bounded_unavailable_and_healthy_projects_remain(self):
        broken = self.project()
        healthy = self.project("chat")
        hidden = self.project("other")
        self.associate(broken)
        journal = self.projects.path(broken) / "research/events.jsonl"
        journal.write_bytes(journal.read_bytes() + b'{"partial":')
        before = tree_bytes(self.root)
        context = self.access.context("chat")
        self.assertEqual(context["current_project_id"], broken)
        rows = {row["id"]: row for row in context["projects"]}
        self.assertEqual(set(rows), {broken, healthy})
        self.assertIsNone(rows[broken]["root_ref"])
        self.assertEqual(rows[broken]["capability"], {"read": False, "record": False})
        self.assertEqual(rows[broken]["warning"]["code"], "INCOMPLETE_TAIL")
        self.assertTrue(rows[healthy]["capability"]["record"])
        self.assertEqual(tree_bytes(self.root), before)
        (self.projects.path(broken) / "project.json").write_bytes(b"invalid metadata")
        (self.projects.path(hidden) / "project.json").write_bytes(b"invalid metadata")
        context = self.access.context("chat")
        self.assertEqual({row["id"] for row in context["projects"]}, {broken, healthy})
        self.assertEqual(context["current_project_id"], broken)
        self.assertEqual(self.access.context("chat", hidden)["projects"], [])

    def test_corrupt_chain_and_unsafe_paths_fail_closed(self):
        project = self.project()
        self.associate(project)
        transaction = self.root / "project-access" / "00000000000000000001.json"
        event = json.loads(transaction.read_bytes())
        event["result"]["associated_project_ids"] = []
        transaction.write_bytes(canonical(event))
        self.error("CORRUPT_SIDECAR", self.read, project)
        self.error("CORRUPT_SIDECAR", self.access.context, "chat")
        other_root = Path(self.temp.name).resolve() / "unsafe"
        other_root.mkdir()
        (other_root / "project-access").symlink_to(self.root / "project-access", target_is_directory=True)
        self.error("UNSAFE_PATH", ProjectAccess, other_root)


if __name__ == "__main__":
    unittest.main()
