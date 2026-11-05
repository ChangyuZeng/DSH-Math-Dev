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
                candidates=[json.loads(p.read_text()) for p in sorted((root/'runs'/row['run_id']/'candidates').glob('*.json'))]
                submitted=next(c for c in candidates if c['source']=='submit')
                self.assertEqual(result['first_valid_submission_seconds'],submitted['tool_started_elapsed_seconds'])
                self.assertEqual(result['first_valid_check_completed_seconds'],submitted['elapsed_seconds'])
                self.assertLessEqual(result['first_valid_submission_seconds'],result['first_valid_check_completed_seconds'])
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

    def test_native_exact_integer_submission_both_policies(self):
        from test_four_harness_x_live import call_response
        for policy in ('full','omit-host-metrics'):
            for integer in (9007199254740993, -9007199254740993, 2**255+19):
                with self.subTest(policy=policy,integer=integer),tempfile.TemporaryDirectory(prefix='optimization-integer-test-') as temporary:
                    root=Path(temporary).resolve()
                    card={'id':'INTEGER-TOY','split':'dev','family':'non-target-fixture','kind':'determinant',
                        'parameters':{'A':[[integer]]},'statement':'Compute this one-by-one integer determinant exactly.',
                        'certificate':'Exactly {values:[integer]}.','domain':'integers','quantifiers':'one input',
                        'source':'synthetic non-target compatibility fixture','sha256':'integer-fixture'}
                    (root/'tasks.json').write_text(json.dumps([card]))
                    config='A' if policy=='full' else 'B'
                    row={'run_id':'INTEGER-TOY-r1-'+config,'task':'INTEGER-TOY','config':config,
                        'repeat':1,'split':'dev','ordinal':1,'block_order':'AB'}
                    response=call_response(1,'submit',{'candidate':{'values':[integer]}})
                    response['reasoning']={'effort':'max'}
                    with patch.object(experiment,'ROOT',root):
                        result=experiment.execute(row,{'source_commit':'offline-integer-fixture',
                            config:{'context_policy':policy}},offline_responses=[response])
                    self.assertTrue(result['formal_success'],result)
                    self.assertEqual(result['physical_requests'],1,result)
                    candidates=[json.loads(p.read_text()) for p in (root/'runs'/row['run_id']/'candidates').glob('*.json')]
                    self.assertEqual([c['candidate']['values'] for c in candidates],[[integer]])

class PrecisionFreeze(unittest.TestCase):
    def test_addendum_preserves_original_protocol_results_and_source_guards(self):
        with tempfile.TemporaryDirectory(prefix='precision-freeze-test-') as temporary:
            root=Path(temporary)
            (root/'tasks.json').write_text('[]')
            schedule=[{'run_id':f'T{i//2}-'+('A' if i%2==0 else 'B')} for i in range(8)]
            p={'source_commit':'old','source_inventory':{},'schedule':schedule,
                'cards_sha256':experiment.digest(root/'tasks.json'),'promotion':{'threshold':.15}}
            (root/'protocol.json').write_text(json.dumps(p))
            (root/'protocol-freeze.json').write_text(json.dumps({'sha256':experiment.digest(root/'protocol.json')}))
            completed=[]
            for row in schedule:
                dest=root/'runs'/row['run_id']/'result.json';dest.parent.mkdir(parents=True)
                dest.write_text(json.dumps({'source_commit':'old'}))
                completed.append({'run_id':row['run_id'],'sha256':experiment.digest(dest)})
            a={'kind':'lossless_numeric_tool_transport','completed_prefix_length':8,'old_source_commit':'old',
                'base_protocol_sha256':experiment.digest(root/'protocol.json'),'source_commit':'new',
                'source_inventory':{},'completed_results':completed}
            (root/'precision-reliability-addendum.json').write_text(json.dumps(a))
            (root/'precision-reliability-addendum-freeze.json').write_text(json.dumps({
                'sha256':experiment.digest(root/'precision-reliability-addendum.json')}))
            with patch.object(experiment,'ROOT',root),patch.object(experiment,'git',return_value='new'),patch.object(experiment,'inventory',return_value={}):
                verified=experiment.verify_freeze()
                self.assertEqual(verified['source_commit'],'new')
                self.assertEqual(verified['original_source_commit'],'old')
                self.assertEqual(verified['promotion'],p['promotion'])
                with patch.object(experiment,'inventory',return_value={'changed':'source'}):
                    with self.assertRaisesRegex(Exception,'Tracked source changed'):experiment.verify_freeze()
                old_result=root/'runs'/schedule[0]['run_id']/'result.json'
                original=old_result.read_bytes();old_result.write_bytes(original+b' ')
                with self.assertRaisesRegex(Exception,'Preserved pre-repair result bytes changed'):experiment.verify_freeze()
                old_result.write_bytes(original)
                (root/'protocol.json').write_text(json.dumps({**p,'promotion':{'threshold':0}}))
                with self.assertRaisesRegex(Exception,'Frozen protocol bytes changed'):experiment.verify_freeze()

if __name__=='__main__':unittest.main()
