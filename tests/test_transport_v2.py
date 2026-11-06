"""Deterministic P0 transport fixtures. Only the gateway's loopback server uses sockets."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import errno
import hashlib
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'))
from gateway import Gateway, FixtureTransport
from ledger import GateError, Ledger, OPTIMIZATION_BUDGET, retryable_response, retryable_transport_failure
from transport import HostTransport, Credential, STAGES, _DeadlineHTTPSConnection
from tools import CommonTools, ToolLimits
from test_four_harness_transport import FakeConnection, FakeResponse, FakeSocket, payload, response, terminal, event

SECRET = 'secret-access-account-https://private.invalid/token'


def request_body(**changes):
    return {**payload(), 'max_output_tokens': 128000, **changes}


def credential():
    return Credential('fixture-access', 'fixture-account', time.time() * 1000 + 60000)


def context(cancel=None, **changes):
    return dict(request_id='physical-1', logical_request_id='logical-1', retry_of=None, attempt=1,
                cancel_event=cancel or threading.Event(), **changes)


class Clock:
    def __init__(self):
        self.value = 0.
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, amount):
        with self.lock:
            self.value += amount


class Chunks(FakeResponse):
    def __init__(self, pieces, clock, intervals=None, status=200, headers=None):
        super().__init__()
        self.pieces, self.clock = list(pieces), clock
        self.intervals = list(intervals or [0] * len(pieces))
        self.status, self.headers = status, headers or {}

    def read1(self, count=-1):
        if not self.pieces:
            return b''
        chunk = self.pieces.pop(0)
        self.clock.advance(self.intervals.pop(0))
        if len(chunk) > count >= 0:
            self.pieces.insert(0, chunk[count:])
            self.intervals.insert(0, 0)
            chunk = chunk[:count]
        return chunk

    def getheader(self, name, default=None):
        return next((value for key, value in self.headers.items() if key.lower() == name.lower()),
                    super().getheader(name, default))


class TimedConnection(FakeConnection):
    def __init__(self, clock, *, connect=0, send=0, headers=0, upstream=None,
                 connect_error=None, send_error=None):
        super().__init__()
        self.clock, self.delays = clock, (connect, send, headers)
        self.upstream = upstream if upstream is not None else FakeResponse(terminal())
        self.connect_error, self.send_error = connect_error, send_error

    def connect(self):
        self.clock.advance(self.delays[0])
        if self.connect_error:
            raise self.connect_error
        super().connect()

    def request(self, *args, **kwargs):
        super().request(*args, **kwargs)
        self.clock.advance(self.delays[1])
        if self.send_error:
            raise self.send_error

    def getresponse(self):
        self.clock.advance(self.delays[2])
        return self.upstream


def host(connection=None, **options):
    defaults = dict(profile='optimization', max_output_tokens=128000, auth_loader=credential)
    if connection is not None:
        defaults['connection_factory'] = lambda *args, **kwargs: connection
    return HostTransport(**{**defaults, **options})


class TransportV2(unittest.TestCase):
    def assert_redacted(self, error, receipts):
        self.assertNotIn(SECRET, repr(error) + str(error) + json.dumps(receipts))
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)

    def test_profile_defaults_and_explicit_legacy_compatibility(self):
        for profile in ('optimization', 'workflow'):
            value = host(profile=profile)
            self.assertFalse(value.legacy_timeouts)
            self.assertEqual((value.connect_timeout, value.send_timeout, value.headers_timeout,
                              value.stream_idle_timeout), (30, 60, 300, 300))
        value = HostTransport(dispatch_timeout=3, response_timeout=7)
        self.assertTrue(value.legacy_timeouts)
        self.assertEqual((value.dispatch_timeout, value.response_timeout), (3, 7))
        self.assertTrue(host(timeout_profile='legacy').legacy_timeouts)
        for options in ({'send_timeout': 0}, {'connect_timeout': float('nan')}, {'headers_timeout': True},
                        {'dispatch_timeout': 4, 'send_timeout': 60}, {'response_timeout': 181}):
            with self.subTest(options=options), self.assertRaises(GateError):
                host(**options)

    def test_sync_validation_and_credential_failures_emit_safe_evidence(self):
        for cause in (OSError(errno.EACCES, SECRET), GateError(SECRET, SECRET)):
            receipts = []
            def failed_auth():
                raise cause
            with self.assertRaises(GateError) as caught:
                host(auth_loader=failed_auth, evidence_sink=receipts.append).start(request_body(), context())
            error = caught.exception
            self.assert_redacted(error, receipts)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]['stage'], 'credential')
            self.assertEqual(receipts[0]['send_state'], 'not_started')
            self.assertFalse(retryable_transport_failure(error))
            self.assertEqual(error.evidence, receipts[0])
        receipts = []
        with self.assertRaises(GateError):
            host(auth_loader=lambda: self.fail('validation must precede auth'),
                 evidence_sink=receipts.append).start(request_body(model='wrong'), context())
        self.assertEqual(receipts[0]['error_code'], 'MODEL_DRIFT')

    def test_sync_sink_failure_does_not_hide_original_safe_failure(self):
        def sink(_):
            raise RuntimeError(SECRET)
        with self.assertRaises(GateError) as caught:
            host(evidence_sink=sink).start(request_body(model='wrong'), context())
        self.assertEqual(caught.exception.code, 'MODEL_DRIFT')
        self.assertTrue(caught.exception.evidence['evidence_sink_failed'])
        self.assert_redacted(caught.exception, caught.exception.evidence)

    def test_actual_dns_errors_are_typed_and_only_temporary_is_retryable(self):
        for code, kind, retryable in ((socket.EAI_AGAIN, 'dns_temporary', True),
                                      (socket.EAI_NONAME, 'dns_permanent', False)):
            receipts = []
            with patch('transport.socket.getaddrinfo', side_effect=socket.gaierror(code, SECRET)), \
                    patch('transport.socket.socket') as create, self.assertRaises(GateError) as caught:
                host(evidence_sink=receipts.append).start(request_body(), context())
            create.assert_not_called()
            self.assertEqual(receipts[0]['stage'], 'dns')
            self.assertEqual(receipts[0]['failure_kind'], kind)
            self.assertEqual(retryable_transport_failure(caught.exception), retryable)
            self.assert_redacted(caught.exception, receipts)

    def test_actual_tcp_tls_errors_preserve_stage_and_never_leak_text(self):
        address = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('203.0.113.1', 443))]
        for stage, cause, kind, retryable in (
            ('tcp', ConnectionRefusedError(errno.ECONNREFUSED, SECRET), 'connection_refused', True),
            ('tcp', OSError(errno.ENETUNREACH, SECRET), 'network_unreachable', True),
            ('tcp', TimeoutError(SECRET), 'timeout', True),
            ('tls', ConnectionResetError(errno.ECONNRESET, SECRET), 'connection_reset', True),
            ('tls', ssl.SSLCertVerificationError(1, SECRET), 'tls_certificate', False),
            ('tls', ssl.SSLError(1, SECRET), 'tls_protocol', False)):
            with self.subTest(stage=stage, kind=kind):
                class Socket(FakeSocket):
                    def connect(self, _):
                        if stage == 'tcp':
                            raise cause
                    def do_handshake(self):
                        raise cause
                    def close(self):
                        pass
                wire, tls, receipts = Socket(), ssl.create_default_context(), []
                with patch('transport.socket.getaddrinfo', return_value=address), \
                        patch('transport.socket.socket', return_value=wire), \
                        patch('transport.ssl.create_default_context', return_value=tls), \
                        patch.object(tls, 'wrap_socket', return_value=wire), self.assertRaises(GateError) as caught:
                    host(evidence_sink=receipts.append).start(request_body(), context())
                self.assertEqual(receipts[0]['stage'], stage)
                self.assertEqual(receipts[0]['send_state'], 'not_started')
                self.assertEqual(receipts[0]['failure_kind'], kind)
                self.assertEqual(retryable_transport_failure(caught.exception), retryable)
                self.assert_redacted(caught.exception, receipts)

    def test_stage_timing_separates_dns_tcp_tls_and_attempt_linkage(self):
        clock, receipts = Clock(), []
        address = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('203.0.113.1', 443))]
        class Socket(FakeSocket):
            def connect(self, _): clock.advance(3)
            def do_handshake(self): clock.advance(5)
            def sendall(self, _): clock.advance(1)
            def close(self): pass
        wire, tls = Socket(), ssl.create_default_context()
        def dns(*args, **kwargs):
            clock.advance(2)
            return address
        def headers(_):
            clock.advance(7)
            return FakeResponse(terminal())
        with patch('transport.socket.getaddrinfo', side_effect=dns), \
                patch('transport.socket.socket', return_value=wire), \
                patch('transport.ssl.create_default_context', return_value=tls), \
                patch.object(tls, 'wrap_socket', return_value=wire), \
                patch.object(_DeadlineHTTPSConnection, 'getresponse', headers):
            pending = host(clock=clock, evidence_sink=receipts.append).start(request_body(),
                {**context(), 'attempt': 2, 'retry_of': 'physical-0'})
            self.assertEqual(pending.result(2)['status'], 'completed')
        evidence = receipts[0]
        self.assertEqual(evidence, pending.evidence)
        self.assertEqual(evidence['logical_request_id'], 'logical-1')
        self.assertEqual((evidence['retry_of'], evidence['attempt']), ('physical-0', 2))
        self.assertEqual(set(evidence['stage_timings']), set(STAGES))
        for stage, duration in (('dns', 2), ('tcp', 3), ('tls', 5), ('send', 2), ('response_headers', 7)):
            self.assertEqual(evidence['stage_timings'][stage]['elapsed_seconds'], duration, evidence)
        self.assertEqual(evidence['elapsed_seconds'], 19)
        self.assertEqual(evidence['send_state'], 'completed')
        self.assertEqual(evidence['stage'], 'terminal')

    def test_independent_deadlines_and_byte_idle_allow_long_total_solve(self):
        clock = Clock()
        raw = terminal()
        pieces = [raw[i:i+17] for i in range(0, len(raw), 17)]
        upstream = Chunks(pieces, clock, [299] * len(pieces))
        connection = TimedConnection(clock, connect=29, send=59, headers=299, upstream=upstream)
        pending = host(connection, clock=clock).start(request_body(), context())
        self.assertEqual(pending.result(2)['status'], 'completed')
        self.assertGreater(pending.evidence['elapsed_seconds'], 3000)
        self.assertEqual(pending.evidence['stage_timings']['send']['elapsed_seconds'], 59)
        self.assertEqual(pending.evidence['stage_timings']['response_headers']['elapsed_seconds'], 299)

    def test_each_deadline_fails_at_its_own_stage(self):
        for changes, stage, synchronous, sent in (
            ({'connect': 31}, 'tcp', True, 'not_started'),
            ({'send': 61}, 'send', True, 'completed'),
            ({'headers': 301}, 'response_headers', False, 'completed'),
            ({'idle': 301}, 'stream', False, 'completed')):
            with self.subTest(stage=stage):
                clock, receipts = Clock(), []
                options = dict(changes)
                idle = options.pop('idle', 0)
                connection = TimedConnection(clock, upstream=Chunks([terminal()], clock, [idle]), **options)
                with self.assertRaises(GateError) as caught:
                    pending = host(connection, clock=clock, evidence_sink=receipts.append).start(request_body(), context())
                    self.assertFalse(synchronous)
                    pending.result(2)
                self.assertEqual(receipts[0]['stage'], stage)
                self.assertEqual(receipts[0]['send_state'], sent)
                self.assertEqual(receipts[0]['failure_kind'], 'timeout')
                self.assertEqual(retryable_transport_failure(caught.exception), stage == 'tcp')

    def test_legacy_dispatch_deadline_is_still_shared(self):
        clock = Clock()
        connection = TimedConnection(clock, connect=3, send=2)
        with self.assertRaises(GateError) as caught:
            host(connection, clock=clock, dispatch_timeout=4, response_timeout=60).start(request_body(), context())
        self.assertEqual(caught.exception.evidence['stage'], 'send')
        self.assertEqual(caught.exception.evidence['timeout_profile'], 'legacy')

    def test_started_send_error_never_becomes_connection_retry_proof(self):
        clock, receipts = Clock(), []
        connection = TimedConnection(clock, send_error=ConnectionResetError(errno.ECONNRESET, SECRET))
        with self.assertRaises(GateError) as caught:
            host(connection, evidence_sink=receipts.append).start(request_body(), context())
        self.assertEqual(len(connection.sent), 1)
        self.assertEqual(receipts[0]['stage'], 'send')
        self.assertEqual(receipts[0]['send_state'], 'started')
        self.assertFalse(retryable_transport_failure(caught.exception))
        self.assert_redacted(caught.exception, receipts)

    def test_header_watchdog_interrupts_blocking_io_without_socket_progress(self):
        class Blocked(FakeConnection):
            def getresponse(self):
                if not self.sock.stopped.wait(1):
                    raise AssertionError('header watchdog failed')
                raise ConnectionResetError(errno.ECONNRESET, SECRET)
        pending = host(Blocked(), headers_timeout=.03).start(request_body(), context())
        with self.assertRaises(GateError) as caught:
            pending.result(2)
        self.assertEqual(pending.evidence['stage'], 'response_headers')
        self.assertEqual(pending.evidence['failure_kind'], 'timeout')
        self.assert_redacted(caught.exception, pending.evidence)

    def test_cancel_during_dns_never_connects_after_late_resolution(self):
        entered, release, resolved, cancel = (threading.Event() for _ in range(4))
        def dns(*args, **kwargs):
            entered.set()
            release.wait(2)
            resolved.set()
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('203.0.113.1', 443))]
        with patch('transport.socket.getaddrinfo', side_effect=dns), patch('transport.socket.socket') as create, \
                ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(host().start, request_body(), context(cancel))
            try:
                self.assertTrue(entered.wait(1))
                cancel.set()
                with self.assertRaises(GateError) as caught:
                    pending.result(1)
                self.assertEqual(caught.exception.code, 'STOPPED')
                self.assertEqual(caught.exception.evidence['stage'], 'dns')
            finally:
                release.set()
                self.assertTrue(resolved.wait(1))
            create.assert_not_called()

    def test_cancel_during_tls_handshake_closes_published_tls_socket(self):
        entered, cancel = threading.Event(), threading.Event()
        class Socket(FakeSocket):
            def connect(self, _): pass
            def do_handshake(self):
                entered.set()
                if not self.stopped.wait(1):
                    raise AssertionError('TLS socket was not cancellable')
                raise ConnectionResetError(errno.ECONNRESET, SECRET)
            def close(self): pass
        wire, tls = Socket(), ssl.create_default_context()
        with patch('transport.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('203.0.113.1', 443))]), \
                patch('transport.socket.socket', return_value=wire), \
                patch('transport.ssl.create_default_context', return_value=tls), \
                patch.object(tls, 'wrap_socket', return_value=wire) as wrap, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(host().start, request_body(), context(cancel))
            self.assertTrue(entered.wait(1))
            cancel.set()
            with self.assertRaises(GateError) as caught:
                pending.result(1)
            self.assertEqual(caught.exception.evidence['stage'], 'tls')
            self.assertTrue(wire.stopped.is_set())
            self.assertFalse(wrap.call_args.kwargs['do_handshake_on_connect'])

    def test_error_output_guard_schema_auth_drift_and_truncation_remain_conservative(self):
        for value in (b'not-json', b'[]', b'null', b'{"output":', b'{"error":{}}' + b' ' * 8193,
                      {'output': None}, {'output': {}}, {'output': [{'type': 'message'}]},
                      {'reasoning': 'bad'}, {'model': 'other'}, {'usage': {'output_tokens': 'bad'}},
                      {'error': {'code': 'invalid_api_key'}}, {'error': {'code': 'insufficient_quota'}},
                      {'error': {'type': 'invalid_request_error'}}):
            with self.subTest(value=repr(value)[:80]):
                clock = Clock()
                raw = value if isinstance(value, bytes) else json.dumps(value).encode()
                connection = TimedConnection(clock, upstream=Chunks([raw], clock, status=503))
                with self.assertRaises(GateError) as caught:
                    host(connection).start(request_body(), context()).result(2)
                self.assertEqual(caught.exception.code, 'UPSTREAM_HTTP')
                self.assertEqual(len(connection.sent), 1)

    def test_retry_headers_are_numeric_only_and_error_text_is_not_evidence(self):
        clock, receipts = Clock(), []
        upstream = Chunks([json.dumps({'error': {'message': SECRET}}).encode()], clock, status=429,
            headers={'Retry-After': SECRET, 'retry-after-ms': '2500'})
        pending = host(TimedConnection(clock, upstream=upstream), evidence_sink=receipts.append).start(request_body(), context())
        result = pending.result(2)
        self.assertTrue(retryable_response(result))
        self.assertEqual(result['error']['headers'], {'retry-after-ms': '2500.0'})
        self.assertNotIn(SECRET, json.dumps(receipts) + json.dumps(result))
        upstream = Chunks([b'{"error":{}}'], clock, status=503,
            headers={'Retry-After': 'Thu, 01 Jan 1970 00:00:20 GMT'})
        with patch('transport.time.time', return_value=0):
            pending = host(TimedConnection(clock, upstream=upstream)).start(request_body(), context())
            self.assertEqual(pending.result(2)['error']['headers']['Retry-After'], '20.0')

    def test_partial_output_before_failed_empty_terminal_is_recorded(self):
        failed = response('failed', output=[], usage=None, error={'code': 'http_503'})
        for kind in ('response.output_text.delta', 'response.reasoning_text.delta', 'response.refusal.delta',
                     'response.reasoning_summary_text.done', 'response.content_part.added', 'response.future_output'):
            with self.subTest(kind=kind):
                raw = event(kind, delta='partial') + terminal(failed)
                pending = host(FakeConnection(raw)).start(request_body(), context())
                self.assertEqual(pending.result(2)['status'], 'failed')
                self.assertTrue(pending.evidence['partial_output_observed'])

    def test_duplicate_json_members_fail_in_sse_and_terminal_json(self):
        raw = json.dumps(response('failed', output=[], usage=None, error={'code': 'http_503'})).replace(
            '"output": []', '"output": [{}], "output": []').encode()
        sse = b'event: response.failed\ndata: {"type":"response.failed","response":' + raw + b'}\n\n'
        for content, media, code in ((raw, 'application/json', 'UPSTREAM_JSON'), (sse, 'text/event-stream', 'SSE_SCHEMA')):
            with self.subTest(media=media):
                clock = Clock()
                upstream = Chunks([content], clock, headers={'Content-Type': media})
                pending = host(TimedConnection(clock, upstream=upstream)).start(request_body(), context())
                with self.assertRaises(GateError) as raised:
                    pending.result(2)
                self.assertEqual(raised.exception.code, code)

    def test_malformed_error_envelopes_cannot_hide_output_or_schema_drift(self):
        for raw in (b'{"output":[{}],"output":[]}', b'{"error":{"code":{},"type":[]}}',
                    b'{"response":{},"output":[{}]}', b'{"response":{},"usage":{"output_tokens":1}}',
                    b'{"response":{},"error":{"code":"invalid_api_key"}}',
                    b'{"error":{},"status":"completed"}', b'{"error":{},"object":"unexpected"}'):
            with self.subTest(raw=raw):
                clock = Clock()
                pending = host(TimedConnection(clock, upstream=Chunks([raw], clock, status=503))).start(request_body(), context())
                with self.assertRaises(GateError):
                    pending.result(2)


class GatewayV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ledger = Ledger(self.root / 'ledger.sqlite')
        self.ledger.create_campaign('campaign', max_requests=120, max_tokens=None, scope='optimization')
        self.ledger.create_run('run', 'campaign', 'M', budget=OPTIMIZATION_BUDGET)
        self.payload = request_body()

    def gateway(self, transport, **options):
        material = self.root / 'fixture.txt'
        material.write_text('Offline synthetic public task.\n')
        tools = CommonTools('run', self.root / 'tools', {'fixture': {'path': material,
            'sha256': hashlib.sha256(material.read_bytes()).hexdigest()}},
            limits=ToolLimits(cpu_seconds=None, timeout_seconds=None))
        live_fixture = {}
        if type(transport) is HostTransport:
            class Guard:
                def check(self): pass
            live_fixture = dict(execution=dict(mode='authorized_optimization', arm='M', run_id='run',
                campaign_id='campaign', model_requests=120, campaign_max_requests=120), state_guards={'run': Guard()})
        gw = Gateway(self.ledger, transport=transport, bindings={'fixture-capability': {'run_id': 'run', 'arm': 'M', 'role': 'worker'}},
            tools={'run': tools}, frozen_materials={'run': tools.material_inventory()}, **live_fixture, **options).start()
        self.addCleanup(gw.close)
        return gw

    def post(self, gw, body=None, endpoint='v1/responses', headers=None):
        request = Request(gw.url + '/M/worker/' + endpoint, json.dumps(body if body is not None else self.payload).encode(),
            {'Authorization': 'Bearer fixture-capability', 'Content-Type': 'application/json', **(headers or {})})
        try:
            received = build_opener(ProxyHandler({})).open(request, timeout=3)
        except HTTPError as error:
            received = error
        with received:
            raw = received.read()
            return received.status, raw if received.headers['Content-Type'] == 'text/event-stream' else json.loads(raw), dict(received.headers)

    def test_every_physical_retry_is_projected_with_unknown_usage_and_final_core_only(self):
        final = response(usage={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12})
        fixture = FixtureTransport([{'status': 'failed', 'output': [], 'error': {'code': 'http_503'}}, final])
        gw = self.gateway(fixture)
        with patch('gateway.retry_delay', return_value=0):
            status, body, headers = self.post(gw)
        self.assertEqual(status, 200, body)
        accounting = body['four_harness_accounting']
        ids = [row['request_id'] for row in fixture.requests]
        self.assertEqual(accounting['request_ids'], ids)
        self.assertEqual(json.loads(headers['X-Four-Request-Ids']), ids)
        self.assertEqual(headers['X-Four-Request-Id'], ids[-1])
        self.assertEqual(headers['X-Four-Logical-Request-Id'], ids[0])
        first, last = accounting['requests']
        self.assertEqual((first['status'], last['status']), ('unknown', 'finished'))
        self.assertEqual((last['retry_of'], last['attempt']), (ids[0], 2))
        self.assertIsNone(first['usage']['total_tokens'])
        self.assertTrue(last['usage']['complete'])
        self.assertFalse(last['usage']['detail_complete'])
        self.assertIsNone(last['usage']['cached_input_tokens'])
        self.assertIsNone(self.ledger.snapshot('run')['total_tokens'])

    def test_sse_terminal_carries_all_retry_ids(self):
        fixture = FixtureTransport([{'status': 'failed', 'output': [], 'error': {'code': 'http_429'}}, response()])
        gw = self.gateway(fixture)
        with patch('gateway.retry_delay', return_value=0):
            status, raw, headers = self.post(gw, {**self.payload, 'stream': True})
        self.assertEqual(status, 200)
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b'data: {')]
        final = [item['response'] for item in events if item.get('type') == 'response.completed'][0]
        self.assertEqual(final['four_harness_accounting']['request_ids'], json.loads(headers['X-Four-Request-Ids']))
        self.assertEqual(len(final['four_harness_accounting']['requests']), 2)

    def test_maximum_two_retries_and_failure_projection_keeps_all_three_ids(self):
        fixture = FixtureTransport([{'status': 'failed', 'output': [], 'error': {'code': 'http_503'}}] * 4)
        gw = self.gateway(fixture)
        with patch('gateway.retry_delay', return_value=0):
            status, body, headers = self.post(gw)
        self.assertEqual(status, 409)
        self.assertEqual(len(fixture.requests), 3)
        self.assertEqual(self.ledger.snapshot('run')['requests'], 3)
        self.assertEqual(len(body['four_harness_accounting']['requests']), 3)
        self.assertEqual(len([item for item in self.ledger.events() if item['type'] == 'retry_scheduled']), 2)

    def test_sync_not_sent_connection_retry_uses_new_id_and_same_payload(self):
        clock = Clock()
        connections = [TimedConnection(clock, connect_error=ConnectionRefusedError(errno.ECONNREFUSED, SECRET)),
                       TimedConnection(clock)]
        receipts, created = [], []
        def factory(*args, **kwargs):
            connection = connections[len(created)]
            created.append(connection)
            return connection
        gw = self.gateway(host(connection_factory=factory, evidence_sink=receipts.append))
        with patch('gateway.retry_delay', return_value=0):
            status, body, headers = self.post(gw)
        self.assertEqual(status, 200, body)
        first, last = body['four_harness_accounting']['requests']
        self.assertEqual(first['transport']['send_state'], 'not_started')
        self.assertEqual(first['transport']['stage'], 'tcp')
        self.assertEqual(last['transport']['attempt'], 2)
        self.assertEqual(last['transport']['retry_of'], first['request_id'])
        self.assertEqual(first['transport']['semantic_payload_sha256'], last['transport']['semantic_payload_sha256'])
        self.assertEqual(len(created), 2)
        self.assertEqual(len(connections[0].sent), 0)
        self.assertIsNone(self.ledger.snapshot('run')['total_tokens'])
        self.assertNotIn(SECRET, json.dumps(body) + json.dumps(self.ledger.events()))

    def test_sync_connection_retries_also_stop_at_three_attempts(self):
        created = []
        def factory(*args, **kwargs):
            connection = TimedConnection(Clock(), connect_error=ConnectionRefusedError(errno.ECONNREFUSED, SECRET))
            created.append(connection)
            return connection
        gw = self.gateway(host(connection_factory=factory))
        with patch('gateway.retry_delay', return_value=0):
            status, body, _ = self.post(gw)
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'TRANSPORT_SEND')
        self.assertEqual(len(created), 3)
        self.assertEqual(len(body['four_harness_accounting']['requests']), 3)

    def test_started_send_and_partial_stream_never_retry_or_execute_tool(self):
        connection = TimedConnection(Clock(), send_error=ConnectionResetError(errno.ECONNRESET, SECRET))
        gw = self.gateway(host(connection))
        status, body, _ = self.post(gw)
        self.assertEqual(status, 409)
        self.assertEqual(len(connection.sent), 1)
        self.assertEqual(self.ledger.snapshot('run')['requests'], 1)
        self.assertFalse(any(item['type'].startswith('tool_') for item in self.ledger.events()))
        self.assertNotIn(SECRET, json.dumps(body))

    def test_partial_delta_then_failed_empty_output_never_retries(self):
        failed = response('failed', output=[], usage=None, error={'code': 'http_503'})
        connection = FakeConnection(event('response.function_call_arguments.delta', delta='{}') + terminal(failed))
        gw = self.gateway(host(connection))
        status, body, _ = self.post(gw)
        self.assertEqual(status, 409)
        self.assertEqual(len(connection.sent), 1)
        self.assertTrue(body['four_harness_accounting']['requests'][0]['transport']['partial_output_observed'])
        self.assertFalse(any(item['type'].startswith('tool_') for item in self.ledger.events()))

    def test_malformed_or_nonterminal_tool_response_cannot_authorize_side_effects(self):
        reply = response('incomplete', output=[{'type': 'function_call', 'name': 'compute', 'call_id': 'call', 'arguments': '{}'}])
        fixture = FixtureTransport([reply])
        gw = self.gateway(fixture)
        status, body, _ = self.post(gw)
        self.assertEqual(status, 409)
        self.assertEqual(body['error']['code'], 'RESPONSE_SCHEMA')
        self.assertNotIn('output', body)
        status, _, _ = self.post(gw, {'name': 'compute', 'arguments': {'code': 'print(1)'}}, endpoint='tools',
            headers={'X-Four-Parent-Request-Id': fixture.requests[0]['request_id']})
        self.assertEqual(status, 409)
        self.assertFalse(any(item['type'].startswith('tool_') for item in self.ledger.events()))

    def test_tool_side_effect_waits_for_full_terminal_validation(self):
        entered, release = threading.Event(), threading.Event()
        def audit(kind, rid, value):
            if kind == 'response':
                entered.set()
                if not release.wait(2):
                    raise AssertionError('validation barrier not released')
        fixture = FixtureTransport([response()])
        gw = self.gateway(fixture, audit_sink=audit)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.post, gw)
            try:
                self.assertTrue(entered.wait(1))
                parent = fixture.requests[0]['request_id']
                status, body, _ = self.post(gw, {'name': 'workspace_write', 'arguments': {'path': 'candidate.txt', 'text': 'fixture'}},
                    endpoint='tools', headers={'X-Four-Parent-Request-Id': parent})
                self.assertEqual((status, body.get('error', {}).get('code')), (409, 'NATIVE_PARENT_UNKNOWN'))
                self.assertFalse(any(item['type'].startswith('tool_') for item in self.ledger.events()))
            finally:
                release.set()
            self.assertEqual(pending.result(2)[0], 200)
        status, _, _ = self.post(gw, {'name': 'workspace_write', 'arguments': {'path': 'candidate.txt', 'text': 'fixture'}},
            endpoint='tools', headers={'X-Four-Parent-Request-Id': parent})
        self.assertEqual(status, 200)

    def test_completed_core_with_malformed_optional_usage_is_not_tool_authority(self):
        fixture = FixtureTransport([response(usage={'input_tokens': 1, 'output_tokens': 0, 'total_tokens': 1,
                                                   'input_tokens_details': 'invalid'})])
        gw = self.gateway(fixture)
        status, body, _ = self.post(gw)
        self.assertEqual((status, body.get('error', {}).get('code')), (409, 'RESPONSE_SCHEMA'))
        self.assertEqual(len(fixture.requests), 1)

    def test_retry_after_and_cancellation_dispatch_no_additional_attempt(self):
        fixture = FixtureTransport([{'status': 'failed', 'output': [], 'error': {'code': 'http_429',
            'headers': {'Retry-After': '3600'}}}, response()])
        gw = self.gateway(fixture)
        record = self.ledger.record
        def cancel_on_wait(run, kind, **data):
            record(run, kind, **data)
            if kind == 'retry_scheduled':
                self.assertEqual(data['wait_seconds'], 3600)
                gw.cancel_event.set()
        with patch.object(self.ledger, 'record', side_effect=cancel_on_wait):
            status, body, _ = self.post(gw)
        self.assertEqual((status, body['error']['code']), (409, 'STOPPED'))
        self.assertEqual(len(fixture.requests), 1)
        self.assertEqual(len(body['four_harness_accounting']['requests']), 1)

    def test_global_quota_counts_retries_and_denies_attempt_121(self):
        for _ in range(118):
            rid = self.ledger.reserve_request('run', 'worker', self.payload)
            self.ledger.start_request(rid)
            self.ledger.finish_request(rid, response())
        fixture = FixtureTransport([{'status': 'failed', 'output': [], 'error': {'code': 'http_503'}}] * 3)
        gw = self.gateway(fixture)
        with patch('gateway.retry_delay', return_value=0):
            status, body, _ = self.post(gw)
        self.assertEqual((status, body['error']['code']), (409, 'REQUEST_BUDGET'))
        self.assertEqual(len(fixture.requests), 2)
        self.assertEqual(self.ledger.snapshot('run')['requests'], 120)
        self.assertEqual(len(body['four_harness_accounting']['requests']), 2)

    def test_shutdown_signals_cancellation_while_sync_send_owns_lifecycle_lock(self):
        entered = threading.Event()
        class Blocked(FakeConnection):
            def request(self, *args, **kwargs):
                super().request(*args, **kwargs)
                entered.set()
                if not self.sock.stopped.wait(2):
                    raise AssertionError('close deadlocked behind synchronous send')
                raise ConnectionResetError(errno.ECONNRESET, SECRET)
        connection = Blocked()
        gw = self.gateway(host(connection), drain_timeout=0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.post, gw)
            self.assertTrue(entered.wait(1))
            closing = pool.submit(gw.close)
            self.assertTrue(gw.cancel_event.wait(1))
            closing.result(2)
            status, body, _ = pending.result(2)
        self.assertEqual(status, 409)
        self.assertEqual(len(connection.sent), 1)
        self.assertEqual(len(body['four_harness_accounting']['requests']), 1)

    def test_retry_predicate_rejects_schema_uncertain_send_drift_and_positive_output(self):
        base = {'status': 'failed', 'output': [], 'error': {'code': 'http_503'}}
        for updates in ({'output': None}, {'output': {}}, {'reasoning': []}, {'model': 'other'},
                        {'usage': {'output_tokens': 1}}, {'usage': {'output_tokens': True}},
                        {'usage': {'input_tokens': 2, 'output_tokens': 0, 'total_tokens': 1}},
                        {'usage': {'input_tokens': 2, 'total_tokens': 1}}, {'object': 'invalid'}, {'id': 42},
                        {'partial_output_observed': True}, {'usage': 'bad'},
                        {'error': {'code': 'authentication_error'}}, {'error': {'code': []}},
                        {'error': {'code': 'http_503', 'type': 'authentication_error'}},
                        {'error': {'code': 'http_503', 'type': {}}}):
            self.assertFalse(retryable_response({**base, **updates}), updates)
        for state in ('started', 'completed', 'unknown'):
            error = GateError('TRANSPORT_SEND', 'safe')
            error.evidence = dict(format='four-harness-transport-v2', stage='tcp', send_state=state,
                                  failure_kind='timeout')
            self.assertFalse(retryable_transport_failure(error))


if __name__ == '__main__':
    unittest.main()
