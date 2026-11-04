"""Operator-entry gates and a published certificate replay, never target search."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from copy import deepcopy

SOURCE = Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'
sys.path.insert(0, str(SOURCE))
from audit import validate_witness
import qualify
from qualify import draft, main, smoke_plan, fixture_acceptance


class QualificationEntryTests(unittest.TestCase):
    def test_public_nine_eighths_certificate_is_exactly_reproduced_not_improved(self):
        # Public thread274, core in sixteenths with three zero-pair padding:
        # https://einsteinarena.com/problems/ring-loading-15/threads/274
        core = [(5,11),(4,4),(5,11),(12,2),(8,8),(2,12),
                (11,5),(4,4),(5,11),(12,2),(8,8),(2,12)]
        witness = {'pairs': [[a * 65536, b * 65536] for a, b in core] + [[0, 0]] * 3}
        checked = validate_witness(witness)
        self.assertEqual(checked['H'], 1179648)
        self.assertFalse(checked['target_met'])

    def test_live_entry_is_closed_even_if_a_file_claims_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.json'
            path.write_text(json.dumps({'execution_authorized': True, 'gates': {'claimed': True}}))
            output = StringIO()
            with redirect_stdout(output):
                code = main(['launch', '--scope', 'smoke', '--arm', 'M', '--manifest', str(path)])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())['code'], 'LIVE_NOT_AUTHORIZED')

    def test_smoke_is_bounded_non_target_and_not_execution_permission(self):
        plan = smoke_plan()
        self.assertFalse(plan['authorized'])
        self.assertFalse(plan['executable'])
        self.assertFalse(plan['target_problem_allowed'])
        self.assertFalse(plan['target_checker_and_submission_allowed'])
        self.assertEqual({tool['name'] for tool in plan['common_tools']},
                         {'materials_list', 'materials_read', 'workspace_read', 'workspace_write', 'compute'})
        self.assertEqual(plan['model'], {'provider': 'openai-codex', 'model': 'gpt-6-astra', 'effort': 'max'})
        self.assertEqual(sum(arm['max_requests'] for arm in plan['arms']), 8)
        self.assertEqual(plan['per_run_budget']['model_requests'], 2)
        self.assertEqual(plan['per_run_budget']['max_output_tokens'], 2048)

    def test_fixture_exit_code_is_nonzero_on_failed_qualification(self):
        output = StringIO()
        with patch('qualify.fixture', return_value={'passed': False}), redirect_stdout(output):
            self.assertEqual(main(['fixture', 'M', '--output', 'unused']), 2)
        self.assertFalse(json.loads(output.getvalue())['passed'])

    def test_success_requires_successful_tool_and_matching_native_continuation(self):
        state = {'terminal': None, 'requests': 2, 'accounting_complete': True, 'detail_complete': True}
        receipt = {'tool': 'materials.list', 'ok': True, 'request_id': 'tool1', 'result_sha256': 'a' * 64}
        events = [{'type': 'tool_finished', 'tool': receipt}]
        output = {'ok': True, 'result': [], 'metrics': {'request_id': 'tool1', 'result_sha256': 'a' * 64}}
        requests = [{'payload': {}}, {'payload': {'input': [{'type': 'function_call_output',
                    'call_id': 'call_fixture_1', 'output': json.dumps(output)}]}}]
        self.assertTrue(fixture_acceptance({'returncode': 0}, state, events, requests)['passed'])
        failed = deepcopy(events); failed[0]['tool']['ok'] = False
        self.assertFalse(fixture_acceptance({'returncode': 0}, state, failed, requests)['passed'])
        missing = deepcopy(requests); missing[1]['payload']['input'] = []
        self.assertFalse(fixture_acceptance({'returncode': 0}, state, events, missing)['passed'])
        mismatched = deepcopy(requests); mismatched[1]['payload']['input'][0]['call_id'] = 'different-call'
        self.assertFalse(fixture_acceptance({'returncode': 0}, state, events, mismatched)['passed'])
        self.assertFalse(fixture_acceptance({'returncode': 0}, {**state, 'detail_complete': False}, events, requests)['passed'])

    def test_active_patch_edits_change_arm_frozen_adapter_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = draft(tmp)
            actual_sha = qualify.sha
            def changed_sha(path):
                return 'f' * 64 if Path(path).name == 'xean-native.ts' else actual_sha(path)
            with patch('qualify.sha', side_effect=changed_sha):
                changed = draft(tmp)
        self.assertTrue({'patches/xean-native.ts', 'x_full.py', 'patches/xean-host-fixture.ts',
                         'patches/rethlas-mcp.py', 'patches/rethlas-mcp-requirements.txt',
                         'patches/rethlas-mcp-fixture.py', 'patches/rethlas-mcp-fixture-child.py'}
                        <= original['adapter_sources_sha256'].keys())
        arms = {a['id']: a['adapter_sha256'] for a in original['arms']}
        changed_arms = {a['id']: a['adapter_sha256'] for a in changed['arms']}
        self.assertNotEqual(arms['X'], changed_arms['X'])
        self.assertEqual(arms['M'], changed_arms['M'])

    def test_missing_sources_cannot_produce_an_executable_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            frozen = draft(tmp)
        self.assertEqual(frozen['status'], 'draft_not_executable')
        self.assertFalse(frozen['execution_authorized'])
        self.assertTrue(all(value is False for value in frozen['gates'].values()))
        self.assertTrue(all(not arm['source'].get('source_verified', False) for arm in frozen['arms'][:3]))
        self.assertEqual(frozen['model']['resolved_version'], None)
        self.assertEqual(frozen['problem']['materials_sha256'], frozen['cold_input']['materials_sha256'])
        self.assertEqual(frozen['protocol_revision'], 'mac-cold-start-v2')
        self.assertFalse(frozen['information_policy']['public_baseline_or_routes_visible'])
        self.assertFalse(frozen['resource_policy']['equal_cpu_or_memory_budget'])
        self.assertFalse({'cpu_seconds', 'cpu_cores', 'memory_bytes'} & frozen['per_run_budget'].keys())
        self.assertEqual(len(frozen['adapter_sources_sha256']['ledger.py']), 64)
        self.assertFalse(frozen['runtime']['qualified'])


if __name__ == '__main__':
    unittest.main()
