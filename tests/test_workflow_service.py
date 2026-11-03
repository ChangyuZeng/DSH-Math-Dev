"""Cross-store commit recovery and service serialization, entirely offline."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_workflow_store as fixtures
from schema import StoreError
from workflow_service import commit_proposal, control, dispatch_internal, transaction
from workflow_store import WorkflowStore


class ServiceTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    request = fixtures.WorkflowTests.request
    call = fixtures.WorkflowTests.call
    prepare = fixtures.WorkflowTests.prepare
    head = fixtures.WorkflowTests.head
    context = fixtures.WorkflowTests.context
    reserve = fixtures.WorkflowTests.reserve
    assertCode = fixtures.WorkflowTests.assertCode

    def proposed(self, *, next_target=None, changes=True):
        run = self.reserve(self.call('start', self.prepare()))
        data = {'note_type': 'argument', 'title': 'Candidate', 'summary': 'Unreviewed', 'body': 'Candidate argument',
                'statement': 'Candidate claim', 'assumptions': [], 'problem_ref': {'id': 'P', 'rev': 1},
                'route_id': None, 'uses': [], 'candidate_for': [], 'evidence': [], 'gaps': ['Needs review'], 'failure': None}
        proposal = {'summary': 'Candidate only', 'changes': [{'kind': 'Note', 'id': 'N', 'expected_rev': 0, 'data': data}] if changes else [],
                    'next_action': 'check', 'next_target': next_target, 'progress': 'new_argument', 'limitations': ['Not accepted']}
        return self.call('propose', run, action_id=run['actions'][0]['action_id'], proposal=proposal,
                         usage={'total_tokens': 40, 'output_tokens': 20})

    def archive(self, run):
        return commit_proposal(self.root, self.project, 'r1', run['rev'], run['actions'][0]['action_id'])

    def test_commit_records_exact_refs_model_authority_and_is_idempotent(self):
        run = self.proposed(next_target={'id': 'N', 'rev': 1})
        result = self.archive(run)
        self.assertEqual(result['actions'][0]['output_refs'], [{'id': 'N', 'rev': 1}])
        first_head = self.head()
        self.assertEqual(self.archive(run), result)
        self.assertEqual(self.head(), first_head)
        with self.projects.open_project(self.project) as (_, research):
            event = research.operations['workflow-' + run['actions'][0]['action_id']]
            self.assertEqual(event['actor']['kind'], 'model')
        self.assertEqual(result['scientific_status'], 'candidate_pending_review')

    def crash_after_research_commit(self, run):
        original = WorkflowStore.dispatch
        def interrupted(instance, request):
            if request['action'] == 'commit':
                raise OSError('sidecar publication interrupted')
            return original(instance, request)
        with patch.object(WorkflowStore, 'dispatch', interrupted):
            with self.assertRaises(OSError):
                self.archive(run)
        return self.head()

    def test_exact_receipt_recovers_crash_without_second_research_commit(self):
        run = self.proposed()
        committed_head = self.crash_after_research_commit(run)
        recovered = self.archive(run)
        self.assertEqual(self.head(), committed_head)
        self.assertEqual(recovered['actions'][0]['status'], 'committed')

    def test_reconcile_only_reads_receipt_and_never_reexecutes_research(self):
        run = self.proposed()
        command = {'action': 'reconcile', 'run_id': 'r1', 'expected_rev': run['rev'], 'operation_id': 'reconcile'}
        self.assertCode('RECONCILE_REQUIRED', control, self.root, self.project, 'session', command)
        self.assertEqual((self.projects.path(self.project) / 'research/events.jsonl').read_bytes(), self.before)
        committed_head = self.crash_after_research_commit(run)
        recovered = control(self.root, self.project, 'session', command)
        self.assertEqual(recovered['actions'][0]['status'], 'committed')
        self.assertEqual(self.head(), committed_head)

    def test_next_target_must_exist_before_research_write(self):
        run = self.proposed(next_target={'id': 'missing', 'rev': 1})
        self.assertCode('NOT_FOUND', self.archive, run)
        self.assertEqual((self.projects.path(self.project) / 'research/events.jsonl').read_bytes(), self.before)

    def test_control_commit_and_cancelled_proposal_are_safe(self):
        run = self.proposed()
        command = {'action': 'commit', 'run_id': 'r1', 'expected_rev': run['rev'], 'action_id': run['actions'][0]['action_id']}
        result = control(self.root, self.project, 'session', command)
        self.assertEqual(result['actions'][0]['status'], 'committed')
        self.assertEqual(control(self.root, self.project, 'session', command), result)

    def test_all_service_mutations_share_cross_process_lock(self):
        run = self.proposed()
        with transaction(self.root, self.project):
            self.assertCode('LOCKED', self.archive, run)
            self.assertCode('LOCKED', dispatch_internal, self.root, 'workflow-state', self.request('cancel', run))
            script = ('from workflow_service import commit_proposal; from schema import StoreError; import sys\n'
                      'try: commit_proposal(sys.argv[1],sys.argv[2],"r1",int(sys.argv[3]),sys.argv[4])\n'
                      'except StoreError as e: print(e.code)\n')
            result = subprocess.run([sys.executable, '-B', '-c', script, str(self.root), self.project,
                                     str(run['rev']), run['actions'][0]['action_id']],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'LOCKED')
        cancelled = dispatch_internal(self.root, 'workflow-state', self.request('cancel', run))
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertCode('INVALID_STATE', self.archive, run)
        self.assertEqual((self.projects.path(self.project) / 'research/events.jsonl').read_bytes(), self.before)

    def test_foreign_receipt_is_never_adopted(self):
        run = self.proposed()
        changes = run['actions'][0]['proposal']['changes']
        with self.projects.open_project(self.project, writable=True) as (_, research):
            research.commit('workflow-' + run['actions'][0]['action_id'], changes)
        self.assertCode('OPERATION_CONFLICT', self.archive, run)

    def test_receipt_cannot_be_hidden_by_cancelling_after_sidecar_failure(self):
        run = self.proposed()
        self.crash_after_research_commit(run)
        self.assertCode('RECONCILE_REQUIRED', dispatch_internal, self.root, 'workflow-state', self.request('cancel', run))
        self.assertCode('RECONCILE_REQUIRED', dispatch_internal, self.root, 'workflow-state',
                        self.request('fail', run, action_id=run['actions'][0]['action_id'], reason='discard'))
        self.assertEqual(self.archive(run)['actions'][0]['status'], 'committed')

    def test_empty_committed_proposal_retry_ignores_later_premise_changes(self):
        run = self.proposed(changes=False)
        committed = self.archive(run)
        with self.projects.open_project(self.project, writable=True) as (_, research):
            problem = research.get('P', 1)['data']
            problem['statement'] = 'Updated human premise'
            research.commit('human-update', [{'kind': 'Problem', 'id': 'P', 'expected_rev': 1, 'data': problem}])
        self.assertEqual(self.archive(run), committed)

    def test_public_read_adds_projections_without_changing_durable_state(self):
        committed = self.archive(self.proposed())
        request = {'action': 'read', 'project_id': self.project, 'run_id': 'r1'}
        durable = self.store.dispatch(request)
        research_head = self.head()
        event_dir = self.projects.path(self.project) / 'workflows'
        events = {path.name: path.read_bytes() for path in event_dir.glob('*.json')}
        result = control(self.root, self.project, 'session', {'action': 'read', 'run_id': 'r1'})
        self.assertEqual({key: value for key, value in result.items() if key not in ('strategy', 'completion_projection')}, durable)
        self.assertEqual(result['rev'], committed['rev'])
        self.assertEqual(result['strategy']['routes'][0]['state'], 'active')
        self.assertEqual(result['strategy']['next_action']['kind'], 'check')
        self.assertFalse(result['strategy']['human_accepted'])
        self.assertFalse(result['strategy']['auto_execute'])
        self.assertEqual(result['strategy']['reuse_candidates'], [])
        claim = next(row for row in result['completion_projection'] if row['ref'] == {'id': 'N', 'rev': 1})
        self.assertFalse(claim['human_accepted'])
        self.assertFalse(claim['global_problem_proved'])
        self.assertEqual(self.store.dispatch(request), durable)
        internal = dispatch_internal(self.root, 'workflow-state', request)
        self.assertNotIn('strategy', internal)
        self.assertNotIn('completion_projection', internal)
        self.assertEqual(self.head(), research_head)
        self.assertEqual({path.name: path.read_bytes() for path in event_dir.glob('*.json')}, events)

    def test_public_read_projects_stale_dependencies_and_keeps_rev(self):
        committed = self.archive(self.proposed(changes=False))
        with self.projects.open_project(self.project, writable=True) as (_, research):
            problem = research.get('P', 1)['data']
            problem['statement'] = 'Updated human premise'
            research.commit('human-update', [{'kind': 'Problem', 'id': 'P', 'expected_rev': 1, 'data': problem}])
        result = control(self.root, self.project, 'session', {'action': 'read', 'run_id': 'r1'})
        self.assertEqual(result['rev'], committed['rev'])
        self.assertEqual(result['status'], committed['status'])
        self.assertEqual(result['strategy']['routes'][0]['state'], 'needs_recheck')
        self.assertTrue(result['strategy']['next_action']['rebuild_context_required'])

    def test_public_read_checks_authorization_and_shared_lock_before_projection(self):
        self.prepare()
        command = {'action': 'read', 'run_id': 'r1'}
        with patch('workflow_service.derive_strategy') as strategy:
            with self.assertRaises(StoreError):
                control(self.root, self.project, 'unassociated-session', command)
            strategy.assert_not_called()
            with transaction(self.root, self.project):
                self.assertCode('LOCKED', control, self.root, self.project, 'session', command)
            strategy.assert_not_called()

    def test_partial_stream_usage_cannot_unlock_after_failure(self):
        run = self.proposed(changes=False)
        run = self.call('fail', run, action_id=run['actions'][0]['action_id'], reason='interrupted stream', accounting_complete=False)
        self.assertFalse(run['usage']['complete'])
        self.assertEqual(run['usage']['total_tokens'], 40)
        self.assertCode('INVALID_STATE', self.call, 'start', run)


if __name__ == '__main__':
    unittest.main()
