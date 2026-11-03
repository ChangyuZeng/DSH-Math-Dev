from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_strategy import derive_strategy, reuse_candidates


class WorkflowStrategyTests(unittest.TestCase):
    def setUp(self):
        self.records = {'P': [{'id': 'P', 'rev': 1}], 'N': [{'id': 'N', 'rev': 1}]}
        self.snapshot = {'refs': [{'id': 'P', 'rev': 1}, {'id': 'N', 'rev': 1}],
                         'verification_key': 'fixture-key',
                         'action': {'kind': 'check', 'target_ref': {'id': 'N', 'rev': 1}, 'route_id': 'r'}}
        self.run = {'run_id': 'run', 'project_id': 'project', 'status': 'running',
                    'spec': {'problem_ref': {'id': 'P', 'rev': 1}, 'routes': [{'id': 'r'}, {'id': 'other'}],
                             'model_policy': {'provider': 'fixture', 'model': 'fixed', 'effort': 'fixed'}}, 'actions': []}

    def attempt(self, action_id, progress='no_progress', status='committed'):
        return {'action_id': action_id, 'kind': 'check', 'status': status, 'context_snapshot': deepcopy(self.snapshot),
                'proposal': {'summary': 'Advisory check with unresolved gaps', 'changes': [], 'next_action': 'revise',
                             'next_target': {'id': 'N', 'rev': 1}, 'progress': progress, 'limitations': ['Synthetic result']},
                'output_refs': []}

    def test_no_progress_requires_reassessment_without_mathematical_verdict(self):
        self.run['actions'] = [self.attempt('a'), self.attempt('transport', status='unknown'), self.attempt('b')]
        result = derive_strategy(self.run, self.records)
        route = result['routes'][0]
        self.assertEqual(route['state'], 'needs_reassessment')
        self.assertEqual(route['consecutive_no_progress'], 2)
        self.assertEqual(route['technical_errors'], 1)
        self.assertEqual(route['mathematical_verdict'], 'undetermined')
        self.assertEqual(result['next_action']['kind'], 'stop')
        self.assertIn('require_reassessment', result['next_action']['reason'])
        self.assertFalse(result['human_accepted'])
        self.assertFalse(result['auto_execute'])
        self.assertEqual(result['routes'][1]['state'], 'active')
        self.run['actions'].append(self.attempt('progress', 'new_argument'))
        route = derive_strategy(self.run, self.records)['routes'][0]
        self.assertEqual(route['state'], 'active')
        self.assertEqual(route['consecutive_no_progress'], 0)
        self.assertEqual(route['next_action']['kind'], 'revise')

    def test_technical_failures_and_uncommitted_results_do_not_count(self):
        self.run['actions'] = [self.attempt('a', status='failed'), self.attempt('b', status='unknown'),
                               self.attempt('c', status='proposed')]
        route = derive_strategy(self.run, self.records)['routes'][0]
        self.assertEqual(route['state'], 'active')
        self.assertEqual(route['committed_actions'], 0)
        self.assertEqual(route['consecutive_no_progress'], 0)
        self.assertEqual(route['technical_errors'], 2)

    def test_stale_snapshot_takes_precedence_and_no_reuse(self):
        self.run['actions'] = [self.attempt('a'), self.attempt('b')]
        self.records['N'].append({'id': 'N', 'rev': 2})
        result = derive_strategy(self.run, self.records, context_snapshot=self.snapshot)
        self.assertEqual(result['routes'][0]['state'], 'needs_recheck')
        self.assertEqual(result['next_action']['kind'], 'check')
        self.assertEqual(result['routes'][0]['stale_refs'][0]['ref'], {'id': 'N', 'rev': 1})
        self.assertEqual(result['reuse_candidates'], [])
        # A new committed check with fresh premises replaces the stale latest outcome.
        self.snapshot['refs'][1]['rev'] = 2
        self.snapshot['action']['target_ref']['rev'] = 2
        self.run['actions'].append(self.attempt('fresh', 'new_argument'))
        self.assertEqual(derive_strategy(self.run, self.records)['routes'][0]['state'], 'active')

    def test_pending_stale_snapshot_requires_rebuild_even_without_a_commit(self):
        self.run['actions'] = [self.attempt('pending', status='proposed')]
        self.records['N'].append({'id': 'N', 'rev': 2})
        result = derive_strategy(self.run, self.records)
        self.assertEqual(result['routes'][0]['state'], 'needs_recheck')
        self.assertEqual(result['routes'][0]['context_action_id'], 'pending')
        self.assertTrue(result['next_action']['rebuild_context_required'])

    def test_reuse_matches_exact_fresh_context_and_is_only_advisory(self):
        old = deepcopy(self.run)
        old['run_id'] = 'old'
        old['actions'] = [self.attempt('checked', 'new_argument')]
        before = deepcopy((self.run, old, self.records, self.snapshot))
        result = derive_strategy(self.run, self.records, history=[old, old], context_snapshot=self.snapshot)
        self.assertEqual(len(result['reuse_candidates']), 1)
        candidate = result['reuse_candidates'][0]
        self.assertEqual(candidate['run_id'], 'old')
        self.assertEqual(candidate['action_id'], 'checked')
        self.assertEqual(candidate['refs'], self.snapshot['refs'])
        self.assertEqual(candidate['limitations'], ['Synthetic result'])
        self.assertEqual(candidate['authority'], 'model_advisory')
        self.assertFalse(candidate['auto_skip'])
        self.assertFalse(candidate['human_accepted'])
        self.assertEqual((self.run, old, self.records, self.snapshot), before)
        candidate['refs'][0]['rev'] = 99
        self.assertEqual(old['actions'][0]['context_snapshot']['refs'][0]['rev'], 1)
        self.assertEqual(reuse_candidates(self.run, self.records, None, history=[old]), [])

    def test_reuse_rejects_wrong_key_policy_status_kind_and_authority(self):
        self.run['actions'] = [self.attempt('check', 'new_argument')]
        mutations = [
            lambda run: run['actions'][0]['context_snapshot'].update(verification_key='other-key'),
            lambda run: run['spec']['model_policy'].update(model='other-model'),
            lambda run: run.update(project_id='other-project'),
            lambda run: run['actions'][0].update(status='proposed'),
            lambda run: run['actions'][0].update(kind='derive'),
            lambda run: run['actions'][0]['proposal'].update(human_accepted=True),
            lambda run: run['actions'][0]['proposal'].update(changes=[{'kind': 'Review', 'id': 'R', 'expected_rev': 0, 'data': {}}]),
            lambda run: run['actions'][0]['context_snapshot'].update(refs=[]),
            lambda run: run['actions'][0].update(output_refs=[{'id': 'missing', 'rev': 1}]),
        ]
        current = deepcopy(self.run)
        current['actions'] = []
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                source = deepcopy(self.run)
                source['run_id'] = 'source'
                mutation(source)
                self.assertEqual(reuse_candidates(current, self.records, self.snapshot, history=[source]), [])
        self.run['actions'][0]['kind'] = 'reconstruct'
        self.run['actions'][0]['context_snapshot']['action']['kind'] = 'reconstruct'
        requested = deepcopy(self.snapshot)
        requested['action']['kind'] = 'reconstruct'
        self.assertEqual(len(reuse_candidates(self.run, self.records, requested)), 1)

    def test_reuse_with_advisory_note_requires_fresh_exact_output_refs(self):
        attempt = self.attempt('check', 'new_argument')
        note = {'note_type': 'argument', 'title': 'Advisory check', 'summary': 'Unresolved', 'body': 'Check details',
                'statement': 'Fixture statement', 'assumptions': [], 'problem_ref': {'id': 'P', 'rev': 1},
                'route_id': None, 'uses': [], 'candidate_for': [], 'evidence': [], 'gaps': ['Gap'], 'failure': None}
        attempt['proposal']['changes'] = [{'kind': 'Note', 'id': 'CHECK', 'expected_rev': 0, 'data': note}]
        attempt['output_refs'] = [{'id': 'CHECK', 'rev': 1}]
        self.records['CHECK'] = [{'id': 'CHECK', 'rev': 1, 'kind': 'Note', 'data': note}]
        self.run['actions'] = [attempt]
        candidate = reuse_candidates(self.run, self.records, self.snapshot)[0]
        self.assertEqual(candidate['output_refs'], [{'id': 'CHECK', 'rev': 1}])
        self.records['CHECK'].append({'id': 'CHECK', 'rev': 2})
        self.assertEqual(reuse_candidates(self.run, self.records, self.snapshot), [])

    def test_terminal_run_stops_and_current_snapshot_overrides_old_copy(self):
        old_copy = deepcopy(self.run)
        old_copy['actions'] = [self.attempt('old', 'new_argument')]
        for state in ('completed', 'cancelled', 'needs_reconcile'):
            self.run['status'] = state
            result = derive_strategy(self.run, self.records, history=[old_copy], context_snapshot=self.snapshot)
            self.assertEqual(result['next_action']['kind'], 'stop')
            self.assertEqual(result['next_action']['reason'], 'run_' + state)
            self.assertEqual(result['reuse_candidates'], [])
            self.assertEqual(result['scientific_status'], 'candidate_pending_review')


if __name__ == '__main__':
    unittest.main()
