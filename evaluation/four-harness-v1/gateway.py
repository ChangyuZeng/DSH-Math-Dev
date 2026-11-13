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

from ledger import (GateError, POLICY, RESOURCE_POLICY, canonical, digest, require, native_identity,
                    OPTIMIZATION_BUDGET, retryable_response, retryable_transport_failure, retry_delay, normalize_usage,
                    usage_schema_valid)
from cold_start import INFORMATION_POLICY, canonical_inputs

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
CANDIDATE_FILE = schema({'path': STRING, 'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                         'size': INTEGER}, ['path', 'sha256', 'size'])


def certificate_schema(candidate):
    return {**schema({'candidate': candidate, 'candidate_file': CANDIDATE_FILE}),
            'oneOf': [{'required': ['candidate']}, {'required': ['candidate_file']}]}


TOOL_SCHEMAS = [
    ('materials_list', 'List the frozen common materials by ID and hash.', schema({})),
    ('materials_read', 'Read a byte window of a frozen material.', schema({'id': STRING, **WINDOW}, ['id'])),
    ('workspace_read', 'Read a byte window of a file in this run only.', schema({'path': STRING, **WINDOW}, ['path'])),
    ('workspace_write', 'Write a bounded UTF-8 file in this run only.', schema({'path': STRING, 'text': STRING}, ['path', 'text'])),
    ('compute', 'Run Python in the fixed offline compute sandbox.', schema({'code': STRING}, ['code'])),
    ('check', 'Compute the frozen exact integer certificate score.', certificate_schema(CANDIDATE)),
    ('submit', 'Submit an immutable certificate for independent exact checking.', certificate_schema(CANDIDATE)),
]
TOOL_NAMES = {name for name, _, _ in TOOL_SCHEMAS}
INTERNAL_NAMES = {'materials_list': 'materials.list', 'materials_read': 'materials.read',
                  'workspace_read': 'workspace.read', 'workspace_write': 'workspace.write',
                  **{name: name for name in ('compute', 'check', 'submit')}}


def target_tool(name):
    return isinstance(name, str) and name.rsplit('__', 1)[-1] in {'check', 'submit'}


def responses_tools(scope=None):
    return [dict(type='function', name=name, description=description, parameters=parameters)
            for name, description, parameters in TOOL_SCHEMAS if scope != 'smoke' or not target_tool(name)]


def mcp_tools(scope=None):
    return [dict(name=tool['name'], description=tool['description'], inputSchema=tool['parameters'])
            for tool in responses_tools(scope)]


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

    start must synchronously dispatch within its connection/send deadlines, then
    return a receipt Future without queuing dispatch or waiting for a response. It runs under
    the shutdown lock. The future may resolve after cancellation and is still
    accounted. Only this gateway may retry (at most two retries per logical call).
    A live transport must separately qualify this contract,
    cancellation, provider framing and OS coverage before it can be installed.
    """
    def __init__(self, ledger, *, transport, bindings, tools=None, allowed_tools=None, intrinsic_tools=None, inject_output_cap=False, drain_timeout=5.0, execution=None, state_guards=None, request_adapters=None, audit_sink=None, frozen_materials=None, candidate_schema=None):
        require(callable(getattr(transport, 'start', None)), 'TRANSPORT_CONTRACT', 'Transport requires an atomic start returning a receipt Future')
        self.ledger, self.transport = ledger, transport
        self.is_offline = type(transport) is FixtureTransport
        self.bindings = deepcopy(bindings)
        self.execution = deepcopy(execution)
        self.state_guards = state_guards or {}
        self.request_adapters = request_adapters or {}
        self.audit_sink = audit_sink
        self.frozen_materials = deepcopy(frozen_materials or {})
        self.candidate_schema = deepcopy(candidate_schema)
        self.is_live = type(transport).__module__ == 'transport' and type(transport).__name__ == 'HostTransport'
        if self.is_live:
            from transport import HostTransport
            require(type(transport) is HostTransport and self.execution is not None,
                    'LIVE_NOT_AUTHORIZED', 'Live transport requires a host-authorized smoke execution')
        if self.execution is not None:
            require(self.is_live, 'EXECUTION_MODE', 'Smoke execution requires the qualified live transport')
            for binding in self.bindings.values():
                with ledger.connection() as db:
                    run = ledger._run(db, binding['run_id'])
                    campaign = db.execute('SELECT spec FROM campaign WHERE id=?', (run['campaign'],)).fetchone()
                    caps = json.loads(campaign['spec'])
                    budget = json.loads(run['spec'])['budget']
                expected = dict(mode='authorized_smoke', arm=binding['arm'], run_id=binding['run_id'],
                                campaign_id=run['campaign'], model_requests=2, campaign_max_requests=8)
                smoke = (self.execution == expected and caps['scope'] == 'smoke' and
                         caps['max_requests'] <= 8 and caps['max_tokens'] <= 524288 and
                         budget == __import__('ledger').SMOKE_BUDGET)
                optimization = (self.execution == dict(mode='authorized_optimization', arm='M',
                    run_id=binding['run_id'], campaign_id=run['campaign'], model_requests=120,
                    campaign_max_requests=120) and binding['arm'] == 'M' and caps['scope'] == 'optimization'
                    and caps['max_requests'] == 120 and caps['max_tokens'] is None and budget == OPTIMIZATION_BUDGET)
                require(smoke or optimization, 'EXECUTION_MODE', 'Exact authorized execution profile required')
                require(binding['run_id'] in self.state_guards, 'STATE_GUARD', 'Live native state watchdog required')
        self.tools = tools or {}
        self.allowed_tools = allowed_tools or {}
        self.intrinsic_tools = intrinsic_tools or {}  # (run_id, role) -> exact native state-tool names
        self.cold_scopes = {}
        with ledger.connection() as db:
            for run_id in {binding['run_id'] for binding in self.bindings.values()}:
                run = ledger._run(db, run_id)
                campaign = db.execute('SELECT spec FROM campaign WHERE id=?', (run['campaign'],)).fetchone()
                scope = json.loads(campaign['spec'])['scope']
                if scope in {'smoke', 'formal', 'optimization'}:
                    self.cold_scopes[run_id] = scope
        self.cold_accounting = {}
        for run_id in self.cold_scopes:
            from tools import CommonTools
            host = self.tools.get(run_id)
            require(type(host) is CommonTools and host.run_id == run_id,
                    'COLD_RUN_BINDING', 'Cold tools must belong to this exact run')
            require(host.pristine_for_cold_start(), 'COLD_STATE', 'Cold tools must have empty state and no prior tool attempts')
            with ledger.connection() as db:
                run = ledger._run(db, run_id)
                budget = json.loads(run['spec'])['budget']
            require((host.limits.timeout_seconds is None if budget['tool_timeout_seconds'] is None else
                     host.limits.timeout_seconds is not None and host.limits.timeout_seconds <= budget['tool_timeout_seconds']) and
                    host.limits.artifact_bytes <= budget['artifact_bytes'],
                    'TOOL_BUDGET', 'Common tool timeout/artifact limits exceed the run budget')
            def account(event, run_id=run_id):
                require(event.get('run_id') == run_id, 'COLD_RUN_BINDING', 'Tool receipt belongs to another run')
                ledger.tool_event(run_id, event)
            # The gateway owns the mandatory callback, not a caller-supplied
            # optional lambda which could silently charge a different ledger/run.
            host.accounting = account
            self.cold_accounting[run_id] = account
            self.check_cold_materials(run_id)
        self.inject_output_cap = inject_output_cap
        self.cancel_event = threading.Event()
        require(isinstance(drain_timeout, (int, float)) and 0 <= drain_timeout <= 30,
                'INVALID_SPEC', 'Gateway drain timeout must be bounded')
        self.drain_timeout = drain_timeout
        self.lifecycle = threading.Condition()
        self.close_lock = threading.Lock()
        self.active_handlers = 0
        # A cost receipt is not tool authority: validation/framing may still fail.
        self.validated_requests = set()
        self.closing = self.closed = False
        self.thread = None
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass  # Never log capability tokens or request bodies to stderr.

            def accounting_projection(self):
                ids = self.request_ids
                requests = []
                with gateway.ledger.connection() as db:
                    for index, rid in enumerate(ids):
                        row = db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
                        usage = json.loads(row['usage']) if row['usage'] else normalize_usage(None, terminal=False)
                        requests.append(dict(request_id=rid, parent_request_id=row['parent'],
                            logical_request_id=ids[0], retry_of=ids[index - 1] if index else None, attempt=index + 1,
                            status='finished' if row['state'] == 'finished' else 'unknown', usage=usage,
                            raw_usage=json.loads(row['raw_usage']) if row['raw_usage'] else None,
                            transport=self.attempt_evidence.get(rid)))
                return dict(format='four-harness-accounting-v2', logical_request_id=ids[0] if ids else None,
                            request_ids=list(ids), requests=requests)

            def reply(self, status, body, *, request_id=None, sse=False):
                raw = body if isinstance(body, bytes) else canonical(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if sse else 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Connection', 'close')
                if request_id:
                    self.send_header('X-Four-Request-Id', request_id)
                if getattr(self, 'request_ids', None):
                    self.send_header('X-Four-Request-Ids', canonical(self.request_ids))
                    self.send_header('X-Four-Logical-Request-Id', self.request_ids[0])
                self.end_headers()
                self.wfile.write(raw)
                self.close_connection = True

            def do_GET(self):
                self.reply(405, {'error': {'code': 'ENDPOINT', 'message': 'Only registered POST endpoints are available', 'dispatched': False}})

            def do_POST(self):
                self.request_ids, self.attempt_evidence = [], {}
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
                    require(not path.query and path.path in {base + '/v1/responses', base + '/responses', base + '/tools', base + '/prepare-candidate', base + '/manifest', base + '/intrinsic'},
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
                    gateway.check_cold_materials(run_id)
                    guard = gateway.state_guards.get(run_id)
                    if guard is not None:
                        guard.check()
                    if path.path.endswith('/manifest'):
                        require(payload == {}, 'SCHEMA', 'Manifest request must be empty')
                        with gateway.ledger.connection() as db:
                            frozen = json.loads(gateway.ledger._run(db, run_id)['spec'])
                        self.reply(200, {'offline': gateway.is_offline, 'model_policy': POLICY,
                                         'tools': gateway.manifest_tools(run_id), 'scope': gateway.cold_scopes.get(run_id, 'offline'),
                                         'context_window': frozen['budget']['context_tokens'],
                                         'budget': frozen['budget'], 'resource_policy': RESOURCE_POLICY,
                                         'information_policy': INFORMATION_POLICY,
                                         'canonical_cold_input_checked': run_id in gateway.cold_scopes,
                                          **({'execution': gateway.execution} if gateway.execution is not None else {})})
                        return
                    metadata = codex_metadata(payload, self.headers.get('X-Codex-Turn-Metadata'),
                                              required=binding.get('require_native_metadata', False))
                    explicit_parent = self.headers.get('X-Four-Parent-Request-Id')
                    native_tool_parent = (tool_parent(gateway.ledger, run_id, metadata, explicit_parent)
                                          if path.path.endswith(('/intrinsic', '/tools', '/prepare-candidate')) else None)
                    if path.path.endswith(('/intrinsic', '/tools', '/prepare-candidate')) and (native_tool_parent or gateway.cold_scopes.get(run_id) == 'optimization'):
                        with gateway.lifecycle:
                            require(native_tool_parent in gateway.validated_requests, 'NATIVE_PARENT_UNKNOWN',
                                    'Tool parent lacks a fully validated completed response')
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
                    if path.path.endswith(('/tools', '/prepare-candidate')):
                        preparing = path.path.endswith('/prepare-candidate')
                        expected_keys = {'name', 'arguments'}
                        require((set(payload) == expected_keys or not preparing and set(payload) == expected_keys | {'prepared_candidate'}) and payload['name'] in INTERNAL_NAMES,
                                'TOOL_NOT_ALLOWED', 'Only common tool names are permitted')
                        require(gateway.cold_scopes.get(run_id) != 'smoke' or not target_tool(payload['name']),
                                'TOOL_NOT_ALLOWED', 'Target checker/submission is unavailable in non-target smoke')
                        require(run_id in gateway.tools, 'TOOLS_UNAVAILABLE', 'Common tools not bound')
                        native_call_id=self.headers.get('X-Four-Native-Call-Id')
                        require(native_call_id is None or (0<len(native_call_id)<=200 and all(32<ord(c)<127 for c in native_call_id)),
                                'NATIVE_TOOL_ID','Invalid native tool call identity')
                        audit_id = 'native-tool-' + digest([run_id, role, native_tool_parent,
                                                            native_call_id or secrets.token_hex(16)])
                        if gateway.audit_sink is not None:
                            gateway.audit_sink('candidate_prepare_request' if preparing else 'tool_request', audit_id, {
                                'run_id': run_id, 'role': role, 'parent_request_id': native_tool_parent,
                                'native_call_id': native_call_id, **deepcopy(payload)})
                        candidate_binding = {'run_id': run_id, 'role': role, 'parent_request_id': native_tool_parent,
                                             'call_id': native_call_id}
                        if preparing or 'prepared_candidate' in payload:
                            require(payload['name'] in {'check', 'submit'} and native_call_id is not None and
                                    native_tool_parent in gateway.validated_requests,
                                    'CANDIDATE_PREPARE_BINDING', 'Host preparation needs a validated parent and native call identity')
                        if preparing:
                            from tools import ToolError
                            try:
                                result = gateway.tools[run_id].prepare_candidate(payload['name'], payload['arguments'],
                                    binding=candidate_binding, cancel_event=gateway.cancel_event)
                            except ToolError as error:
                                result = error.receipt
                            if gateway.audit_sink is not None:
                                gateway.audit_sink('candidate_prepare_response', audit_id, deepcopy(result))
                            self.reply(200, result)
                            return
                        options = {'cancel_event': gateway.cancel_event}
                        if 'prepared_candidate' in payload:
                            options.update(prepared_candidate=payload['prepared_candidate'], candidate_binding=candidate_binding)
                        result = gateway.tools[run_id].call(INTERNAL_NAMES[payload['name']], payload['arguments'], **options)
                        if native_tool_parent:
                            result['metrics']['model_request_id'] = native_tool_parent
                            gateway.ledger.record(run_id, 'native_tool_context', tool_request_id=result['metrics']['request_id'],
                                                  parent_request_id=native_tool_parent, native_metadata=metadata,
                                                  native_call_id=native_call_id, tool_name=payload['name'],
                                                  arguments_sha256=digest(payload['arguments']))
                        if gateway.cold_scopes.get(run_id)=='smoke':
                            # Every arm receives the same compact host receipt.
                            # Mathematical results stay byte-for-byte in result;
                            # full timing/isolation observations remain host-side.
                            full=deepcopy(result)
                            result={**result,'metrics':{key:value for key,value in result.get('metrics',{}).items()
                                     if key in {'request_id','model_request_id','result_sha256'}}}
                            if payload['name']=='compute' and type(result.get('result')) is dict:
                                result['result']={key:value for key,value in result['result'].items() if key in
                                    {'status','returncode','stdout','stderr','output_bytes','output_truncated'}}
                            sent_result_sha=digest({key:value for key,value in result.items() if key!='metrics'})
                            result['metrics']['result_sha256']=sent_result_sha
                            if gateway.audit_sink is not None:
                                gateway.audit_sink('tool_reply_full',full['metrics']['request_id'],full)
                            gateway.ledger.record(run_id,'common_reply_projection',tool_request_id=full['metrics']['request_id'],
                                full_sha256=digest(full),sent_sha256=digest(result),
                                full_result_sha256=full['metrics']['result_sha256'],sent_result_sha256=sent_result_sha,
                                omitted_result_fields=sorted(set(full.get('result',{}))-set(result.get('result',{})))
                                    if isinstance(full.get('result'),dict) else [],
                                omitted_metrics=sorted(set(full['metrics'])-set(result['metrics'])))
                        if gateway.audit_sink is not None:
                            gateway.audit_sink('tool_response', audit_id, deepcopy(result))
                        self.reply(200, result)
                        return
                    require('provider' not in payload or payload['provider'] == POLICY['provider'], 'MODEL_DRIFT', 'Provider drift')
                    payload.pop('provider', None)
                    request_input = payload.get('input', [])
                    adapter = gateway.request_adapters.get((run_id, role))
                    declared = payload.get('tools', [])
                    allowed = set(gateway.allowed_tools.get(run_id, TOOL_NAMES))
                    allowed.update(gateway.intrinsic_tools.get((run_id, role), set()))
                    if gateway.cold_scopes.get(run_id) == 'smoke':
                        allowed = {name for name in allowed if not target_tool(name)}
                    if adapter is not None:
                        adapter.validate_request(payload, allowed_names=allowed, scope=gateway.cold_scopes.get(run_id, 'offline'))
                        adapter.observe_inputs(payload, ledger=gateway.ledger, run_id=run_id)
                    else:
                        # Additional native namespaces stay fail-closed unless an
                        # explicitly frozen direct-tool adapter validates them.
                        require(not (isinstance(request_input, list) and any(
                            isinstance(item, dict) and item.get('type') == 'additional_tools' for item in request_input)),
                            'TOOL_NOT_ALLOWED', 'Native additional_tools/code-mode namespaces are not qualified')
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
                    retry_of, attempts = None, 0
                    while True:
                        require(not gateway.cancel_event.is_set(), 'STOPPED', 'Gateway stopped before reservation')
                        request_id = gateway.ledger.reserve_request(run_id, dispatch_role, payload,
                            parent_request_id=explicit_parent, thread_id=self.headers.get('X-Four-Thread-Id'),
                            native_metadata=metadata, retry_of=retry_of)
                        self.request_ids.append(request_id)
                        gateway.ledger.start_request(request_id)
                        attempts += 1
                        can_retry = gateway.cold_scopes.get(run_id) == 'optimization' and attempts < 3
                        response = pending = retry_receipt = None
                        try:
                            if gateway.audit_sink is not None:
                                gateway.audit_sink('request', request_id, deepcopy(payload))
                            with gateway.lifecycle:
                                require(not gateway.closing and not gateway.cancel_event.is_set(), 'STOPPED', 'Gateway stopped before dispatch')
                                dispatched = True
                                pending = gateway.ledger.dispatch_request(request_id, lambda: gateway.transport.start(
                                    payload, dict(request_id=request_id, logical_request_id=self.request_ids[0],
                                        retry_of=retry_of, attempt=attempts, run_id=run_id, role=dispatch_role,
                                        cancel_event=gateway.cancel_event)), recover_not_sent=can_retry)
                                require(isinstance(pending, Future), 'TRANSPORT_CONTRACT', 'Transport start must return a receipt Future')
                            response = pending.result()
                        except GateError as error:
                            evidence = getattr(error, 'evidence', None)
                            if isinstance(evidence, dict):
                                self.attempt_evidence[request_id] = deepcopy(evidence)
                                gateway.ledger.record(run_id, 'transport_evidence', request_id=request_id, evidence=evidence)
                            if not (can_retry and retryable_transport_failure(error) and not gateway.cancel_event.is_set()):
                                raise
                            retry_receipt = {'error': {'code': 'connection_not_sent'}}
                        except Exception:
                            # Never expose the original exception or its secret-bearing context.
                            raise GateError('TRANSPORT_ERROR', 'Transport lost its receipt; no automatic retry') from None
                        if pending is not None and isinstance(getattr(pending, 'evidence', None), dict):
                            self.attempt_evidence[request_id] = deepcopy(pending.evidence)
                            gateway.ledger.record(run_id, 'transport_evidence', request_id=request_id, evidence=pending.evidence)
                        if retry_receipt is None:
                            require(isinstance(response, dict), 'RESPONSE_SCHEMA', 'Transport returned no Responses object')
                            recoverable = (can_retry and retryable_response(response) and
                                not self.attempt_evidence.get(request_id, {}).get('partial_output_observed'))
                            usage = gateway.ledger.finish_request(request_id, response, late_receipt=True,
                                terminal=response.get('status') in {'completed', 'incomplete', 'failed', 'cancelled'},
                                recoverable=recoverable)
                            if gateway.audit_sink is not None:
                                gateway.audit_sink('response', request_id, deepcopy(response))
                            if not recoverable:
                                break
                            retry_receipt = response
                        delay = retry_delay(retry_receipt, attempts)
                        gateway.ledger.record(run_id, 'retry_scheduled', request_id=request_id,
                                              logical_request_id=self.request_ids[0], retry_of=retry_of,
                                              attempt=attempts, wait_seconds=delay,
                                              error_code=retry_receipt['error']['code'])
                        require(not gateway.cancel_event.wait(delay), 'STOPPED', 'Cancelled during retry wait')
                        gateway.ledger.record(run_id, 'retry_ready', request_id=request_id, attempt=attempts)
                        retry_of = request_id
                    require(usage_schema_valid(response.get('usage')), 'RESPONSE_SCHEMA', 'Invalid provider usage schema')
                    require(usage['complete'], 'USAGE_UNKNOWN', 'Provider usage missing or inconsistent')
                    require(response.get('model') == POLICY['model'], 'MODEL_DRIFT', 'Response model differs from frozen model')
                    reasoning = response.get('reasoning')
                    require(reasoning is None or (isinstance(reasoning, dict) and reasoning.get('effort') in (None, POLICY['effort'])),
                            'MODEL_DRIFT', 'Response effort differs from frozen policy')
                    require(response.get('status') in {'completed', 'incomplete', 'failed', 'cancelled'} and
                            isinstance(response.get('output'), list) and all(isinstance(item, dict) for item in response['output']),
                            'RESPONSE_SCHEMA', 'A validated terminal output is required')
                    for item in response['output']:
                        if item.get('type') in {'function_call', 'custom_tool_call'}:
                            require(response['status'] == 'completed', 'RESPONSE_SCHEMA', 'Only completed responses may authorize tools')
                    if adapter is not None:
                        adapter.validate_response(response, request_id=request_id, ledger=gateway.ledger, run_id=run_id)
                    else:
                        for item in response.get('output', []):
                            if isinstance(item, dict) and item.get('type') == 'function_call':
                                require(item.get('name') in allowed, 'TOOL_NOT_ALLOWED', 'Response requested an unadmitted tool')
                                require(isinstance(item.get('call_id'), str) and bool(item['call_id']) and
                                        isinstance(item.get('arguments'), str), 'RESPONSE_SCHEMA', 'Invalid terminal tool call')
                                require(isinstance(json.loads(item['arguments']), dict), 'RESPONSE_SCHEMA', 'Tool arguments must be an object')
                            require(not (isinstance(item, dict) and item.get('type') == 'custom_tool_call'),
                                    'TOOL_NOT_ALLOWED', 'Custom executable tool responses are not admitted')
                    if response.get('status') in {'failed', 'cancelled'}:
                        gateway.ledger.stop(run_id, 'provider_' + response['status'])
                    else:
                        require(gateway.ledger.snapshot(run_id)['terminal'] is None, 'STOPPED', 'Request exceeded the frozen budget or stopped')
                    with gateway.lifecycle:
                        require(not gateway.closing and not gateway.cancel_event.is_set(), 'STOPPED', 'Receipt retained after cancellation; no result delivery')
                        sse = payload.get('stream') is True
                        delivered = {**response, 'four_harness_accounting': self.accounting_projection()}
                        framed = as_sse(delivered) if sse else canonical(delivered).encode()
                        if response['status'] == 'completed':
                            gateway.validated_requests.add(request_id)
                        self.reply(200, framed, request_id=request_id, sse=sse)
                except (BrokenPipeError, ConnectionResetError):
                    # A complete provider receipt still counts even if the native
                    # client disappears; stop the run rather than replaying it.
                    if request_id:
                        gateway.ledger.stop(binding['run_id'], 'client_disconnected')
                except Exception as error:
                    if request_id:
                        with gateway.lifecycle:
                            gateway.cancel_event.set()
                            fragments=getattr(error,'partial_output_events',[])
                            if fragments and gateway.audit_sink is not None:
                                gateway.audit_sink('partial_response',request_id,{'output_events':deepcopy(fragments),
                                    'terminal':False,'usage':None,'uncommitted':True})
                            partial=getattr(error,'observed_response',None)
                            if isinstance(partial,dict):
                                # Framing/HTTP failure does not erase counters already seen.
                                response=partial
                                gateway.ledger.finish_request(request_id,partial,terminal=False,source='transport.error.partial',late_receipt=True)
                                if gateway.audit_sink is not None:
                                    gateway.audit_sink('error_receipt',request_id,deepcopy(partial))
                            gateway.ledger.unknown_request(request_id, getattr(error, 'code', 'transport_error'))
                            if not gateway.closing:
                                gateway.ledger.stop(binding['run_id'], getattr(error, 'code', 'transport_error'))
                    code = getattr(error, 'code', 'INVALID_REQUEST' if not dispatched else 'TRANSPORT_ERROR')
                    if not dispatched and binding:
                        names = [tool.get('name', tool.get('type')) for tool in payload.get('tools', []) if isinstance(tool, dict)] if isinstance(locals().get('payload'), dict) else []
                        gateway.ledger.record(binding['run_id'], 'request_rejected', role=binding['role'], code=code, tool_names=names)
                    try:
                        self.reply(409 if isinstance(error, GateError) else 502,
                                   {'error': {'code': code, 'message': 'Gateway request failed; consult structured host evidence', 'dispatched': dispatched,
                                              'request_id': request_id,
                                               'usage': response.get('usage') if isinstance(response, dict) else None},
                                     'four_harness_accounting': self.accounting_projection()}, request_id=request_id)
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

    def manifest_tools(self, run_id):
        scope = self.cold_scopes.get(run_id)
        result = deepcopy(responses_tools(scope))
        if self.candidate_schema is not None:
            require(scope == 'optimization', 'TOOL_SCOPE', 'Custom certificates require optimization scope')
            for tool in result:
                if target_tool(tool['name']):
                    tool['parameters'] = certificate_schema(self.candidate_schema)
        return result

    def check_cold_materials(self, run_id):
        scope = self.cold_scopes.get(run_id)
        if scope is None:
            return
        host = self.tools.get(run_id)
        require(getattr(host, 'run_id', None) == run_id and host.accounting is self.cold_accounting.get(run_id),
                'COLD_RUN_BINDING', 'Cold tool identity or mandatory accounting binding changed')
        require(callable(getattr(host, 'material_inventory', None)), 'COLD_INPUT', 'Canonical cold material inventory is required')
        import hashlib
        if scope == 'optimization':
            expected = self.frozen_materials.get(run_id)
            require(isinstance(expected, list) and bool(expected) and all(
                isinstance(item, dict) and set(item) == {'id', 'size', 'sha256'}
                and isinstance(item['id'], str) and isinstance(item['size'], int) and item['size'] > 0
                and isinstance(item['sha256'], str) and len(item['sha256']) == 64
                for item in expected), 'COLD_INPUT', 'Optimization requires a frozen task-specific public inventory')
        else:
            expected = [{'id': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                        for name, data in sorted(canonical_inputs(scope).items())]
        require(host.material_inventory() == expected, 'COLD_INPUT', 'Smoke/formal input must match the canonical cold task exactly')

    def start(self):
        require(self.thread is None, 'ALREADY_STARTED', 'Gateway already started')
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()
        return self

    def close(self):
        # Synchronous DNS/TCP/TLS/send holds lifecycle. Signal first so that I/O
        # can be interrupted before close waits to acquire that lock.
        self.cancel_event.set()
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
