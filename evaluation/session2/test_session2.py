"""Offline Session2 integration tests. No transport credentials or upstream calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1]
_spec = importlib.util.spec_from_file_location('session2_test_protocol', HERE / 'protocol.py')
_protocol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_protocol)
claim_slot, exclusive_json = _protocol.claim_slot, _protocol.exclusive_json
load_verified, registration = _protocol.load_verified, _protocol.registration


class Session2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (HERE / '.offline').mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix='math-', dir=HERE / '.offline')
        cls.root = Path(cls.directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def registered(self, task='S1'):
        path = self.root / (str(uuid.uuid4()) + '.json')
        exclusive_json(path, registration([task], fixture=True))
        return path

    def run_fixture(self, responses, *, expected_exit=0, task='S1'):
        path = self.registered(task)
        output = self.root / str(uuid.uuid4())
        response_path = self.root / (str(uuid.uuid4()) + '-responses.json')
        exclusive_json(response_path, responses)
        result = subprocess.run([shutil.which('node'), str(HERE / 'terminal.mjs'), '--mode', 'fixture',
            '--registration', str(path), '--slot', f'{task}--math-current--r1', '--output', str(output),
            '--python', sys.executable, '--responses', str(response_path)], cwd=SOURCE,
            capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=180)
        self.assertEqual(result.returncode, expected_exit, result.stderr + result.stdout +
            ((output / 'process.json').read_text() if (output / 'process.json').exists() else ''))
        return output, json.loads((output / 'process.json').read_text()), path

    @staticmethod
    def response(output):
        return {'id': 'fixture-' + str(uuid.uuid4()), 'model': 'gpt-6-astra', 'status': 'completed',
            'reasoning': {'effort': 'max'}, 'output': output,
            'usage': {'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30}}

    @classmethod
    def proposal(cls, *, stop=False, progress='new_argument'):
        value = {'summary': 'Offline fixture continuation; no research claim.', 'changes': [],
            'next_action': 'stop' if stop else 'derive', 'next_target': None,
            'progress': progress, 'limitations': ['Fixture only.']}
        return cls.response([{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': json.dumps(value)}]}])

    def test_native_loop_has_no_session1_caps_or_diagnostic_note(self):
        output, record, path = self.run_fixture([self.proposal() for _ in range(8)] + [self.proposal(stop=True)])
        self.assertIsNone(record.get('failure'))
        self.assertEqual(record['close'], {'drained': True})
        self.assertEqual(len(record['run']['physical_requests']), 9)
        self.assertEqual(len(record['run']['actions']), 9)
        self.assertEqual(record['run']['spec']['budget']['requests'], 120)
        self.assertIsNone(record['max_actions'])
        self.assertEqual(record['run']['spec']['execution']['verification_policy'], 'model-directed-v1')
        self.assertNotIn('Paley', record['run']['spec']['stage_goal'])
        self.assertNotIn('First action', record['run']['spec']['stage_goal'])
        self.assertEqual(record['run']['status'], 'paused')
        self.assertEqual(record['run']['usage']['requests'], 9)
        self.assertTrue((output / 'preflight.json').exists())
        with self.assertRaises(FileExistsError):
            claim_slot(path, 'S1--math-current--r1', 'unused', output)

    def test_d8_actual_model_snapshot_excludes_host_source_metadata(self):
        output, record, _ = self.run_fixture([self.proposal(stop=True)], task='D8')
        self.assertIsNone(record.get('failure'))
        preflight = json.loads((output / 'preflight.json').read_text())
        self.assertTrue(any('Singer' in value for value in preflight['host_problem_sources']))
        runtime = Path(record['runtime']['directory'])
        requests = list((runtime / 'audit').glob('*.request.*.json'))
        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0].read_text())
        snapshot = json.loads(payload['input'][0]['content'][0]['text'])
        problem = next(row for row in snapshot['records'] if row['kind'] == 'Problem')
        self.assertEqual(problem['data']['sources'],
            ['Frozen public task statement supplied for this evaluation.'])
        self.assertEqual(problem['data']['statement'], preflight['task_card']['statement'])
        serialized = json.dumps(payload).casefold()
        for excluded in ('singer', 'doi', '10.1090/', 'solvability'):
            self.assertNotIn(excluded, serialized)
        self.assertNotIn('solvability', problem['data'])

    def test_compute_check_and_independent_submit_remain_production_tools(self):
        residues = {i * i % 17 for i in range(1, 17)}
        candidate = {'values': [int((j - i) % 17 in residues) for i in range(17) for j in range(i + 1, 17)]}
        responses = []
        for name, args in [('compute', {'code': 'print(2+2)'}),
                           ('check', {'candidate': candidate}), ('submit', {'candidate': candidate})]:
            responses.append(self.response([{'type': 'function_call', 'name': name,
                'call_id': 'fixture-' + name, 'arguments': json.dumps(args)}]))
        output, record, _ = self.run_fixture(responses)
        self.assertIsNone(record.get('failure'))
        self.assertEqual(len(record['run']['physical_requests']), 3)
        events = record['run']['execution_events']
        self.assertTrue(any(event['type'] == 'native_stop' and
            event.get('reason') == 'first_independent_submit_success' for event in events))
        self.assertEqual(record['close'], {'drained': True})

    def test_two_no_progress_is_native_stop(self):
        _, record, _ = self.run_fixture([self.proposal(progress='no_progress') for _ in range(2)])
        self.assertEqual(record['run']['status'], 'paused')
        self.assertEqual(len(record['run']['physical_requests']), 2)

    def test_missing_usage_stays_unknown_and_stops(self):
        response = self.proposal(stop=True)
        response.pop('usage')
        _, record, _ = self.run_fixture([response], expected_exit=1)
        self.assertEqual(record['failure']['code'], 'USAGE_UNKNOWN')
        self.assertEqual(len(record['run']['physical_requests']), 1)
        self.assertFalse(record['run']['usage']['complete'])
        self.assertIsNone(record['run']['usage']['total_tokens'])
        self.assertEqual(record['close'], {'drained': True})

    def test_native_host_and_qualification_evidence_are_pinned(self):
        native = self.root / (str(uuid.uuid4()) + '-native-fixture.txt')
        evidence = self.root / (str(uuid.uuid4()) + '-evidence-fixture.txt')
        native.write_text('synthetic host-pin test only')
        evidence.write_text('synthetic evidence-pin test only')
        artifact = self.root / (str(uuid.uuid4()) + '-qualification.json')
        exclusive_json(artifact, {'format': 'dsh-math-session2-plain-qualification-v1',
            'qualified': True, 'fixture_only': True, 'native_version': 'synthetic-test-only',
            'native_files': [{'path': str(native), 'sha256': hashlib.sha256(native.read_bytes()).hexdigest()}],
            'evidence_files': [{'path': str(evidence), 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}]})
        path = self.root / (str(uuid.uuid4()) + '-pinned-registration.json')
        exclusive_json(path, registration(['S1'], fixture=True, plain_qualification_path=artifact))
        load_verified(path, 'S1--math-current--r1', 'fixture')
        native.write_text('changed native module')
        with self.assertRaisesRegex(ValueError, 'PLAIN_HOST_DRIFT'):
            load_verified(path, 'S1--math-current--r1', 'fixture')

    def test_formal_registration_requires_native_qualification(self):
        with self.assertRaisesRegex(ValueError, 'passed plain native offline qualification'):
            registration(['D7', 'D8', 'D9'])
        with self.assertRaisesRegex(ValueError, 'exactly D7,D8,D9'):
            registration(['S1'])

    def test_fixture_cannot_authorize_real_or_unknown_slot(self):
        path = self.registered()
        with self.assertRaisesRegex(ValueError, 'fixture preregistration'):
            load_verified(path, 'S1--math-current--r1', 'real')
        with self.assertRaisesRegex(ValueError, 'uniquely preregistered'):
            load_verified(path, 'H1--math-current--r1', 'fixture')

    def test_source_drift_rejected_before_project_creation(self):
        path = self.registered()
        value = json.loads(path.read_text())
        value['source']['sha256'] = '0' * 64
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'SOURCE_DRIFT'):
            load_verified(path, 'S1--math-current--r1', 'fixture')

    def test_real_requires_explicit_permission_before_output(self):
        path = self.registered()
        output = self.root / str(uuid.uuid4())
        result = subprocess.run([shutil.which('node'), str(HERE / 'terminal.mjs'), '--mode', 'real',
            '--registration', str(path), '--slot', 'S1--math-current--r1', '--output', str(output)],
            capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('UPSTREAM_UNAUTHORIZED', result.stderr)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
