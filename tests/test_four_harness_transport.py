"""Offline transport qualification. No sockets, auth files, or model calls."""
from concurrent.futures import Future
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'evaluation/four-harness-v1'
sys.path.insert(0, str(SOURCE))
from ledger import GateError, digest
from transport import Credential, HostTransport, parse_sse, load_host_credential, _DeadlineHTTPSConnection


def payload(**changes):
    return dict(model='gpt-6-astra', reasoning={'effort': 'max'}, max_output_tokens=2048,
                input=[{'role': 'user', 'content': 'offline fixture'}], **changes)


def response(status='completed', **changes):
    return {**dict(id='resp_fixture', model='gpt-6-astra', status=status, output=[],
                   usage={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12,
                          'input_tokens_details': {'cached_tokens': 0},
                          'output_tokens_details': {'reasoning_tokens': 1}},
                   reasoning={'effort': 'max'}), **changes}


def event(kind, **data):
    return ('event: ' + kind + '\ndata: ' + json.dumps(dict(type=kind, **data)) + '\n\n').encode()


def terminal(value=None):
    value = response() if value is None else value
    return event('response.' + value['status'], response=value)


class FakeSocket:
    def __init__(self):
        self.stopped = threading.Event()
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def shutdown(self, _):
        self.stopped.set()


class FakeResponse(BytesIO):
    status = 200

    def getheader(self, name, default=None):
        return {'Content-Type': 'text/event-stream'}.get(name, default)


class FakeConnection:
    def __init__(self, raw=None, wait=None):
        self.sock = FakeSocket()
        self.raw = terminal() if raw is None else raw
        self.wait = wait
        self.sent = []
        self.connected = False
        self.closed = threading.Event()
        self.reader_started = threading.Event()

    def connect(self):
        self.connected = True

    def request(self, method, path, body, headers):
        if not self.connected:
            raise AssertionError('deferred dispatch')
        self.sent.append((method, path, body, headers))

    def getresponse(self):
        self.reader_started.set()
        if self.wait is not None and not self.wait.wait(2):
            raise OSError('fixture wait exceeded')
        return FakeResponse(self.raw)

    def close(self):
        self.closed.set()


class ParserTests(unittest.TestCase):
    def fails(self, code, raw, **bounds):
        with self.assertRaises(GateError) as caught:
            parse_sse(BytesIO(raw), **bounds)
        self.assertEqual(caught.exception.code, code)

    def test_truncated_candidate_deltas_are_retained_as_uncommitted_evidence(self):
        raw=event('response.output_text.delta',delta='{"values":[1')
        with self.assertRaises(GateError) as caught:
            parse_sse(BytesIO(raw))
        self.assertEqual(caught.exception.code,'SSE_TRUNCATED')
        self.assertEqual(caught.exception.partial_output_events,[{'type':'response.output_text.delta','delta':'{"values":[1'}])
        self.assertFalse(hasattr(caught.exception,'observed_response'))

    def test_terminal_statuses_retain_exact_usage_model_and_reasoning(self):
        for status in ('completed', 'incomplete', 'failed', 'cancelled'):
            value = response(status, usage=None, reasoning=None, model='raw-provider-model')
            self.assertEqual(parse_sse(BytesIO(terminal(value))), value)

    def test_missing_usage_and_effort_remain_absent(self):
        value = response()
        del value['usage'], value['reasoning']
        self.assertEqual(parse_sse(BytesIO(terminal(value))), value)

    def test_known_incremental_events_comments_crlf_and_done(self):
        raw = b': keepalive\r\n\r\n' + event('response.created', response={})
        raw += event('response.output_text.delta', delta='hello') + terminal() + b'data: [DONE]\n\n'
        self.assertEqual(parse_sse(BytesIO(raw)), response())

    def test_unknown_malformed_truncated_and_duplicate(self):
        for raw, code in [(b'', 'SSE_TRUNCATED'), (b'data: [DONE]\n\n', 'SSE_TRUNCATED'),
                          (terminal()[:-1], 'SSE_TRUNCATED'), (b'data: {\n\n', 'SSE_SCHEMA'),
                          (event('response.future'), 'SSE_TRUNCATED'),
                          (terminal() + terminal(), 'SSE_DUPLICATE'),
                          (terminal() + event('response.created'), 'SSE_DUPLICATE'),
                          (terminal() + b'data: [DONE]\n\n' + terminal(), 'SSE_SCHEMA'),
                          (b'event: wrong\ndata: {"type":"response.created"}\n\n', 'SSE_SCHEMA'),
                          (event('response.completed', response={'status': 'failed'}), 'SSE_SCHEMA'),
                          (b'bad: value\n\n', 'SSE_SCHEMA'),
                          (b'data: \xff\n\n', 'SSE_SCHEMA')]:
            with self.subTest(code=code, raw=raw[:40]):
                self.fails(code, raw)

    def test_stream_event_and_byte_bounds(self):
        self.fails('SSE_LIMIT', terminal(), max_bytes=10)
        self.fails('SSE_LIMIT', terminal(), max_event_bytes=10)
        self.fails('SSE_LIMIT', b': ping\n\n' + terminal(), max_events=1)

    def test_io_abort_after_terminal_retains_complete_receipt(self):
        class Interrupted(BytesIO):
            def readline(self, limit):
                if self.tell() == len(self.getvalue()):
                    raise OSError('cancelled fixture')
                return super().readline(limit)
        cancelled = threading.Event()
        cancelled.set()
        self.assertEqual(parse_sse(Interrupted(terminal()), cancel_event=cancelled), response())
        with self.assertRaises(GateError):
            parse_sse(Interrupted(terminal()))
        with self.assertRaises(GateError):
            parse_sse(Interrupted(event('response.created')), cancel_event=cancelled)


class TransportTests(unittest.TestCase):
    def make(self, connection=None, **kwargs):
        connection = connection or FakeConnection()
        self.calls = []
        def factory(host, port, **settings):
            self.calls.append((host, port, settings))
            return connection
        transport = HostTransport(auth_loader=lambda: Credential('fixture-access-secret', 'fixture-account-secret',
                                                                 time.time() * 1000 + 60000),
                                  connection_factory=factory, **kwargs)
        return transport, connection

    def context(self, request_id='offline-request'):
        return dict(request_id=request_id, run_id='M1', role='worker', cancel_event=threading.Event())

    def test_partial_candidate_survives_transport_future_without_replay(self):
        raw=event('response.function_call_arguments.delta',delta='{"candidate":{"values":[1]}}')
        transport,connection=self.make(FakeConnection(raw=raw))
        with self.assertRaises(GateError) as caught:
            transport.start(payload(),self.context()).result(timeout=1)
        self.assertEqual(caught.exception.code,'SSE_TRUNCATED')
        self.assertEqual(caught.exception.partial_output_events[0]['delta'],'{"candidate":{"values":[1]}}')
        self.assertEqual(len(connection.sent),1)
        self.assertFalse(hasattr(caught.exception,'observed_response'))

    def test_terminal_usage_survives_later_invalid_stream_and_duplicate(self):
        value=response(usage={'input_tokens':40000,'output_tokens':2,'total_tokens':40002})
        for suffix in (b'data: {\n\n',terminal(),b'event: response.created\ndata: {"type":"response.created"}\n\n'):
            transport,connection=self.make(FakeConnection(raw=terminal(value)+suffix))
            with self.assertRaises(GateError) as caught:
                transport.start(payload(),self.context()).result(timeout=1)
            self.assertEqual(caught.exception.observed_response['usage']['total_tokens'],40002)
            self.assertEqual(len(connection.sent),1)

    def test_http_error_diagnostics_are_bounded_and_partial_usage_is_retained(self):
        evidence=[]
        connection=FakeConnection()
        raw={'error':{'message':'Unsupported parameter: max_output_tokens fixture-access-secret'},
             'usage':{'input_tokens':20000}, 'model':'gpt-6-astra'}
        rejected=FakeResponse(json.dumps(raw).encode()); rejected.status=400
        connection.getresponse=lambda:rejected
        transport,_=self.make(connection,evidence_sink=evidence.append)
        with self.assertRaises(GateError) as caught:
            transport.start(payload(),self.context()).result(timeout=1)
        self.assertEqual(caught.exception.code,'UPSTREAM_HTTP')
        self.assertEqual(caught.exception.observed_response['usage']['input_tokens'],20000)
        self.assertIsNone(caught.exception.observed_response['usage']['total_tokens'])
        self.assertEqual(evidence[0]['http_status'],400)
        self.assertTrue(evidence[0]['unsupported_output_limit_reported'])
        self.assertNotIn('fixture-access-secret',json.dumps(evidence))
        self.assertNotIn('fixture-access-secret',str(caught.exception))
        self.assertEqual(len(connection.sent),1)

    def test_optimization_transient_http_receipt_has_safe_retry_after_and_no_replay(self):
        from ledger import retryable_response, retry_delay
        rejected = FakeResponse(b'{"error":{"message":"temporary fixture-access-secret"}}')
        rejected.status = 503
        original_header = rejected.getheader
        rejected.getheader = lambda name, default=None: '3' if name.lower() == 'retry-after' else original_header(name, default)
        transport, connection = self.make(profile='optimization', max_output_tokens=128000)
        connection.getresponse = lambda: rejected
        received = transport.start({**payload(), 'max_output_tokens':128000}, self.context()).result(2)
        self.assertTrue(retryable_response(received), received)
        self.assertIsNone(received.get('usage'))
        self.assertEqual(retry_delay(received, 1), 3)
        self.assertNotIn('fixture-access-secret', json.dumps(received))
        self.assertNotIn('private', json.dumps(received))
        self.assertEqual(len(connection.sent), 1)

    def test_actual_400_diagnostic_digest_and_no_replay(self):
        # Reconstructed offline from the known JSON diagnostic, verified against
        # the immutable smoke's recorded digest/length; no artifact is rewritten.
        raw = b'{"detail":"Unsupported parameter: truncation"}'
        expected_sha = '3ff2fba08b30fc4400c615241d0f7aae409846e6ab7cfd2ea6e84edc9bac5c73'
        self.assertEqual(len(raw), 46)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_sha)
        evidence = []
        rejected = FakeResponse(raw)
        rejected.status = 400
        transport, connection = self.make(evidence_sink=evidence.append)
        connection.getresponse = lambda: rejected
        context = self.context()
        pending = transport.start(payload(truncation='disabled'), context)
        with self.assertRaises(GateError) as caught:
            pending.result(2)
        self.assertEqual(caught.exception.code, 'UPSTREAM_HTTP')
        self.assertFalse(hasattr(caught.exception, 'observed_response'))
        self.assertEqual(evidence[0]['error_body_sha256'], expected_sha)
        self.assertEqual(evidence[0]['error_body_bytes_observed'], 46)
        self.assertFalse(evidence[0]['error_body_truncated'])
        self.assertEqual(evidence[0]['rejected_parameter'], 'truncation')
        self.assertFalse(evidence[0]['unsupported_output_limit_reported'])
        self.assertNotIn('Unsupported parameter', json.dumps(evidence))
        with self.assertRaises(GateError) as replay:
            transport.start(payload(truncation='disabled'), context)
        self.assertEqual(replay.exception.code, 'NO_REPLAY')
        self.assertEqual(len(connection.sent), 1)

    def test_disabled_truncation_omission_is_explicit_and_preserves_semantics_hash(self):
        original = payload(truncation='disabled', unknown_fixture_field={'keep': True})
        snapshot = deepcopy(original)
        transport, connection = self.make()
        pending = transport.start(original, self.context())
        pending.result(2)
        wire = json.loads(connection.sent[0][2])
        self.assertEqual(original, snapshot)
        self.assertNotIn('truncation', wire)
        self.assertEqual(wire['max_output_tokens'], 2048)
        self.assertEqual(wire['model'], original['model'])
        self.assertEqual(wire['reasoning'], original['reasoning'])
        self.assertEqual(wire['input'], original['input'])
        self.assertEqual(wire['unknown_fixture_field'], original['unknown_fixture_field'])
        self.assertEqual(pending.evidence['semantic_payload_sha256'], digest(original))
        self.assertEqual(pending.evidence['wire_payload_sha256'], digest(wire))
        self.assertNotEqual(digest(original), digest(wire))
        self.assertEqual([item for item in pending.evidence['adaptations'] if item['field'] == 'truncation'],
                         [{'field': 'truncation', 'before_present': True,
                           'before_sha256': digest('disabled'), 'after_present': False,
                           'contract': 'native_codex_omission_responses_default_disabled'}])

    def test_other_truncation_values_fail_before_auth_or_network(self):
        for value in ('auto', None, '', False, 0, ['disabled'], {'value': 'disabled'}):
            transport, connection = self.make()
            with patch.object(transport, '_auth_loader') as auth:
                with self.assertRaises(GateError) as caught:
                    transport.start(payload(truncation=value), self.context())
                self.assertEqual(caught.exception.code, 'TRUNCATION_POLICY')
                auth.assert_not_called()
            self.assertEqual(self.calls, [])
            self.assertEqual(connection.sent, [])

    def test_diagnostic_shapes_allowlist_and_bounds_never_store_server_text(self):
        secret = 'fixture-access-secret fixture-account-secret'
        for raw_value, expected in [
                ({'message': 'Unsupported parameter: max_output_tokens ' + secret}, 'max_output_tokens'),
                ({'detail': 'Unsupported parameter: truncation'}, 'truncation'),
                ({'error': {'message': "Unsupported parameter: 'truncation' " + secret}}, 'truncation'),
                ({'message': 'Unsupported parameter: fixture_access_secret'}, None),
                ({'message': 'Unsupported parameter: unknown_fixture_field'}, None),
                ({'message': 'Unsupported parameter: temperature'}, None),
                ({'message': 'Unsupported parameter: truncation' + 'x' * 1024}, None),
                ({'detail': {'message': 'Unsupported parameter: truncation'}}, None),
                ({'message': 'Unsupported parameter: truncation',
                  'detail': 'Unsupported parameter: max_output_tokens'}, None),
                ({'message': 'Unsupported parameter: truncation', 'padding': 'x' * 9000}, None)]:
            with self.subTest(expected=expected, shape=list(raw_value)):
                evidence = []
                rejected = FakeResponse(json.dumps(raw_value).encode())
                rejected.status = 400
                transport, connection = self.make(evidence_sink=evidence.append)
                connection.getresponse = lambda: rejected
                pending = transport.start(payload(truncation='disabled', unknown_fixture_field=True), self.context())
                with self.assertRaises(GateError):
                    pending.result(2)
                self.assertEqual(evidence[0]['rejected_parameter'], expected)
                self.assertLessEqual(evidence[0]['error_body_bytes_observed'], 8193)
                self.assertNotIn(secret, json.dumps(evidence))
                self.assertNotIn('Unsupported parameter', json.dumps(evidence))
                self.assertEqual(evidence[0]['unsupported_output_limit_reported'], expected == 'max_output_tokens')
                self.assertEqual(len(connection.sent), 1)

    def test_dispatch_is_synchronous_pending_receipt_and_frozen_tls_headers(self):
        released = threading.Event()
        connection = FakeConnection(wait=released)
        transport, connection = self.make(connection)
        original = payload()
        snapshot = deepcopy(original)
        started = time.monotonic()
        pending = transport.start(original, self.context())
        try:
            self.assertLess(time.monotonic() - started, 1)
            self.assertIsInstance(pending, Future)
            self.assertFalse(pending.done())
            self.assertFalse(pending.cancel())
            self.assertEqual(original, snapshot)
            self.assertEqual(len(connection.sent), 1)
            method, path, raw, headers = connection.sent[0]
            self.assertEqual((method, path), ('POST', '/backend-api/codex/responses'))
            wire = json.loads(raw)
            self.assertEqual(wire['max_output_tokens'], 2048)
            self.assertEqual(wire['reasoning'], original['reasoning'])
            self.assertEqual(wire['input'], original['input'])
            self.assertEqual(wire['instructions'], 'You are a helpful assistant.')
            self.assertEqual(wire['include'], ['reasoning.encrypted_content'])
            self.assertIs(wire['store'], False)
            self.assertIs(wire['stream'], True)
            self.assertEqual(headers['Authorization'], 'Bearer fixture-access-secret')
            self.assertEqual(headers['chatgpt-account-id'], 'fixture-account-secret')
            self.assertEqual(headers['OpenAI-Beta'], 'responses=experimental')
            self.assertEqual(headers['originator'], 'pi')
            self.assertEqual(headers['x-client-request-id'], 'offline-request')
            self.assertEqual(self.calls[0][:2], ('chatgpt.com', 443))
            tls = self.calls[0][2]['context']
            self.assertTrue(tls.check_hostname)
            self.assertEqual(pending.evidence['semantic_payload_sha256'], digest(original))
            self.assertEqual(pending.evidence['wire_payload_sha256'], digest(wire))
            self.assertEqual({item['field'] for item in pending.evidence['adaptations']},
                             {'store', 'stream', 'instructions', 'include'})
            self.assertNotIn('fixture-access-secret', repr(pending.evidence))
            self.assertNotIn('fixture-account-secret', repr(pending.evidence))
        finally:
            released.set()
        self.assertEqual(pending.result(2), response())
        self.assertTrue(connection.closed.wait(1))

    def test_existing_semantics_are_preserved_and_framing_changes_recorded(self):
        transport, connection = self.make()
        original = payload(instructions='Keep these instructions', include=['custom'], store=True, stream=False,
                           client_metadata={'x-codex-turn-metadata': {'thread_id': 'fixture'}})
        pending = transport.start(original, self.context())
        pending.result(2)
        wire = json.loads(connection.sent[0][2])
        self.assertEqual(wire['instructions'], original['instructions'])
        self.assertEqual(wire['client_metadata'], original['client_metadata'])
        self.assertEqual(wire['include'], ['custom', 'reasoning.encrypted_content'])

    def test_drift_and_cap_fail_before_auth_or_network(self):
        for change in ({'model': 'other'}, {'reasoning': {'effort': 'high'}}, {'max_output_tokens': 2047},
                       {'max_output_tokens': True}, {'provider': 'other'}):
            transport, connection = self.make()
            with self.assertRaises(GateError):
                transport.start({**payload(), **change}, self.context())
            self.assertEqual(self.calls, [])
            self.assertFalse(connection.sent)

    def test_expired_or_injected_header_auth_rejected_before_connect(self):
        for credential in [Credential('fixture', 'account', 0), Credential('bad\r\nheader', 'account', 1e20)]:
            transport, connection = self.make()
            transport._auth_loader = lambda: credential
            with self.assertRaises(GateError) as caught:
                transport.start(payload(), self.context())
            self.assertEqual(self.calls, [])
            self.assertNotIn('bad\r\nheader', str(caught.exception))
            self.assertIsNone(caught.exception.__context__)

    def test_cancel_before_dispatch_and_no_identity_replay(self):
        transport, connection = self.make()
        context = self.context()
        context['cancel_event'].set()
        with self.assertRaises(GateError):
            transport.start(payload(), context)
        self.assertEqual(self.calls, [])
        context['cancel_event'].clear()
        transport.start(payload(), context).result(2)
        with self.assertRaises(GateError) as caught:
            transport.start(payload(), context)
        self.assertEqual(caught.exception.code, 'NO_REPLAY')
        self.assertEqual(len(connection.sent), 1)

    def test_cancel_interrupts_io_but_retains_late_terminal(self):
        released = threading.Event()
        connection = FakeConnection(wait=released)
        transport, connection = self.make(connection)
        context = self.context()
        pending = transport.start(payload(), context)
        self.assertTrue(connection.reader_started.wait(1))
        context['cancel_event'].set()
        self.assertTrue(connection.sock.stopped.wait(1))
        released.set()
        self.assertEqual(pending.result(2), response())

    def test_cancellation_before_send_after_connect(self):
        transport, connection = self.make()
        context = self.context()
        def connect():
            context['cancel_event'].set()
        connection.connect = connect
        with self.assertRaises(GateError) as caught:
            transport.start(payload(), context)
        self.assertEqual(caught.exception.code, 'STOPPED')
        self.assertFalse(connection.sent)

    def test_http_errors_redirects_and_content_types_never_replayed(self):
        for status in (301, 302, 307, 308, 401, 429, 500):
            transport, connection = self.make()
            value = FakeResponse(b'fixture-access-secret')
            value.status = status
            connection.getresponse = lambda: value
            with self.assertRaises(GateError) as caught:
                transport.start(payload(), self.context()).result(2)
            self.assertEqual(caught.exception.code, 'UPSTREAM_HTTP')
            self.assertNotIn('fixture-access-secret', str(caught.exception))
            self.assertEqual(len(connection.sent), 1)
        transport, connection = self.make()
        value = FakeResponse(b'{}')
        value.getheader = lambda *_: 'application/json'
        connection.getresponse = lambda: value
        with self.assertRaises(GateError) as caught:
            transport.start(payload(), self.context()).result(2)
        self.assertEqual(caught.exception.code, 'UPSTREAM_TYPE')

    def test_connection_send_and_read_exceptions_cannot_leak_credentials(self):
        for phase in ('connect', 'request', 'getresponse'):
            transport, connection = self.make()
            def fail(*args, **kwargs):
                raise RuntimeError('fixture-access-secret fixture-account-secret')
            setattr(connection, phase, fail)
            with self.assertRaises(GateError) as caught:
                pending = transport.start(payload(), self.context())
                pending.result(2)
            self.assertNotIn('fixture-access-secret', str(caught.exception))
            self.assertNotIn('fixture-account-secret', repr(caught.exception))
            self.assertIsNone(caught.exception.__context__)
            self.assertTrue(connection.closed.wait(1))

    def test_host_credential_repr_is_redacted(self):
        value = Credential('fixture-access-secret', 'fixture-account-secret', 0)
        self.assertEqual(repr(value), '<host credential redacted>')
        self.assertEqual(str(value), repr(value))

    def test_auth_file_schema_expiry_permissions_and_parser_errors_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_path = Path(directory) / 'auth.json'
            valid = {'version': 1, 'credential': {'type': 'oauth', 'access': 'fixture-access-secret',
                      'refresh': 'fixture-refresh-secret', 'expires': time.time() * 1000 + 60000,
                      'accountId': 'fixture-account-secret', 'idToken': 'fixture-id-secret', 'email': 'fixture@example.test'}}
            auth_path.write_text(json.dumps(valid))
            auth_path.chmod(0o600)
            with patch('transport.AUTH_PATH', auth_path):
                credential = load_host_credential()
                self.assertEqual(credential.access, 'fixture-access-secret')
                self.assertEqual(credential.account_id, 'fixture-account-secret')
                self.assertNotIn('fixture', repr(credential))
                auth_path.chmod(0o644)
                with self.assertRaises(GateError):
                    load_host_credential()
                auth_path.chmod(0o600)
                auth_path.write_text('{"fixture-access-secret": malformed}')
                with self.assertRaises(GateError) as caught:
                    load_host_credential()
                self.assertNotIn('fixture-access-secret', repr(caught.exception))
                self.assertIsNone(caught.exception.__context__)
                auth_path.write_text(json.dumps({**valid, 'credential': {**valid['credential'], 'type': 'api_key'}}))
                with self.assertRaises(GateError):
                    load_host_credential()
                auth_path.write_text(json.dumps({**valid, 'credential': {**valid['credential'], 'expires': 0}}))
                with self.assertRaises(GateError) as caught:
                    load_host_credential()
                self.assertEqual(caught.exception.code, 'AUTH_EXPIRED')

    def test_cancel_stops_detached_httpresponse_socket_without_terminal(self):
        transport, connection = self.make()
        io_socket = connection.sock
        reading = threading.Event()
        class BlockedResponse(FakeResponse):
            def read1(self, limit):
                reading.set()
                if not io_socket.stopped.wait(1):
                    raise AssertionError('cancellation did not shutdown detached socket')
                raise OSError('fixture cancelled')
        def getresponse():
            connection.sock = None  # Mirrors HTTPConnection on Connection: close.
            return BlockedResponse(b'')
        connection.getresponse = getresponse
        context = self.context()
        pending = transport.start(payload(), context)
        self.assertTrue(reading.wait(1))
        context['cancel_event'].set()
        with self.assertRaises(GateError) as caught:
            pending.result(2)
        self.assertEqual(caught.exception.code, 'SSE_TRUNCATED')
        self.assertTrue(io_socket.stopped.is_set())

    def test_evidence_sink_runs_outside_start_and_failure_preserves_receipt(self):
        released = threading.Event()
        entered = threading.Event()
        def sink(evidence):
            entered.set()
            released.wait(1)
            raise RuntimeError('fixture sink failed')
        transport, connection = self.make(evidence_sink=sink)
        pending = transport.start(payload(), self.context())
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(pending.done())
            self.assertEqual(len(connection.sent), 1)
        finally:
            released.set()
        self.assertEqual(pending.result(2), response())
        self.assertTrue(pending.evidence['evidence_sink_failed'])

    def test_default_construction_has_no_auth_or_network_side_effects(self):
        with patch('transport.os.open') as open_auth, patch('transport.socket.getaddrinfo') as dns:
            HostTransport()
            open_auth.assert_not_called()
            dns.assert_not_called()

    def test_real_http_serializer_uses_verified_tls_sni_and_one_synchronous_send(self):
        import socket
        import ssl
        from unittest.mock import MagicMock
        tls = ssl.create_default_context()
        io_socket = MagicMock()
        address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('203.0.113.1', 443))
        with patch('transport.socket.getaddrinfo', return_value=[address]), \
             patch('transport.socket.socket', return_value=io_socket), \
             patch.object(tls, 'wrap_socket', return_value=io_socket) as wrap:
            connection = _DeadlineHTTPSConnection('chatgpt.com', 443, timeout=4, context=tls)
            connection.connect()
            connection.request('POST', '/backend-api/codex/responses', body=b'{}',
                               headers={'Authorization': 'Bearer fixture', 'Connection': 'close'})
            io_socket.connect.assert_called_once_with(('203.0.113.1', 443))
            wrap.assert_called_once_with(io_socket, server_hostname='chatgpt.com')
            wire = b''.join(call.args[0] for call in io_socket.sendall.call_args_list)
            self.assertIn(b'POST /backend-api/codex/responses HTTP/1.1', wire)
            self.assertIn(b'Host: chatgpt.com', wire)
            self.assertTrue(wire.endswith(b'{}'))
            self.assertTrue(all(0 < call.args[0] <= 4 for call in io_socket.settimeout.call_args_list))

    def test_dns_timeout_never_dispatches_or_connects_later(self):
        connection = _DeadlineHTTPSConnection('chatgpt.com', 443, timeout=0.03,
                                             context=__import__('ssl').create_default_context())
        released = threading.Event()
        resolved = threading.Event()
        def lookup(*args, **kwargs):
            released.wait(1)
            resolved.set()
            return []
        with patch('transport.socket.getaddrinfo', side_effect=lookup), patch('transport.socket.socket') as create:
            try:
                with self.assertRaises(TimeoutError):
                    connection.connect()
                self.assertIsNone(connection.sock)
            finally:
                released.set()
                self.assertTrue(resolved.wait(1))
            create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
