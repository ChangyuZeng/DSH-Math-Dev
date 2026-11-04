"""Request-level budget, unknown outcome and crash fixtures; no model/network."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'
sys.path.insert(0, str(ROOT))
from ledger import Ledger, GateError, FORMAL_BUDGET, SMOKE_BUDGET, normalize_usage


def request(**changes):
    return dict(model='gpt-6-astra', reasoning={'effort': 'max'}, max_output_tokens=100,
                input=[{'role': 'user', 'content': 'Non-target fixture: return 2+3.'}], **changes)


def response(**changes):
    data = dict(id='fixture-response', model='gpt-6-astra', status='completed', usage={
        'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120,
        'input_tokens_details': {'cached_tokens': 60}, 'output_tokens_details': {'reasoning_tokens': 10}})
    data.update(changes)
    return data


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name).resolve() / 'host' / 'ledger.sqlite')
        self.ledger.create_campaign('fixture', max_requests=8, max_tokens=131072, scope='smoke')
        self.budget = dict(SMOKE_BUDGET)
        self.ledger.create_run('C1', 'fixture', 'C', budget=self.budget)

    def code(self, code, fn, *args, **kwargs):
        with self.assertRaises(GateError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def dispatched(self, run='C1', role='agent', **kwargs):
        rid = self.ledger.reserve_request(run, role, request(), **kwargs)
        self.ledger.start_request(rid)
        return rid

    def test_numeric_cpu_subtotal_does_not_erase_incomplete_observation(self):
        for tool, flags, expected in [
            ('partial', {'cpu_observation_complete': False, 'cpu_accounting_complete': False}, False),
            ('unattested', {}, None),
            ('complete', {'cpu_accounting_complete': True}, True),
        ]:
            self.ledger.tool_event('C1', {'type': 'tool_started', 'request_id': tool})
            self.ledger.tool_event('C1', {'type': 'tool_finished', 'request_id': tool,
                'metrics': {'cpu_seconds': 0.25, **flags}})
            rows = self.ledger.snapshot('C1')['resource_observations']['tools']
            self.assertEqual(rows[-1]['cpu_seconds'], 0.25)
            self.assertIs(rows[-1]['metrics']['cpu_observation_complete'], expected)
        self.assertFalse(self.ledger.snapshot('C1')['resource_observations']['tool_cpu_complete'])

    def test_cpu_is_observed_without_equal_budget_or_invented_totals(self):
        self.assertFalse({'cpu_seconds', 'cpu_cores', 'memory_bytes'} & FORMAL_BUDGET.keys())
        for tool, observed in [('long', 1000000.0), ('missing', None)]:
            self.ledger.tool_event('C1', {'type': 'tool_started', 'request_id': tool})
            self.ledger.tool_event('C1', {'type': 'tool_finished', 'request_id': tool, 'cpu_seconds': observed})
        snapshot = self.ledger.snapshot('C1')
        self.assertIsNone(snapshot['terminal'])
        observed = snapshot['resource_observations']
        self.assertFalse(observed['equal_cpu_or_memory_budget'])
        self.assertFalse(observed['tool_cpu_complete'])
        self.assertIsNone(observed['aggregate_cpu_seconds'])
        self.assertEqual([row['cpu_seconds'] for row in observed['tools']], [1000000.0, None])
        rid = self.dispatched()
        self.ledger.finish_request(rid, response())
        self.assertEqual(self.ledger.snapshot('C1')['requests'], 1)

    def test_usage_subsets_not_double_counted_and_details_remain_unknown(self):
        usage = normalize_usage(response()['usage'])
        self.assertEqual(usage['total_tokens'], 120)
        self.assertEqual(usage['cached_input_tokens'], 60)
        self.assertEqual(usage['reasoning_tokens'], 10)
        self.assertTrue(usage['detail_complete'])
        usage = normalize_usage({'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120})
        self.assertIsNone(usage['cached_input_tokens'])
        self.assertIsNone(usage['reasoning_tokens'])
        self.assertTrue(usage['complete'])
        self.assertFalse(usage['detail_complete'])
        self.assertFalse(normalize_usage({'input_tokens': True, 'output_tokens': 2, 'total_tokens': 3})['complete'])
        self.assertFalse(normalize_usage({'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 5})['complete'])

    def test_model_or_effort_drift_stops_before_reservation(self):
        for field, value in [('model', 'other'), ('reasoning', {'effort': 'xhigh'})]:
            payload = request(); payload[field] = value
            self.code('MODEL_DRIFT', self.ledger.reserve_request, 'C1', 'agent', payload)
        self.assertEqual(self.ledger.snapshot('C1')['requests'], 0)

    def test_missing_usage_retains_reservation_and_blocks_new_run(self):
        rid = self.dispatched()
        reserved = self.ledger.snapshot('C1')['charged_or_reserved_tokens']
        self.ledger.finish_request(rid, response(usage=None))
        state = self.ledger.snapshot('C1')
        self.assertEqual(state['charged_or_reserved_tokens'], reserved)
        self.assertIsNone(state['total_tokens'])
        self.assertFalse(state['accounting_complete'])
        self.code('STOPPED', self.ledger.reserve_request, 'C1', 'agent', request())
        self.code('CAMPAIGN_CLOSED', self.ledger.create_run, 'X1', 'fixture', 'X', budget=self.budget)
        self.code('NO_REPLAY', self.ledger.start_request, rid)

    def test_missing_or_inconsistent_total_keeps_observed_cost_lower_bound(self):
        for reported_total in (None, 2):
            with self.subTest(reported_total=reported_total):
                # Independent ledger identity, never reset the interrupted run.
                isolated = Ledger(Path(self.tmp.name).resolve() / ('partial-' + str(reported_total) + '.sqlite'))
                isolated.create_campaign('p', max_requests=2, max_tokens=50000)
                isolated.create_run('P1', 'p', 'C')
                rid = isolated.reserve_request('P1', 'agent', request())
                isolated.start_request(rid)
                isolated.finish_request(rid, response(usage={'input_tokens': 20000, 'output_tokens': 1,
                                                            'total_tokens': reported_total}))
                state = isolated.snapshot('P1')
                self.assertIsNone(state['total_tokens'])
                self.assertEqual(state['known_total_tokens'], 20001)
                self.assertEqual(state['charged_or_reserved_tokens'], 20001)
                self.assertEqual(state['terminal'], 'usage_unknown')

    def test_late_incomplete_receipt_cannot_erase_observed_cost(self):
        rid = self.dispatched()
        self.ledger.finish_request(rid, response(usage={'input_tokens': 20000, 'output_tokens': 1}))
        self.code('RECEIPT_REGRESSION', self.ledger.finish_request, rid, response(usage=None), late_receipt=True)
        self.code('RECEIPT_REGRESSION', self.ledger.finish_request, rid,
                  response(usage={'input_tokens': 19999, 'output_tokens': 1, 'total_tokens': 20000}), late_receipt=True)
        state = self.ledger.snapshot('C1')
        self.assertEqual(state['known_total_tokens'], 20001)
        self.assertEqual(state['charged_or_reserved_tokens'], 20001)
        self.assertIsNone(state['total_tokens'])

    def test_late_model_and_tool_receipts_never_reopen_stopped_run(self):
        rid = self.dispatched()
        self.ledger.tool_event('C1', {'type': 'tool_started', 'request_id': 'late-tool'})
        self.ledger.stop('C1', 'cancelled')
        self.ledger.finish_request(rid, response(), late_receipt=True)
        self.ledger.tool_event('C1', {'type': 'tool_finished', 'request_id': 'late-tool', 'cpu_seconds': 0.2})
        state = self.ledger.snapshot('C1')
        self.assertEqual(state['terminal'], 'cancelled')
        self.assertEqual(state['total_tokens'], 120)
        self.code('STOPPED', self.ledger.reserve_request, 'C1', 'agent', request())
        self.code('NO_REPLAY', self.ledger.finish_request, rid, response(), late_receipt=True)
        self.code('TOOL_SCHEMA', self.ledger.tool_event, 'C1',
                  {'type': 'tool_finished', 'request_id': 'late-tool', 'cpu_seconds': 0.2})

    def test_concurrent_reservations_never_overbook(self):
        def reserve(_):
            try:
                return self.ledger.reserve_request('C1', 'agent', request())
            except GateError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=12) as workers:
            outcomes = list(workers.map(reserve, range(12)))
        self.assertEqual(outcomes.count('REQUEST_BUDGET'), 10)
        self.assertEqual(self.ledger.snapshot('C1')['requests'], 2)

    def test_total_token_budget_checks_reservations_not_only_known_usage(self):
        self.ledger.create_campaign('tiny', max_requests=10, max_tokens=5000)
        self.ledger.create_run('tiny1', 'tiny', 'M', budget=FORMAL_BUDGET)
        self.ledger.reserve_request('tiny1', 'worker', request())
        self.code('CAMPAIGN_BUDGET', self.ledger.reserve_request, 'tiny1', 'worker', request())

    def test_every_role_and_descendant_uses_one_run_budget(self):
        self.ledger.create_run('R1', 'fixture', 'R', budget=self.budget)
        parent = self.dispatched('R1', 'generator')
        self.ledger.finish_request(parent, response())
        child = self.dispatched('R1', 'verifier', parent_request_id=parent, thread_id='child')
        self.ledger.finish_request(child, response())
        self.code('REQUEST_BUDGET', self.ledger.reserve_request, 'R1', 'repair', request())
        self.assertEqual(self.ledger.snapshot('R1')['total_tokens'], 240)
        self.code('CROSS_RUN', self.ledger.reserve_request, 'C1', 'agent', request(), parent_request_id=parent)

    def test_cancel_keeps_outstanding_cost_and_never_replays(self):
        rid = self.dispatched()
        self.ledger.stop('C1', 'cancelled')
        state = self.ledger.reconcile('C1')
        self.assertEqual(state['terminal'], 'cancelled')
        self.assertEqual(state['request_states'][0]['state'], 'unknown')
        self.assertGreater(state['charged_or_reserved_tokens'], 0)
        self.code('NO_REPLAY', self.ledger.start_request, rid)

    def test_crash_reopen_marks_reserved_and_started_unknown_once(self):
        self.dispatched()
        self.ledger.reserve_request('C1', 'compaction', request())
        reopened = Ledger(self.ledger.path)
        first = reopened.reconcile('C1')
        count = len(reopened.events())
        self.assertEqual(first['requests'], 2)
        self.assertEqual([r['state'] for r in first['request_states']], ['unknown', 'unknown'])
        reopened.reconcile('C1')
        self.assertEqual(len(reopened.events()), count)

    def test_response_drift_cost_and_actual_overrun_are_preserved(self):
        rid = self.dispatched()
        self.ledger.finish_request(rid, response(model='other'))
        self.assertEqual(self.ledger.snapshot('C1')['total_tokens'], 120)
        self.assertEqual(self.ledger.snapshot('C1')['terminal'], 'response_model_drift')

    def test_overrun_is_not_clamped_or_discarded(self):
        rid = self.dispatched()
        self.ledger.finish_request(rid, response(usage={'input_tokens': 100, 'output_tokens': 200, 'total_tokens': 300}))
        self.assertEqual(self.ledger.snapshot('C1')['total_tokens'], 300)
        self.assertEqual(self.ledger.snapshot('C1')['terminal'], 'reservation_exceeded')

    def test_invalid_context_and_output_are_rejected(self):
        payload = request(); payload.pop('max_output_tokens')
        self.code('OUTPUT_CAP', self.ledger.reserve_request, 'C1', 'agent', payload)
        payload = request(); payload['input'] = 'x' * 70000
        self.code('CONTEXT_BUDGET', self.ledger.reserve_request, 'C1', 'agent', payload)
        payload = request(); payload['previous_response_id'] = 'unseen'
        self.code('HIDDEN_CONTEXT', self.ledger.reserve_request, 'C1', 'agent', payload)

    def test_cross_run_tool_completion_and_duplicate_identity_rejected(self):
        self.ledger.create_run('M1', 'fixture', 'M', budget=self.budget)
        self.ledger.tool_event('C1', {'type': 'tool_started', 'request_id': 'tool-1'})
        self.code('TOOL_SCHEMA', self.ledger.tool_event, 'M1',
                  {'type': 'tool_finished', 'request_id': 'tool-1', 'cpu_seconds': 0.1})
        self.ledger.tool_event('C1', {'type': 'tool_finished', 'request_id': 'tool-1', 'cpu_seconds': 0.1})
        self.code('ALREADY_EXISTS', self.ledger.create_run, 'C1', 'fixture', 'M', budget=self.budget)

    def test_scope_ceilings_and_failed_slots_cannot_be_replaced(self):
        self.code('BUDGET', self.ledger.create_campaign, 'too-big-smoke', max_requests=8, max_tokens=131073, scope='smoke')
        self.code('BUDGET', self.ledger.create_campaign, 'too-big-formal', max_requests=2401, max_tokens=20000000, scope='formal')
        for key in SMOKE_BUDGET:
            self.code('BUDGET', self.ledger.create_run, 'bad-' + key, 'fixture', 'X',
                      budget={**SMOKE_BUDGET, key: SMOKE_BUDGET[key] + 1})
        self.ledger.stop('C1', 'failed_before_first_request')
        self.code('NO_REPLACEMENT', self.ledger.create_run, 'C2', 'fixture', 'C', budget=SMOKE_BUDGET)
        self.ledger.create_campaign('formal', max_requests=2400, max_tokens=20000000, scope='formal')
        for index in range(5):
            self.ledger.create_run('F' + str(index), 'formal', 'C', budget=FORMAL_BUDGET)
            self.ledger.stop('F' + str(index), 'fixture_failure')
        self.code('NO_REPLACEMENT', self.ledger.create_run, 'F5', 'formal', 'C', budget=FORMAL_BUDGET)
        self.code('BUDGET', self.ledger.create_run, 'Xtoo', 'formal', 'X',
                  budget={**FORMAL_BUDGET, 'model_requests': 121})

    def test_observed_usage_subsets_preserve_unknown_cost_lower_bounds(self):
        cases = [({'output_tokens': 10, 'input_tokens_details': {'cached_tokens': 20000}}, 20010),
                 ({'input_tokens': 10, 'output_tokens_details': {'reasoning_tokens': 20000}}, 20010),
                 ({'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20,
                   'input_tokens_details': {'cached_tokens': 20000},
                   'output_tokens_details': {'reasoning_tokens': 30000}}, 50000)]
        for index, (usage, expected) in enumerate(cases):
            campaign = f'subsets-{index}'; run = f'cost-{index}'
            self.ledger.create_campaign(campaign, max_requests=2, max_tokens=100000)
            self.ledger.create_run(run, campaign, 'C')
            rid = self.ledger.reserve_request(run, 'agent', request())
            self.ledger.start_request(rid); self.ledger.finish_request(rid, response(usage=usage))
            state = self.ledger.snapshot(run)
            self.assertIsNone(state['total_tokens'])
            self.assertEqual(state['known_total_tokens'], expected)
            self.assertEqual(state['charged_or_reserved_tokens'], expected)

    def test_reported_response_effort_drift_stops_without_losing_usage(self):
        rid = self.ledger.reserve_request('C1', 'agent', request())
        self.ledger.start_request(rid)
        raw = response(); raw['reasoning'] = {'effort': 'high'}
        self.ledger.finish_request(rid, raw)
        state = self.ledger.snapshot('C1')
        self.assertEqual(state['terminal'], 'response_effort_drift')
        self.assertEqual(state['total_tokens'], raw['usage']['total_tokens'])
        self.assertEqual([e for e in self.ledger.events() if e['type'] == 'request_finished'][0]['actual_effort'], 'high')

    def test_native_lineage_is_durable_exact_and_shared_by_descendants(self):
        self.ledger.create_campaign('native', max_requests=12, max_tokens=500000)
        self.ledger.create_run('RN', 'native', 'R')
        root = {'thread_id': 'root', 'turn_id': 'root-turn', 'request_kind': 'turn'}
        parent = self.ledger.reserve_request('RN', 'generator', request(), native_metadata=root)
        self.ledger.start_request(parent); self.ledger.finish_request(parent, response())
        child_meta = {'thread_id': 'child', 'turn_id': 'child-turn', 'parent_thread_id': 'root',
                      'parent_turn_id': 'root-turn', 'subagent_kind': 'thread_spawn', 'request_kind': 'turn'}
        child = self.ledger.reserve_request('RN', 'strategy', request(), native_metadata=child_meta)
        self.ledger.start_request(child); self.ledger.finish_request(child, response())
        reopened = Ledger(self.ledger.path)
        self.assertEqual(reopened.resolve_native_request('RN', child_meta), child)
        continuation = reopened.reserve_request('RN', 'strategy', request(), native_metadata=child_meta)
        states = {r['id']: r for r in reopened.snapshot('RN')['request_states']}
        self.assertEqual(states[child]['parent'], parent)
        self.assertEqual(states[continuation]['parent'], child)
        self.assertEqual(states[child]['thread'], 'child')
        self.code('NATIVE_LINEAGE_DRIFT', reopened.reserve_request, 'RN', 'strategy', request(),
                  native_metadata={**child_meta, 'parent_thread_id': 'unrelated'})
        self.code('NATIVE_PARENT_UNKNOWN', reopened.resolve_native_request, 'RN', child_meta)

    def test_native_async_spawn_never_guesses_latest_physical_parent(self):
        self.ledger.create_campaign('native', max_requests=12, max_tokens=500000)
        self.ledger.create_run('RN', 'native', 'R')
        root = {'thread_id': 'root', 'turn_id': 'p1'}
        first = self.ledger.reserve_request('RN', 'generator', request(), native_metadata=root)
        self.ledger.start_request(first); self.ledger.finish_request(first, response())
        following = self.ledger.reserve_request('RN', 'generator', request(), native_metadata=root)
        self.ledger.start_request(following)
        child = {'thread_id': 'child', 'turn_id': 'c1', 'parent_thread_id': 'root', 'parent_turn_id': 'p1', 'subagent_kind': 'thread_spawn'}
        self.code('NATIVE_PARENT_UNKNOWN', self.ledger.reserve_request, 'RN', 'strategy', request(), native_metadata=child)
        bound = self.ledger.reserve_request('RN', 'strategy', request(), native_metadata=child, parent_request_id=first)
        self.ledger.start_request(bound); self.ledger.finish_request(bound, response())
        self.ledger.finish_request(following, response())
        self.code('NATIVE_PARENT_UNKNOWN', self.ledger.reserve_request, 'RN', 'strategy', request(),
                  native_metadata={**child, 'thread_id': 'second-child'})
        states = {row['id']: row for row in self.ledger.snapshot('RN')['request_states']}
        self.assertEqual(states[bound]['parent'], first)
        self.assertNotEqual(states[bound]['parent'], following)

    def test_native_static_host_parent_header_does_not_break_tool_continuation(self):
        self.ledger.create_campaign('native', max_requests=12, max_tokens=500000)
        self.ledger.create_run('RN', 'native', 'R')
        parent = self.ledger.reserve_request('RN', 'generator', request())
        self.ledger.start_request(parent); self.ledger.finish_request(parent, response())
        meta = {'thread_id': 'verifier', 'turn_id': 'v1'}
        first = self.ledger.reserve_request('RN', 'verifier', request(), native_metadata=meta, parent_request_id=parent)
        self.ledger.start_request(first); self.ledger.finish_request(first, response())
        following = self.ledger.reserve_request('RN', 'verifier', request(), native_metadata=meta, parent_request_id=parent)
        states = {row['id']: row for row in self.ledger.snapshot('RN')['request_states']}
        self.assertEqual(states[following]['parent'], first)

    def test_native_reused_child_thread_binds_each_new_parent_turn(self):
        self.ledger.create_campaign('native', max_requests=12, max_tokens=500000)
        self.ledger.create_run('RN', 'native', 'R')
        def completed(thread, turn, parent_turn=None):
            meta = {'thread_id': thread, 'turn_id': turn}
            if parent_turn:
                meta.update(parent_thread_id='root', parent_turn_id=parent_turn, subagent_kind='thread_spawn')
            rid = self.ledger.reserve_request('RN', 'generator', request(), native_metadata=meta)
            self.ledger.start_request(rid); self.ledger.finish_request(rid, response())
            return rid, meta
        first, _ = completed('root', 'p1')
        child1, meta1 = completed('child', 'c1', 'p1')
        second, _ = completed('root', 'p2')
        self.ledger = Ledger(self.ledger.path)
        child2, meta2 = completed('child', 'c2', 'p2')
        states = {row['id']: row for row in self.ledger.snapshot('RN')['request_states']}
        self.assertEqual(states[child1]['parent'], first)
        self.assertEqual(states[child2]['parent'], second)
        self.code('NATIVE_LINEAGE_DRIFT', self.ledger.reserve_request, 'RN', 'generator', request(),
                  native_metadata={**meta2, 'parent_turn_id': 'p1'})
        continuation = self.ledger.reserve_request('RN', 'generator', request(), native_metadata=meta2)
        states = {row['id']: row for row in self.ledger.snapshot('RN')['request_states']}
        self.assertEqual(states[continuation]['parent'], child2)

    def test_native_unknown_ambiguous_and_cross_run_parents_fail_before_dispatch(self):
        self.ledger.create_campaign('native', max_requests=12, max_tokens=500000)
        self.ledger.create_run('RN', 'native', 'R')
        self.ledger.create_run('RN2', 'native', 'R')
        for turn in ['t1', 't2']:
            rid = self.ledger.reserve_request('RN', 'generator', request(), native_metadata={'thread_id': 'root', 'turn_id': turn})
            self.ledger.start_request(rid); self.ledger.finish_request(rid, response())
        child = {'thread_id': 'child', 'turn_id': 't3', 'parent_thread_id': 'root', 'subagent_kind': 'thread_spawn'}
        self.code('NATIVE_PARENT_UNKNOWN', self.ledger.reserve_request, 'RN', 'strategy', request(), native_metadata=child)
        self.code('NATIVE_PARENT_UNKNOWN', self.ledger.reserve_request, 'RN2', 'strategy', request(),
                  native_metadata={**child, 'parent_turn_id': 't1'})
        self.assertEqual(self.ledger.snapshot('RN2')['requests'], 0)
        self.code('NATIVE_FORK_UNQUALIFIED', self.ledger.reserve_request, 'RN', 'strategy', request(),
                  native_metadata={'thread_id': 'fork', 'forked_from_thread_id': 'root'})
        self.code('NATIVE_KIND_UNQUALIFIED', self.ledger.reserve_request, 'RN', 'generator', request(),
                  native_metadata={'thread_id': 'root', 'request_kind': 'memory'})

    def test_hash_chain_detects_tamper(self):
        self.assertGreater(len(self.ledger.events()), 0)
        with self.ledger.connection() as db:
            db.execute("UPDATE events SET event='{}' WHERE seq=1")
        with self.assertRaises((GateError, KeyError)):
            self.ledger.events()


if __name__ == '__main__':
    unittest.main()
