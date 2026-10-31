from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from backend import Projects, open_store, dispatch
from schema import StoreError, canonical
from research_store import ResearchStore
from bundle import export_project
from replay import aggregate
from verify_bundle import verify

P = {"title": "Known parity fixture", "statement": "For all integers n, n(n+1) is even", "quantifiers": "for all n", "domain": "integers", "conventions": ["even means divisible by 2"], "target_type": "prove_or_disprove", "parent": None, "relation": "", "completion_criteria": ["full proof with human review"], "sources": ["self-contained known-result fixture"], "knowledge_status": "known_result"}
REF = {"id": "P", "rev": 1}


def obligation():
    return {"statement": "Resolve the source version conflict", "scope": "source application only", "completion_criteria": ["human reads both sources"], "check_method": "human", "checker_id": None, "problem_ref": REF, "uses": [], "candidates": [], "evidence": [], "next_action": "Read sources", "state": "open", "reason": "unreviewed"}


def note():
    return {"note_type": "argument", "title": "Candidate argument", "summary": "Full text retained", "body": "If n is even use n, otherwise use n+1.", "statement": "n(n+1) is even", "assumptions": ["n is an integer"], "problem_ref": REF, "route_id": None, "uses": [], "candidate_for": [], "evidence": [], "gaps": [], "failure": None}


class Product(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.projects = Projects(self.root / "data")
        self.project = self.projects.create("session-fixture", deepcopy(P))
        self.id = self.project["id"]
        self.research = self.projects.path(self.id) / "research"

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, kind, id, data, rev=0):
        return self.projects.mutate(self.id, "commit", {"operation_id": os.urandom(8).hex(), "changes": [{"kind": kind, "id": id, "expected_rev": rev, "data": data}]})

    def test_create_reopen_exact_integer_and_scope(self):
        p = deepcopy(P)
        p["statement"] = "det(A) >= 17776121037665193653653203126"
        self.commit("Problem", "P", p, 1)
        result = Projects(self.projects.root).overview(self.id)
        self.assertEqual(result["project"]["session_id"], "session-fixture")
        self.assertIn("17776121037665193653653203126", result["groups"]["targets"][0]["object"]["data"]["statement"])
        self.assertFalse(result["project"]["execution_authorized"])
        self.assertIsNone(result["session_model_requests"])
        self.assertEqual(result["operation_model_requests"], 0)

    def test_legacy_schema_not_silently_migrated(self):
        raw = (self.research / "events.jsonl").read_bytes()
        with self.assertRaises(StoreError):
            with open_store(self.research, writable=True) as store:
                store.commit("invalid", [{"kind":"Problem", "id":"P", "expected_rev":1, "data": {**P, "knowledge_status":"open"}}])
        self.assertEqual((self.research / "events.jsonl").read_bytes(), raw)

    def test_model_cannot_pass_or_change_target(self):
        self.commit("Note", "N", note())
        with ResearchStore(self.research, writable=True, actor={"kind":"model","id":"fixture-model"}) as store:
            with self.assertRaises(StoreError):
                store.commit("fake-target", [{"kind":"Problem","id":"P","expected_rev":1,"data":P}])
            with self.assertRaises(StoreError):
                store.review("fake-pass", "R", {"id":"N","rev":1}, None, "pass", "scope", ["fake"], "0"*64)

    def test_source_conflict_bidirectional_and_stale_basis(self):
        self.commit("Obligation", "GAP", obligation())
        evidence = {"evidence_type":"source_excerpt", "problem_ref":REF,"attachments":[],"source":{"locator":"https://example.org/fixture","version":"v5-fixture","accessed_at":"2026-10-27","conditions":["version conflict"]},"source_status":"provided","support_scope":"source claim only","limits":["synthetic fixture, not actual Q7 evidence"],"production":{"method":"human test","version":"1"},"citation":{"title":"Version fixture","page":"1","section":None,"theorem":"Open Problem fixture","excerpt_sha256":"0"*64,"condition_map":[{"condition":"version conflict","assessment":"unresolved","explanation":"compare fixed README commit","obligation":{"id":"GAP","rev":1}}]}}
        self.projects.mutate(self.id,"evidence",{"operation_id":"evidence","id":"E","expected_rev":0,"data":evidence,"excerpt":"SYNTHETIC version fixture.\n","logical_name":"excerpt.txt"})
        n = note(); n["evidence"] = [{"id":"E","rev":1}]
        self.commit("Note","N",n)
        preview = self.projects.mutate(self.id,"review-preview",{"target":{"id":"N","rev":1},"candidate":None})
        self.assertFalse(preview["eligible_for_pass"])
        self.assertIn("source_condition_gap:E",preview["blockers"])
        view = self.projects.overview(self.id)
        self.assertEqual(len(view["links"]),2)
        before = (self.research/"events.jsonl").read_bytes()
        with self.assertRaises(StoreError):
            self.projects.mutate(self.id,"review",{"operation_id":"bad-review","review_id":"R","target":{"id":"N","rev":1},"candidate":None,"verdict":"pass","scope":preview["required_scope"],"findings":["test attempt"],"basis_hash":preview["basis_hash"]})
        self.assertEqual((self.research/"events.jsonl").read_bytes(),before)
        p = deepcopy(P); p["conventions"].append("changed premise")
        self.commit("Problem","P",p,1)
        state = next(x for x in self.projects.overview(self.id)["snapshot"]["state"]["research"]["claims"] if x["ref"]["id"] == "N")
        self.assertEqual(state["state"],"needs_recheck")
        self.assertIn("outdated:P",state["reasons"])

    def test_export_move_verify_and_fail_closed(self):
        bundle = export_project(self.projects,self.id,[],1)
        moved = self.root/"moved"
        shutil.move(bundle["directory"],moved)
        self.assertEqual(verify(moved)["status"],"passed")
        self.assertEqual(json.loads((moved/"REVIEW-RECORD.json").read_bytes())["R1"],None)
        (moved/"project.json").write_text("{}")
        with self.assertRaises(ValueError): verify(moved)
        n = note(); n["body"] = "password=fixture-secret"
        self.commit("Note","N",n)
        with self.assertRaises(StoreError): export_project(self.projects,self.id,[],2)

    def test_replay_cancel_unknown_and_corruption(self):
        source = self.root/"ledger.jsonl"
        row = {"seq":1,"previous":"0"*64,"elapsed":0.0,"kind":"request.reserved","request_id":"cancelled","note":"局部 ✓"}
        encoded = canonical(row)
        row["sha256"] = hashlib.sha256(encoded).hexdigest()
        source.write_bytes(canonical(row)+b"\n")
        selected = [{"adapter":"ledger-py-v1","path":str(source)}]
        before = source.read_bytes()
        replay = aggregate(self.projects,self.id,selected)
        self.assertEqual(replay["requests"][0]["outcome"],"unknown")
        self.assertIsNone(replay["requests"][0]["usage"])
        self.assertEqual(source.read_bytes(),before)
        bundle = export_project(self.projects,self.id,selected,1)
        self.assertEqual(verify(bundle["directory"])["status"],"passed")
        source.write_bytes(before[:-1])
        with self.assertRaises(StoreError): aggregate(self.projects,self.id,selected)

    def test_transport_request_ids_and_missing_usage(self):
        source = self.root / "transport.jsonl"
        node = os.environ.get("DSH_MATH_NODE", "node")
        script = """import {writeFileSync} from 'node:fs';import {createHash} from 'node:crypto';let previous='0'.repeat(64),seq=0;const rows=[{kind:'request.reserved',id:1},{kind:'request.settled',id:1,complete:true,usage:{totalTokens:12}},{kind:'request.reserved',id:2},{kind:'request.settled',id:2,complete:false,usage:null}].map(data=>{const row={seq:++seq,previous,...data};const sha256=createHash('sha256').update(JSON.stringify(row)).digest('hex');previous=sha256;return JSON.stringify({...row,sha256});});writeFileSync(process.argv[1],rows.join('\\n')+'\\n');"""
        subprocess.run([node,"--input-type=module","-e",script,str(source)],check=True)
        replay = aggregate(self.projects,self.id,[{"adapter":"audit-js-v1","path":str(source)}])
        self.assertEqual([r["request_id"] for r in replay["requests"]],[1,2])
        self.assertEqual(replay["requests"][1]["outcome"],"unknown")
        self.assertIsNone(replay["requests"][1]["usage"])

    def test_schema3_readonly_copy_export(self):
        legacy = self.root / "legacy"
        cli = HERE / "runtime/p2c/cli.py"
        problem = deepcopy(P)
        problem["target_type"] = "engineering_fixture"
        request = self.root / "schema3-problem.json"
        request.write_bytes(canonical({"operation_id": "synthetic-schema3-problem", "changes": [
            {"kind": "Problem", "id": "P", "expected_rev": 0, "data": problem}
        ]}) + b"\n")
        for command in ([str(legacy), "init"], [str(legacy), "commit", str(request)]):
            result = subprocess.run([sys.executable, "-B", str(cli), *command], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        before = {str(p.relative_to(legacy)): hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy.rglob("*") if p.is_file()}
        imported = self.projects.import_legacy("session-legacy", str(legacy))
        overview = self.projects.overview(imported["id"])
        self.assertEqual(overview["project"]["schema_version"],3)
        with self.assertRaises(StoreError): self.projects.mutate(imported["id"], "commit", {"operation_id":"deny","changes":[]})
        bundle = export_project(self.projects,imported["id"],[],1)
        self.assertEqual(verify(bundle["directory"])["status"],"passed")
        self.assertEqual(before,{str(p.relative_to(legacy)):hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy.rglob("*") if p.is_file()})

    def test_binary_attachment_exact_bytes_and_revision_conflict(self):
        import base64
        raw = b"\x00\xff\x01binary"
        data = {"evidence_type":"witness","problem_ref":REF,"attachments":[],"source":{"locator":"local fixture","version":"1","accessed_at":None,"conditions":[]},"source_status":"provided","support_scope":"bytes only","limits":["not mathematical acceptance"],"production":{"method":"test","version":"1"},"citation":None}
        request = {"operation_id":"binary","id":"E-BIN","expected_rev":0,"data":data,"excerpt":None,"logical_name":"unused.txt","files":[{"logical_name":"sample.bin","base64":base64.b64encode(raw).decode()}]}
        self.projects.mutate(self.id,"evidence",request)
        descriptor = self.projects.overview(self.id)["objects"][-1]["data"]["attachments"][0]
        self.assertEqual((self.research / descriptor["relative_path"]).read_bytes(),raw)
        self.assertEqual(descriptor["sha256"],hashlib.sha256(raw).hexdigest())
        with self.assertRaises(StoreError): self.projects.mutate(self.id,"evidence",{**request,"operation_id":"stale"})
        request["files"][0]["logical_name"] = "../unsafe"
        with self.assertRaises(StoreError): self.projects.mutate(self.id,"evidence",{**request,"operation_id":"bad-path","id":"E-OTHER"})

    def test_path_and_authority_rejected(self):
        with self.assertRaises(StoreError): self.projects.path("../outside")
        with self.assertRaises(StoreError): dispatch(self.projects.root,{"action":"solve","args":{}})


if __name__ == "__main__":
    unittest.main()
