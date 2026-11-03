"""One host-side Responses/tool boundary shared by authentic harnesses.

The default injected transport is a fixture; this module has no upstream URL or
credential discovery. HTTPS transport must be constructed separately after an
operator-approved manifest passes qualification. Never forwards arbitrary paths,
auth headers, provider IDs, hidden previous-response contexts or tool servers.
"""
from concurrent.futures import Future
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import secrets
import threading
from urllib.parse import urlsplit

from ledger import GateError, POLICY, canonical, digest, require, native_identity

# Tool contracts are shared by MCP, Pi and the workflow worker. Names use API-
# compatible underscores; internal dotted names have exactly the same semantics.
def schema(properties, required=()):
    return dict(type='object', properties=properties, required=list(required), additionalProperties=False)


STRING = {'type': 'string'}
INTEGER = {'type': 'integer', 'minimum': 0}
WINDOW = {'offset': INTEGER, 'length': {'type': 'integer', 'minimum': 1, 'maximum': 65536}}
CANDIDATE = schema({'pairs': {'type': 'array', 'minItems': 15, 'maxItems': 15,
                            'items': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                                      'items': {'type': 'integer', 'minimum': 0}}}}, ['pairs'])
TOOL_SCHEMAS = [
    ('materials_list', 'List the frozen common materials by ID and hash.', schema({})),
    ('materials_read', 'Read a byte window of a frozen material.', schema({'id': STRING, **WINDOW}, ['id'])),
    ('workspace_read', 'Read a byte window of a file in this run only.', schema({'path': STRING, **WINDOW}, ['path'])),
    ('workspace_write', 'Write a bounded UTF-8 file in this run only.', schema({'path': STRING, 'text': STRING}, ['path', 'text'])),
    ('compute', 'Run Python in the fixed offline compute sandbox.', schema({'code': STRING}, ['code'])),
    ('check', 'Compute the frozen exact integer certificate score.', schema({'candidate': CANDIDATE}, ['candidate'])),
    ('submit', 'Submit an immutable certificate for independent exact checking.', schema({'candidate': CANDIDATE}, ['candidate'])),
]
TOOL_NAMES = {name for name, _, _ in TOOL_SCHEMAS}
INTERNAL_NAMES = {'materials_list': 'materials.list', 'materials_read': 'materials.read',
                  'workspace_read': 'workspace.read', 'workspace_write': 'workspace.write',
                  **{name: name for name in ('compute', 'check', 'submit')}}


def responses_tools():
    return [dict(type='function', name=name, description=description, parameters=parameters)
            for name, description, parameters in TOOL_SCHEMAS]


def mcp_tools():
    return [dict(name=name, description=description, inputSchema=parameters)
            for name, description, parameters in TOOL_SCHEMAS]


def as_sse(response):
    """Lossless fixture/transport output framing; native clients keep their loops."""
    result = []
    def event(kind, **data):
        result.append('event: ' + kind + '\ndata: ' + canonical(dict(type=kind, **data)) + '\n\n')
    event('response.created', response={**response, 'output': [], 'usage': None, 'status': 'in_progress'})
    for index, item in enumerate(response.get('output', [])):
        event('response.output_item.added', output_index=index, item=item)
        if item.get('type') == 'message':
            for ci, content in enumerate(item.get('content', [])):
                if content.get('type') == 'output_text':
                    event('response.content_part.added', item_id=item['id'], output_index=index, content_index=ci, part=content)
                    event('response.output_text.delta', item_id=item['id'], output_index=index, content_index=ci, delta=content['text'])
                    event('response.output_text.done', item_id=item['id'], output_index=index, content_index=ci, text=content['text'])
        elif item.get('type') == 'function_call':
            event('response.function_call_arguments.done', item_id=item['id'], output_index=index, arguments=item['arguments'])
        event('response.output_item.done', output_index=index, item=item)
    status = response.get('status')
    require(status in {'completed', 'incomplete', 'failed', 'cancelled'}, 'RESPONSE_STATUS', 'Unknown terminal response status')
    event('response.' + status, response=response)
    return ''.join(result).encode()


def codex_metadata(payload, header=None, *, required=False):
    """Canonical Codex body metadata and header projections must agree on lineage."""
    client = payload.get('client_metadata', {})
    require(isinstance(client, dict), 'NATIVE_METADATA', 'Invalid client metadata object')
    body = client.get('x-codex-turn-metadata')
    def parse(value):
        if isinstance(value, str):
            require(len(value.encode()) <= 65536, 'NATIVE_METADATA', 'Native metadata exceeds its bound')
            try:
                value = json.loads(value)
            except (TypeError, ValueError) as error:
                raise GateError('NATIVE_METADATA', 'Malformed native metadata JSON') from error
        return native_identity(value)
    canonical_body = parse(body) if body is not None else None
    projection = parse(header) if header is not None else None
    require(canonical_body is None or projection is None or canonical_body == projection,
            'NATIVE_LINEAGE_DRIFT', 'Canonical native metadata disagrees with its HTTP projection')
    metadata = canonical_body if canonical_body is not None else projection
    require(not required or metadata is not None, 'NATIVE_METADATA', 'Native caller must supply thread/turn metadata')
    return metadata


def tool_parent(ledger, run_id, metadata, explicit):
    resolved = ledger.resolve_native_request(run_id, metadata) if metadata else explicit
    require(explicit is None or explicit == resolved, 'NATIVE_LINEAGE_DRIFT', 'Tool parent disagrees with native turn')
    if resolved:
        with ledger.connection() as db:
            ledger._run(db, run_id, active=False)
            row = db.execute('SELECT state FROM requests WHERE run=? AND id=?', (run_id, resolved)).fetchone()
            require(row is not None and row['state'] == 'finished', 'NATIVE_PARENT_UNKNOWN', 'Tool parent lacks a completed same-run request')
    return resolved


class FixtureTransport:
    """Deterministic response queue with no network or credential access."""
    def __init__(self, responses):
        self.responses = deepcopy(responses)
        self.requests = []
        self.lock = threading.Lock()

    def start(self, payload, context):
        with self.lock:
            require(not context['cancel_event'].is_set(), 'STOPPED', 'Transport cancelled before dispatch')
            self.requests.append({'payload': deepcopy(payload), 'request_id': context['request_id'], 'role': context['role']})
            require(bool(self.responses), 'FIXTURE_EXHAUSTED', 'No fixture response remains')
            pending = Future()
            pending.set_result(self.responses.pop(0))
            return pending


class Gateway:
    """Local capability endpoint; transport.start(payload, context) -> Future.

    start must synchronously initiate dispatch and promptly return a receipt Future;
    it must NOT queue a later dispatch or wait for a provider response. It runs under
    the shutdown lock. The future may resolve after cancellation and is still
    accounted. No retries. A live transport must separately qualify this contract,
    cancellation, provider framing and OS coverage before it can be installed.
    """
    def __init__(self, ledger, *, transport, bindings, tools=None, allowed_tools=None, intrinsic_tools=None, inject_output_cap=False, drain_timeout=5.0):
        require(callable(getattr(transport, 'start', None)), 'TRANSPORT_CONTRACT', 'Transport requires an atomic start returning a receipt Future')
        self.ledger, self.transport = ledger, transport
        self.is_offline = type(transport) is FixtureTransport
        self.bindings = deepcopy(bindings)
        self.tools = tools or {}
        self.allowed_tools = allowed_tools or {}
        self.intrinsic_tools = intrinsic_tools or {}  # (run_id, role) -> exact native state-tool names
        self.inject_output_cap = inject_output_cap
        self.cancel_event = threading.Event()
        require(isinstance(drain_timeout, (int, float)) and 0 <= drain_timeout <= 30,
                'INVALID_SPEC', 'Gateway drain timeout must be bounded')
        self.drain_timeout = drain_timeout
        self.lifecycle = threading.Condition()
        self.close_lock = threading.Lock()
        self.active_handlers = 0
        self.closing = self.closed = False
        self.thread = None
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass  # Never log capability tokens or request bodies to stderr.

            def reply(self, status, body, *, request_id=None, sse=False):
                raw = body if isinstance(body, bytes) else canonical(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if sse else 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Connection', 'close')
                if request_id:
                    self.send_header('X-Four-Request-Id', request_id)
                self.end_headers()
                self.wfile.write(raw)
                self.close_connection = True

            def do_GET(self):
                self.reply(405, {'error': {'code': 'ENDPOINT', 'message': 'Only registered POST endpoints are available', 'dispatched': False}})

            def do_POST(self):
                request_id = None
                dispatched = False
                binding = payload = response = None
                entered = False
                try:
                    with gateway.lifecycle:
                        require(not gateway.closing, 'STOPPED', 'Gateway has stopped')
                        gateway.active_handlers += 1
                        entered = True
                    token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                    binding = next((value for key, value in gateway.bindings.items() if hmac.compare_digest(key, token)), None)
                    require(binding is not None, 'UNAUTHORIZED', 'Run capability missing')
                    arm, role, run_id = binding['arm'], binding['role'], binding['run_id']
                    base = f'/{arm}/{role}'
                    path = urlsplit(self.path)
                    require(not path.query and path.path in {base + '/v1/responses', base + '/responses', base + '/tools', base + '/manifest', base + '/intrinsic'},
                            'ENDPOINT', 'Unregistered role or endpoint')
                    length = self.headers.get('Content-Length')
                    require(length is not None and length.isdecimal() and 0 < int(length) <= 2 * 1024 * 1024,
                            'REQUEST_SIZE', 'Bounded Content-Length required')
                    self.connection.settimeout(15)
                    raw = self.rfile.read(int(length))
                    require(len(raw) == int(length), 'TRUNCATED', 'Incomplete request body')
                    payload = json.loads(raw)
                    require(type(payload) is dict, 'SCHEMA', 'Expected request object')
                    require(not gateway.cancel_event.is_set(), 'STOPPED', 'Gateway has stopped')
                    if path.path.endswith('/manifest'):
                        require(payload == {}, 'SCHEMA', 'Manifest request must be empty')
                        with gateway.ledger.connection() as db:
                            frozen = json.loads(gateway.ledger._run(db, run_id)['spec'])
                        self.reply(200, {'offline': gateway.is_offline, 'model_policy': POLICY,
                                         'tools': responses_tools(), 'context_window': frozen['budget']['context_tokens'],
                                         'budget': frozen['budget']})
                        return
                    metadata = codex_metadata(payload, self.headers.get('X-Codex-Turn-Metadata'),
                                              required=binding.get('require_native_metadata', False))
                    explicit_parent = self.headers.get('X-Four-Parent-Request-Id')
                    native_tool_parent = (tool_parent(gateway.ledger, run_id, metadata, explicit_parent)
                                          if path.path.endswith(('/intrinsic', '/tools')) else None)
                    if path.path.endswith('/intrinsic'):
                        name, phase, call_id = payload.get('name'), payload.get('phase'), payload.get('call_id')
                        require(name in gateway.intrinsic_tools.get((run_id, role), set()),
                                'TOOL_NOT_ALLOWED', 'Native state tool is not frozen for this role')
                        require(isinstance(call_id, str) and 0 < len(call_id) <= 200 and phase in {'start', 'finish'},
                                'TOOL_SCHEMA', 'Invalid intrinsic tool identity/phase')
                        hash_key = 'args_sha256' if phase == 'start' else 'result_sha256'
                        receipt_hash = payload.get(hash_key)
                        require(isinstance(receipt_hash, str) and len(receipt_hash) == 64 and
                                all(c in '0123456789abcdef' for c in receipt_hash), 'TOOL_SCHEMA', 'Receipt SHA-256 required')
                        event = {**payload, 'type': 'tool_started' if phase == 'start' else 'tool_finished',
                                 'request_id': 'intrinsic-' + digest([run_id, role, name, call_id]),
                                 'native_role': role, 'name': name, 'parent_request_id': native_tool_parent,
                                  'native_metadata': metadata}
                        gateway.ledger.tool_event(run_id, event)
                        self.reply(200, {'ok': True, 'tool_id': event['request_id'], 'parent_request_id': native_tool_parent})
                        return
                    if path.path.endswith('/tools'):
                        require(set(payload) == {'name', 'arguments'} and payload['name'] in INTERNAL_NAMES,
                                'TOOL_NOT_ALLOWED', 'Only common tool names are permitted')
                        require(run_id in gateway.tools, 'TOOLS_UNAVAILABLE', 'Common tools not bound')
                        result = gateway.tools[run_id].call(INTERNAL_NAMES[payload['name']], payload['arguments'],
                                                            cancel_event=gateway.cancel_event)
                        if native_tool_parent:
                            result['metrics']['model_request_id'] = native_tool_parent
                            gateway.ledger.record(run_id, 'native_tool_context', tool_request_id=result['metrics']['request_id'],
                                                  parent_request_id=native_tool_parent, native_metadata=metadata)
                        self.reply(200, result)
                        return
                    require('provider' not in payload or payload['provider'] == POLICY['provider'], 'MODEL_DRIFT', 'Provider drift')
                    payload.pop('provider', None)
                    declared = payload.get('tools', [])
                    allowed = gateway.allowed_tools.get(run_id, TOOL_NAMES)
                    require(type(declared) is list, 'TOOL_NOT_ALLOWED', 'Invalid tools declaration')
                    for tool in declared:
                        require(type(tool) is dict and tool.get('type') == 'function' and tool.get('name') in allowed,
                                'TOOL_NOT_ALLOWED', 'Unmetered native or network tool in request')
                    if gateway.inject_output_cap and 'max_output_tokens' not in payload:
                        with gateway.ledger.connection() as db:
                            run = gateway.ledger._run(db, run_id)
                            cap = json.loads(run['spec'])['budget']['max_output_tokens']
                        payload['max_output_tokens'] = cap
                        gateway.ledger.record(run_id, 'transport_adaptation', field='max_output_tokens', value=cap,
                                              reason='frozen_explicit_output_cap')
                    dispatch_role = 'compaction' if metadata and metadata.get('request_kind') == 'compaction' else role
                    request_id = gateway.ledger.reserve_request(run_id, dispatch_role, payload,
                        parent_request_id=explicit_parent, thread_id=self.headers.get('X-Four-Thread-Id'), native_metadata=metadata)
                    gateway.ledger.start_request(request_id)
                    # Actual start and shutdown share a lock, not just admission.
                    # Waiting for the response occurs outside it so shutdown can
                    # cancel active I/O without discarding a late receipt.
                    try:
                        with gateway.lifecycle:
                            require(not gateway.closing and not gateway.cancel_event.is_set(), 'STOPPED', 'Gateway stopped before dispatch')
                            dispatched = True
                            pending = gateway.transport.start(payload, dict(request_id=request_id, run_id=run_id, role=dispatch_role,
                                                                           cancel_event=gateway.cancel_event))
                            require(isinstance(pending, Future), 'TRANSPORT_CONTRACT', 'Transport start must return a receipt Future')
                        response = pending.result()
                    except GateError:
                        raise
                    except Exception as error:
                        raise GateError('TRANSPORT_ERROR', 'Transport lost its receipt; no automatic retry') from error
                    require(isinstance(response, dict), 'RESPONSE_SCHEMA', 'Transport returned no Responses object')
                    usage = gateway.ledger.finish_request(request_id, response, late_receipt=True,
                        terminal=response.get('status') in {'completed', 'incomplete', 'failed', 'cancelled'})
                    require(usage['complete'], 'USAGE_UNKNOWN', 'Provider usage missing or inconsistent')
                    require(response.get('model') == POLICY['model'], 'MODEL_DRIFT', 'Response model differs from frozen model')
                    if response.get('status') in {'failed', 'cancelled'}:
                        gateway.ledger.stop(run_id, 'provider_' + response['status'])
                    else:
                        require(gateway.ledger.snapshot(run_id)['terminal'] is None, 'STOPPED', 'Request exceeded the frozen budget or stopped')
                    with gateway.lifecycle:
                        require(not gateway.closing and not gateway.cancel_event.is_set(), 'STOPPED', 'Receipt retained after cancellation; no result delivery')
                        sse = payload.get('stream') is True
                        self.reply(200, as_sse(response) if sse else response, request_id=request_id, sse=sse)
                except (BrokenPipeError, ConnectionResetError):
                    # A complete provider receipt still counts even if the native
                    # client disappears; stop the run rather than replaying it.
                    if request_id:
                        gateway.ledger.stop(binding['run_id'], 'client_disconnected')
                except Exception as error:
                    if request_id:
                        gateway.ledger.unknown_request(request_id, getattr(error, 'code', 'transport_error'))
                    code = getattr(error, 'code', 'INVALID_REQUEST' if not dispatched else 'TRANSPORT_ERROR')
                    if not dispatched and binding:
                        names = [tool.get('name', tool.get('type')) for tool in payload.get('tools', []) if isinstance(tool, dict)] if isinstance(locals().get('payload'), dict) else []
                        gateway.ledger.record(binding['run_id'], 'request_rejected', role=binding['role'], code=code, tool_names=names)
                    try:
                        self.reply(409 if isinstance(error, GateError) else 502,
                                   {'error': {'code': code, 'message': str(error), 'dispatched': dispatched,
                                              'request_id': request_id,
                                               'usage': response.get('usage') if isinstance(response, dict) else None}}, request_id=request_id)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                finally:
                    if entered:
                        with gateway.lifecycle:
                            gateway.active_handlers -= 1
                            gateway.lifecycle.notify_all()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def start(self):
        require(self.thread is None, 'ALREADY_STARTED', 'Gateway already started')
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()
        return self

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            with self.lifecycle:
                self.closing = True
                self.cancel_event.set()
            if self.thread:
                self.server.shutdown()
                self.thread.join(timeout=5)
            self.server.server_close()
            with self.lifecycle:
                self.lifecycle.wait_for(lambda: self.active_handlers == 0, timeout=self.drain_timeout)
            # Unfinished work remains unknown with its reservation. A later
            # transport receipt can update costs, never reopen dispatch.
            for run_id in {binding['run_id'] for binding in self.bindings.values()}:
                self.ledger.stop(run_id, 'gateway_closed')
            self.closed = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


def capability():
    return secrets.token_urlsafe(32)
