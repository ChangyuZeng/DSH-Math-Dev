"""Physical receipt and lineage regressions; no network or model calls."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_workflow_store as fixtures
from workflow_v2 import ACCOUNTING, MODEL, TIMEOUTS, normalize_accounting, project_usage


def row(identity='physical-1', *, status='finished', total=30):
    return {'request_id': identity, 'logical_request_id': 'logical-1', 'parent_request_id': None,
            'role': 'worker', 'status': status, 'usage_complete': total is not None and status == 'finished',
            'usage': None if total is None else {'input_tokens': total - 10, 'output_tokens': 10,
                'total_tokens': total, 'cached_input_tokens': None, 'reasoning_tokens': None},
            'raw_usage': None if total is None else {'input_tokens': total - 10, 'output_tokens': 10, 'total_tokens': total}}


class WorkflowV2Tests(unittest.TestCase):
    # Reuse setup helpers, not the v1 behavioral tests.
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    request = fixtures.WorkflowTests.request
    call = fixtures.WorkflowTests.call
    head = fixtures.WorkflowTests.head
    context = fixtures.WorkflowTests.context
    reserve = fixtures.WorkflowTests.reserve
    commit = fixtures.WorkflowTests.commit
    assertCode = fixtures.WorkflowTests.assertCode

    def prepare(self):
        self.spec.update(format='dsh-math-workflow-v2', model_policy=deepcopy(MODEL),
            budget={'requests': 120, 'total_tokens': None, 'max_output_tokens': 128000},
            context_budget={'max_tokens': None}, execution={'transport': 'metered-codex',
            'accounting_authority': 'gateway', 'verification_policy': 'model-directed-v1',
            'max_revisions': 2, 'timeouts': deepcopy(TIMEOUTS), 'isolation': 'os-process', 'checker': None})
        return self.call('prepare', spec=self.spec)

    def account(self, run, rows):
        return self.call('account', run, action_id=run['actions'][-1]['action_id'],
                         accounting={'format': ACCOUNTING, 'requests': rows})

    def proposal(self, run):
        return self.call('propose', run, action_id=run['actions'][-1]['action_id'],
                         proposal={'candidate': 'unreviewed'}, usage=None)

    def test_retry_rows_count_physically_and_optional_details_stay_unknown(self):
        run = self.reserve(self.call('start', self.prepare()))
        self.assertEqual(run['usage']['requests'], 0)
        run = self.account(run, [row('retry', status='retryable', total=None), row('terminal')])
        run = self.proposal(run)
        self.assertEqual(run['usage']['requests'], 2)
        self.assertEqual(run['usage']['known_total_tokens'], 30)
        self.assertIsNone(run['usage']['total_tokens'])
        self.assertIsNone(run['usage']['cached_input_tokens'])
        self.assertEqual(run['usage']['unknown_requests'], 1)
        self.assertEqual(run['status'], 'running')
        run = self.commit(run)
        # No cumulative cap: incomplete prior retry usage is not a fabricated zero
        # and does not suppress a subsequent independent action.
        self.assertEqual(self.reserve(run)['usage']['requests'], 2)

    def test_complete_core_does_not_require_optional_details(self):
        value = project_usage([row()])
        self.assertTrue(value['complete'])
        self.assertEqual(value['total_tokens'], 30)
        self.assertIsNone(value['reasoning_tokens'])

    def test_partial_counters_are_lower_bounds_even_when_every_core_number_is_present(self):
        partial = row(status='unknown')
        partial['usage_complete'] = False
        value = project_usage([partial])
        self.assertIsNone(value['total_tokens'])
        self.assertEqual(value['known_total_tokens'], 30)
        self.assertFalse(value['complete'])
        self.assertEqual(value['unknown_requests'], 1)

    def test_immutable_receipts_and_started_to_terminal_reconciliation(self):
        run = self.reserve(self.call('start', self.prepare()))
        run = self.account(run, [row(status='started', total=None)])
        run = self.account(run, [row()])
        unchanged = self.account(run, [row()])
        self.assertEqual(unchanged['usage']['requests'], 1)
        self.assertCode('OPERATION_CONFLICT', self.account, unchanged, [row(total=31)])
        self.assertCode('ACCOUNTING', self.account, unchanged, [row(), row()])

    def test_refuses_logical_usage_and_proposal_without_receipts(self):
        run = self.reserve(self.call('start', self.prepare()))
        self.assertCode('ACCOUNTING', self.proposal, run)
        run = self.account(run, [row()])
        self.assertCode('ACCOUNTING', self.call, 'propose', run, action_id=run['actions'][-1]['action_id'],
                        proposal={}, usage={'total_tokens': 30, 'output_tokens': 10})

    def test_continuation_retains_cumulative_physical_quota(self):
        run = self.reserve(self.call('start', self.prepare()))
        run = self.account(run, [row()])
        run = self.commit(self.proposal(run))
        run = self.call('pause', run)
        child = self.call('continue', run, spec=self.spec, new_run_id='child', research_head=self.head())
        self.assertEqual(child['lineage_root_run_id'], 'r1')
        self.assertEqual(child['usage']['requests'], 1)
        changed = deepcopy(self.spec)
        changed['budget']['requests'] = 119
        self.assertCode('LINEAGE_POLICY', self.call, 'continue', run, spec=changed,
                        new_run_id='different', research_head=self.head())
        v1 = deepcopy(self.spec)
        v1['format'] = 'dsh-math-workflow-v1'
        del v1['execution']
        self.assertCode('LINEAGE_POLICY', self.call, 'continue', run, spec=v1,
                        new_run_id='downgrade', research_head=self.head())

    def test_121st_physical_receipt_cannot_enter_journal(self):
        run = self.reserve(self.call('start', self.prepare()))
        run = self.account(run, [row('p' + str(i)) for i in range(120)])
        self.assertCode('BUDGET', self.account, run, [row('p120')])
        run = self.proposal(run)
        self.assertEqual(run['reason'], 'budget_exhausted')
        self.assertEqual(run['usage']['requests'], 120)

    def test_unreconciled_dispatch_never_reports_zero_total_or_invents_attempt_count(self):
        run = self.reserve(self.call('start', self.prepare()))
        action_id = run['actions'][-1]['action_id']
        run = self.call('event', run, action_id=action_id,
                        event={'type': 'model_started', 'logical_id': 'logical-1', 'role': 'worker'})
        self.assertIsNone(run['usage']['total_tokens'])
        self.assertFalse(run['usage']['requests_complete'])
        self.assertEqual(run['usage']['requests'], 0)  # Known receipts, not a claimed full count.
        self.assertEqual(run['usage']['unreconciled_calls'], 1)
        run = self.account(run, [row()])
        self.assertIsNone(run['usage']['total_tokens'])
        self.assertEqual(run['usage']['known_total_tokens'], 30)
        run = self.call('event', run, action_id=action_id, event={'type': 'model_finished', 'logical_id': 'logical-1'})
        self.assertTrue(run['usage']['requests_complete'])
        self.assertEqual(run['usage']['total_tokens'], 30)
        failed = self.call('fail', run, action_id=action_id, reason='tool_outcome_unknown')
        self.assertTrue(failed['usage']['complete'])
        self.assertEqual(failed['usage']['total_tokens'], 30)
        self.assertEqual(failed['status'], 'needs_reconcile')

    def test_failed_dispatch_without_projection_preserves_unknown_accounting(self):
        run = self.reserve(self.call('start', self.prepare()))
        action_id = run['actions'][-1]['action_id']
        for event in ({'type': 'model_started', 'logical_id': 'lost'},
                      {'type': 'model_failed', 'logical_id': 'lost', 'accounting_available': False}):
            run = self.call('event', run, action_id=action_id, event=event)
        run = self.call('fail', run, action_id=action_id, reason='transport_receipt_lost')
        self.assertFalse(run['usage']['complete'])
        self.assertIsNone(run['usage']['total_tokens'])
        self.assertEqual(run['usage']['known_total_tokens'], 0)
        child = self.call('continue', run, spec=self.spec, new_run_id='pending-child', research_head=self.head())
        self.assertIsNone(child['usage']['total_tokens'])
        self.assertFalse(child['usage']['requests_complete'])
        self.assertEqual(child['usage']['unreconciled_calls'], 1)

    def test_tool_effects_and_checker_binding_are_required(self):
        run = self.prepare()
        spec = deepcopy(self.spec)
        spec['tools'] = [{'name': 'submit', 'version': 'v2', 'read_only': False,
                         'schema_sha256': 'a' * 64, 'effect': 'submit'}]
        self.assertCode('CHECKER_BINDING', self.call, 'prepare', spec=spec, run_id='bad')
        spec['execution']['checker'] = {'id': 'fixture', 'code_sha256': 'b' * 64, 'task_sha256': 'c' * 64}
        spec['tools'][0]['read_only'] = True
        self.assertCode('TOOL_CAPABILITY', self.call, 'prepare', spec=spec, run_id='bad')
        self.assertEqual(run['scientific_status'], 'candidate_pending_review')


if __name__ == '__main__':
    unittest.main()
