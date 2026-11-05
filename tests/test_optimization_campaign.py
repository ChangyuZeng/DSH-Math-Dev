import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'evaluation/optimization-v1'
sys.path.insert(0,str(HERE))
import experiment

@unittest.skipUnless(os.environ.get('FOUR_HARNESS_NATIVE_FIXTURES')=='1','explicit isolated native fixture opt-in')
class Campaign(unittest.TestCase):
    def test_native_both_policies_keep_formal_submit_and_separate_check(self):
        from test_four_harness_x_live import call_response
        for policy in ('full','omit-host-metrics'):
            with self.subTest(policy=policy),tempfile.TemporaryDirectory(prefix='optimization-driver-test-') as temporary:
                root=Path(temporary).resolve()
                card={'id':'T1','split':'dev','family':'non-target-fixture','kind':'magic_square','parameters':{'n':1},
                    'statement':'Construct a normal 1 by 1 magic square.', 'certificate':'Exactly {values:[integers]}',
                    'domain':'integers','quantifiers':'one certificate','source':'synthetic non-target fixture','sha256':'fixture'}
                (root/'tasks.json').write_text(json.dumps([card]))
                config='A' if policy=='full' else 'B'
                row={'run_id':'T1-r1-'+config,'task':'T1','config':config,'repeat':1,'split':'dev','ordinal':1,'block_order':'AB'}
                responses=[call_response(1,'check',{'candidate':{'values':[1]}}),
                           call_response(2,'submit',{'candidate':{'values':[1]}})]
                for r in responses:r['reasoning']={'effort':'max'}
                with patch.object(experiment,'ROOT',root):
                    result=experiment.execute(row,{'source_commit':'offline-fixture',config:{'context_policy':policy}},offline_responses=responses)
                self.assertTrue(result['formal_success'],result)
                self.assertEqual(result['physical_requests'],2,result)
                self.assertEqual(result['candidate_checks'],2,result)
                self.assertEqual(result['exact_duplicate_candidates'],1,result)
                self.assertEqual(result['stop_reason'],'first_independent_submit_success',result)
                self.assertTrue(result['fair_comparison_valid'],result)
                self.assertTrue((root/'runs'/row['run_id']/'capture/stdout.log').exists())
                requests=[json.loads(p.read_text()) for p in (root/'runs'/row['run_id']/'audit').glob('*.request.json')]
                outputs=[json.loads(item['output']) for request in requests for item in request['input'] if item.get('type')=='function_call_output']
                self.assertTrue(outputs)
                self.assertEqual(any('metrics' in o for o in outputs),policy=='full')
                import audit_results, hashlib
                frozen=json.dumps({'schedule':[row]})
                (root/'protocol.json').write_text(frozen)
                (root/'protocol-freeze.json').write_text(json.dumps({'sha256':hashlib.sha256(frozen.encode()).hexdigest()}))
                with patch.object(audit_results,'ROOT',root):audit_results.main()
                self.assertTrue(json.loads((root/'independent-artifact-audit.json').read_text())['passed'])

if __name__=='__main__':unittest.main()
