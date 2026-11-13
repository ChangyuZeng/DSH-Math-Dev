"""Offline hash-bound file certificates; all inputs live in fresh temporary runs."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_four_harness_tools import Fixture, candidate, digest, tools


class CandidateFiles(Fixture):
    def file_args(self, data=None, path='candidate.json'):
        data = json.dumps(candidate()).encode() if data is None else data
        self.host.workspace.write(path, data)
        return {'candidate_file': {'path': path, 'sha256': digest(data), 'size': len(data)}}

    def binding(self, **extra):
        return {'run_id': self.host.run_id, 'role': 'worker', 'parent_request_id': 'request-1',
                'call_id': 'call-1', **extra}

    def prepare(self, args, name='check'):
        return self.host.prepare_candidate(name, args, binding=self.binding())

    def test_inline_and_file_have_same_canonical_hash_and_math(self):
        inline = self.okay(self.call('check', {'candidate': candidate()}))
        args = self.file_args()
        file = self.okay(self.call('check', args))
        for key in ('candidate_sha256', 'legal', 'target_met', 'checker_sha256'):
            self.assertEqual(file[key], inline[key])
        self.assertEqual(file['candidate_file'], args['candidate_file'])
        self.assertEqual(self.host.submissions.read(file['candidate_sha256'] + '.json'), tools._json_bytes(candidate()))

    def test_strict_json_rejects_duplicates_constants_overflow_encoding_trailing(self):
        cases = [b'{"pairs":[],"pairs":[]}', b'{"x":NaN}', b'{"x":Infinity}',
                 b'{"x":1e999}', b'{}{}', b'\xff', b'{"x":"\\ud800"}', b'']
        for data in cases:
            with self.subTest(data=data):
                self.error(self.call('check', self.file_args(data)), 'invalid_candidate_json')
        attempts = list(self.host.control.path.glob('attempt-*.json'))
        self.assertEqual(len(attempts), len(cases))
        self.assertTrue(all(not json.loads(path.read_bytes())['ok'] for path in attempts))

    def test_expected_hash_size_and_exclusive_source(self):
        args = self.file_args()
        for descriptor in ({**args['candidate_file'], 'sha256': '0' * 64},
                           {**args['candidate_file'], 'size': 1}):
            self.error(self.call('check', {'candidate_file': descriptor}), 'candidate_file_mismatch')
        for invalid in ({}, {**args, 'candidate': candidate()},
                        {'candidate_file': {**args['candidate_file'], 'size': True}},
                        {'candidate_file': {**args['candidate_file'], 'sha256': 'A' * 64}}):
            self.error(self.call('check', invalid), 'invalid_arguments')

    def test_unsafe_paths_and_links_fail_closed(self):
        args = self.file_args()
        for path in ('../candidate.json', '/etc/passwd', 'a/../candidate.json', './candidate.json'):
            self.error(self.call('check', {'candidate_file': {**args['candidate_file'], 'path': path}}), 'invalid_path')
        os.symlink(self.host.workspace.path / 'candidate.json', self.host.workspace.path / 'linked.json')
        os.link(self.host.workspace.path / 'candidate.json', self.host.workspace.path / 'hard.json')
        for path in ('linked.json', 'hard.json'):
            self.error(self.call('check', {'candidate_file': {**args['candidate_file'], 'path': path}}), 'unsafe_path')

    def test_prepare_pins_snapshot_across_replacement_and_deletion(self):
        args = self.file_args()
        prepared = self.prepare(args)
        self.host.workspace.write('candidate.json', b'{"pairs":[]}')
        self.host.workspace.path.joinpath('candidate.json').unlink()
        with patch.object(self.host.workspace, 'read', side_effect=AssertionError('must not reread')):
            value = self.okay(self.host.call('check', args, prepared_candidate=prepared['prepare_id'],
                                            candidate_binding=self.binding()))
        self.assertEqual(value['candidate_sha256'], prepared['candidate_sha256'])
        self.error(self.host.call('check', args, prepared_candidate=prepared['prepare_id'],
                                 candidate_binding=self.binding()), 'candidate_prepare_binding')

    def test_prepare_is_bound_to_name_arguments_identity_and_process(self):
        args = self.file_args()
        for name, other_args, binding in [('submit', args, self.binding()),
            ('check', {'candidate_file': {**args['candidate_file'], 'size': 0}}, self.binding()),
            ('check', args, self.binding(call_id='other')),
            ('check', args, self.binding(run_id='other')),
            ('check', args, self.binding(role='reviser')),
            ('check', args, self.binding(parent_request_id='other'))]:
            prepared = self.prepare(args)
            self.error(self.host.call(name, other_args, prepared_candidate=prepared['prepare_id'],
                                      candidate_binding=binding), 'candidate_prepare_binding')
        self.error(self.host.call('check', args, prepared_candidate='missing',
                                 candidate_binding=self.binding()), 'candidate_prepare_binding')

    def test_prepared_expiry_loss_null_and_snapshot_tampering_never_reread(self):
        import time
        args = self.file_args()
        prepared = self.prepare(args)
        future = time.monotonic() + 301
        with patch.object(tools.time, 'monotonic', return_value=future), patch.object(self.host.workspace, 'read', side_effect=AssertionError('no reread')):
            self.error(self.host.call('check', args, prepared_candidate=prepared['prepare_id'],
                                     candidate_binding=self.binding()), 'candidate_prepare_binding')
        prepared = self.prepare(args)
        self.host.submissions.write(prepared['candidate_sha256'] + '.json', b'{}')
        with patch.object(self.host.workspace, 'read', side_effect=AssertionError('no reread')):
            self.error(self.host.call('check', args, prepared_candidate=prepared['prepare_id'],
                                     candidate_binding=self.binding()), 'candidate_prepare_binding')
            self.error(self.host.call('check', args, prepared_candidate=None,
                                     candidate_binding=self.binding()), 'candidate_prepare_binding')
        # Lost in-memory authority cannot be reconstructed from durable snapshot files.
        self.host.submissions.write(prepared['candidate_sha256'] + '.json', tools._json_bytes(candidate()))
        prepared = self.prepare(args)
        self.host._prepared_candidates.clear()
        self.error(self.host.call('check', args, prepared_candidate=prepared['prepare_id'],
                                 candidate_binding=self.binding()), 'candidate_prepare_binding')

    def test_prepare_failure_and_cancellation_leave_receipts(self):
        import threading
        args = self.file_args(b'bad json')
        with self.assertRaises(tools.ToolError):
            self.prepare(args)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(tools.ToolError):
            self.host.prepare_candidate('check', args, binding=self.binding(), cancel_event=cancel)
        receipts = list(self.host.control.path.glob('prepare-receipt-*.json'))
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(not json.loads(path.read_bytes())['ok'] for path in receipts))

    def test_prearchive_happens_before_checker_and_big_integers_are_exact(self):
        value = {'integer': 2 ** 90 + 3}
        args = self.file_args(json.dumps(value).encode())
        prepared = self.prepare(args)
        self.assertEqual(prepared['candidate'], value)
        def validate(received):
            self.assertEqual(received, value)
            self.assertEqual(self.host.submissions.read(prepared['candidate_sha256'] + '.json'), tools._json_bytes(value))
            raise ValueError('invalid toy')
        with patch.object(self.host._audit, 'validate_witness', side_effect=validate):
            result = self.okay(self.host.call('check', args, prepared_candidate=prepared['prepare_id'],
                                            candidate_binding=self.binding()))
        self.assertFalse(result['legal'])

    def test_prepared_submit_uses_only_one_remaining_file_slot(self):
        self.host = self.make_host('quota-pinned', limits=replace(self.limits, max_files=3))
        args = self.file_args()
        prepared = self.prepare(args, 'submit')
        self.assertEqual(self.host._totals()[2], 2)  # Workspace source plus pinned candidate.
        result = self.okay(self.host.call('submit', args, prepared_candidate=prepared['prepare_id'],
                                         candidate_binding=self.binding()))
        self.assertEqual(self.host._totals()[2], 3)
        self.assertEqual(result['candidate_sha256'], prepared['candidate_sha256'])
        self.assertTrue(self.host.submissions.read('receipt-' + result['receipt_id'] + '.json'))

    def test_prepare_reserves_candidate_and_receipt_when_only_one_slot_remains(self):
        self.host = self.make_host('quota-prepare-full', limits=replace(self.limits, max_files=2))
        args = self.file_args()
        with self.assertRaises(tools.ToolError) as raised:
            self.prepare(args, 'submit')
        self.assertEqual(raised.exception.code, 'artifact_limit')
        self.assertEqual(self.host.submissions.inventory()[0], [])
        self.assertEqual(self.host._totals()[2], 1)

    def test_repeated_inline_submit_reuses_candidate_but_reserves_each_receipt(self):
        self.host = self.make_host('quota-inline-repeat', limits=replace(self.limits, max_files=3))
        args = {'candidate': candidate()}
        first = self.okay(self.call('submit', args))
        self.assertEqual(self.host._totals()[2], 2)
        second = self.okay(self.call('submit', args))
        self.assertEqual(self.host._totals()[2], 3)
        self.assertEqual(first['candidate_sha256'], second['candidate_sha256'])
        self.assertNotEqual(first['receipt_id'], second['receipt_id'])
        with patch.object(self.host, '_run_process', side_effect=AssertionError('full quota must stop before checker')):
            self.error(self.call('submit', args), 'artifact_limit')
        self.assertEqual(self.host._totals()[2], 3)
        for receipt in (first, second):
            self.assertTrue(self.host.submissions.read('receipt-' + receipt['receipt_id'] + '.json'))

    def test_new_inline_candidate_needs_two_slots_before_checker(self):
        self.host = self.make_host('quota-inline-new', limits=replace(self.limits, max_files=1))
        with patch.object(self.host, '_run_process', side_effect=AssertionError('insufficient quota must stop before checker')):
            self.error(self.call('submit', {'candidate': candidate()}), 'artifact_limit')
        self.assertEqual(self.host.submissions.inventory()[0], [])

    def test_fresh_submit_reads_exact_prepared_snapshot(self):
        args = self.file_args()
        prepared = self.prepare(args, 'submit')
        self.host.workspace.write('candidate.json', b'{}')
        original = self.host._run_process
        def observed(command, cwd, cancel):
            self.assertEqual(Path(command[-1]).read_bytes(), tools._json_bytes(candidate()))
            return original(command, cwd, cancel)
        with patch.object(self.host, '_run_process', side_effect=observed):
            result = self.okay(self.host.call('submit', args, prepared_candidate=prepared['prepare_id'],
                                            candidate_binding=self.binding()))
        self.assertEqual(result['candidate_sha256'], prepared['candidate_sha256'])
        self.assertEqual(result['verification_basis'], 'fresh_process_exact_audit_rerun')
        self.assertIs(type(result['independent_check']['legal']), bool)
        self.assertFalse(result['independent_check']['target_met'])
        self.assertTrue(self.host.submissions.read('receipt-' + result['receipt_id'] + '.json'))


if __name__ == '__main__':
    unittest.main()
