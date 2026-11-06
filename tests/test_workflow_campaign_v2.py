"""Offline queue/gate/recovery regressions. No upstream calls or target solves."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'evaluation' / 'workflow-v2'))
from campaign import Campaign, positive_gate, paired_metric, usage_projection, unknown_result, mechanism_active
from protocol import GATE, pair_queue, digest, public_tasks, validate_qualification, build_protocol, REQUIRED_PREFLIGHT, campaign_root
from recovery import verify_recovery, fingerprint, matrix_mul
from workflow_runtime import tool_contracts
from driver import immediate_failure
from schema import StoreError


def document():
    protocol = {'gate': deepcopy(GATE), 'qualification': {'fixture': True},
        'queue': pair_queue(20261106, 'dev', [f'D{i}' for i in range(1, 7)]) + pair_queue(20261106, 'holdout', ['H1', 'H2', 'H3'])}
    return {'protocol': protocol, 'sha256': digest(protocol)}


def result(slot, *, infra=False, success=True, tokens=100, wall=10, activate=True, immediate=None):
    value = {**slot, 'formal_success': success and not infra, 'infra_terminated': infra,
             'immediate': immediate, 'differential_defect': False, 'total_tokens': tokens,
             'wall_seconds': wall, 'physical_requests': [], 'comparable': {'checks': True, 'isolation': True, 'source': True},
             'execution_events': [{'type': 'revision_triggered'}] if slot['arm'] == 'B' and activate else []}
    return value


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.doc = document()
        self.queue = Campaign(self.root, self.doc)

    def connected(self):
        for _ in range(2):
            row = self.queue.claim_probe()
            self.queue.finish_probe(row['probe_id'], {'success': True, 'physical_requests': 1, 'usage': None})

    def paused(self):
        self.connected()
        for _ in range(2):
            slot = self.queue.claim_slot()
            self.queue.finish_slot(result(slot, infra=True))
        self.assertEqual(self.queue.read()['status'], 'paused')

    def test_balanced_adjacent_pairs_exact_sizes_and_seed(self):
        queue = self.doc['protocol']['queue']
        self.assertEqual((sum(r['phase'] == 'dev' for r in queue), sum(r['phase'] == 'holdout' for r in queue)), (36, 18))
        self.assertEqual(queue[:36], pair_queue(20261106, 'dev', [f'D{i}' for i in range(1, 7)]))
        self.assertNotEqual(queue[:36], pair_queue(20261107, 'dev', [f'D{i}' for i in range(1, 7)]))
        for first, second in zip(queue[::2], queue[1::2]):
            self.assertEqual(first['pair_id'], second['pair_id'])
            self.assertEqual({first['arm'], second['arm']}, {'A', 'B'})
        self.assertEqual(sum(row['order'] == ['A', 'B'] for row in queue[:36:2]), 9)
        self.assertFalse(any(row['task'].startswith('R') for row in queue))

    def test_two_initial_successes_required_no_slot_consumed(self):
        first = self.queue.claim_probe()
        self.queue.finish_probe(first['probe_id'], {'success': True, 'physical_requests': 1, 'usage': None})
        with self.assertRaises(StoreError):
            self.queue.claim_slot()
        self.assertEqual(self.queue.read()['cursor'], 0)
        second = self.queue.claim_probe()
        self.queue.finish_probe(second['probe_id'], {'success': False, 'physical_requests': 1, 'usage': None})
        self.assertEqual(self.queue.read()['status'], 'stopped')
        with self.assertRaises(StoreError):
            self.queue.claim_probe()

    def test_crashed_started_slot_and_partial_pair_are_never_replayed(self):
        self.connected()
        slot = self.queue.claim_slot()
        reopened = Campaign(self.root, self.doc)
        self.assertEqual(reopened.read()['active_slot'], slot)
        with self.assertRaises(StoreError):
            reopened.claim_slot()
        with reopened.driver_lock():
            reopened.reconcile_interrupted()
        state = reopened.read()
        self.assertEqual(state['cursor'], 1)
        self.assertEqual(state['results'][0]['termination'], 'interrupted_started_slot')
        self.assertIsNone(state['results'][0]['total_tokens'])
        self.assertEqual(state['partial_pair']['completed'], [slot['slot_id']])
        other = reopened.claim_slot()
        self.assertEqual(other['pair_id'], slot['pair_id'])
        self.assertNotEqual(other['slot_id'], slot['slot_id'])

    def test_recovery_preflight_probe_bound_and_second_pause(self):
        self.paused()
        cursor = self.queue.read()['cursor']
        with self.assertRaises(StoreError):
            self.queue.claim_slot()
        with self.assertRaises(StoreError):
            self.queue.begin_recovery('wrong')
        self.assertEqual(self.queue.read()['cursor'], cursor)
        self.queue.begin_recovery(digest(self.doc['protocol']['qualification']))
        for index in range(2):
            probe = self.queue.claim_probe()
            self.assertEqual(len(self.queue.read()['probes']), index + 3)
            self.queue.finish_probe(probe['probe_id'], {'success': True, 'physical_requests': 1, 'usage': None})
        self.assertEqual(self.queue.read()['status'], 'running')
        self.assertEqual(self.queue.read()['cursor'], cursor)
        for _ in range(2):
            slot = self.queue.claim_slot()
            self.queue.finish_slot(result(slot, infra=True))
        self.assertEqual(self.queue.read()['status'], 'stopped')
        self.assertEqual(len(self.queue.read()['probes']), 4)
        with self.assertRaises(StoreError):
            self.queue.begin_recovery(digest(self.doc['protocol']['qualification']))
        with self.assertRaises(StoreError):
            self.queue.claim_probe()

    def test_interrupted_probe_consumes_physical_reservation_and_stops(self):
        self.queue.claim_probe()
        reopened = Campaign(self.root, self.doc)
        with reopened.driver_lock():
            reopened.reconcile_interrupted()
        self.assertEqual(len(reopened.read()['probes']), 1)
        self.assertEqual(reopened.read()['probes'][0]['status'], 'unknown')
        self.assertEqual(reopened.read()['status'], 'stopped')
        with self.assertRaises(StoreError):
            reopened.claim_probe()

    def test_immediate_drift_pauses_after_one_and_keeps_partial_pair(self):
        self.connected()
        slot = self.queue.claim_slot()
        self.queue.finish_slot(result(slot, infra=True, immediate='model_drift'))
        state = self.queue.read()
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['cursor'], 1)
        self.assertEqual(state['partial_pair']['pair_id'], slot['pair_id'])

    def test_serial_lock_and_atomic_state_corruption(self):
        with self.queue.driver_lock():
            with self.assertRaises(RuntimeError):
                with self.queue.driver_lock():
                    pass
        path = self.root / 'campaign-state.json'
        packet = json.loads(path.read_text())
        packet['state']['cursor'] = 3
        path.write_text(json.dumps(packet))
        with self.assertRaises(StoreError):
            self.queue.read()

    def test_inherited_lock_outlives_driver_until_owned_child_exits(self):
        with self.queue.driver_lock() as fd:
            child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)'],
                                     stdin=subprocess.PIPE, pass_fds=(fd,))
        try:
            with self.assertRaises(RuntimeError):
                with self.queue.driver_lock():
                    pass
        finally:
            child.communicate(b'x', timeout=5)
        self.assertEqual(child.returncode, 0)
        with self.queue.driver_lock():
            pass

    def test_host_auth_and_protocol_evidence_pause_immediately(self):
        for status in (401, 403):
            self.assertEqual(immediate_failure('UPSTREAM_HTTP', [
                {'type': 'transport_evidence', 'evidence': {'http_status': status}}]), 'auth')
        self.assertEqual(immediate_failure('MODEL_INCOMPLETE', [
            {'type': 'request_finished', 'anomaly': 'response_model_drift'}]), 'model_drift')
        self.assertEqual(immediate_failure('UPSTREAM_HTTP', [
            {'type': 'transport_evidence', 'evidence': {'rejected_parameter': 'max_output_tokens'}}]), 'protocol_drift')
        self.assertEqual(immediate_failure('SSE_SCHEMA', []), 'protocol_drift')
        self.assertIsNone(immediate_failure('UPSTREAM_HTTP', [
            {'type': 'transport_evidence', 'evidence': {'http_status': 503}}]))

    def test_holdout_remains_conditional_and_default_requires_both_gates(self):
        self.connected()
        for _ in range(36):
            slot = self.queue.claim_slot()
            self.queue.finish_slot(result(slot, tokens=99 if slot['arm'] == 'B' else 100))
        self.assertEqual(self.queue.read()['status'], 'awaiting_gate')
        with self.assertRaises(StoreError):
            self.queue.claim_slot()
        self.assertTrue(self.queue.evaluate()['positive'])
        self.assertFalse(self.queue.read()['B_default_on'])
        for _ in range(18):
            slot = self.queue.claim_slot()
            self.queue.finish_slot(result(slot, tokens=99 if slot['arm'] == 'B' else 100))
        self.assertTrue(self.queue.evaluate()['positive'])
        self.assertTrue(self.queue.read()['B_default_on'])
        self.assertEqual(self.queue.read()['cursor'], 54)

    def test_negative_development_never_opens_holdout(self):
        self.connected()
        for _ in range(36):
            self.queue.finish_slot(result(self.queue.claim_slot()))
        self.assertFalse(self.queue.evaluate()['positive'])
        self.assertEqual(self.queue.read()['status'], 'completed')
        self.assertEqual(self.queue.read()['cursor'], 36)


class GateTests(unittest.TestCase):
    def rows(self):
        doc = document()
        return doc['protocol'], [result(row) for row in doc['protocol']['queue'] if row['phase'] == 'dev']

    def test_positive_any_magnitude_and_direction(self):
        protocol, rows = self.rows()
        self.assertFalse(positive_gate(protocol, rows)['positive'])
        for row in rows:
            if row['arm'] == 'B':
                row['total_tokens'] = 99.9999
        self.assertTrue(positive_gate(protocol, rows)['positive'])
        for row in rows:
            if row['arm'] == 'B':
                row['total_tokens'], row['wall_seconds'] = 101, 10.01
        self.assertFalse(positive_gate(protocol, rows)['positive'])
        for row in rows:
            if row['arm'] == 'B':
                row['wall_seconds'] = 9.9999
        self.assertTrue(positive_gate(protocol, rows)['positive'])

    def test_formal_priority_infra_noninferiority_activation_and_comparability(self):
        protocol, rows = self.rows()
        next(row for row in rows if row['arm'] == 'A')['formal_success'] = False
        self.assertTrue(positive_gate(protocol, rows)['positive'])
        b = next(row for row in rows if row['arm'] == 'B')
        b['infra_terminated'] = True
        self.assertFalse(positive_gate(protocol, rows)['positive'])
        b['infra_terminated'] = False
        b['comparable']['isolation'] = False
        self.assertFalse(positive_gate(protocol, rows)['positive'])
        b['comparable']['isolation'] = True
        for row in rows:
            row['execution_events'] = [{'type': 'verification_triggered', 'basis': 'program'}]
        self.assertFalse(positive_gate(protocol, rows)['positive'])
        b['execution_events'].append({'type': 'model_finished', 'role': 'verifier', 'request_id': 'v'})
        self.assertFalse(mechanism_active(b))
        b['physical_requests'] = [{'request_id': 'v', 'role': 'verifier', 'status': 'finished'}]
        self.assertTrue(positive_gate(protocol, rows)['positive'])
        b['differential_defect'] = True
        self.assertFalse(positive_gate(protocol, rows)['positive'])

    def test_task_medians_not_pooled_and_unknowns_excluded(self):
        rows = []
        for task, ratios in [('D1', [.5, .5, .5]), ('D2', [1.2]), ('D3', [1.3])]:
            for index, ratio in enumerate(ratios):
                for arm in ('A', 'B'):
                    rows.append({'task': task, 'pair_id': task + str(index), 'arm': arm, 'formal_success': True,
                                 'total_tokens': 100 if arm == 'A' else 100 * ratio})
        metric = paired_metric(rows, 'total_tokens')
        self.assertEqual(metric['median_B_over_A'], 1.2)
        self.assertLess(metric['improvement'], 0)
        rows[-1]['total_tokens'] = None
        metric = paired_metric(rows, 'total_tokens')
        self.assertEqual(metric['task_count'], 2)
        self.assertIsNone(metric['improvement'])

    def test_every_physical_attempt_unknown_core_and_optional_are_distinct(self):
        known = {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15, 'cached_input_tokens': None, 'reasoning_tokens': None, 'complete': True}
        projected = usage_projection([{'request_id': '1', 'usage': known}])
        self.assertTrue(projected['core_complete'])
        self.assertFalse(projected['optional_complete'])
        self.assertIsNone(projected['reasoning_tokens'])
        projected = usage_projection([{'request_id': '1', 'usage': known}, {'request_id': '2', 'usage': None}])
        self.assertEqual(projected['physical_requests'], 2)
        self.assertIsNone(projected['total_tokens'])
        self.assertFalse(projected['core_complete'])

    def test_public_inventory_and_required_qualification_hashes(self):
        tasks = public_tasks()
        self.assertEqual(len(tasks), 12)
        self.assertEqual([row['id'] for row in tasks[-3:]], ['R1', 'R2', 'R3'])
        self.assertTrue(all(row['native_checker_sha256'] is None for row in tasks[-3:]))
        source, dependency = {'sha256': 'fixture'}, {'python': 'fixture'}
        qualification = {'format': 'dsh-math-workflow-preflight-v2', 'source_sha256': 'fixture',
            'dependency_sha256': digest(dependency), 'task_sha256': digest(tasks), 'tool_schema_sha256': digest(tool_contracts()),
            'checks': {key: True for key in REQUIRED_PREFLIGHT},
            'certificate_hashes': {row['id']: {key: row[key] for key in ('checker', 'native_checker_sha256', 'certificate_source_sha256')} for row in tasks}}
        validate_qualification(qualification, source, dependency, tasks)
        protocol = build_protocol(source, dependency, tasks, qualification)
        self.assertEqual(protocol['gate'], GATE)
        qualification['checks']['shared_executor_runtime'] = False
        with self.assertRaises(StoreError):
            validate_qualification(qualification, source, dependency, tasks)
        for path in (SOURCE, SOURCE / 'artifacts', '/experiments/dsh-math-optimization/new'):
            with self.assertRaises(StoreError):
                campaign_root(path)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def persist(self, task_id, rows, start, checkpoint):
        entries = []
        for index, row in enumerate(rows, start):
            raw = json.dumps(row).encode()
            path = f'row-{index}.json'
            (self.root / path).write_bytes(raw)
            entries.append({'index': index, 'path': path, 'sha256': hashlib.sha256(raw).hexdigest()})
        (self.root / 'recovery.json').write_text(json.dumps({'format': 'dsh-math-workflow-recovery-v2',
            'task_id': task_id, 'resume_index': checkpoint, 'entries': entries}))

    def test_toy_dp_fresh_resume_and_missing_rows_independent_of_formal(self):
        # Tiny synthetic dimensions, never the real D5 task or a private solution.
        task = {'id': 'D5', 'kind': 'partition_dp', 'parameters': {'N': 3, 'm': 3}}
        rows = [[1, 1, 1, 0], [1, 1, 2, 1], [1, 1, 2, 2]]
        self.persist('D5', rows, 1, 1)
        candidate = {'values': [2, *map(fingerprint, rows)]}
        report = verify_recovery(task, self.root, candidate)
        self.assertTrue(report['resumed'])
        self.assertTrue(report['formal_success_unchanged'])
        without_submission = verify_recovery(task, self.root, None)
        self.assertTrue(without_submission['persisted'])
        self.assertTrue(without_submission['resumed'])
        self.assertFalse(without_submission['candidate_available'])
        self.assertIsNone(without_submission['candidate_matches'])
        self.assertTrue(without_submission['formal_success_unchanged'])
        (self.root / 'row-2.json').write_text('[0]')
        report = verify_recovery(task, self.root, candidate)
        self.assertFalse(report['resumed'])
        self.assertTrue(report['formal_success_unchanged'])

    def test_toy_matrix_chain_is_required_and_resumed_in_fresh_process(self):
        task = {'id': 'D6', 'kind': 'matrix_power', 'parameters': {'A': [[2]], 'b': [3], 'p': 11, 'exponent': 5}}
        rows = [[[2]], [[4]], [[5]]]
        self.persist('D6', rows, 0, 1)
        report = verify_recovery(task, self.root, {'values': [8, *map(fingerprint, rows)]})
        self.assertTrue(report['persisted'])
        self.assertTrue(report['resumed'])
        manifest = json.loads((self.root / 'recovery.json').read_text())
        manifest['entries'][0]['path'] = '../escape.json'
        (self.root / 'recovery.json').write_text(json.dumps(manifest))
        self.assertFalse(verify_recovery(task, self.root, {'values': []})['resumed'])


if __name__ == '__main__':
    unittest.main()
