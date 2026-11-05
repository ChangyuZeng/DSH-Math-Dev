"""P0 regressions: no network or model requests."""
import sys
import threading
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evaluation' / 'four-harness-v1'))
from tools import ToolLimits


class OptionalComputeLimits(unittest.TestCase):
    def test_null_limits_disable_only_cpu_and_wall_guards(self):
        limits = ToolLimits(cpu_seconds=None, timeout_seconds=None)
        self.assertIsNone(limits.cpu_seconds)
        self.assertIsNone(limits.timeout_seconds)
        self.assertGreater(limits.memory_bytes, 0)
        self.assertGreater(limits.max_output_bytes, 0)

    def test_invalid_nonnull_guards_still_rejected(self):
        for value in (0, -1, float('inf'), True):
            for key in ('cpu_seconds', 'timeout_seconds'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    ToolLimits(**{key: value})


class NativeOutputDurability(unittest.TestCase):
    def test_logs_survive_cleanup_failure_without_rerunning_process(self):
        import native, tempfile, os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            captures = Path(directory).resolve() / 'captures'
            with patch.object(native.OwnedProcessTree, 'cleanup', side_effect=RuntimeError('cleanup fixture')):
                with self.assertRaises(RuntimeError):
                    native.run_process([sys.executable, '-c', "print('saved-before-cleanup', flush=True)"],
                                       env=dict(os.environ), cwd=directory, capture_dir=captures)
            self.assertEqual((captures / 'stdout.log').read_text(), 'saved-before-cleanup\n')

    def test_no_cumulative_deadline_is_supported(self):
        import native, tempfile, os
        with tempfile.TemporaryDirectory() as directory:
            result = native.run_process([sys.executable, '-c', "print('done')"],
                                       env=dict(os.environ), cwd=directory, timeout=None)
            self.assertEqual(result['stdout'], 'done\n')
            self.assertFalse(result['timed_out'])


class OptimizationLedger(unittest.TestCase):
    def setUp(self):
        import tempfile
        from ledger import Ledger, OPTIMIZATION_BUDGET
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name).resolve() / 'ledger.sqlite')
        self.ledger.create_campaign('c', max_requests=120, max_tokens=None, scope='optimization')
        self.ledger.create_run('r', 'c', 'M', budget=OPTIMIZATION_BUDGET)
        self.payload = {'model': 'gpt-6-astra', 'reasoning': {'effort': 'max'},
                        'max_output_tokens': 128000, 'input': '数学' * 60000}

    def gateway(self, responses):
        import hashlib
        from gateway import Gateway, FixtureTransport
        from tools import CommonTools, ToolLimits
        root = Path(self.tmp.name).resolve()
        material = root / 'public.txt'
        material.write_text('Non-target fixture task.\n')
        host = CommonTools('r', root / 'tools', {'task': {'path': material,
            'sha256': hashlib.sha256(material.read_bytes()).hexdigest()}},
            limits=ToolLimits(cpu_seconds=None, timeout_seconds=None))
        transport = FixtureTransport(responses)
        gateway = Gateway(self.ledger, transport=transport,
            bindings={'secret': {'run_id': 'r', 'arm': 'M', 'role': 'worker'}}, tools={'r': host},
            frozen_materials={'r': host.material_inventory()}).start()
        self.addCleanup(gateway.close)
        return gateway, transport

    def post(self, gateway):
        import json
        from urllib.request import Request, build_opener, ProxyHandler
        from urllib.error import HTTPError
        request = Request(gateway.url + '/M/worker/v1/responses', json.dumps(self.payload).encode(),
            {'Authorization': 'Bearer secret', 'Content-Type': 'application/json'})
        try:
            result = build_opener(ProxyHandler({})).open(request, timeout=3)
        except HTTPError as error:
            result = error
        with result:
            return result.status, result.read()

    @unittest.skipUnless(__import__('os').environ.get('FOUR_HARNESS_NATIVE_FIXTURES') == '1', 'explicit isolated native fixture opt-in')
    def test_optimization_native_null_budget_compute_and_commit(self):
        import json, subprocess
        from native import run_process
        from x_live import prepare_m_optimization
        from test_four_harness_x_live import call_response, _fixture_response
        import os, shutil
        node = Path(os.environ.get('DSH_MATH_NODE') or shutil.which('node')).resolve()
        bridge = Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1/m_bridge.mjs'
        proposal = json.loads(subprocess.run([str(node), '--input-type=module', '-e',
            f'import {{syntheticProposal}} from {json.dumps(bridge.as_uri())}; console.log(JSON.stringify(syntheticProposal()));'],
            capture_output=True, text=True, check=True).stdout)
        end = _fixture_response(2, [{'type':'message', 'id':'m2', 'role':'assistant', 'status':'completed',
            'content':[{'type':'output_text', 'text':json.dumps(proposal)}]}])
        responses = [call_response(1, 'compute', {'code':'print(2+3)'}), end]
        for response in responses:
            response['reasoning'] = {'effort':'max'}
        gw, transport = self.gateway(responses)
        problem = {'title':'Non-target fixture', 'statement':'Compute 2+3.', 'quantifiers':'one input',
            'domain':'integers', 'conventions':[], 'target_type':'exploratory', 'parent':None, 'relation':'',
            'completion_criteria':['Record result'], 'sources':['synthetic fixture'], 'knowledge_status':'synthetic_fixture'}
        plan = prepare_m_optimization(Path(self.tmp.name).resolve() / 'native', node=node, python=sys.executable,
            gateway_url=gw.url, token='secret', execution={'mode':'authorized_optimization', 'arm':'M',
            'run_id':'r', 'campaign_id':'c', 'model_requests':120, 'campaign_max_requests':120}, offline=True,
            bootstrap={'problem':problem, 'stageGoal':'Record the synthetic result.'})
        result = run_process(plan['command'], env=plan['env'], cwd=plan['cwd'], timeout=None,
            capture_dir=Path(self.tmp.name).resolve() / 'capture')
        self.assertEqual(result['returncode'], 0, result)
        evidence = json.loads(Path(plan['evidence_path']).read_text())
        self.assertEqual(len(transport.requests), 2, evidence)
        self.assertEqual(self.ledger.snapshot('r')['requests'], 2)

    def test_gateway_retry_is_metered_and_preserves_unknown_subtotals(self):
        from unittest.mock import patch
        from test_four_harness_gateway import response
        gw, transport = self.gateway([
            {'status': 'failed', 'output': [], 'error': {'code': 'http_503'}}, response()])
        with patch('gateway.retry_delay', return_value=0):
            self.assertEqual(self.post(gw)[0], 200)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(self.ledger.snapshot('r')['requests'], 2)
        self.assertIsNone(self.ledger.snapshot('r')['total_tokens'])

    def test_gateway_cancel_during_retry_wait_dispatches_no_second_attempt(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        gw, transport = self.gateway([{'status': 'failed', 'output': [], 'error': {'code': 'http_503'}}])
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.post, gw)
            deadline = time.monotonic() + 2
            while not any(event.get('type') == 'retry_scheduled' for event in self.ledger.events()) and time.monotonic() < deadline:
                threading.Event().wait(.01)
            gw.cancel_event.set()
            self.assertEqual(pending.result(timeout=2)[0], 409)
        self.assertEqual(len(transport.requests), 1)

    def attempt(self, retry_of=None):
        rid = self.ledger.reserve_request('r', 'worker', self.payload, retry_of=retry_of)
        self.ledger.start_request(rid)
        return rid

    def test_explicit_transient_keeps_unknown_usage_and_charges_each_attempt(self):
        from ledger import retryable_response, GateError
        failed = {'status': 'failed', 'error': {'code': 'server_is_overloaded'}, 'output': []}
        self.assertTrue(retryable_response(failed))
        rid = self.attempt()
        self.ledger.finish_request(rid, failed, recoverable=True)
        second = self.attempt(retry_of=rid)
        with self.assertRaises(GateError):
            self.attempt(retry_of=rid)
        self.assertIsNone(self.ledger.snapshot('r')['total_tokens'])
        self.assertEqual(self.ledger.snapshot('r')['requests'], 2)
        self.assertNotEqual(rid, second)

    def test_retries_cannot_evade_120_physical_requests(self):
        from ledger import GateError
        failed = {'status': 'failed', 'error': {'code': 'http_503'}, 'output': []}
        previous = None
        for _ in range(120):
            previous = self.attempt(previous)
            self.ledger.finish_request(previous, failed, recoverable=True)
        with self.assertRaises(GateError) as caught:
            self.attempt(previous)
        self.assertEqual(caught.exception.code, 'REQUEST_BUDGET')
        self.assertEqual(self.ledger.snapshot('r')['requests'], 120)

    def test_nontransient_drift_output_and_ambiguous_receipts_never_retry(self):
        from ledger import retryable_response
        for value in [
            {'status': 'failed', 'error': {'code': 'invalid_request_error'}},
            {'status': 'failed', 'model': 'other', 'error': {'code': 'http_503'}},
            {'status': 'failed', 'reasoning': {'effort': 'high'}, 'error': {'code': 'http_503'}},
            {'status': 'failed', 'output': [{'type': 'function_call'}], 'error': {'code': 'http_503'}},
            {'status': 'incomplete', 'error': {'code': 'http_503'}},
            None]:
            self.assertFalse(retryable_response(value))

    def test_retry_after_numeric_date_invalid_and_backoff(self):
        from ledger import retry_delay
        self.assertEqual(retry_delay({'error': {'headers': {'Retry-After': '12'}}}, 1), 12)
        self.assertEqual(retry_delay({'error': {'headers': {'retry-after-ms': '2500'}}}, 1), 2.5)
        self.assertEqual(retry_delay({'error': {'headers': {'Retry-After': 'bad'}}}, 2), 10)
        self.assertEqual(retry_delay({'error': {'headers': []}}, 20), 60)
        from unittest.mock import patch
        with patch('ledger.time.time', return_value=0):
            self.assertEqual(retry_delay({'error': {'headers': {'Retry-After': 'Thu, 01 Jan 1970 00:00:20 GMT'}}}, 1), 20)


if __name__ == '__main__':
    unittest.main()
