"""Host-only Codex HTTPS transport. Import/construction never contacts upstream.

start(payload, context) synchronously connects and sends exactly once, then returns
an uncancellable Future whose result is the unmodified terminal Responses object.
The gateway owns admission/accounting; this transport never refreshes auth/retries.
Injected connection/auth factories are trusted HOST test seams, not client options.
"""
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
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

from ledger import GateError, POLICY, canonical, digest, require, normalize_usage

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


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    """One address, no fallback/retry; resolver never receives auth/request bytes."""
    def __init__(self, host, port, *, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self.deadline = time.monotonic() + timeout

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return remaining

    def connect(self):
        resolved = Future()
        def resolve():
            try:
                resolved.set_result(socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM))
            except Exception:
                resolved.set_exception(OSError('Host resolution failed'))
        threading.Thread(target=resolve, daemon=True, name='codex-dns').start()
        addresses = resolved.result(timeout=self.remaining())
        if not addresses:
            raise OSError('Host resolution failed')
        family, kind, protocol, _, address = addresses[0]
        self.sock = socket.socket(family, kind, protocol)
        self.sock.settimeout(self.remaining())
        self.sock.connect(address)
        self.sock.settimeout(self.remaining())
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)

    def send(self, data):
        if self.sock is None:
            self.connect()
        self.sock.settimeout(self.remaining())
        super().send(data)


class ReceiptFuture(Future):
    """Cancellation cannot erase dispatch/accounting evidence; use cancel_event."""
    def cancel(self):
        return False


def parse_sse(source, *, max_bytes=8 * 1024 * 1024, max_events=100000,
              max_event_bytes=2 * 1024 * 1024, cancel_event=None):
    observed=[]; partial_output_events=[]
    try:
        return _parse_sse(source,max_bytes=max_bytes,max_events=max_events,
                          max_event_bytes=max_event_bytes,cancel_event=cancel_event,observed=observed,
                          partial_output_events=partial_output_events)
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
            event = json.loads(raw)
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
        if kind in {'response.output_item.added','response.output_item.done','response.output_text.delta',
                    'response.output_text.done','response.function_call_arguments.delta','response.function_call_arguments.done'}:
            # Bounded by the same aggregate wire limit; evidence only, never a receipt/tool dispatch.
            partial_output_events.append(event)
        if kind in TERMINALS:
            response = event.get('response')
            require(isinstance(response, dict) and response.get('status') == TERMINALS[kind],
                    'SSE_SCHEMA', 'Terminal event lacks a matching Responses object')
            terminal = response


class _BoundedBody:
    """Decode incrementally, bounding both wire and decoded bytes independently."""
    def __init__(self, source, encoding, limit, evidence):
        require(encoding in {'identity', 'gzip', 'deflate'},
                'UPSTREAM_TYPE', 'Unsupported upstream content encoding')
        self.source, self.limit, self.evidence = source, limit, evidence
        self.decoder = (zlib.decompressobj(31 if encoding == 'gzip' else 15)
                        if encoding != 'identity' else None)
        self.buffer = b''
        self.ended = False
        self.wire_bytes = self.decoded_bytes = 0
        self.wire_hash, self.decoded_hash = hashlib.sha256(), hashlib.sha256()

    def _fill(self):
        if self.ended:
            return
        chunk = getattr(self.source, 'read1', self.source.read)(
            min(65536, self.limit - self.wire_bytes + 1))
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
        value = json.loads(raw)
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
                 evidence_sink=None, dispatch_timeout=4.0, response_timeout=60.0,
                 max_output_tokens=2048, max_bytes=8 * 1024 * 1024, max_events=100000, profile='smoke'):
        require(type(dispatch_timeout) in (int, float) and 0 < dispatch_timeout <= 4.5 and
                type(response_timeout) in (int, float) and 0 < response_timeout <= 180,
                'TRANSPORT_CONFIG', 'Transport timeouts must be positive and bounded')
        require(profile in {'smoke', 'optimization'} and type(max_output_tokens) is int and
                max_output_tokens == (128000 if profile == 'optimization' else 2048),
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

    def start(self, payload, context):
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
                                    'before_sha256': digest(wire[key]) if key in wire else None, 'after': value})
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
        evidence = dict(request_id=request_id, upstream=UPSTREAM, semantic_payload_sha256=digest(payload),
                        wire_payload_sha256=digest(wire), adaptations=adaptations,
                        dispatch_point='synchronous_https_request_send_completed', retries=0, redirects=0)
        body = canonical(wire).encode()
        connection = None
        failed = False
        try:
            credential = self._auth_loader()
            credential.validate()
            tls = ssl.create_default_context()
            connection = self._factory('chatgpt.com', 443, timeout=self.dispatch_timeout, context=tls)
            connection.connect()
            require(not cancel.is_set(), 'STOPPED', 'Transport cancelled before request send')
            headers = {'Authorization': 'Bearer ' + credential.access, 'chatgpt-account-id': credential.account_id,
                       'OpenAI-Beta': 'responses=experimental', 'originator': 'pi', 'accept': 'text/event-stream',
                       'Accept-Encoding': 'identity',
                       'content-type': 'application/json', 'x-client-request-id': request_id, 'Connection': 'close'}
            connection.request('POST', '/backend-api/codex/responses', body=body, headers=headers)
        except GateError as error:
            code = error.code
            failed = True
        except Exception:
            code = 'TRANSPORT_SEND'
            failed = True
        if failed:
            if connection is not None:
                self._abort(connection)
            raise GateError(code, 'Host dispatch failed; outcome may be unknown; no automatic retry')
        # Headers/credential never enter Future evidence, result, repr or reports.
        del headers, credential
        future = ReceiptFuture()
        future.evidence = deepcopy(evidence)
        done = threading.Event()
        # HTTPConnection.getresponse may detach self.sock when Connection: close
        # is received. Keep the actual socket so cancellation can still shutdown
        # a blocked HTTPResponse.makefile reader after that detachment.
        io_socket = connection.sock

        def read():
            result = error_code = upstream = observed_response = None
            partial_output_events=[]
            try:
                io_socket.settimeout(self.response_timeout)
                upstream = connection.getresponse()
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
                    # headers or credentials. The rejected request still has an
                    # unknown usage result and is never automatically replayed.
                    error_body = upstream.read(8193)
                    evidence['error_body_sha256'] = hashlib.sha256(error_body).hexdigest()
                    evidence['error_body_bytes_observed'] = len(error_body)
                    evidence['error_body_truncated'] = len(error_body) > 8192
                    rejected = None
                    try:
                        value = json.loads(error_body)
                        if not evidence['error_body_truncated']:
                            rejected = rejected_parameter(value, set(payload) | set(wire))
                            # Retain only allowlisted structure and hashes. Arbitrary
                            # upstream text can echo credentials or private inputs.
                        candidate = value.get('response', value) if isinstance(value, dict) else {}
                        if isinstance(candidate, dict):
                            evidence['error_model_drift'] = candidate.get('model') not in (None, POLICY['model'])
                            reasoning = candidate.get('reasoning')
                            evidence['error_effort_drift'] = (isinstance(reasoning, dict) and
                                reasoning.get('effort') not in (None, POLICY['effort']))
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
                    if self.profile == 'optimization' and upstream.status in {429, 500, 502, 503, 504}:
                        safe_headers = {}
                        for key in ('retry-after', 'retry-after-ms'):
                            value = upstream.getheader(key)
                            if isinstance(value, str) and len(value) <= 128 and all(ord(c) < 127 and (c.isalnum() or c in ' ,:.-+') for c in value):
                                safe_headers[key] = value
                        evidence['retry_headers'] = safe_headers
                    evidence['rejected_parameter'] = rejected
                    evidence['unsupported_output_limit_reported'] = rejected == 'max_output_tokens'
                    raise GateError('UPSTREAM_HTTP', 'Upstream rejected HTTP request; no retry or redirect')
                source = _BoundedBody(upstream, encoding, self.max_bytes, evidence)
                if media_type == 'text/event-stream':
                    result = parse_sse(source, max_bytes=self.max_bytes, max_events=self.max_events, cancel_event=cancel)
                else:
                    # Sniff bounded actual framing, including mislabelled responses.
                    prefix = source.readline(min(4096, self.max_bytes + 1))
                    while prefix and not prefix.strip() and not source.ended:
                        prefix += source.readline(min(4096, self.max_bytes - len(prefix) + 1))
                    source.buffer = prefix + source.buffer
                    sniff = prefix.lstrip()
                    if sniff.startswith((b'data:', b'event:', b':', b'id:', b'retry:')):
                        evidence['response_body_format'] = 'sse'
                        result = parse_sse(source, max_bytes=self.max_bytes, max_events=self.max_events, cancel_event=cancel)
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
                                partial = json.loads(b''.join(chunks) + source.buffer)
                                candidate = partial.get('response', partial) if isinstance(partial, dict) else None
                                observed_response = _observed_usage(candidate)
                            except (ValueError, TypeError, GateError):
                                pass
                            raise
                        result = _parse_terminal_json(b''.join(chunks))
            except GateError as error:
                error_code = error.code
                observed_response = getattr(error,'observed_response',observed_response)
                partial_output_events=getattr(error,'partial_output_events',[])
            except Exception:
                error_code = 'TRANSPORT_READ'
            finally:
                done.set()
                self._abort(connection, io_socket)
                if upstream is not None:
                    try:
                        upstream.close()
                    except Exception:
                        pass
            if self._sink is not None:
                # This callback runs outside the gateway lifecycle lock. A sink
                # failure never suppresses a receipt or causes another dispatch.
                try:
                    self._sink(deepcopy(evidence))
                except Exception:
                    future.evidence['evidence_sink_failed'] = True
            if (self.profile == 'optimization' and error_code == 'UPSTREAM_HTTP'
                    and evidence.get('http_status') in {429, 500, 502, 503, 504}
                    and evidence.get('rejected_parameter') is None
                    and not evidence.get('error_model_drift') and not evidence.get('error_effort_drift')):
                result = {'status': 'failed', 'output': [], 'model': POLICY['model'],
                          'reasoning': {'effort': POLICY['effort']},
                          'usage': observed_response.get('usage') if observed_response else None,
                          'error': {'code': 'http_' + str(evidence['http_status']),
                                    'headers': evidence.get('retry_headers', {})}}
                error_code = None
            if error_code:
                failure=GateError(error_code, 'Host receipt unavailable or invalid; no automatic retry')
                if partial_output_events:
                    failure.partial_output_events=partial_output_events
                if observed_response is not None:
                    failure.observed_response=observed_response
                future.set_exception(failure)
            else:
                future.set_result(result)

        def stop_io():
            while not done.wait(0.02):
                if cancel.is_set():
                    self._abort(connection, io_socket)
                    return
        # All actual HTTP dispatch has happened synchronously above this point.
        threading.Thread(target=read, daemon=True, name='codex-receipt').start()
        threading.Thread(target=stop_io, daemon=True, name='codex-cancel').start()
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
