"""Versioned workflow contracts and host-only physical-accounting projections.

The gateway is the reservation authority. These pure transitions preserve its
receipts in the existing workflow journal; they never dispatch or retry anything.
"""
from copy import deepcopy
from schema import canonical, fields, identifier, integer, require, sha, string

FORMAT = 'dsh-math-workflow-v2'
ACCOUNTING = 'dsh-math-physical-accounting-v2'
MODEL = {'provider': 'openai-codex', 'model': 'gpt-6-astra', 'effort': 'max'}
TIMEOUTS = {'connect_seconds': 30, 'send_seconds': 60,
            'headers_seconds': 300, 'stream_idle_seconds': 300, 'max_retries': 2}
POLICIES = ('model-directed-v1', 'verify-revise-v1')
EFFECTS = {'materials_list': 'read', 'materials_read': 'read', 'workspace_read': 'read',
           'workspace_write': 'workspace_write', 'compute': 'compute',
           'check': 'check', 'submit': 'submit'}
USAGE_KEYS = ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens')
ROLES = ('worker', 'verifier', 'reviser', 'summary')


def validate_execution(spec):
    require(spec['model_policy'] == MODEL, 'MODEL_POLICY', 'Workflow v2 requires the frozen Astra/max model')
    require(spec['budget']['requests'] <= 120, 'BUDGET', 'At most 120 physical attempts per lineage')
    execution = spec['execution']
    fields(execution, 'transport accounting_authority verification_policy max_revisions timeouts isolation checker')
    require(execution['transport'] == 'metered-codex' and execution['accounting_authority'] == 'gateway',
            'ACCOUNTING', 'A qualified physical gateway is required')
    require(execution['verification_policy'] in POLICIES and execution['max_revisions'] == 2,
            'INVALID_SCHEMA', 'Unknown verification policy or revision limit')
    require(execution['timeouts'] == TIMEOUTS, 'INVALID_SCHEMA', 'Transport policy differs from qualified v2 defaults')
    require(execution['isolation'] == 'os-process', 'ISOLATION', 'Run requires verified process isolation')
    checker = execution['checker']
    if checker is not None:
        fields(checker, 'id code_sha256 task_sha256')
        identifier(checker['id'])
        sha(checker['code_sha256'])
        sha(checker['task_sha256'])
    for tool in spec['tools']:
        fields(tool, 'name version read_only schema_sha256 effect')
        require(tool['name'] in EFFECTS and tool['effect'] == EFFECTS[tool['name']],
                'TOOL_CAPABILITY', 'Tool effect differs from the fixed contract')
        require(tool['read_only'] is (tool['effect'] == 'read'), 'TOOL_CAPABILITY', 'Tool effect/read-only mismatch')
        sha(tool['schema_sha256'])
    names = {tool['name'] for tool in spec['tools']}
    require(not names.intersection({'check', 'submit'}) or checker is not None,
            'CHECKER_BINDING', 'Certificate tools require a frozen checker binding')


def initial_usage():
    return {'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
            'cached_input_tokens': 0, 'reasoning_tokens': 0, 'known_input_tokens': 0,
            'known_output_tokens': 0, 'known_total_tokens': 0, 'unknown_requests': 0,
            'complete': True, 'authority': 'gateway', 'requests_complete': True, 'unreconciled_calls': 0}


def can_call(run):
    budget, usage = run['spec']['budget'], run['usage']
    # Missing retry usage does not invent a zero or forbid another request when
    # the cumulative token budget is explicitly absent. The gateway enforces it.
    return (usage.get('requests_complete', True) and usage['requests'] < budget['requests'] and
            (budget['total_tokens'] is None or usage['complete'] and
             usage['total_tokens'] < budget['total_tokens']))


def normalize_accounting(value):
    fields(value, 'format requests')
    require(value['format'] == ACCOUNTING and type(value['requests']) is list and len(value['requests']) <= 120,
            'ACCOUNTING', 'Bounded physical receipt projection required')
    ids = set()
    for row in value['requests']:
        fields(row, 'request_id logical_request_id parent_request_id role status usage usage_complete raw_usage')
        require(type(row['usage_complete']) is bool, 'ACCOUNTING', 'Terminal usage completeness must be explicit')
        identifier(row['request_id'])
        require(row['request_id'] not in ids, 'ACCOUNTING', 'Duplicate physical request identity')
        ids.add(row['request_id'])
        for key in ('logical_request_id', 'parent_request_id'):
            if row[key] is not None:
                identifier(row[key])
        require(row['role'] in ROLES, 'ACCOUNTING', 'Unknown model role')
        require(row['status'] in ('started', 'finished', 'retryable', 'unknown', 'not_sent', 'cancelled'),
                'ACCOUNTING', 'Unknown physical attempt status')
        require(row['raw_usage'] is None or type(row['raw_usage']) is dict, 'ACCOUNTING', 'Raw usage must remain explicit')
        usage = row['usage']
        if usage is None:
            require(not row['usage_complete'], 'ACCOUNTING', 'Missing usage cannot be complete')
            continue
        if row['usage_complete']:
            require(all(usage.get(key) is not None for key in ('input_tokens', 'output_tokens', 'total_tokens')),
                    'ACCOUNTING', 'Complete usage requires all core counters')
        fields(usage, 'input_tokens output_tokens total_tokens cached_input_tokens reasoning_tokens')
        for key in USAGE_KEYS:
            if usage[key] is not None:
                integer(usage[key])
        if all(usage[key] is not None for key in ('input_tokens', 'output_tokens', 'total_tokens')):
            require(usage['total_tokens'] == usage['input_tokens'] + usage['output_tokens'],
                    'ACCOUNTING', 'Core usage is inconsistent')
        for subset, total in (('cached_input_tokens', 'input_tokens'), ('reasoning_tokens', 'output_tokens')):
            if usage[subset] is not None and usage[total] is not None:
                require(usage[subset] <= usage[total], 'ACCOUNTING', 'Usage subset exceeds its total')
    return deepcopy(value)


def project_usage(rows):
    result = initial_usage()
    result['requests'] = len(rows)
    for key in USAGE_KEYS:
        values = [(row.get('usage') or {}).get(key) for row in rows]
        known = sum(v for v in values if v is not None)
        result[key] = known if all(row.get('usage_complete') is True for row in rows) and all(v is not None for v in values) else None
        if key in ('input_tokens', 'output_tokens', 'total_tokens'):
            result['known_' + key] = known
    result['unknown_requests'] = sum(row.get('usage_complete') is not True or any((row.get('usage') or {}).get(key) is None
        for key in ('input_tokens', 'output_tokens', 'total_tokens')) for row in rows)
    result['complete'] = result['unknown_requests'] == 0
    return result


def refresh_usage(run):
    """Known physical receipts remain separate from unreconciled logical dispatches."""
    usage = project_usage(run.get('physical_requests', []))
    pending = set()
    for event in run.get('execution_events', []):
        logical_id = event.get('logical_id')
        if not logical_id:
            continue
        if event['type'] == 'model_started':
            pending.add(logical_id)
        elif event['type'] == 'model_finished' or (event['type'] == 'model_failed' and
                                                   event.get('accounting_available') is True):
            pending.discard(logical_id)
    usage['unreconciled_calls'] = len(pending)
    if pending or run.get('accounting_unreconciled'):
        usage.update(complete=False, requests_complete=False)
        for key in USAGE_KEYS:
            usage[key] = None
    run['usage'] = usage
    return usage


def account(run, request, base):
    fields(request, base + ' action_id accounting')
    identifier(request['action_id'])
    active = next((a for a in run['actions'] if a['action_id'] == request['action_id']), None)
    require(active is not None and active['status'] in ('running', 'proposed', 'unknown'),
            'INVALID_STATE', 'Physical receipts require an existing action claim')
    receipt = normalize_accounting(request['accounting'])
    rows = run.setdefault('physical_requests', [])
    by_id = {row['request_id']: row for row in rows}
    owned = active.setdefault('physical_request_ids', [])
    for row in receipt['requests']:
        previous = by_id.get(row['request_id'])
        if previous is not None:
            require(row['request_id'] in owned, 'ACCOUNTING', 'Physical request belongs to another action')
            require(previous == row or previous['status'] == 'started' and
                    all(previous[key] == row[key] for key in ('request_id', 'logical_request_id', 'parent_request_id', 'role')),
                    'OPERATION_CONFLICT', 'Completed physical receipt is immutable')
            previous.clear()
            previous.update(row)
        else:
            rows.append(row)
            by_id[row['request_id']] = row
            owned.append(row['request_id'])
    refresh_usage(run)
    require(run['usage']['requests'] <= run['spec']['budget']['requests'], 'BUDGET', 'Physical receipt exceeds frozen quota')
    cap = run['spec']['budget']['max_output_tokens']
    if any((row.get('usage') or {}).get('output_tokens') is not None and row['usage']['output_tokens'] > cap for row in rows):
        run['status'], run['reason'] = 'paused', 'budget_exceeded'
    total_cap = run['spec']['budget']['total_tokens']
    if total_cap is not None and (not run['usage']['complete'] or run['usage']['total_tokens'] > total_cap):
        run['status'], run['reason'] = 'paused', 'usage_missing' if not run['usage']['complete'] else 'budget_exceeded'


def record_event(run, request, base):
    fields(request, base + ' action_id event')
    identifier(request['action_id'])
    active = next((a for a in run['actions'] if a['action_id'] == request['action_id']), None)
    require(active is not None and active['status'] in ('running', 'proposed', 'committed'),
            'INVALID_STATE', 'Event needs an existing workflow action')
    event = request['event']
    require(type(event) is dict and len(canonical(event)) <= 256 * 1024, 'TOO_LARGE', 'Bounded host event required')
    string(event.get('type'))
    require(event['type'] in ('tool_started', 'tool_finished', 'candidate_archived', 'check_completed',
            'submit_completed', 'verification_triggered', 'verification_completed', 'revision_triggered', 'verification_inconclusive',
            'revision_limit', 'model_started', 'model_finished', 'model_failed', 'native_stop'),
            'INVALID_SCHEMA', 'Unknown workflow event type')
    # Event payloads are host-only data, never research authority or instructions.
    run.setdefault('execution_events', []).append({**deepcopy(event), 'action_id': active['action_id']})
    refresh_usage(run)


def propose(run, request, active):
    require(active is not None and active['status'] == 'running', 'INVALID_STATE', 'Proposal needs a claimed action')
    require(type(request['proposal']) is dict and len(canonical(request['proposal'])) <= 1024 * 1024,
            'TOO_LARGE', 'Bounded proposal required')
    # No logical-call aggregate may silently replace the physical authority.
    require(request['usage'] is None, 'ACCOUNTING', 'V2 usage is supplied only by physical receipts')
    ids = set(active.get('physical_request_ids', []))
    rows = [row for row in run.get('physical_requests', []) if row['request_id'] in ids]
    require(rows and run['usage'].get('requests_complete', True), 'ACCOUNTING',
            'Cannot commit a model proposal without reconciled physical receipts')
    require(rows[-1]['status'] == 'finished', 'ACCOUNTING', 'Proposal lacks a completed terminal response')
    active['status'], active['proposal'], active['usage'] = 'proposed', deepcopy(request['proposal']), project_usage(rows)
    if not can_call(run) and run['reason'] is None:
        run['status'], run['reason'] = 'paused', 'budget_exhausted'
