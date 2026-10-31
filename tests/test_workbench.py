"""New synthetic temporary fixtures only; no research runs or historical packages."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
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
from preparation_tools import execute
from schema import StoreError, canonical, decode
from workbench_store import Workbench

PARTIAL = {"title": "合成准备夹具", "statement": "仅测试编辑与保存：α₁ + β₂ = γ；不进行研究。", "quantifiers": "固定合成符号", "domain": "合成字符串", "completion_criteria": ["核对原始 UTF-8 内容"], "sources": ["新建的合成临时夹具"], "knowledge_status": "synthetic_fixture"}
FULL = {**PARTIAL, "target_type": "exploratory", "conventions": []}
PROBLEM = {**FULL, "parent": None, "relation": ""}


def tree_bytes(root):
    if not root.exists():
        return {}
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def note():
    return {"note_type": "observation", "title": "合成笔记", "summary": "保留内容", "body": "只是临时测试", "statement": "无数学接受声明", "assumptions": [], "problem_ref": {"id": "P", "rev": 1}, "route_id": None, "uses": [], "candidate_for": [], "evidence": [], "gaps": [], "failure": None}


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-workbench-synthetic-")
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        self.projects = Projects(self.data)
        self.wb = Workbench(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(StoreError) as result:
            function(*args, **kwargs)
        self.assertEqual(result.exception.code, code, str(result.exception))

    def save(self, patch_value=None, **kwargs):
        return self.wb.draft_save("session-a", 0, "save-draft", patch_value if patch_value is not None else deepcopy(FULL), **kwargs)

    def project(self, session="session-a"):
        return self.projects.create(session, deepcopy(PROBLEM))

    def pad(self, project_id, **kwargs):
        args = {"session_id": "session-a", "project_id": project_id, "expected_rev": 0, "operation_id": "pad-save", "title": "草稿 α", "body": "\r\nα + e\u0301 = 𝛼\n$$x^2$$\t  \n"}
        args.update(kwargs)
        return self.wb.scratchpad_save(**args)

    def model(self, name, arguments, session="session-a", call="call-1"):
        return execute(self.data, {"session_id": session, "tool_call_id": call, "name": name, "arguments": arguments})

    def legacy(self, attachment=False):
        source = self.root / ("legacy-" + os.urandom(4).hex())
        problem = {**deepcopy(PROBLEM), "target_type": "engineering_fixture"}
        self.cli(source, "init")
        self.legacy_commit(source, "create", "Problem", "P", problem)
        if attachment:
            (source / "staging/blob.bin").write_bytes(b"\x00\xffsynthetic attachment\n")
            descriptor = self.cli(source, "archive", "blob.bin")
            evidence = {"evidence_type": "witness", "problem_ref": {"id": "P", "rev": 1}, "attachments": [descriptor], "source": {"locator": "synthetic", "version": "fixture1", "accessed_at": None, "conditions": []}, "source_status": "provided", "support_scope": "bytes only", "limits": ["synthetic fixture"], "production": {"method": "test", "version": "1"}, "citation": None}
            self.legacy_commit(source, "evidence", "Evidence", "E", evidence)
        return source

    def cli(self, source, *command):
        result = subprocess.run([sys.executable, "-B", str(HERE / "runtime/p2c/cli.py"), str(source), *command], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        return decode(result.stdout)

    def legacy_commit(self, source, operation_id, kind, object_id, data, rev=0):
        request = self.root / (os.urandom(8).hex() + ".json")
        request.write_bytes(canonical({"operation_id": operation_id, "changes": [{"kind": kind, "id": object_id, "expected_rev": rev, "data": data}]}))
        return self.cli(source, "commit", str(request))

    def test_read_missing_root_is_pure_for_human_and_model(self):
        self.assertEqual(self.wb.draft_read("session-a"), {"draft": None, "validation": None, "pending_finalization": None})
        self.assertEqual(self.model("math_project_draft_read", {}), {"draft": None, "validation": None, "pending_finalization": None})
        self.assertFalse(self.data.exists())
        project = self.project()
        before = tree_bytes(self.data)
        self.assertEqual(self.wb.scratchpad_read("session-a", project["id"]), {"scratchpad": None, "history": []})
        self.assertEqual(self.model("math_scratchpad_read", {"project_id": project["id"]}), {"scratchpad": None, "history": []})
        self.assertEqual(tree_bytes(self.data), before)
        self.assertFalse((self.data / "workbench").exists())

    def test_read_empty_interrupted_initialization_does_not_create_lock(self):
        directory = self.data / "workbench"
        directory.mkdir(parents=True)
        self.assertIsNone(self.wb.draft_read("session-a")["draft"])
        self.assertEqual(list(directory.iterdir()), [])
        saved = self.save({"title": "Recovered"})
        self.assertEqual(Workbench(self.data).draft_read("session-a"), saved)

    def test_interrupted_sidecar_temp_is_invisible_and_preserved(self):
        first = self.save({"title": "A"})
        with patch("workbench_store.os.rename", side_effect=OSError("synthetic sidecar interruption")):
            with self.assertRaises(OSError):
                self.wb.draft_save("session-a", 1, "second", {"title": "B"}, first["draft"]["id"])
        orphan = next((self.data / "workbench").glob(".pending-*"))
        before = orphan.read_bytes()
        self.assertEqual(Workbench(self.data).draft_read("session-a"), first)
        second = self.wb.draft_save("session-a", 1, "second", {"title": "B"}, first["draft"]["id"])
        self.assertEqual(second["draft"]["rev"], 2)
        self.assertEqual(orphan.read_bytes(), before)

    def test_latest_active_draft_follows_transactions_not_wall_clock(self):
        with patch("workbench_store.now", side_effect=["2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"]):
            first = self.save({"title": "first"})
            second = self.wb.draft_save("session-a", 0, "second", {"title": "second"})
            self.assertEqual(self.wb.draft_read("session-a"), second)
            updated = self.wb.draft_save("session-a", 1, "update-first", {"title": "updated"}, first["draft"]["id"])
            self.assertEqual(Workbench(self.data).draft_read("session-a"), updated)

    def test_partial_defaults_validation_questions_and_restart(self):
        saved = self.save({"statement": "保留 17776121037665193653653203126"})
        fields_value = saved["draft"]["fields"]
        self.assertEqual(fields_value["target_type"], "prove_or_disprove")
        self.assertEqual(fields_value["knowledge_status"], "unverified_open")
        self.assertEqual(fields_value["conventions"], [])
        self.assertNotIn("domain", fields_value)
        self.assertFalse(saved["validation"]["complete"])
        self.assertEqual({item["id"] for item in saved["validation"]["questions"]}, set(saved["validation"]["missing_fields"]))
        self.assertEqual(Workbench(self.data).draft_read("session-a"), saved)
        self.assertEqual(self.projects.listing(), [])
        self.assertFalse((self.data / "projects").exists())

    def test_invalid_enum_and_blank_content_are_saved_not_completed(self):
        saved = self.save({**FULL, "target_type": "unknown", "sources": [""]})
        validation = saved["validation"]
        self.assertFalse(validation["complete"])
        self.assertIn("sources", validation["missing_fields"])
        self.assertEqual(next(e["code"] for e in validation["errors"] if e["field"] == "target_type"), "INVALID_ENUM")
        self.assertTrue(next(q for q in validation["questions"] if q["id"] == "target_type")["options"])
        self.assertEqual(saved["draft"]["fields"]["sources"], [""])
        self.error("INCOMPLETE_DRAFT", self.wb.finalize_draft, "session-a", saved["draft"]["id"], 1, "finalize")
        self.assertEqual(self.projects.listing(), [])

    def test_strict_shape_and_bounded_fields(self):
        for bad in ({"parent": None}, {"title": None}, {"title": 1}, {"conventions": "x"}, {"sources": [1]}, {"actor": {"kind": "human"}}):
            self.error("INVALID_SCHEMA", self.save, bad)
        self.error("TOO_LARGE", self.save, {"title": "a" * 2001})
        self.error("TOO_LARGE", self.save, {"statement": "中" * 30000})
        self.error("TOO_LARGE", self.save, {"sources": ["x" * 60000] * 5})
        self.error("INVALID_SCHEMA", self.save, {"title": "\ud800"})
        self.assertFalse(self.data.exists())

    def test_draft_cas_null_id_retry_and_differing_operation_conflict(self):
        first = self.save({"title": "A"})
        self.assertEqual(Workbench(self.data).draft_save("session-a", 0, "save-draft", {"title": "A"}), first)
        draft = first["draft"]
        second = self.wb.draft_save("session-a", 1, "second", {"domain": "symbols"}, draft["id"])
        self.assertEqual(second["draft"]["rev"], 2)
        self.assertEqual(second["draft"]["fields"]["title"], "A")
        self.assertEqual(self.save({"title": "A"}), first)
        self.error("OPERATION_CONFLICT", self.save, {"title": "B"})
        self.error("VERSION_CONFLICT", self.wb.draft_save, "session-a", 1, "stale", {"title": "C"}, draft["id"])
        self.error("VERSION_CONFLICT", self.wb.draft_save, "session-a", 1, "new-stale", {})
        self.assertEqual(self.wb.draft_read("session-a"), second)

    def test_session_binding_newest_active_draft_and_explicit_committed_read(self):
        first = self.save()
        second = self.wb.draft_save("session-a", 0, "newer", {"title": "newer"})
        self.assertEqual(self.wb.draft_read("session-a"), second)
        self.error("PROJECT_SCOPE", self.wb.draft_read, "session-b", first["draft"]["id"])
        self.error("PROJECT_SCOPE", self.wb.draft_save, "session-b", 1, "wrong", {}, first["draft"]["id"])
        self.assertEqual(self.wb.draft_read("session-b"), {"draft": None, "validation": None, "pending_finalization": None})
        done = self.wb.finalize_draft("session-a", first["draft"]["id"], 1, "finalize")
        self.assertEqual(self.wb.draft_read("session-a", first["draft"]["id"])["draft"], done["draft"])
        self.assertEqual(self.wb.draft_read("session-a"), second)

    def test_finalize_exact_revision_immutable_once_and_schema4(self):
        draft = self.save()["draft"]
        self.error("VERSION_CONFLICT", self.wb.finalize_draft, "session-a", draft["id"], 2, "stale")
        result = self.wb.finalize_draft("session-a", draft["id"], 1, "finalize")
        self.assertEqual(result["draft"]["fields"], draft["fields"])
        self.assertEqual(result["draft"]["rev"], 2)
        self.assertEqual(result["draft"]["status"], "committed")
        self.assertFalse(result["project"]["execution_authorized"])
        self.assertEqual(Workbench(self.data).finalize_draft("session-a", draft["id"], 1, "finalize"), result)
        self.error("OPERATION_CONFLICT", self.wb.finalize_draft, "session-a", draft["id"], 2, "finalize")
        self.error("DRAFT_COMMITTED", self.wb.finalize_draft, "session-a", draft["id"], 1, "different")
        self.error("DRAFT_COMMITTED", self.wb.draft_save, "session-a", 2, "edit", {"title": "changed"}, draft["id"])
        view = self.projects.overview(result["project"]["id"])
        root = view["groups"]["targets"][0]["object"]
        self.assertEqual(root["schema_version"], 4)
        self.assertEqual(root["data"], PROBLEM)
        self.assertEqual(len(self.projects.listing()), 1)
        self.assertEqual(self.wb.draft_read("session-a"), {"draft": None, "validation": None, "pending_finalization": None})

    def test_pending_intent_freezes_revision_and_restarts(self):
        draft = self.save()["draft"]
        with patch.object(Projects, "_create_project", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.wb.finalize_draft("session-a", draft["id"], 1, "finalize")
        self.assertEqual(self.projects.listing(), [])
        self.error("FINALIZE_PENDING", self.wb.draft_save, "session-a", 1, "changed", {}, draft["id"])
        self.error("FINALIZE_PENDING", self.wb.finalize_draft, "session-a", draft["id"], 1, "different")
        result = Workbench(self.data).finalize_draft("session-a", draft["id"], 1, "finalize")
        self.assertEqual(len(self.projects.listing()), 1)
        self.assertEqual(result["draft"]["status"], "committed")

    def test_pending_finalization_discoverable_before_newer_active_draft(self):
        first = self.save()["draft"]
        with patch.object(Projects, "_create_project", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.wb.finalize_draft("session-a", first["id"], 1, "pending-finalize")
        newer = self.wb.draft_save("session-a", 0, "newer-draft", {"title": "newer active"})
        other = self.wb.draft_save("session-b", 0, "other-session", {"title": "other session"})
        restarted = Workbench(self.data)
        before = tree_bytes(self.data)
        pending = restarted.draft_read("session-a")
        self.assertEqual(pending["draft"]["id"], first["id"])
        self.assertEqual(pending["pending_finalization"], {"operation_id": "pending-finalize", "expected_rev": 1})
        self.assertEqual(restarted.draft_read("session-a", newer["draft"]["id"]), newer)
        self.assertEqual(restarted.draft_read("session-b"), other)
        self.assertEqual(tree_bytes(self.data), before)
        restarted.finalize_draft("session-a", pending["draft"]["id"], **pending["pending_finalization"])
        self.assertEqual(restarted.draft_read("session-a"), newer)

    def test_interrupted_staging_is_invisible_and_recovers_without_rewriting(self):
        draft = self.save()["draft"]
        with patch.object(Projects, "_publish", side_effect=OSError("synthetic publish interruption")):
            with self.assertRaises(OSError):
                self.wb.finalize_draft("session-a", draft["id"], 1, "finalize")
        stages_before = tree_bytes(self.data / "project-staging")
        self.assertTrue(stages_before)
        self.assertEqual(self.projects.listing(), [])
        result = Workbench(self.data).finalize_draft("session-a", draft["id"], 1, "finalize")
        self.assertEqual(tree_bytes(self.data / "project-staging"), stages_before)
        self.assertEqual(self.projects.listing(), [result["project"]])

    def test_process_interruption_after_publish_recovers_same_project(self):
        draft = self.save()["draft"]
        script = """import os,sys
from backend import Projects
from workbench_store import Workbench
original=Projects._publish
def stop(self,path,project_id):
    original(self,path,project_id)
    os._exit(91)
Projects._publish=stop
Workbench(sys.argv[1]).finalize_draft('session-a',sys.argv[2],1,'finalize')
"""
        process = subprocess.run([sys.executable, "-B", "-c", script, str(self.data), draft["id"]], cwd=HERE, capture_output=True)
        self.assertEqual(process.returncode, 91, process.stderr)
        listed = self.projects.listing()
        self.assertEqual(len(listed), 1)
        project_before = tree_bytes(self.projects.path(listed[0]["id"]))
        restarted = Workbench(self.data)
        before_read = tree_bytes(self.data)
        recovered = restarted.draft_read("session-a")
        self.assertEqual(recovered["pending_finalization"], {"operation_id": "finalize", "expected_rev": 1})
        self.assertEqual(tree_bytes(self.data), before_read)
        self.error("PROJECT_SCOPE", restarted.draft_read, "session-b", recovered["draft"]["id"])
        # Recover using only the persisted read response, not in-memory UI state.
        result = restarted.finalize_draft("session-a", recovered["draft"]["id"], **recovered["pending_finalization"])
        self.assertIsNone(restarted.draft_read("session-a", recovered["draft"]["id"])["pending_finalization"])
        self.assertEqual(result["project"], listed[0])
        self.assertEqual(tree_bytes(self.projects.path(listed[0]["id"])), project_before)
        self.assertEqual(self.projects.listing(), listed)

    def test_recovery_resyncs_publication_before_recording_completion(self):
        from backend import sync_directory
        draft = self.save()["draft"]
        def interrupted_publish(projects, staging, project_id):
            destination = projects.path(project_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.rename(staging, destination)
            raise OSError("synthetic stop before parent fsync")
        with patch.object(Projects, "_publish", interrupted_publish):
            with self.assertRaises(OSError):
                self.wb.finalize_draft("session-a", draft["id"], 1, "finalize")
        with patch("backend.sync_directory", wraps=sync_directory) as synced:
            done = Workbench(self.data).finalize_draft("session-a", draft["id"], 1, "finalize")
        directories = [call.args[0] for call in synced.call_args_list]
        self.assertIn(self.data / "projects", directories)
        self.assertIn(self.data / "project-staging", directories)
        self.assertEqual(self.projects.listing(), [done["project"]])

    def test_old_create_api_is_atomic_and_never_publishes_partial_project(self):
        with patch.object(Projects, "_publish", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.project()
        self.assertEqual(self.projects.listing(), [])
        self.assertTrue(tree_bytes(self.data / "project-staging"))
        self.assertEqual(self.project()["schema_version"], 4)

    def test_scratchpad_exact_utf8_immutable_history_cas_restore_and_restart(self):
        project = self.project()
        research = self.projects.path(project["id"]) / "research"
        before = tree_bytes(research)
        first = self.pad(project["id"])
        files_before = tree_bytes(self.data / "workbench")
        self.assertEqual(Workbench(self.data).scratchpad_read("session-a", project["id"]), first)
        self.assertEqual(self.pad(project["id"]), first)
        second = self.pad(project["id"], operation_id="second", expected_rev=1, body="new\n")
        self.error("VERSION_CONFLICT", self.pad, project["id"], operation_id="stale", body="lost update")
        self.error("OPERATION_CONFLICT", self.pad, project["id"], body="different payload")
        restored = self.wb.scratchpad_restore("session-a", project["id"], 2, "restore", 1)
        self.assertEqual(restored["scratchpad"]["body"], first["scratchpad"]["body"])
        self.assertEqual(restored["scratchpad"]["rev"], 3)
        self.assertEqual([x["rev"] for x in restored["history"]], [1, 2, 3])
        self.assertEqual(self.wb.scratchpad_read("session-a", project["id"], 2)["scratchpad"], second["scratchpad"])
        self.assertEqual(self.wb.scratchpad_restore("session-a", project["id"], 2, "restore", 1), restored)
        self.assertEqual(tree_bytes(research), before)
        for name, raw in files_before.items():
            self.assertEqual((self.data / "workbench" / name).read_bytes(), raw)

    def test_scratchpad_size_type_and_exact_boundaries(self):
        project = self.project()
        self.error("INVALID_SCHEMA", self.pad, project["id"], title=None)
        self.error("INVALID_SCHEMA", self.pad, project["id"], expected_rev=True)
        self.error("TOO_LARGE", self.pad, project["id"], title="a" * 2001)
        self.error("TOO_LARGE", self.pad, project["id"], body="中" * 90000)
        self.error("INVALID_SCHEMA", self.pad, project["id"], body="\ud800")
        body = "a" * (256 * 1024)
        result = self.pad(project["id"], title="中" * 2000, body=body)
        self.assertEqual(result["scratchpad"]["body"], body)
        self.error("TOO_LARGE", self.pad, project["id"], body=body + "a")
        self.error("NOT_FOUND", self.wb.scratchpad_read, "session-a", project["id"], 9)
        self.error("NOT_FOUND", self.wb.scratchpad_restore, "session-a", project["id"], 1, "restore-invalid", 9)

    def test_concurrent_scratchpad_cas_never_loses_an_update(self):
        project = self.project()
        def save(index):
            try:
                return Workbench(self.data).scratchpad_save("session-a", project["id"], 0, "op-" + str(index), "title", str(index))
            except StoreError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, (1, 2)))
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertTrue(any(value in ("LOCKED", "VERSION_CONFLICT") for value in results if type(value) is str))
        self.assertEqual(len(self.wb.scratchpad_read("session-a", project["id"])["history"]), 1)

    def test_promotion_exact_existing_note_provenance_only(self):
        project = self.project()
        self.pad(project["id"])
        target = {"id": "N", "rev": 1}
        self.error("NOT_FOUND", self.wb.scratchpad_promote_link, "session-a", project["id"], 1, target, "link")
        committed = self.projects.mutate(project["id"], "commit", {"operation_id": "note", "changes": [{"kind": "Note", "id": "N", "expected_rev": 0, "data": note()}]})
        self.assertEqual(committed["versions"], [target])
        research_before = tree_bytes(self.projects.path(project["id"]) / "research")
        original_pad = self.wb.scratchpad_read("session-a", project["id"])
        linked = self.wb.scratchpad_promote_link("session-a", project["id"], 1, target, "link")
        self.assertEqual(linked, {"linked": True, "target": target})
        self.assertEqual(self.wb.scratchpad_promote_link("session-a", project["id"], 1, target, "link"), linked)
        self.error("NOT_FOUND", self.wb.scratchpad_promote_link, "session-a", project["id"], 2, target, "link-bad-source")
        self.error("REFERENCE_KIND", self.wb.scratchpad_promote_link, "session-a", project["id"], 1, {"id": "P", "rev": 1}, "link-problem")
        self.error("NOT_FOUND", self.wb.scratchpad_promote_link, "session-a", project["id"], 1, {"id": "N", "rev": 2}, "link-future")
        self.assertEqual(self.wb.scratchpad_read("session-a", project["id"]), original_pad)
        self.assertEqual(tree_bytes(self.projects.path(project["id"]) / "research"), research_before)
        with self.wb._locked() as state:
            self.assertEqual(state["links"][0]["target"], target)
            self.assertEqual(len(state["links"]), 1)

    def test_metadata_mode_gate_protects_all_mutation_paths(self):
        project = self.project()
        self.pad(project["id"])
        metadata = self.projects.path(project["id"]) / "project.json"
        metadata.write_bytes(canonical({**project, "mode": "legacy_readonly_copy"}))
        before = tree_bytes(self.data)
        self.error("READ_ONLY", self.pad, project["id"], expected_rev=1, operation_id="blocked")
        self.error("READ_ONLY", self.wb.scratchpad_restore, "session-a", project["id"], 1, "restore", 1)
        self.error("READ_ONLY", self.wb.scratchpad_promote_link, "session-a", project["id"], 1, {"id": "P", "rev": 1}, "promote")
        for action in ("commit", "evidence", "review", "review-preview"):
            self.error("READ_ONLY", self.projects.mutate, project["id"], action, {})
        self.assertEqual(tree_bytes(self.data), before)

    def test_actual_schema_mismatch_and_corrupt_research_fail_closed(self):
        legacy = self.legacy()
        imported = self.projects.import_legacy("session-a", str(legacy))
        metadata = self.projects.path(imported["id"]) / "project.json"
        metadata.write_bytes(canonical({**imported, "schema_version": 4, "mode": "daily_research"}))
        self.error("UNKNOWN_SCHEMA", self.pad, imported["id"])
        self.error("UNKNOWN_SCHEMA", self.projects.mutate, imported["id"], "commit", {})
        project = self.project()
        log = self.projects.path(project["id"]) / "research/events.jsonl"
        log.write_bytes(log.read_bytes() + b'{"partial":')
        self.error("INCOMPLETE_TAIL", self.pad, project["id"])
        self.error("INCOMPLETE_TAIL", self.wb.scratchpad_read, "session-a", project["id"])
        self.error("INCOMPLETE_TAIL", self.projects.mutate, project["id"], "commit", {})

    def test_legacy_preview_is_pure_fingerprint_and_attachment_count(self):
        source = self.legacy(attachment=True)
        before = tree_bytes(source)
        preview = self.projects.import_legacy_preview(str(source))["preview"]
        self.assertEqual(preview["schema_version"], 3)
        self.assertEqual(preview["object_count"], 2)
        self.assertEqual(preview["attachment_count"], 1)
        self.assertEqual(len(preview["fingerprint"]), 64)
        self.assertEqual(self.projects.import_legacy_preview(str(source))["preview"], preview)
        self.assertEqual(tree_bytes(source), before)
        self.assertFalse(self.data.exists())

    def test_legacy_preview_counts_only_referenced_archives_including_checker_source(self):
        # Archive/register an incomplete synthetic attempt, but never execute a
        # checker, model, review, or mathematical research run.
        source = self.root / "synthetic-checker-archive"
        script = """import sys
from copy import deepcopy
from pathlib import Path
from research_store import ResearchStore
from schema import canonical,digest
from domain import ResearchView
from checking import ACTOR,ORIGIN,CHECKER,checker_hash
from checkers.primitive_pythagorean_v1 import PROBLEM_FIELDS,OBLIGATION_FIELDS,CHECKER_ID
with ResearchStore(sys.argv[1],create=True,writable=True) as store:
    problem={**PROBLEM_FIELDS,'title':'Synthetic archive-count fixture','parent':None,'relation':'','target_type':'engineering_fixture','knowledge_status':'synthetic_fixture','sources':['new temporary test']}
    store.commit('root',[{'kind':'Problem','id':'P','expected_rev':0,'data':problem}])
    descriptor=store._archive_bytes(canonical({'a':3,'b':4,'c':5}),'synthetic-witness.json')
    evidence={'evidence_type':'witness','problem_ref':{'id':'P','rev':1},'attachments':[descriptor],'source':{'locator':'synthetic','version':'1','accessed_at':None,'conditions':[]},'source_status':'provided','support_scope':'fixture','limits':['synthetic'],'production':{'method':'test','version':'1'},'citation':None}
    obligation={**OBLIGATION_FIELDS,'problem_ref':{'id':'P','rev':1},'uses':[],'candidates':[],'evidence':[{'id':'E','rev':1}],'next_action':'Do not execute; archive-count test only','state':'open','reason':'Synthetic archive-count fixture; not executed'}
    store.commit('evidence',[{'kind':'Evidence','id':'E','expected_rev':0,'data':evidence}])
    store.commit('obligation',[{'kind':'Obligation','id':'O','expected_rev':0,'data':obligation}])
    basis=ResearchView(store).basis({'id':'O','rev':1})
    control={'action':'check.started','request':{'checker_id':CHECKER_ID,'target':{'id':'O','rev':1},'witness':{'id':'E','rev':1}},'requested_by':deepcopy(store.actor),'requested_origin':deepcopy(store.origin),'basis':basis,'basis_hash':digest(basis),'checker_sha256':checker_hash(),'input_sha256':descriptor['sha256'],'checker_source':store._archive_bytes(CHECKER.read_bytes(),'checker-source.py')}
    store.actor=deepcopy(ACTOR);store.origin=deepcopy(ORIGIN)
    store.control('synthetic-incomplete-attempt',control)
    store._archive_bytes(b'unreferenced temporary bytes','unreferenced.bin')
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(source)], cwd=HERE / "runtime/p2c", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = tree_bytes(source)
        preview = self.projects.import_legacy_preview(str(source))["preview"]
        self.assertEqual(preview["attachment_count"], 2)
        self.assertEqual(preview["object_count"], 3)
        self.assertEqual(len(list((source / "attachments/sha256").iterdir())), 3)
        self.assertEqual(tree_bytes(source), before)
        self.assertFalse(self.data.exists())

    def test_legacy_changed_fingerprint_rejected_without_publish(self):
        source = self.legacy()
        preview = self.projects.import_legacy_preview(str(source))["preview"]
        problem = {**deepcopy(PROBLEM), "target_type": "engineering_fixture", "title": "new synthetic revision"}
        self.legacy_commit(source, "revision", "Problem", "P", problem, 1)
        before = tree_bytes(source)
        self.error("SOURCE_CHANGED", self.wb.import_legacy_confirm, "session-a", str(source), preview["fingerprint"], "import")
        self.assertEqual(self.projects.listing(), [])
        self.assertEqual(tree_bytes(source), before)

    def test_legacy_confirm_staging_retry_source_unchanged_and_readonly(self):
        source = self.legacy(attachment=True)
        before = tree_bytes(source)
        preview = self.projects.import_legacy_preview(str(source))["preview"]
        with patch.object(Projects, "_publish", side_effect=OSError("synthetic import interruption")):
            with self.assertRaises(OSError):
                self.wb.import_legacy_confirm("session-a", str(source), preview["fingerprint"], "import")
        self.assertEqual(self.projects.listing(), [])
        stages_before = tree_bytes(self.data / "project-staging")
        imported = Workbench(self.data).import_legacy_confirm("session-a", str(source), preview["fingerprint"], "import")
        self.assertEqual(tree_bytes(self.data / "project-staging"), stages_before)
        self.assertEqual(imported["schema_version"], 3)
        self.assertEqual(imported["mode"], "legacy_readonly_copy")
        self.assertEqual(self.wb.import_legacy_confirm("session-a", str(source), preview["fingerprint"], "import"), imported)
        self.error("OPERATION_CONFLICT", self.wb.import_legacy_confirm, "session-a", str(source), "0" * 64, "import")
        self.error("READ_ONLY", self.pad, imported["id"])
        self.error("READ_ONLY", self.projects.mutate, imported["id"], "commit", {})
        self.assertEqual(self.projects.overview(imported["id"])["snapshot"]["state"]["head_hash"], preview["head_hash"])
        self.assertEqual(tree_bytes(source), before)

    def test_legacy_after_publish_interruption_recovery(self):
        source = self.legacy()
        preview = self.projects.import_legacy_preview(str(source))["preview"]
        append = Workbench._append
        def stop(self, state, kind, request, **kwargs):
            if kind == "import-complete":
                raise OSError("synthetic completion interruption")
            return append(self, state, kind, request, **kwargs)
        with patch.object(Workbench, "_append", stop):
            with self.assertRaises(OSError):
                self.wb.import_legacy_confirm("session-a", str(source), preview["fingerprint"], "import")
        before = self.projects.listing()
        self.assertEqual(len(before), 1)
        imported = Workbench(self.data).import_legacy_confirm("session-a", str(source), preview["fingerprint"], "import")
        self.assertEqual(imported, before[0])
        self.assertEqual(self.projects.listing(), before)

    def test_legacy_rejects_output_inside_source_before_any_write(self):
        source = self.legacy()
        preview = self.projects.import_legacy_preview(str(source))["preview"]
        before = tree_bytes(source)
        for output in (source, source / "nested-output"):
            self.error("UNSAFE_PATH", Workbench(output).import_legacy_confirm, "session-a", str(source), preview["fingerprint"], "import")
        self.assertEqual(tree_bytes(source), before)
        self.assertFalse((source / "nested-output").exists())

    def test_legacy_rejects_schema4_empty_corrupt_attachments_and_links(self):
        project = self.project()
        self.error("UNKNOWN_SCHEMA", self.projects.import_legacy_preview, str(self.projects.path(project["id"]) / "research"))
        empty = self.root / "empty"
        self.cli(empty, "init")
        self.error("UNKNOWN_SCHEMA", self.projects.import_legacy_preview, str(empty))
        source = self.legacy(attachment=True)
        attachment = next((source / "attachments/sha256").iterdir())
        attachment.write_bytes(b"changed")
        self.error("LEGACY_READ_FAILED", self.projects.import_legacy_preview, str(source))
        linked = self.root / "linked"
        linked.symlink_to(source, target_is_directory=True)
        self.error("UNSAFE_PATH", self.projects.import_legacy_preview, str(linked))

    def test_symlink_hardlink_traversal_and_corrupt_sidecar_rejected(self):
        self.error("INVALID_SCHEMA", self.projects.path, "../elsewhere")
        outside = self.root / "outside"
        outside.mkdir()
        self.data.mkdir()
        (self.data / "workbench").symlink_to(outside, target_is_directory=True)
        self.error("UNSAFE_PATH", self.wb.draft_read, "session-a")
        self.error("UNSAFE_PATH", self.save)
        self.assertEqual(tree_bytes(outside), {})
        safe = Workbench(self.root / "safe")
        safe.draft_save("session-a", 0, "draft", {"title": "x"})
        transaction = next((self.root / "safe/workbench").glob("*.json"))
        os.link(transaction, self.root / "hardlinked.json")
        self.error("UNSAFE_PATH", safe.draft_read, "session-a")
        corrupt = Workbench(self.root / "corrupt")
        corrupt.draft_save("session-a", 0, "draft", {"title": "x"})
        transaction = next((self.root / "corrupt/workbench").glob("*.json"))
        value = decode(transaction.read_bytes()); value["event_hash"] = "0" * 64
        transaction.write_bytes(canonical(value))
        self.error("CORRUPT_SIDECAR", corrupt.draft_read, "session-a")

    def test_unsafe_project_and_lock_links_rejected(self):
        project = self.project()
        metadata = self.projects.path(project["id"]) / "project.json"
        os.link(metadata, self.root / "linked-metadata.json")
        self.error("UNSAFE_PATH", self.pad, project["id"])
        safe = Workbench(self.root / "lock-fixture")
        safe.draft_save("session-a", 0, "draft", {})
        os.link(self.root / "lock-fixture/workbench/.writer.lock", self.root / "lock-link")
        self.error("UNSAFE_PATH", safe.draft_read, "session-a")
        self.error("UNSAFE_PATH", safe.draft_save, "session-a", 0, "other", {})

    def test_model_identity_scope_and_no_human_dispatch(self):
        project = self.project()
        with patch("backend.dispatch", side_effect=AssertionError("human dispatch forbidden")), patch.object(Projects, "mutate", side_effect=AssertionError("human mutate forbidden")):
            prepared = self.model("math_project_prepare", {"draft_id": None, "expected_rev": 0, "operation_id": "prepare", "patch": {"statement": "model exact text"}})
            saved = self.model("math_scratchpad_save", {"project_id": project["id"], "expected_rev": 0, "operation_id": "model-pad", "title": "模型草稿", "body": "未审阅内容"}, call="call-2")
        for value in (prepared["draft"], saved["scratchpad"]):
            self.assertEqual(value["actor"], {"kind": "model", "id": "dsh-math-chat"})
            self.assertEqual(value["origin"]["source"], "dsh-math-chat")
            self.assertEqual(value["origin"]["session_id"], "session-a")
            self.assertIsNone(value["origin"]["run_id"])
        self.assertEqual(saved["scratchpad"]["origin"]["tool_call_id"], "call-2")
        other = self.project("session-b")
        self.error("PROJECT_SCOPE", self.model, "math_scratchpad_read", {"project_id": other["id"]})
        self.error("PROJECT_SCOPE", self.model, "math_scratchpad_save", {"project_id": other["id"], "expected_rev": 0, "operation_id": "wrong", "title": "x", "body": "y"})
        self.error("PROJECT_SCOPE", self.model, "math_project_draft_read", {"draft_id": prepared["draft"]["id"]}, session="session-child")
        # Human selection across sessions remains allowed.
        self.assertIsNone(self.wb.scratchpad_read("session-a", other["id"])["scratchpad"])

    def test_model_denies_extra_keys_unknown_tools_and_identity_escalation(self):
        for name in ("create", "finalize-draft", "review", "export", "math_run", "math_scratchpad_restore"):
            self.error("FORBIDDEN", self.model, name, {})
        for injected in ("root", "session_id", "actor", "origin", "tool_call_id", "run_id", "path"):
            self.error("INVALID_SCHEMA", self.model, "math_project_draft_read", {injected: "spoof"})
        self.error("INVALID_SCHEMA", execute, self.data, {"session_id": "session-a", "tool_call_id": "call", "name": "math_project_draft_read", "arguments": {}, "actor": {"kind": "human"}})
        self.error("INVALID_SCHEMA", self.model, "math_scratchpad_save", {"project_id": None, "expected_rev": 0, "operation_id": "missing", "title": "x", "body": "y"})
        self.error("INVALID_SCHEMA", self.model, "math_project_prepare", {"draft_id": None, "expected_rev": 0, "operation_id": "unsafe", "patch": {"parent": None}})
        model = Workbench(self.data, actor={"kind": "model", "id": "dsh-math-chat"}, origin={"source": "dsh-math-chat", "session_id": "session-a", "run_id": None, "tool_call_id": "call"})
        self.error("FORBIDDEN", model.finalize_draft, "session-a", "draft", 1, "finalize")
        self.error("FORBIDDEN", model.import_legacy_confirm, "session-a", "ignored", "0" * 64, "import")
        self.assertFalse(self.data.exists())

    def test_model_actual_entry_retry_new_call_id_preserves_original_provenance(self):
        project = self.project()
        command = [sys.executable, "-B", str(HERE / "preparation_tools.py"), str(self.data)]
        for name, arguments in (
            ("math_project_prepare", {"draft_id": None, "expected_rev": 0, "operation_id": "model-prepare-retry", "patch": {"title": "model"}}),
            ("math_scratchpad_save", {"project_id": project["id"], "expected_rev": 0, "operation_id": "model-pad-retry", "title": "model", "body": "exact content"}),
        ):
            request = {"session_id": "session-a", "tool_call_id": "original-call", "name": name, "arguments": arguments}
            first = subprocess.run(command, input=canonical(request), capture_output=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            before = tree_bytes(self.data)
            request["tool_call_id"] = "new-host-call-for-retry"
            retried = subprocess.run(command, input=canonical(request), capture_output=True)
            self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
            self.assertEqual(decode(retried.stdout), decode(first.stdout))
            value = decode(retried.stdout)["value"]
            record = value["draft"] if name == "math_project_prepare" else value["scratchpad"]
            self.assertEqual(record["origin"]["tool_call_id"], "original-call")
            self.assertEqual(tree_bytes(self.data), before)
            if name == "math_project_prepare":
                self.error("OPERATION_CONFLICT", self.wb.draft_save, "session-a", 0, arguments["operation_id"], arguments["patch"])
                request["arguments"] = {**arguments, "patch": {"title": "changed"}}
            else:
                self.error("OPERATION_CONFLICT", self.wb.scratchpad_save, "session-a", project["id"], 0, arguments["operation_id"], "model", "exact content")
                request["arguments"] = {**arguments, "body": "changed"}
            changed = subprocess.run(command, input=canonical(request), capture_output=True)
            self.assertNotEqual(changed.returncode, 0)
            self.assertEqual(decode(changed.stdout)["error"], "OPERATION_CONFLICT")

    def test_python_entry_envelope_exit_codes_and_pure_read(self):
        request = {"session_id": "session-a", "tool_call_id": "call", "name": "math_project_draft_read", "arguments": {"draft_id": None}}
        command = [sys.executable, "-B", str(HERE / "preparation_tools.py"), str(self.data)]
        result = subprocess.run(command, input=canonical(request), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result.stdout), {"ok": True, "value": {"draft": None, "validation": None, "pending_finalization": None}})
        self.assertFalse(self.data.exists())
        request["arguments"]["session_id"] = "spoof"
        result = subprocess.run(command, input=canonical(request), capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(decode(result.stdout)["error"], "INVALID_SCHEMA")
        result = subprocess.run(command, input=b'{"session_id":"a","session_id":"b"}', capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(decode(result.stdout)["error"], "INVALID_JSON")

    def test_human_dispatch_contract_and_no_actor_injection(self):
        def call(action, **args):
            return dispatch(self.data, {"action": action, "args": args})
        self.assertIsNone(call("project-draft-read", session_id="session-a")["draft"])
        saved = call("project-draft-save", session_id="session-a", expected_rev=0, operation_id="save", patch=FULL)
        finalized = call("finalize-draft", session_id="session-a", draft_id=saved["draft"]["id"], expected_rev=1, operation_id="finalize")
        project_id = finalized["project"]["id"]
        self.assertIsNone(call("scratchpad-read", session_id="session-a", project_id=project_id)["scratchpad"])
        self.assertEqual(call("scratchpad-save", session_id="session-a", project_id=project_id, expected_rev=0, operation_id="pad", title="x", body="y")["scratchpad"]["rev"], 1)
        self.assertEqual(call("scratchpad-restore", session_id="session-a", project_id=project_id, expected_rev=1, operation_id="restore", restore_rev=1)["scratchpad"]["rev"], 2)
        self.error("INVALID_SCHEMA", call, "project-draft-save", session_id="session-a", expected_rev=0, operation_id="spoof", patch={}, actor={"kind": "human"})
        source = self.legacy()
        preview = call("import-legacy-preview", campaign=str(source))["preview"]
        imported = call("import-legacy-confirm", session_id="session-a", campaign=str(source), expected_fingerprint=preview["fingerprint"], operation_id="import")
        self.assertEqual(imported["schema_version"], 3)

    def test_sidecars_excluded_from_verified_export_and_relocated_copy(self):
        from bundle import export_project
        project = self.project()
        self.pad(project["id"], body="SIDECAR-PRIVATE-SYNTHETIC-MARKER")
        self.save({"title": "PREPARATION-PRIVATE-SYNTHETIC-MARKER"})
        exported = export_project(self.projects, project["id"], [], 1)
        original = Path(exported["directory"])
        copied = self.root / "relocated-synthetic-bundle"
        shutil.copytree(original, copied)
        for location in (original, copied):
            result = subprocess.run([sys.executable, "-B", "verify_bundle.py", "."], cwd=location, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(decode(result.stdout)["status"], "passed")
            content = tree_bytes(location)
            self.assertFalse(any("workbench" in name or "scratchpad" in name for name in content))
            self.assertFalse(any(b"SIDECAR-PRIVATE-SYNTHETIC-MARKER" in raw or b"PREPARATION-PRIVATE-SYNTHETIC-MARKER" in raw for raw in content.values()))
            self.assertEqual(decode(content["REVIEW-RECORD.json"]), {"R1": None, "R2": None, "execution_authorized": False})


if __name__ == "__main__":
    unittest.main()
