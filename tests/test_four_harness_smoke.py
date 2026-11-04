"""Authorization and immutable smoke freeze gates; no upstream calls."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'evaluation/four-harness-v1'
sys.path.insert(0,str(HERE))
import smoke
from ledger import GateError, SMOKE_BUDGET

class SmokeGates(unittest.TestCase):
    def setUp(self):
        inventory=patch.object(smoke,'execution_inventory',return_value={'fixture_runtime':True})
        inventory.start();self.addCleanup(inventory.stop)

    def frozen(self):
        return smoke.freeze(offline_evidence={'passed':True,'real_model_requests':0},configuration={})

    def test_freeze_is_only_fixed_approved_smoke_not_formal(self):
        value=self.frozen()
        smoke.validate(value)
        self.assertEqual(value['per_run_budget'],SMOKE_BUDGET)
        self.assertFalse(value['formal_execution_authorized'])
        for key,bad in [('scope','formal'),('max_requests',9),('max_tokens',131073),
                        ('authorization','different'),('replacement_runs',True)]:
            changed={**value,key:bad}
            with self.subTest(key=key),self.assertRaises(GateError):smoke.validate(changed)
        self.assertIn('transport.py',value['source_hashes'])
        self.assertIn('state_guard.py',value['source_hashes'])

    def test_incomplete_offline_evidence_and_source_drift_refused(self):
        with self.assertRaises(GateError):
            smoke.freeze(offline_evidence={'passed':False,'real_model_requests':0},configuration={})
        value=self.frozen()
        with patch.object(smoke,'source_hashes',return_value={}):
            with self.assertRaises(GateError) as error:smoke.validate(value)
            self.assertEqual(error.exception.code,'SOURCE_DRIFT')

    def test_runtime_drift_refused_before_authorization_claim(self):
        value=self.frozen()
        with patch.object(smoke,'execution_inventory',return_value={'changed_runtime':True}),patch.object(smoke,'claim_authorization') as claim:
            with self.assertRaises(GateError) as error:smoke.run_campaign(value,'unused-host-output')
            self.assertEqual(error.exception.code,'RUNTIME_DRIFT');claim.assert_not_called()

    def test_authorization_is_one_shot_across_different_output_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            with patch.object(smoke,'AUTHORIZATION_ROOT',root/'claims'):
                value=self.frozen()
                smoke.claim_authorization(value,root/'A')
                with self.assertRaises(GateError) as error:
                    smoke.claim_authorization(value,root/'B')
                self.assertEqual(error.exception.code,'NO_REPLAY')

    def test_zero_exit_timeout_and_unknown_receipts_cannot_pass(self):
        snapshot={'terminal':None,'requests':0,'accounting_complete':True,'detail_complete':True}
        for native in ({'returncode':0},{'returncode':0,'timed_out':True}):
            result=smoke.assess(native,None,snapshot,[],{'violation':None},{'request':{}})
            self.assertFalse(result['smoke_passed'])
            self.assertFalse(result['compatibility_passed'])
            self.assertEqual(result['status'],'incomplete')

    def test_projected_common_result_requires_full_host_binding_and_matching_body(self):
        import json
        from ledger import digest
        full={'ok':True,'result':{'status':'completed','stdout':'5\n','runtime':{'python':'frozen'}}}
        sent={'ok':True,'result':{'status':'completed','stdout':'5\n'}}
        original=digest(full);projected=digest(sent)
        sent['metrics']={'request_id':'tool-1','result_sha256':projected}
        events=[{'type':'tool_finished','tool':{'request_id':'tool-1','ok':True,'tool':'compute','result_sha256':original}},
                {'type':'common_reply_projection','tool_request_id':'tool-1','full_result_sha256':original,
                 'sent_result_sha256':projected}]
        snapshot={'terminal':None,'requests':2,'accounting_complete':True,'detail_complete':True}
        def assess(value, records=events):
            return smoke.assess({'returncode':0},None,snapshot,records,{'violation':None},
                {'request':{'next':{'input':[{'type':'function_call_output','call_id':'call-1','output':json.dumps(value)}]}}})
        self.assertTrue(assess(sent)['compatibility_passed'])
        self.assertFalse(assess(sent,events[:1])['compatibility_passed'])
        tampered={**sent,'result':{**sent['result'],'stdout':'6\n'}}
        self.assertFalse(assess(tampered)['compatibility_passed'])

    def test_native_prefixed_receipt_requires_exact_audited_continuation(self):
        from ledger import digest
        output='Wall time: 0.01 seconds\nOutput:\n{"native":"verified receipt"}'
        events=[{'type':'tool_finished','tool':{'request_id':'tool-1','ok':True,'tool':'compute','result_sha256':'full'}},
                {'type':'native_function_output','tool_request_id':'tool-1','call_id':'call-1',
                 'model_tool_result_sha256':'full','result_sha256':digest(output),'output_receipt_verified':True}]
        snapshot={'terminal':None,'requests':2,'accounting_complete':True,'detail_complete':True}
        def assess(audit):return smoke.assess({'returncode':0},None,snapshot,events,{'violation':None},audit)
        audit={'request':{'next':{'input':[{'type':'function_call_output','call_id':'call-1','output':output}]}}}
        self.assertTrue(assess(audit)['compatibility_passed'])
        self.assertFalse(assess({'request':{}})['compatibility_passed'])
        audit['request']['next']['input'][0]['output']+='changed'
        self.assertFalse(assess(audit)['compatibility_passed'])

    def test_rethlas_bounded_stop_requires_separate_original_loop_evidence(self):
        snapshot={'terminal':None,'requests':2,'accounting_complete':True,'detail_complete':True}
        native={'returncode':1}
        args=(native,None,snapshot,[],{'violation':None},{'request':{}})
        self.assertFalse(smoke.assess(*args)['checks']['native_exited_cleanly'])
        qualified={'arm':'R','bounded_stop_qualified':True,'full_verification_chain_qualified':False}
        result=smoke.assess(*args,native_evidence=qualified)
        self.assertTrue(result['checks']['native_exited_cleanly'])
        self.assertFalse(result['smoke_passed'])
        self.assertFalse(smoke.assess(*args,native_evidence={**qualified,'arm':'C'})['checks']['native_exited_cleanly'])
        native['timed_out']=True
        self.assertFalse(smoke.assess(*args,native_evidence=qualified)['checks']['native_exited_cleanly'])

    def test_existing_campaign_output_refused_before_transport_or_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            value=self.frozen()
            with patch.object(smoke,'HostTransport') as transport:
                with self.assertRaises(GateError) as error:smoke.run_campaign(value,Path(directory))
                self.assertEqual(error.exception.code,'NO_REPLAY')
                transport.assert_not_called()

if __name__=='__main__':unittest.main()
