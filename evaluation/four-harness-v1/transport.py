"""Host-only Codex HTTPS transport. Import/construction never contacts upstream.

start(payload, context) synchronously connects and sends exactly once, then returns
an uncancellable Future whose result is the unmodified terminal Responses object.
The gateway owns admission/accounting; this transport never refreshes auth/retries.
Injected connection/auth factories are trusted HOST test seams, not client options.
"""
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
from email.utils import parsedate_to_datetime
import errno
import http.client
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import threading
import time
import zlib

from ledger import (GateError, POLICY, canonical, digest, require, normalize_usage, usage_schema_valid,
                    retry_error_schema_valid, NONTRANSIENT_CODES)

UPSTREAM = 'https://chatgpt.com/backend-api/codex/responses'
AUTH_PATH = Path('/Users/zcy/.dsh/.openai-codex-auth.json')
# Diagnostic output is confined to known request field names, never server text.
DIAGNOSTIC_FIELDS = frozenset({'model', 'reasoning', 'max_output_tokens', 'input',
    'instructions', 'include', 'store', 'stream', 'truncation', 'tools', 'tool_choice',
    'parallel_tool_calls', 'text', 'temperature', 'top_p', 'service_tier',
    'prompt_cache_key', 'client_metadata'})


def rejected_parameter(value, request_fields):
    """Recognize bounded unsupported-parameter diagnostics; return only an allowlist token."""
    if not isinstance(value, dict):
        return None
    messages = [value.get('message'), value.get('detail')]
    if isinstance(value.get('error'), dict):
        messages.append(value['error'].get('message'))
    recognized = set()
    for message in messages:
        if not isinstance(message, str) or len(message) > 1024:
            continue
        match = re.match(r"^Unsupported parameter:\s*['\"]?([a-z][a-z0-9_]*)\b", message, re.IGNORECASE)
        if match and match[1] in DIAGNOSTIC_FIELDS and match[1] in request_fields:
            recognized.add(match[1])
    return next(iter(recognized)) if len(recognized) == 1 else None


TERMINALS = {'response.' + status: status for status in ('completed', 'incomplete', 'failed', 'cancelled')}
# Known Responses vocabulary; nonterminal extensions are drained without actions.
# Only TERMINALS can produce a receipt; missing or invalid terminals still fail.
EVENTS = set(TERMINALS) | {'response.created', 'response.in_progress',
    'response.output_item.added', 'response.output_item.done',
    'response.content_part.added', 'response.content_part.done',
    'response.output_text.delta', 'response.output_text.done',
    'response.output_text.annotation.added', 'response.refusal.delta', 'response.refusal.done',
    'response.function_call_arguments.delta', 'response.function_call_arguments.done',
    'response.reasoning_summary_part.added', 'response.reasoning_summary_part.done',
    'response.reasoning_summary_text.delta', 'response.reasoning_summary_text.done',
    'response.reasoning_text.delta', 'response.reasoning_text.done'}


class Credential:
    __slots__ = ('access', 'account_id', 'expires')

    def __init__(self, access, account_id, expires):
        self.access, self.account_id, self.expires = access, account_id, expires

    def __repr__(self):
        return '<host credential redacted>'

    __str__ = __repr__

    def validate(self):
        require(all(isinstance(value, str) and value and value.isascii() and
                    all(32 < ord(char) < 127 for char in value)
                    for value in (self.access, self.account_id)),
                'AUTH_INVALID', 'Host credential fields are invalid')
        require(type(self.expires) in (int, float) and math.isfinite(self.expires) and
                self.expires > time.time() * 1000 + 5000,
                'AUTH_EXPIRED', 'Host credential expired or too close to expiry; no refresh is permitted')


def load_host_credential():
    """Read only the approved host auth file; expiry is SDK epoch milliseconds."""
    failed = False
    credential = None
    try:
        fd = os.open(AUTH_PATH, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r', encoding='utf-8') as source:
            info = os.fstat(source.fileno())
            if not (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                    info.st_nlink == 1 and not info.st_mode & 0o077 and info.st_size <= 65536):
                raise ValueError()
            data = json.load(source)
        raw = data['credential']
        if type(data.get('version')) is not int or raw.get('type') != 'oauth':
            raise ValueError()
        credential = Credential(raw['access'], raw['accountId'], raw['expires'])
    except Exception:
        failed = True
    # Do not retain/chains exceptions from parsing secret-bearing data.
    if failed:
        raise GateError('AUTH_INVALID', 'Approved host credential is unavailable or invalid')
    credential.validate()
    return credential


STAGES = ('credential', 'dns', 'tcp', 'tls', 'send', 'response_headers', 'stream', 'decode', 'terminal')
SAFE_CODES = frozenset({'AUTH_INVALID', 'AUTH_EXPIRED', 'STOPPED', 'TLS_POLICY', 'MODEL_DRIFT',
    'OUTPUT_CAP', 'TRUNCATION_POLICY', 'REQUEST_ID', 'NO_REPLAY', 'SCHEMA', 'UPSTREAM_HTTP',
    'UPSTREAM_TYPE', 'UPSTREAM_JSON', 'UPSTREAM_DECODE', 'SSE_TRUNCATED', 'SSE_SCHEMA',
    'SSE_LIMIT', 'SSE_DUPLICATE', 'TRANSPORT_SEND', 'TRANSPORT_READ'})


def _failure_kind(error):
    # Never copy str(error), exception class names, URLs, addresses, or TLS text.
    if isinstance(error, ssl.SSLCertVerificationError):
        return 'tls_certificate'
    if isinstance(error, ssl.SSLError):
        return 'tls_protocol'
    if isinstance(error, socket.gaierror):
        return 'dns_temporary' if error.errno == socket.EAI_AGAIN else 'dns_permanent'
    if isinstance(error, TimeoutError):
        return 'timeout'
    if isinstance(error, OSError):
        return {errno.ECONNREFUSED: 'connection_refused', errno.ECONNRESET: 'connection_reset',
                errno.ENETUNREACH: 'network_unreachable', errno.EHOSTUNREACH: 'host_unreachable',
                errno.ETIMEDOUT: 'timeout', errno.EPIPE: 'broken_pipe'}.get(error.errno, 'io_error')
    if isinstance(error, GateError):
        return 'cancelled' if error.code == 'STOPPED' else 'validation'
    return 'unknown'


class _Diagnostics:
    def __init__(self, context, clock):
        self.clock, self.started = clock, clock()
        self.active, self.entered = None, self.started
        self.value = dict(format='four-harness-transport-v2', request_id=context.get('request_id'),
            logical_request_id=context.get('logical_request_id', context.get('request_id')),
            retry_of=context.get('retry_of'), attempt=context.get('attempt', 1),
            stage='credential', send_state='not_started',
            stage_timings={stage: None for stage in STAGES})
        self.enter('credential')

    def _end(self, now):
        if self.active is not None:
            row = self.value['stage_timings'][self.active]
            row['elapsed_seconds'] += max(0, now - self.entered)
            row['ended_seconds'] = max(0, now - self.started)

    def enter(self, stage):
        assert stage in STAGES
        now = self.clock()
        self._end(now)
        self.active, self.entered = stage, now
        self.value['stage'] = stage
        if self.value['stage_timings'][stage] is None:
            self.value['stage_timings'][stage] = dict(started_seconds=max(0, now - self.started),
                                                    elapsed_seconds=0.0, ended_seconds=None)

    def finish(self, error=None):
        now = self.clock()
        self._end(now)
        self.active = None
        self.value.update(elapsed_seconds=max(0, now - self.started),
                          outcome='failed' if error else 'completed')
        if error is not None:
            self.value['failure_kind'] = _failure_kind(error)
            if (type(error) is ssl.SSLEOFError and
                    getattr(error, 'reason', None) == 'UNEXPECTED_EOF_WHILE_READING' and
                    self.value.get('stage') == 'tls' and self.value.get('send_state') == 'not_started' and
                    self.value.get('http_status') is None and not self.value.get('partial_output_observed')):
                self.value['failure_kind'] = 'tls_unexpected_eof'


class _IODeadline:
    """Absolute connect/send/header deadlines; only received bytes renew stream idle."""
    def __init__(self, clock):
        self.clock, self.lock = clock, threading.Lock()
        self.deadline, self.expired, self.idle = None, False, None

    def arm(self, seconds, *, idle=False):
        with self.lock:
            self.deadline = self.clock() + seconds
            self.idle = seconds if idle else None
            self.expired = False

    def remaining(self):
        with self.lock:
            if self.deadline is None:
                return None
            remaining = self.deadline - self.clock()
            self.expired = self.expired or remaining <= 0
            if self.expired:
                raise TimeoutError('Transport stage deadline expired')
            return remaining

    def activity(self, count):
        self.remaining()
        with self.lock:
            if count and self.idle is not None:
                self.deadline = self.clock() + self.idle

    def disarm(self):
        with self.lock:
            self.deadline = None


def _retry_headers(response):
    """Normalize Retry-After to numeric seconds; arbitrary header text never escapes."""
    result = {}
    for name in ('Retry-After', 'retry-after-ms'):
        text = response.getheader(name, '')
        if not isinstance(text, str) or not text or len(text) > 128:
            continue
        try:
            number = float(text)
        except ValueError:
            try:
                number = parsedate_to_datetime(text).timestamp() - time.time() if name == 'Retry-After' else -1
            except (TypeError, ValueError, OverflowError):
                continue
        if math.isfinite(number) and number >= 0:
            result[name] = str(number)
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON member')
        result[key] = value
    return result


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    """One address, no fallback/retry; resolver never receives auth/request bytes."""
    def __init__(self, host, port, *, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self.clock = time.monotonic
        self.deadline = self.clock() + timeout
        self.diagnostics, self.cancel_event = None, None

    def remaining(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise GateError('STOPPED', 'Transport cancelled')
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError()
        return remaining

    def connect(self):
        if self.diagnostics:
            self.diagnostics.enter('dns')
        resolved = Future()
        def resolve():
            try:
                resolved.set_result(socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM))
            except Exception as error:
                # Keep only a typed errno to distinguish transient DNS, not resolver text.
                safe = socket.gaierror(error.errno, 'Host resolution failed') if isinstance(error, socket.gaierror) else OSError('Host resolution failed')
                resolved.set_exception(safe)
        threading.Thread(target=resolve, daemon=True, name='codex-dns').start()
        while True:
            try:
                addresses = resolved.result(timeout=min(.05, self.remaining()))
                break
            except TimeoutError:
                self.remaining()
        self.remaining()
        if not addresses:
            raise socket.gaierror(socket.EAI_NONAME, 'Host resolution failed')
        if self.diagnostics:
            self.diagnostics.enter('tcp')
        family, kind, protocol, _, address = addresses[0]
        self.sock = socket.socket(family, kind, protocol)
        self.sock.settimeout(self.remaining())
        self.sock.connect(address)
        self.sock.settimeout(self.remaining())
        if self.diagnostics:
            self.diagnostics.enter('tls')
        if self.cancel_event is None:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        else:
            # Publish the SSLSocket before its blocking handshake. wrap_socket's
            # default handshake detaches the raw fd before cancellation can see it.
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host, do_handshake_on_connect=False)
            self.sock.settimeout(self.remaining())
            self.sock.do_handshake()
        self.remaining()

    def send(self, data):
        if self.sock is None:
            self.connect()
        self.sock.settimeout(self.remaining())
        super().send(data)
        self.remaining()


class ReceiptFuture(Future):
    """Cancellation cannot erase dispatch/accounting evidence; use cancel_event."""
    def cancel(self):
        return False


def parse_sse(source, *, max_bytes=8 * 1024 * 1024, max_events=100000,
              max_event_bytes=2 * 1024 * 1024, cancel_event=None, evidence=None):
    observed=[]; partial_output_events=[]
    try:
        result = _parse_sse(source,max_bytes=max_bytes,max_events=max_events,
                          max_event_bytes=max_event_bytes,cancel_event=cancel_event,observed=observed,
                          partial_output_events=partial_output_events)
        if evidence is not None:
            evidence['partial_output_observed'] = bool(partial_output_events)
        return result
    except Exception as original:
        from ledger import Ledger
        error=original if isinstance(original,GateError) else GateError('SSE_SCHEMA','Invalid upstream framing; no retry')
        if partial_output_events:
            error.partial_output_events=deepcopy(partial_output_events)
        if observed:
            error.observed_response=deepcopy(max(observed,key=lambda value:Ledger._known_lower_bound(normalize_usage(value.get('usage'),terminal=False))))
            error.observed_responses=deepcopy(observed)
        raise error from None


def _parse_sse(source, *, max_bytes, max_events, max_event_bytes, cancel_event, observed, partial_output_events):
    """Bound memory/bytes/events; retain complete terminal data on cancellation.

    EOF before a terminal, malformed/unknown events and duplicate terminals fail.
    Cancellation after a complete terminal cannot erase that receipt; unrelated
    I/O truncation still fails closed.
    """
    total = count = event_size = 0
    fields = []
    terminal = None
    ended = False
    while True:
        io_failed = False
        try:
            line = source.readline(min(max_event_bytes + 1, max_bytes - total + 1))
        except (OSError, http.client.HTTPException):
            io_failed = True
        if io_failed:
            if terminal is not None and not fields and cancel_event is not None and cancel_event.is_set():
                return terminal
            raise GateError('SSE_TRUNCATED', 'Upstream stream I/O ended without complete framing')
        if not line:
            require(not fields, 'SSE_TRUNCATED', 'Upstream ended within an event')
            require(terminal is not None, 'SSE_TRUNCATED', 'Upstream ended without a terminal receipt')
            return terminal
        total += len(line)
        event_size += len(line)
        require(total <= max_bytes and event_size <= max_event_bytes,
                'SSE_LIMIT', 'Upstream stream exceeded its byte bound')
        require(line.endswith(b'\n'), 'SSE_TRUNCATED', 'Upstream line was truncated')
        line = line.rstrip(b'\r\n')
        if line:
            fields.append(line)
            continue
        count += 1
        require(count <= max_events, 'SSE_LIMIT', 'Upstream stream exceeded its event bound')
        data_lines, names = [], []
        for field in fields:
            if field.startswith(b':'):
                continue
            key, separator, value = field.partition(b':')
            value = value[1:] if value.startswith(b' ') else value
            require(separator and key in {b'data', b'event', b'id', b'retry'}, 'SSE_SCHEMA', 'Unknown SSE field')
            if key == b'data':
                data_lines.append(value)
            elif key == b'event':
                names.append(value)
        fields, event_size = [], 0
        if not data_lines:
            continue
        require(not ended, 'SSE_SCHEMA', 'Data followed the stream end marker')
        raw = b'\n'.join(data_lines)
        if raw == b'[DONE]':
            require(terminal is not None, 'SSE_TRUNCATED', 'End marker lacks a terminal receipt')
            ended = True
            continue
        failed = False
        try:
            event = json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError):
            failed = True
        if failed:
            raise GateError('SSE_SCHEMA', 'Malformed upstream event')
        require(isinstance(event, dict) and isinstance(event.get('type'), str) and
                re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', event['type']),
                'SSE_SCHEMA', 'Invalid upstream event type')
        # Nonterminal protocol extensions (including rate-limit telemetry) do not
        # execute tools or constitute receipts. Drain them until a known terminal
        # Responses object; EOF without that object still fails with unknown cost.
        kind = event['type']
        if kind in TERMINALS and isinstance(event.get('response'),dict):
            observed.append(event['response'])
        require(not names or names == [kind.encode()], 'SSE_SCHEMA', 'SSE event name disagrees with its data')
        require(terminal is None, 'SSE_DUPLICATE', 'Event followed a terminal receipt')
        if kind not in TERMINALS and ((kind.startswith('response.') and
                kind not in {'response.created', 'response.in_progress'}) or
                (isinstance(event.get('response'), dict) and event['response'].get('output'))):
            # Reasoning, refusal, content and unknown response extensions cannot
            # establish no output. Bounded evidence only, never tool dispatch.
            partial_output_events.append(event)
        if kind in TERMINALS:
            response = event.get('response')
            require(isinstance(response, dict) and response.get('status') == TERMINALS[kind],
                    'SSE_SCHEMA', 'Terminal event lacks a matching Responses object')
            terminal = response


class _BoundedBody:
    """Decode incrementally, bounding both wire and decoded bytes independently."""
    def __init__(self, source, encoding, limit, evidence, *, before_read=None, received=None, diagnostics=None):
        require(encoding in {'identity', 'gzip', 'deflate'},
                'UPSTREAM_TYPE', 'Unsupported upstream content encoding')
        self.source, self.limit, self.evidence = source, limit, evidence
        self.before_read, self.received, self.diagnostics = before_read, received, diagnostics
        self.decoder = (zlib.decompressobj(31 if encoding == 'gzip' else 15)
                        if encoding != 'identity' else None)
        self.buffer = b''
        self.ended = False
        self.wire_bytes = self.decoded_bytes = 0
        self.wire_hash, self.decoded_hash = hashlib.sha256(), hashlib.sha256()

    def _fill(self):
        if self.ended:
            return
        if self.diagnostics:
            self.diagnostics.enter('stream')
        if self.before_read:
            self.before_read()
        chunk = getattr(self.source, 'read1', self.source.read)(
            min(65536, self.limit - self.wire_bytes + 1))
        if self.received:
            self.received(len(chunk))
        if self.diagnostics:
            self.diagnostics.enter('decode')
        self.wire_bytes += len(chunk)
        self.wire_hash.update(chunk)
        self.evidence['response_body_bytes_observed'] = self.wire_bytes
        self.evidence['response_body_sha256'] = self.wire_hash.hexdigest()
        require(self.wire_bytes <= self.limit, 'SSE_LIMIT', 'Upstream wire body exceeded its byte bound')
        if not chunk:
            self.ended = True
            require(self.decoder is None or self.decoder.eof,
                    'UPSTREAM_DECODE', 'Encoded upstream body was truncated')
            return
        try:
            decoded = (self.decoder.decompress(chunk, self.limit - self.decoded_bytes + 1)
                       if self.decoder is not None else chunk)
        except zlib.error:
            raise GateError('UPSTREAM_DECODE', 'Invalid upstream content encoding') from None
        self.decoded_bytes += len(decoded)
        self.decoded_hash.update(decoded)
        self.evidence['response_decoded_bytes_observed'] = self.decoded_bytes
        self.evidence['response_decoded_sha256'] = self.decoded_hash.hexdigest()
        require(self.decoded_bytes <= self.limit, 'SSE_LIMIT', 'Decoded upstream body exceeded its byte bound')
        require(self.decoder is None or not self.decoder.unused_data,
                'UPSTREAM_DECODE', 'Trailing encoded upstream data')
        self.buffer += decoded

    def peek(self):
        while not self.buffer and not self.ended:
            self._fill()
        return self.buffer

    def readline(self, size):
        while b'\n' not in self.buffer and len(self.buffer) < size and not self.ended:
            self._fill()
        newline = self.buffer.find(b'\n')
        count = min(size, newline + 1 if newline >= 0 else len(self.buffer))
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result


def _observed_usage(candidate):
    if not isinstance(candidate, dict) or not isinstance(candidate.get('usage'), dict):
        return None
    usage = normalize_usage(candidate['usage'], terminal=False)
    return {'usage': {**{key: usage[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')},
                      'input_tokens_details': {'cached_tokens': usage['cached_input_tokens']},
                      'output_tokens_details': {'reasoning_tokens': usage['reasoning_tokens']}},
            'model': candidate.get('model') if candidate.get('model') == POLICY['model'] else None}


def _parse_terminal_json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError):
        raise GateError('UPSTREAM_JSON', 'Malformed upstream JSON') from None
    response = value.get('response', value) if isinstance(value, dict) else None
    observed = _observed_usage(response)
    try:
        require(isinstance(value, dict) and isinstance(response, dict),
                'UPSTREAM_JSON', 'Upstream JSON lacks a Responses object')
        require(response.get('object') == 'response' and
                isinstance(response.get('id'), str) and bool(response['id']) and
                response.get('status') in TERMINALS.values() and
                isinstance(response.get('model'), str) and bool(response['model']) and
                isinstance(response.get('output'), list),
                'UPSTREAM_JSON', 'Upstream JSON is not a terminal Responses object')
        if response is not value:
            require(isinstance(value.get('type'), str) and value['type'] in TERMINALS and
                    response['status'] == TERMINALS[value['type']],
                    'UPSTREAM_JSON', 'Terminal JSON event disagrees with its response')
        return response
    except GateError as error:
        if observed is not None:
            error.observed_response = observed
        raise


class HostTransport:
    def __init__(self, *, auth_loader=load_host_credential, connection_factory=_DeadlineHTTPSConnection,
                 evidence_sink=None, dispatch_timeout=None, response_timeout=None,
                 connect_timeout=None, send_timeout=None, headers_timeout=None, stream_idle_timeout=None,
                 timeout_profile=None, clock=None, max_output_tokens=2048,
                 max_bytes=8 * 1024 * 1024, max_events=100000, profile='smoke'):
        # Explicit legacy arguments select the old combined connect+send deadline.
        # Optimization/workflow otherwise uses independent stages, with no solve cap.
        legacy_args = dispatch_timeout is not None or response_timeout is not None
        split_args = any(value is not None for value in (connect_timeout, send_timeout, headers_timeout, stream_idle_timeout))
        require(timeout_profile in (None, 'legacy', 'workflow') and not (legacy_args and split_args) and
                not (legacy_args and timeout_profile == 'workflow') and not (split_args and timeout_profile == 'legacy'),
                'TRANSPORT_CONFIG', 'Select one transport timeout profile')
        self.legacy_timeouts = (timeout_profile == 'legacy' or legacy_args or
            (timeout_profile is None and profile == 'smoke' and not split_args))
        if self.legacy_timeouts:
            dispatch_timeout = 4.0 if dispatch_timeout is None else dispatch_timeout
            response_timeout = 60.0 if response_timeout is None else response_timeout
            require(type(dispatch_timeout) in (int, float) and 0 < dispatch_timeout <= 4.5 and
                    type(response_timeout) in (int, float) and 0 < response_timeout <= 180,
                    'TRANSPORT_CONFIG', 'Legacy transport timeouts must be positive and bounded')
            connect_timeout = send_timeout = dispatch_timeout
            headers_timeout = stream_idle_timeout = response_timeout
        else:
            connect_timeout = 30.0 if connect_timeout is None else connect_timeout
            send_timeout = 60.0 if send_timeout is None else send_timeout
            headers_timeout = 300.0 if headers_timeout is None else headers_timeout
            stream_idle_timeout = 300.0 if stream_idle_timeout is None else stream_idle_timeout
        require(all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in
                    (connect_timeout, send_timeout, headers_timeout, stream_idle_timeout)),
                'TRANSPORT_CONFIG', 'Finite positive transport timeouts required')
        self.connect_timeout, self.send_timeout = connect_timeout, send_timeout
        self.headers_timeout, self.stream_idle_timeout = headers_timeout, stream_idle_timeout
        self.clock = clock or time.monotonic
        require(profile in {'smoke', 'optimization', 'workflow'} and type(max_output_tokens) is int and
                max_output_tokens == (2048 if profile == 'smoke' else 128000),
                'OUTPUT_CAP', 'Transport requires the exact approved profile output cap')
        self.profile = profile
        require(type(max_bytes) is int and 0 < max_bytes <= 8 * 1024 * 1024 and
                type(max_events) is int and 0 < max_events <= 100000,
                'TRANSPORT_CONFIG', 'SSE bounds exceed approved transport limits')
        self._auth_loader, self._factory, self._sink = auth_loader, connection_factory, evidence_sink
        self.dispatch_timeout, self.response_timeout = dispatch_timeout, response_timeout
        self.max_output_tokens, self.max_bytes, self.max_events = max_output_tokens, max_bytes, max_events
        self._seen, self._lock = set(), threading.Lock()

    def __repr__(self):
        return '<HostTransport fixed Codex HTTPS; credentials redacted>'

    def _emit(self, evidence):
        if self._sink is not None:
            try:
                self._sink(deepcopy(evidence))
            except Exception:
                evidence['evidence_sink_failed'] = True

    def start(self, payload, context):
        diagnostics = _Diagnostics(context, self.clock)
        failure = None
        try:
            return self._start(payload, context, diagnostics)
        except Exception as error:
            diagnostics.finish(error)
            if diagnostics.value.get('deadline_exceeded'):
                diagnostics.value['failure_kind'] = 'timeout'
            code = error.code if isinstance(error, GateError) and error.code in SAFE_CODES else 'TRANSPORT_SEND'
            diagnostics.value['error_code'] = code
            failure = GateError(code, 'Host dispatch failed; consult structured transport evidence')
            self._emit(diagnostics.value)
            failure.evidence = deepcopy(diagnostics.value)
        # Raised outside except: no secret-bearing exception/context can escape.
        raise failure

    def _start(self, payload, context, diagnostics):
        require(isinstance(payload, dict) and payload.get('model') == POLICY['model'] and
                payload.get('provider', POLICY['provider']) == POLICY['provider'] and
                isinstance(payload.get('reasoning'), dict) and payload['reasoning'].get('effort') == POLICY['effort'],
                'MODEL_DRIFT', 'Transport request differs from the frozen provider/model/effort')
        require(type(payload.get('max_output_tokens')) is int and payload['max_output_tokens'] == self.max_output_tokens,
                'OUTPUT_CAP', 'Transport requires the exact frozen output cap')
        require('truncation' not in payload or payload['truncation'] == 'disabled',
                'TRUNCATION_POLICY', 'Only default-disabled truncation framing is qualified')
        cancel = context['cancel_event']
        request_id = context['request_id']
        require(isinstance(request_id, str) and 0 < len(request_id) <= 200 and
                all(32 < ord(c) < 127 for c in request_id), 'REQUEST_ID', 'Invalid transport request identity')
        require(not cancel.is_set(), 'STOPPED', 'Transport cancelled before dispatch')
        with self._lock:
            require(request_id not in self._seen, 'NO_REPLAY', 'Transport request identity was already used')
            self._seen.add(request_id)
        wire = deepcopy(payload)
        adaptations = []
        if 'truncation' in wire:
            # Frozen native framing: Pi openai-codex-responses.js buildRequestBody
            # omits this field. Installed openai/resources/responses/responses.d.ts
            # ResponseCreateParamsBase documents disabled as the default (excess
            # context -> HTTP 400). This is source-contract evidence, not proof of
            # backend behavior; never infer auto or drop the required output cap.
            adaptations.append({'field': 'truncation', 'before_present': True,
                                'before_sha256': digest(wire['truncation']), 'after_present': False,
                                'contract': 'native_codex_omission_responses_default_disabled'})
            del wire['truncation']
        def frame(key, value):
            if key not in wire or wire[key] != value:
                adaptations.append({'field': key, 'before_present': key in wire,
                                    'before_sha256': digest(wire[key]) if key in wire else None,
                                    **({'after_sha256': digest(value)} if key == 'include' else {'after': value})})
                wire[key] = value
        frame('store', False)
        frame('stream', True)
        if not wire.get('instructions'):
            frame('instructions', 'You are a helpful assistant.')
        include = wire.get('include', [])
        require(isinstance(include, list) and all(isinstance(v, str) for v in include), 'SCHEMA', 'Invalid include framing')
        if 'reasoning.encrypted_content' not in include:
            frame('include', include + ['reasoning.encrypted_content'])
        require('provider' not in wire, 'SCHEMA', 'Gateway must remove the local provider field')
        evidence = diagnostics.value
        evidence.update(upstream=UPSTREAM, semantic_payload_sha256=digest(payload),
                        wire_payload_sha256=digest(wire), adaptations=adaptations, retries=0, redirects=0,
                        timeout_profile='legacy' if self.legacy_timeouts else 'workflow',
                        deadlines_seconds=dict(connect=self.connect_timeout, send=self.send_timeout,
                            response_headers=self.headers_timeout, stream_idle=self.stream_idle_timeout))
        body = canonical(wire).encode()
        connection = io_socket = None
        done, deadline = threading.Event(), _IODeadline(self.clock)

        def stop_io():
            while not done.wait(0.02):
                expired = False
                try:
                    deadline.remaining()
                except TimeoutError:
                    expired = True
                if cancel.is_set() or expired:
                    self._abort(connection, io_socket)
                    return

        threading.Thread(target=stop_io, daemon=True, name='codex-cancel').start()
        try:
            credential = self._auth_loader()
            credential.validate()
            require(not cancel.is_set(), 'STOPPED', 'Transport cancelled before connect')
            tls = ssl.create_default_context()
            deadline.arm(self.connect_timeout)
            connection = self._factory('chatgpt.com', 443, timeout=self.connect_timeout, context=tls)
            if isinstance(connection, _DeadlineHTTPSConnection):
                connection.clock, connection.diagnostics, connection.cancel_event = self.clock, diagnostics, cancel
                connection.deadline = self.clock() + deadline.remaining()
            else:
                diagnostics.enter('tcp')  # Injected factories may not expose DNS/TLS instrumentation.
            connection.connect()
            deadline.remaining()
            require(not cancel.is_set(), 'STOPPED', 'Transport cancelled before request send')
            headers = {'Authorization': 'Bearer ' + credential.access, 'chatgpt-account-id': credential.account_id,
                       'OpenAI-Beta': 'responses=experimental', 'originator': 'pi', 'accept': 'text/event-stream',
                       'Accept-Encoding': 'identity',
                       'content-type': 'application/json', 'x-client-request-id': request_id, 'Connection': 'close'}
            diagnostics.enter('send')
            if not self.legacy_timeouts:
                deadline.arm(self.send_timeout)
                if isinstance(connection, _DeadlineHTTPSConnection):
                    connection.deadline = self.clock() + deadline.remaining()
            connection.sock.settimeout(deadline.remaining())
            evidence['send_state'] = 'started'
            connection.request('POST', '/backend-api/codex/responses', body=body, headers=headers)
            evidence['send_state'] = 'completed'
            deadline.remaining()
        except Exception:
            if deadline.expired:
                evidence['deadline_exceeded'] = True
            done.set()
            self._abort(connection)
            raise
        # Headers/credential never enter Future evidence, result, repr or reports.
        del headers, credential
        evidence['dispatch_point'] = 'synchronous_https_request_send_completed'
        future = ReceiptFuture()
        future.evidence = deepcopy(evidence)
        # Keep the actual socket even if getresponse detaches connection.sock.
        io_socket = connection.sock
        diagnostics.enter('response_headers')
        if not self.legacy_timeouts:
            deadline.arm(self.headers_timeout)
        else:
            deadline.disarm()

        def before_read():
            remaining = deadline.remaining()
            io_socket.settimeout(self.stream_idle_timeout if remaining is None else remaining)

        def read():
            result = error_code = upstream = observed_response = None
            partial_output_events=[]
            try:
                io_socket.settimeout(self.headers_timeout if self.legacy_timeouts else deadline.remaining())
                upstream = connection.getresponse()
                deadline.remaining()
                diagnostics.enter('stream')
                if not self.legacy_timeouts:
                    deadline.arm(self.stream_idle_timeout, idle=True)
                evidence['http_status'] = upstream.status
                media_type = upstream.getheader('Content-Type', '').split(';')[0].strip().lower()
                encoding = upstream.getheader('Content-Encoding', 'identity').strip().lower()
                # Static classifications only; never persist arbitrary header text.
                evidence['response_media_type'] = media_type if media_type in {
                    'text/event-stream', 'application/json', 'text/html', 'text/plain'} else 'other'
                evidence['response_content_encoding'] = encoding if encoding in {
                    'identity', 'gzip', 'br', 'deflate', 'zstd'} else 'other'
                if upstream.status != 200:
                    # Keep bounded diagnostics without exposing server text,
                    # headers or credentials. Any retry eligibility is returned
                    # as a receipt; only the gateway can admit a new attempt.
                    error_body = b''
                    while len(error_body) < 8193:
                        before_read()
                        chunk = getattr(upstream, 'read1', upstream.read)(8193 - len(error_body))
                        deadline.activity(len(chunk))
                        if not chunk:
                            break
                        error_body += chunk
                    diagnostics.enter('decode')
                    evidence['error_body_sha256'] = hashlib.sha256(error_body).hexdigest()
                    evidence['error_body_bytes_observed'] = len(error_body)
                    evidence['error_body_truncated'] = len(error_body) > 8192
                    rejected = None
                    try:
                        value = json.loads(error_body, object_pairs_hook=_unique_object)
                        if not evidence['error_body_truncated']:
                            rejected = rejected_parameter(value, set(payload) | set(wire))
                            # Retain only allowlisted structure and hashes. Arbitrary
                            # upstream text can echo credentials or private inputs.
                        candidate = value.get('response', value) if isinstance(value, dict) else None
                        envelopes = [value] if candidate is value else [value, candidate]
                        objects = [item for item in envelopes if isinstance(item, dict)]
                        evidence['error_output_absent'] = (not evidence['error_body_truncated'] and
                            len(objects) == len(envelopes) and all(item.get('output', []) == [] and
                            normalize_usage(item.get('usage'), terminal=False)['output_tokens'] in (None, 0)
                            for item in objects))
                        evidence['error_output_present'] = any(bool(item.get('output')) or
                            (normalize_usage(item.get('usage'), terminal=False)['output_tokens'] or 0) > 0
                            for item in objects)
                        evidence['error_model_drift'] = any(item.get('model') not in (None, POLICY['model']) for item in objects)
                        evidence['error_schema_invalid'] = any(not usage_schema_valid(item.get('usage')) or
                            not retry_error_schema_valid(item.get('error')) or item.get('status') not in (None, 'failed') or
                            item.get('object') not in (None, 'response', 'error') for item in objects)
                        evidence['error_nontransient'] = any(code in NONTRANSIENT_CODES for item in objects
                            if isinstance(item.get('error'), dict)
                            for code in (item['error'].get('code'), item['error'].get('type')) if isinstance(code, str))
                        evidence['error_effort_drift'] = any(item.get('reasoning') is not None and
                            (not isinstance(item['reasoning'], dict) or item['reasoning'].get('effort') not in (None, POLICY['effort']))
                            for item in objects)
                        if isinstance(candidate, dict) and isinstance(candidate.get('usage'), dict):
                            usage = normalize_usage(candidate['usage'], terminal=False)
                            # Only numeric counters cross the error boundary; no
                            # arbitrary server text can leak into diagnostics.
                            observed_response = {'usage': {key:usage[key] for key in ('input_tokens','output_tokens','total_tokens')},
                                                 'model': candidate.get('model') if candidate.get('model') == POLICY['model'] else None}
                            observed_response['usage']['input_tokens_details'] = {'cached_tokens':usage['cached_input_tokens']}
                            observed_response['usage']['output_tokens_details'] = {'reasoning_tokens':usage['reasoning_tokens']}
                    except (ValueError, TypeError):
                        pass
                    if self.profile in {'optimization', 'workflow'} and upstream.status in {429, 500, 502, 503, 504}:
                        evidence['retry_headers'] = _retry_headers(upstream)
                    evidence['rejected_parameter'] = rejected
                    evidence['unsupported_output_limit_reported'] = rejected == 'max_output_tokens'
                    raise GateError('UPSTREAM_HTTP', 'Upstream rejected HTTP request; no retry or redirect')
                source = _BoundedBody(upstream, encoding, self.max_bytes, evidence,
                    before_read=before_read, received=deadline.activity, diagnostics=diagnostics)
                if media_type == 'text/event-stream':
                    result = parse_sse(source, max_bytes=self.max_bytes, max_events=self.max_events, cancel_event=cancel, evidence=evidence)
                else:
                    # Sniff bounded actual framing, including mislabelled responses.
                    prefix = source.readline(min(4096, self.max_bytes + 1))
                    while prefix and not prefix.strip() and not source.ended:
                        prefix += source.readline(min(4096, self.max_bytes - len(prefix) + 1))
                    source.buffer = prefix + source.buffer
                    sniff = prefix.lstrip()
                    if sniff.startswith((b'data:', b'event:', b':', b'id:', b'retry:')):
                        evidence['response_body_format'] = 'sse'
                        result = parse_sse(source, max_bytes=self.max_bytes, max_events=self.max_events, cancel_event=cancel, evidence=evidence)
                    else:
                        require(sniff.startswith(b'{'), 'UPSTREAM_TYPE', 'Upstream body is neither SSE nor Responses JSON')
                        evidence['response_body_format'] = 'json'
                        chunks = []
                        try:
                            while True:
                                chunk = source.readline(self.max_bytes + 1)
                                if not chunk:
                                    break
                                chunks.append(chunk)
                        except (GateError, OSError, http.client.HTTPException):
                            # A complete JSON object already buffered can still
                            # carry charge evidence when framing/decode later fails.
                            try:
                                partial = json.loads(b''.join(chunks) + source.buffer, object_pairs_hook=_unique_object)
                                candidate = partial.get('response', partial) if isinstance(partial, dict) else None
                                observed_response = _observed_usage(candidate)
                            except (ValueError, TypeError, GateError):
                                pass
                            raise
                        diagnostics.enter('decode')
                        result = _parse_terminal_json(b''.join(chunks))
                diagnostics.enter('terminal')
                require(isinstance(result, dict) and isinstance(result.get('output'), list) and
                        isinstance(result.get('id'), str) and bool(result['id']) and
                        isinstance(result.get('model'), str) and result.get('status') in TERMINALS.values(),
                        'SSE_SCHEMA', 'Terminal response schema is invalid')
                evidence['partial_output_observed'] = evidence.get('partial_output_observed', False) or bool(result['output'])
                diagnostics.finish()
            except GateError as error:
                error_code = error.code if error.code in SAFE_CODES else 'TRANSPORT_READ'
                observed_response = getattr(error,'observed_response',observed_response)
                partial_output_events=getattr(error,'partial_output_events',[])
                if error_code in {'SSE_SCHEMA', 'SSE_DUPLICATE', 'UPSTREAM_DECODE', 'UPSTREAM_JSON'}:
                    diagnostics.enter('decode')
                elif error_code == 'SSE_TRUNCATED':
                    diagnostics.enter('stream')
                diagnostics.finish(error)
                if deadline.expired:
                    evidence['failure_kind'] = 'timeout'
                evidence['partial_output_observed'] = evidence.get('partial_output_observed', False) or bool(partial_output_events)
            except Exception as error:
                error_code = 'TRANSPORT_READ'
                diagnostics.finish(error)
                if deadline.expired:
                    evidence['failure_kind'] = 'timeout'
            finally:
                done.set()
                self._abort(connection, io_socket)
                if upstream is not None:
                    try:
                        upstream.close()
                    except Exception:
                        pass
            if error_code:
                evidence['error_code'] = error_code
            # Both success and failure evidence is final before Future completion.
            self._emit(evidence)
            future.evidence = deepcopy(evidence)
            if (self.profile in {'optimization', 'workflow'} and error_code == 'UPSTREAM_HTTP'
                    and evidence.get('http_status') in {429, 500, 502, 503, 504}
                    and evidence.get('rejected_parameter') is None
                    and evidence.get('error_output_absent') is True
                    and not evidence.get('error_model_drift') and not evidence.get('error_effort_drift')
                    and not evidence.get('error_schema_invalid') and not evidence.get('error_nontransient')):
                result = {'status': 'failed', 'output': [], 'model': POLICY['model'],
                          'reasoning': {'effort': POLICY['effort']},
                          'usage': observed_response.get('usage') if observed_response else None,
                          'error': {'code': 'http_' + str(evidence['http_status']),
                                    'headers': evidence.get('retry_headers', {})}}
                error_code = None
            if error_code:
                failure=GateError(error_code, 'Host receipt unavailable or invalid; no automatic retry')
                failure.evidence = deepcopy(evidence)
                if partial_output_events:
                    failure.partial_output_events=partial_output_events
                if observed_response is not None:
                    failure.observed_response=observed_response
                future.set_exception(failure)
            else:
                future.set_result(result)

        # All actual HTTP dispatch has happened synchronously above this point.
        try:
            threading.Thread(target=read, daemon=True, name='codex-receipt').start()
        except Exception:
            done.set()
            self._abort(connection, io_socket)
            raise
        return future

    @staticmethod
    def _abort(connection, io_socket=None):
        try:
            active_socket = io_socket if io_socket is not None else connection.sock
            if active_socket is not None:
                active_socket.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass
