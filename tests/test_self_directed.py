from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_m_pilot as pilot
from m_runner import load_manifest
from run_control import dispatch as control
from schema import canonical, StoreError
from run_ledger import Ledger


class SelfDirected(unittest.TestCase):
    setUp = pilot.PilotFixture.setUp
    tearDown = pilot.PilotFixture.tearDown
    descriptor = pilot.PilotFixture.descriptor
    def test_null_totals_manifest_anchor_and_finish(self):
        m = deepcopy(self.manifest)
        m.update(protocol_id='DSH-MATH-M-SELF-DIRECTED-v1', provider='openai-codex', requested_model_label='gpt6.1-sol-max',
                 code=[self.descriptor(Path(__file__).resolve())], review_policy='deferred_needs_review',
                 source_refresh={'accessed_at':'2026-10-27T00:00:00+00:00','record_sha256':'0'*64})
        m['limits'].update(wall_seconds=None,tools=None,cpu_seconds=None)
        self.manifest_path.write_bytes(canonical(m)+b'\n')
        parsed,raw=load_manifest(self.manifest_path)
        anchor=hashlib.sha256(raw).hexdigest()
        result=control({'manifest':str(self.manifest_path),'manifest_sha256':anchor,'action':'open','args':{}})
        self.assertEqual(result['requests'],0)
        with self.assertRaises(StoreError):
            control({'manifest':str(self.manifest_path),'manifest_sha256':'f'*64,'action':'open','args':{}})
        from model_tools import dispatch
        request={'project_id':self.project_id,'session_id':m['session_id'],'run_id':m['run_id'],'tool_call_id':'finish',
                 'name':'research_finish','arguments':{'classification':'no_progress','summary':'Synthetic fixture finished.','references':[]},
                 'manifest':str(self.manifest_path),'manifest_sha256':anchor}
        done=dispatch(self.data,request)
        self.assertTrue(done['result']['value']['request_stop'])
        ledger=Ledger(self.output/'ledger',m)
        self.assertEqual(ledger.reason,'model_finished:no_progress')
        ledger.close()

    def test_v2_supports_frozen_sol_and_astra_routes(self):
        for label, model, effort in (("gpt6.1-sol-max", "gpt-6.1-sol", "max"), ("gpt6-astra-max", "gpt-6-astra", "max"), ("gpt6-astra-ultra", "gpt-6-astra", "ultra")):
            m = deepcopy(self.manifest)
            m.update(protocol_id="DSH-MATH-M-SELF-DIRECTED-v2", provider="openai-codex", requested_model_label=label,
                     mode="live_m_research", model=model, effort=effort, execution_authorized=True,
                     authorization_reference="synthetic route validation only", code=[self.descriptor(Path(__file__).resolve())],
                     review_policy="deferred_needs_review", source_refresh={"accessed_at": "2026-10-27T00:00:00+00:00", "record_sha256": "0" * 64})
            m["limits"].update(wall_seconds=None, requests=None, tools=None, cpu_seconds=None)
            self.manifest_path.write_bytes(canonical(m) + b"\n")
            parsed, _ = load_manifest(self.manifest_path)
            self.assertEqual((parsed["model"], parsed["effort"]), (model, effort))
        m["effort"] = "max"
        self.manifest_path.write_bytes(canonical(m) + b"\n")
        with self.assertRaises(StoreError):
            load_manifest(self.manifest_path)

    def test_evidence_schema_error_identifies_required_empty_attachments(self):
        from m_runner import manifest_sha256
        from model_tools import dispatch
        request = {'project_id': self.project_id, 'session_id': self.manifest['session_id'], 'run_id': self.manifest['run_id'],
                   'tool_call_id': 'bad-evidence', 'name': 'evidence_register',
                   'arguments': {'operation_id': 'bad-evidence', 'id': 'E-BAD', 'expected_rev': 0, 'data': {}, 'attachment_text': '{}'},
                   'manifest': str(self.manifest_path), 'manifest_sha256': manifest_sha256(self.manifest_path.read_bytes())}
        with self.assertRaisesRegex(StoreError, r"data\.attachments.*\[\]"):
            dispatch(self.data, request)

    def test_sandbox_output_fork_and_other_workspace(self):
        from isolated_tools import python_run
        result=python_run(self.manifest,"import os,sys\ntry:\n os.fork()\n print('fork-unexpected')\nexcept OSError:\n print('fork-denied')\nprint('x'*1000000)")
        self.assertEqual(result['status'],'output_limit')
        self.assertLessEqual(len(result['stdout'].encode()),65536)
        self.assertIn('fork-denied',result['stdout'])
        forbidden=self.output/'private.txt';forbidden.write_text('private fixture')
        result=python_run(self.manifest,"from pathlib import Path\ntry:\n Path("+repr(str(forbidden))+").read_text()\n print('read-unexpected')\nexcept OSError:\n print('read-denied')")
        self.assertEqual(result['stdout'],'read-denied\n')


if __name__=='__main__': unittest.main()
