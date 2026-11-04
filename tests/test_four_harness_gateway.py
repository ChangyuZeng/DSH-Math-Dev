"""Loopback integration and MCP capability fixtures, never an upstream model."""
from concurrent.futures import Future, ThreadPoolExecutor
from io import StringIO
import threading
from unittest.mock import patch
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

SOURCE = Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'
sys.path.insert(0, str(SOURCE))
from ledger import FORMAL_BUDGET, Ledger
from gateway import Gateway, FixtureTransport, capability, responses_tools
import mcp


def response(**changes):
    return dict(id='resp_fixture', model='gpt-6-astra', status='completed', output=[{
        'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'status': 'completed',
        'content': [{'type': 'output_text', 'text': '5', 'annotations': []}]}],
        usage={'input_tokens': 20, 'output_tokens': 1, 'total_tokens': 21,
               'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}, **changes)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name).resolve() / 'ledger.sqlite')
        self.ledger.create_campaign('offline', max_requests=8, max_tokens=200000)
        self.ledger.create_run('M1', 'offline', 'M', budget={**FORMAL_BUDGET, 'model_requests': 2})
        self.token = capability()
        self.opener = build_opener(ProxyHandler({}))
        self.payload = {'model': 'gpt-6-astra', 'reasoning': {'effort': 'max'}, 'max_output_tokens': 100,
                        'input': [{'role': 'user', 'content': 'What is 2+3?'}], 'tools': responses_tools()}

    def start(self, transport=None, **kwargs):
        self.transport = transport or FixtureTransport([response(), response()])
        gateway = Gateway(self.ledger, transport=self.transport,
                          bindings={self.token: {'run_id': 'M1', 'arm': 'M', 'role': 'worker'}}, **kwargs).start()
        self.addCleanup(gateway.close)
        return gateway

    def post(self, gw, payload=None, path='/M/worker/v1/responses', token=None, headers=None):
        req = Request(gw.url + path, json.dumps(payload if payload is not None else self.payload).encode(),
                      {'Authorization': 'Bearer ' + (token or self.token), 'Content-Type': 'application/json', **(headers or {})})
        try:
            result = self.opener.open(req, timeout=3)
        except HTTPError as error:
            result = error
        with result:
            raw = result.read().decode()
            return result.status, raw if result.headers['Content-Type'] == 'text/event-stream' else json.loads(raw)

    def test_native_compatible_sse_and_each_physical_request_is_charged(self):
        gw = self.start()
        payload = {**self.payload, 'stream': True}
        status, body = self.post(gw, payload)
        self.assertEqual(status, 200)
        self.assertIn('event: response.completed', body)
        self.assertIn('"reasoning_tokens":0', body)
        self.assertTrue(gw.is_offline)
        self.assertEqual(self.post(gw)[0], 200)
        self.assertEqual(self.post(gw)[1]['error']['code'], 'REQUEST_BUDGET')
        self.assertEqual(len(self.transport.requests), 2)
        self.assertEqual(self.ledger.snapshot('M1')['total_tokens'], 42)

    def test_cross_run_role_or_auth_never_dispatches(self):
        gw = self.start()
        self.assertEqual(self.post(gw, path='/R/verifier/v1/responses')[1]['error']['code'], 'ENDPOINT')
        self.assertEqual(self.post(gw, token='invalid')[1]['error']['code'], 'UNAUTHORIZED')
        self.assertEqual(self.transport.requests, [])

    def test_shell_or_web_tool_and_model_drift_are_rejected(self):
        gw = self.start()
        for tool in [{'type': 'web_search'}, {'type': 'function', 'name': 'shell', 'parameters': {}}]:
            self.assertEqual(self.post(gw, {**self.payload, 'tools': [tool]})[1]['error']['code'], 'TOOL_NOT_ALLOWED')
        self.assertEqual(self.post(gw, {**self.payload, 'model': 'fallback'})[1]['error']['code'], 'MODEL_DRIFT')
        self.assertEqual(self.transport.requests, [])
        rejected = [e for e in self.ledger.events() if e['type'] == 'request_rejected']
        self.assertEqual(len(rejected), 3)

    def test_native_additional_tools_cannot_bypass_top_level_allowlist(self):
        gw = self.start()
        for tools in [[{'type': 'function', 'name': 'shell'}],
                      [{'type': 'namespace', 'name': 'functions', 'tools': [{'type': 'function', 'name': 'apply_patch'}]}]]:
            status, reply = self.post(gw, {**self.payload, 'input': [{'type': 'additional_tools', 'tools': tools}], 'tools': []})
            self.assertEqual(status, 409)
            self.assertEqual(reply['error']['code'], 'TOOL_NOT_ALLOWED')
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(self.ledger.snapshot('M1')['requests'], 0)

    def test_partial_receipt_never_becomes_zero_or_gets_replayed(self):
        partial = response(); partial['usage'] = {'output_tokens': 1}
        gw = self.start(FixtureTransport([partial, response()]))
        status, error = self.post(gw)
        self.assertEqual(status, 409)
        self.assertTrue(error['error']['dispatched'])
        self.assertEqual(error['error']['code'], 'USAGE_UNKNOWN')
        self.assertEqual(self.post(gw)[1]['error']['code'], 'STOPPED')
        self.assertEqual(len(self.transport.requests), 1)
        state = self.ledger.snapshot('M1')
        self.assertIsNone(state['total_tokens'])
        self.assertGreater(state['charged_or_reserved_tokens'], 0)

    def test_native_canonical_metadata_links_children_tools_and_compaction(self):
        gw = self.start(intrinsic_tools={('M1', 'worker'): {'native_verify'}})
        gw.bindings[self.token]['require_native_metadata'] = True
        self.assertEqual(self.post(gw)[1]['error']['code'], 'NATIVE_METADATA')
        root = {'thread_id': 'native-root', 'turn_id': 'native-turn', 'request_kind': 'turn'}
        self.assertEqual(self.post(gw, {**self.payload, 'client_metadata': {'x-codex-turn-metadata': json.dumps(root)}})[0], 200)
        parent = self.transport.requests[0]['request_id']
        tool = {'phase': 'start', 'name': 'native_verify', 'call_id': 'native-call', 'args_sha256': 'a' * 64}
        headers = {'X-Codex-Turn-Metadata': json.dumps(root)}
        status, receipt = self.post(gw, tool, path='/M/worker/intrinsic', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(receipt['parent_request_id'], parent)
        finish = {'phase': 'finish', 'name': 'native_verify', 'call_id': 'native-call', 'result_sha256': 'b' * 64,
                  'cpu_seconds': 0.01, 'wall_seconds': 0.02, 'status': 'completed'}
        self.assertEqual(self.post(gw, finish, path='/M/worker/intrinsic', headers=headers)[0], 200)
        child = {'thread_id': 'native-compact', 'turn_id': 'compact-turn', 'parent_thread_id': 'native-root',
                 'parent_turn_id': 'native-turn', 'subagent_kind': 'compact', 'request_kind': 'compaction'}
        self.assertEqual(self.post(gw, {**self.payload, 'client_metadata': {'x-codex-turn-metadata': json.dumps(child)}})[0], 200)
        states = self.ledger.snapshot('M1')['request_states']
        self.assertEqual(states[1]['role'], 'compaction')
        self.assertEqual(states[1]['parent'], parent)
        self.assertEqual(self.transport.requests[1]['role'], 'compaction')
        self.assertEqual(self.ledger.snapshot('M1')['total_tokens'], 42)

    def test_mcp_forwards_original_native_turn_metadata(self):
        class Tools:
            def call(self, name, args, **kwargs):
                return {'ok': True, 'result': [], 'metrics': {'request_id': 'native-tool', 'result_sha256': 'a' * 64}}
        gw = self.start(tools={'M1': Tools()})
        gw.bindings[self.token]['require_native_metadata'] = True
        native = {'thread_id': 'mcp-root', 'turn_id': 'mcp-turn'}
        self.assertEqual(self.post(gw, {**self.payload, 'client_metadata': {'x-codex-turn-metadata': json.dumps(native)}})[0], 200)
        source = StringIO(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': 'materials_list', 'arguments': {}, '_meta': {'x-codex-turn-metadata': native}}}) + '\n')
        output = StringIO()
        mcp.serve(gw.url + '/M/worker/tools', self.token, source, output)
        result = json.loads(json.loads(output.getvalue())['result']['content'][0]['text'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['metrics']['model_request_id'], self.transport.requests[0]['request_id'])

    def test_native_metadata_conflict_and_unknown_tool_parent_never_dispatch(self):
        gw = self.start(intrinsic_tools={('M1', 'worker'): {'native_verify'}})
        root = {'thread_id': 'native-root', 'turn_id': 'native-turn'}
        body = {**self.payload, 'client_metadata': {'x-codex-turn-metadata': json.dumps(root)}}
        self.assertEqual(self.post(gw, body, headers={'X-Codex-Turn-Metadata': json.dumps({**root, 'thread_id': 'other'})})[1]['error']['code'], 'NATIVE_LINEAGE_DRIFT')
        tool = {'phase': 'start', 'name': 'native_verify', 'call_id': 'native-call', 'args_sha256': 'a' * 64}
        self.assertEqual(self.post(gw, tool, path='/M/worker/intrinsic', headers={'X-Codex-Turn-Metadata': json.dumps(root)})[1]['error']['code'], 'NATIVE_PARENT_UNKNOWN')
        self.assertEqual(len(self.transport.requests), 0)
        self.assertFalse(any(e['type'] == 'tool_started' for e in self.ledger.events()))

    def test_transport_crash_is_unknown_not_retry(self):
        calls = []
        class Crash:
            def start(self, payload, context):
                calls.append(context['request_id'])
                raise ConnectionResetError('fixture disconnected')
        gw = self.start(Crash())
        self.assertEqual(self.post(gw)[1]['error']['code'], 'TRANSPORT_ERROR')
        self.assertEqual(self.post(gw)[1]['error']['code'], 'STOPPED')
        self.assertEqual(len(calls), 1)
        self.assertFalse(gw.is_offline)

    def test_close_between_reservation_and_transport_prevents_dispatch(self):
        started, release = threading.Event(), threading.Event()
        original = self.ledger.start_request
        def paused(request_id):
            original(request_id)
            started.set()
            release.wait(3)
        gw = self.start(drain_timeout=0)
        with patch.object(self.ledger, 'start_request', paused), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.post, gw)
            try:
                self.assertTrue(started.wait(2))
                gw.close()
            finally:
                release.set()
            status, body = pending.result(timeout=3)
        self.assertEqual(status, 409)
        self.assertFalse(body['error']['dispatched'])
        self.assertEqual(self.transport.requests, [])
        state = self.ledger.snapshot('M1')
        self.assertEqual(state['request_states'][0]['state'], 'unknown')
        self.assertIsNone(state['total_tokens'])

    def test_late_receipt_after_shutdown_preserves_actual_overrun(self):
        entered, receipt_future = threading.Event(), Future()
        calls = []
        class Slow:
            def start(self, payload, context):
                calls.append(context['request_id'])
                entered.set()
                return receipt_future
        receipt = response()
        receipt['usage'] = {'input_tokens': 20000, 'output_tokens': 1, 'total_tokens': 20001}
        gw = self.start(Slow(), drain_timeout=0)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.post, gw)
            try:
                self.assertTrue(entered.wait(2))
                gw.close()
                self.assertIsNone(self.ledger.snapshot('M1')['total_tokens'])
            finally:
                receipt_future.set_result(receipt)
            self.assertEqual(pending.result(timeout=3)[0], 409)
        state = self.ledger.snapshot('M1')
        self.assertEqual(state['terminal'], 'gateway_closed')
        self.assertEqual(state['known_total_tokens'], 20001)
        self.assertEqual(state['charged_or_reserved_tokens'], 20001)
        self.assertIsNotNone(state['request_states'][0]['raw_usage'])
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(e.get('late_receipt') for e in self.ledger.events()))

    def test_cancel_during_drain_keeps_usage_without_delivering_success(self):
        entered, receipt_future = threading.Event(), Future()
        class Slow:
            def start(self, payload, context):
                entered.set()
                return receipt_future
        gw = self.start(Slow(), drain_timeout=2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.post, gw)
            self.assertTrue(entered.wait(2))
            closing = pool.submit(gw.close)
            try:
                self.assertTrue(gw.cancel_event.wait(2))
            finally:
                receipt_future.set_result(response())
            status, body = pending.result(timeout=3)
            closing.result(timeout=3)
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'STOPPED')
        self.assertEqual(body['error']['usage']['total_tokens'], 21)
        self.assertEqual(self.ledger.snapshot('M1')['total_tokens'], 21)
        self.assertEqual(self.ledger.snapshot('M1')['terminal'], 'gateway_closed')

    def test_failed_provider_event_is_not_rewritten_as_incomplete(self):
        receipt = response()
        receipt['status'] = 'failed'
        receipt['error'] = {'code': 'fixture_failure', 'message': 'Synthetic provider failure'}
        gw = self.start(FixtureTransport([receipt]))
        status, body = self.post(gw, {**self.payload, 'stream': True})
        self.assertEqual(status, 200)
        self.assertIn('event: response.failed', body)
        self.assertNotIn('event: response.incomplete', body)
        self.assertEqual(self.ledger.snapshot('M1')['total_tokens'], 21)
        self.assertEqual(self.ledger.snapshot('M1')['terminal'], 'provider_failed')
        self.assertEqual(self.post(gw)[1]['error']['code'], 'STOPPED')

    def test_output_injection_is_explicit_recorded_adaptation(self):
        gw = self.start(inject_output_cap=True)
        payload = dict(self.payload); del payload['max_output_tokens']
        self.assertEqual(self.post(gw, payload)[0], 200)
        self.assertEqual(self.transport.requests[0]['payload']['max_output_tokens'], 16384)
        self.assertEqual(len([e for e in self.ledger.events() if e['type'] == 'transport_adaptation']), 1)

    def test_manifest_proves_fixture_and_exact_shared_schema_without_dispatch(self):
        gw = self.start()
        status, body = self.post(gw, {}, path='/M/worker/manifest')
        self.assertEqual(status, 200)
        self.assertTrue(body['offline'])
        self.assertEqual(body['tools'], responses_tools())
        self.assertEqual(body['context_window'], 65536)
        self.assertEqual(body['budget']['model_requests'], 2)
        self.assertEqual(self.transport.requests, [])

    def test_intrinsic_state_tools_reserve_before_execution_and_do_not_replay(self):
        gw = self.start(intrinsic_tools={('M1', 'worker'): {'native_submit'}})
        start = {'phase': 'start', 'name': 'native_submit', 'call_id': 'fixture', 'args_sha256': 'a' * 64}
        finish = {'phase': 'finish', 'name': 'native_submit', 'call_id': 'fixture',
                  'result_sha256': 'b' * 64, 'cpu_seconds': 0.1, 'wall_seconds': 0.2, 'status': 'ok'}
        endpoint = '/M/worker/intrinsic'
        self.assertEqual(self.post(gw, finish, path=endpoint)[1]['error']['code'], 'TOOL_SCHEMA')
        self.assertEqual(self.post(gw, {**start, 'name': 'shell'}, path=endpoint)[1]['error']['code'], 'TOOL_NOT_ALLOWED')
        self.assertEqual(self.post(gw, start, path=endpoint)[0], 200)
        self.assertNotEqual(self.post(gw, start, path=endpoint)[0], 200)
        self.assertEqual(self.post(gw, finish, path=endpoint)[0], 200)
        self.assertEqual(self.post(gw, finish, path=endpoint)[1]['error']['code'], 'TOOL_SCHEMA')
        with self.ledger.connection() as db:
            row = db.execute('SELECT state,cpu FROM tools').fetchone()
        self.assertEqual(tuple(row), ('finished', 0.1))
        self.assertEqual(self.transport.requests, [])

    def test_mcp_lists_and_routes_identical_common_tool_contract(self):
        calls = []
        class Tools:
            def call(self, name, arguments, **kwargs):
                calls.append((name, arguments))
                return {'ok': True, 'result': {'files': []}, 'metrics': {}}
        gw = self.start(tools={'M1': Tools()})
        source = StringIO('\n'.join(json.dumps(r) for r in [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'materials_list', 'arguments': {}}}]) + '\n')
        output = StringIO()
        mcp.serve(gw.url + '/M/worker/tools', self.token, source, output)
        answers = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(answers), 3)
        self.assertEqual(len(answers[1]['result']['tools']), 7)
        self.assertFalse(answers[2]['result']['isError'])
        self.assertEqual(calls, [('materials.list', {})])
        self.assertEqual(self.transport.requests, [])


if __name__ == '__main__':
    unittest.main()
