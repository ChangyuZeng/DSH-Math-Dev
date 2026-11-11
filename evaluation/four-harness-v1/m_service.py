"""Isolated benchmark bridge to the real workflow stores and commit service.

The process is deliberately separate from the plugin host. It accepts only a fresh
OS-temporary root marked by initialize(); it cannot open a user's project. The
only store adaptation aggregates physical Responses requests within one durable
workflow action. No research authority, model transport, or compute is added.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import uuid

REPO = Path(__file__).resolve().parents[2]
# Load only the declared native modules by exact filename. Python's directory
# finder would need permission to enumerate the whole repository in the OS jail.
# Native source bytes, classes and service calls remain unchanged.
for _name in ('schema', 'backend', 'workflow_store', 'workflow_context', 'workflow_strategy', 'workflow_service'):
    _spec = importlib.util.spec_from_file_location(_name, REPO / (_name + '.py'))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)

SOURCE_PINS = {
    'workflow_runner.mjs': 'af7beeac207c59eae4c0b1fa8a2712ade6a4d8afccb30b234e6b73a4e4feb061',
    'workflow_store.py': 'c26f5bc4fdf5514312a99ad920fca6e16105ba93d0ff9c6600de808aacce58a3',
    'workflow_context.py': 'a410b202124d51417eff42f738d88dc9db96d09a4de4d9f5f8b53168e7eec3b4',
    'workflow_service.py': 'c599fff41ef98f33722b8b2f22a427c760b39ac4393753e50c140233c652c200',
}

from backend import Projects, checked_root, write_new
from research_store import read_regular, safe_path
from schema import StoreError, canonical, decode, fields, identifier, integer, require
import workflow_service
from workflow_store import WorkflowStore

MARKER = 'm-benchmark.json'
FORMAT = 'dsh-math-four-harness-m-isolated-v1'
USAGE_KEYS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens')


def verify_sources():
    for name, expected in SOURCE_PINS.items():
        require(hashlib.sha256((REPO / name).read_bytes()).hexdigest() == expected,
                'SOURCE_DRIFT', 'Frozen M workflow source changed: ' + name)
    return SOURCE_PINS


def temporary_root(value):
    path = Path(value)
    require(path.is_absolute() and path.exists() and path.is_dir(), 'ISOLATION', 'Existing absolute temporary root required')
    root = checked_root(path)
    temp = Path(tempfile.gettempdir()).resolve()
    require(root != temp and root.is_relative_to(temp), 'ISOLATION', 'M bridge accepts a fresh OS-temporary root only')
    return root


def validate_accounting(value):
    fields(value, 'format requests')
    require(value['format'] == 'dsh-math-m-action-accounting-v1', 'INVALID_SCHEMA', 'Unknown M accounting format')
    rows = value['requests']
    require(type(rows) is list and len(rows) <= 1000, 'INVALID_SCHEMA', 'Bounded per-request accounting required')
    ids = set()
    for row in rows:
        fields(row, 'request_id parent_request_id status usage raw_usage')
        require(row['raw_usage'] is None or type(row['raw_usage']) is dict, 'INVALID_SCHEMA', 'Raw provider usage must be preserved')
        identifier(row['request_id'])
        require(row['request_id'] not in ids, 'INVALID_SCHEMA', 'Duplicate physical request')
        ids.add(row['request_id'])
        if row['parent_request_id'] is not None:
            identifier(row['parent_request_id'])
        require(row['status'] in ('finished', 'unknown', 'not_dispatched'), 'INVALID_SCHEMA', 'Unknown request state')
        usage = row['usage']
        if usage is None:
            require(row['status'] != 'finished', 'INVALID_SCHEMA', 'Finished request requires usage')
            continue
        fields(usage, 'input_tokens cached_input_tokens output_tokens reasoning_tokens total_tokens source complete')
        require(type(usage['source']) is str and type(usage['complete']) is bool, 'INVALID_SCHEMA', 'Explicit accounting provenance required')
        for key in USAGE_KEYS:
            if usage[key] is not None:
                integer(usage[key])
        require(not usage['complete'] or all(usage[key] is not None for key in USAGE_KEYS),
                'INVALID_SCHEMA', 'Complete usage cannot contain unknown values')
        require(row['status'] != 'finished' or usage['complete'], 'INVALID_SCHEMA', 'Finished request needs complete accounting')
    return deepcopy(value)


class BenchmarkWorkflowStore(WorkflowStore):
    """Same journal/CAS/claims, with physical counts and aggregate action output.

    Native max_output_tokens remains the *physical* request cap. Native action
    usage.output_tokens remains an honest sum. The central gateway ledger reserves
    every physical request and remains the authority for all benchmark budgets.
    """

    @staticmethod
    def _can_call(run):
        budget = run['spec']['budget']
        def within_cap(action):
            accounting = action.get('benchmark_accounting')
            if accounting is None:
                return action['usage'] is None or action['usage']['output_tokens'] <= action['max_output_tokens']
            return all(row['usage'] is None or row['usage']['output_tokens'] is None or
                       row['usage']['output_tokens'] <= action['max_output_tokens'] for row in accounting['requests'])
        return (run['usage']['complete'] and run['usage']['requests'] < budget['requests'] and
                (budget['total_tokens'] is None or run['usage']['total_tokens'] < budget['total_tokens']) and all(within_cap(a) for a in run['actions']))

    def _transition(self, run, request, base, research):
        accounting = request.get('benchmark_accounting')
        if accounting is None:
            return super()._transition(run, request, base, research)
        require(request['action'] in ('propose', 'fail'), 'INVALID_SCHEMA', 'Accounting belongs to action outcomes only')
        accounting = validate_accounting(accounting)
        active = next((a for a in run['actions'] if a['action_id'] == request['action_id']), None)
        require(active is not None and active['status'] in ('running', 'proposed'), 'INVALID_STATE', 'No active benchmark action')
        previous = active.get('benchmark_accounting')
        if previous is not None:
            require(accounting == previous, 'OPERATION_CONFLICT', 'Action accounting is immutable')
        rows = accounting['requests']
        counted = [row for row in rows if row['status'] != 'not_dispatched']
        # reserve already counted one claimed action. Replace it with the exact
        # number of physical dispatches, including unknown dispatched requests.
        old_count = len([r for r in previous['requests'] if r['status'] != 'not_dispatched']) if previous else 1
        run['usage']['requests'] += len(counted) - old_count
        plain = {key: value for key, value in request.items() if key != 'benchmark_accounting'}
        known = [row['usage'] for row in counted if row['usage'] is not None]
        if request['action'] == 'propose' and request['usage'] is not None:
            require(request['usage']['total_tokens'] == sum(u['total_tokens'] or 0 for u in known) and
                    request['usage']['output_tokens'] == sum(u['output_tokens'] or 0 for u in known),
                    'INVALID_SCHEMA', 'Action usage must equal known physical usage subtotal')
        incomplete = any(row['status'] == 'unknown' for row in counted)
        if request['action'] == 'fail' and incomplete:
            plain['accounting_complete'] = False
        original_cap = active['max_output_tokens']
        # The base transition compares aggregate action output with its envelope;
        # expand only while applying it, then restore the frozen physical cap.
        active['max_output_tokens'] = original_cap * max(1, len(counted))
        try:
            super()._transition(run, plain, base, research)
        finally:
            active['max_output_tokens'] = original_cap
        active['benchmark_accounting'] = accounting
        # The native runner checks AbortSignal before consuming a usage chunk.
        # Retain known provider costs even when cancellation drops that chunk;
        # the action outcome remains unknown and is never replayed.
        if request['action'] == 'fail' and previous is None and active['usage'] is None:
            totals = [u for u in known if u['total_tokens'] is not None]
            total = sum(u['total_tokens'] for u in totals)
            output = sum(u['output_tokens'] or 0 for u in totals)
            if totals and total >= output:
                active['usage'] = {'total_tokens': total, 'output_tokens': output}
                run['usage']['total_tokens'] += total
        if any(u['output_tokens'] is not None and u['output_tokens'] > original_cap for u in known):
            run['status'], run['reason'] = 'paused', 'budget_exceeded'
        if incomplete:
            run['usage']['complete'] = False


# This binding is private to the short-lived benchmark process. No production
# file or running host module is patched; context and commits still call the real
# workflow_service implementation and ResearchStore receipts.
workflow_service.WorkflowStore = BenchmarkWorkflowStore


def initialize(root, args):
    fields(args, 'run_id problem model_policy budget context_budget stage_goal tools_sha256')
    require(not any(root.iterdir()), 'ISOLATION', 'Benchmark initialize requires an empty temporary root')
    identifier(args['run_id'])
    session_id = 'benchmark-' + str(uuid.uuid4())
    project = Projects(root).create(session_id, args['problem'])
    spec = {'format': 'dsh-math-workflow-v1', 'project_id': project['id'], 'problem_ref': {'id': 'P', 'rev': 1},
            'model_policy': args['model_policy'], 'budget': args['budget'], 'tools': [],
            'context_budget': args['context_budget'], 'stage_goal': args['stage_goal']}
    run = workflow_service.dispatch_internal(root, 'workflow-state', {
        'action': 'prepare', 'project_id': project['id'], 'run_id': args['run_id'],
        'expected_rev': 0, 'operation_id': str(uuid.uuid4()), 'spec': spec})
    marker = {'format': FORMAT, 'project_id': project['id'], 'run_id': args['run_id'], 'session_id': session_id,
              'source_pins': SOURCE_PINS, 'tools_sha256': args['tools_sha256'],
              'initialization_sha256': hashlib.sha256(canonical(args)).hexdigest()}
    write_new(safe_path(root, MARKER), marker)
    return {'run': run, 'isolation': marker, 'root': str(root)}


def dispatch(root_value, request):
    verify_sources()
    root = temporary_root(root_value)
    fields(request, 'action args')
    if request['action'] == 'initialize':
        return initialize(root, request['args'])
    marker = decode(read_regular(safe_path(root, MARKER)))
    require(marker['format'] == FORMAT and marker['source_pins'] == SOURCE_PINS,
            'ISOLATION', 'Fresh benchmark marker required')
    args = request['args']
    if request['action'] == 'inspect':
        fields(args, '')
        with Projects(root).open_project(marker['project_id']) as (metadata, research):
            return {'metadata': metadata, 'objects': deepcopy(research.records), 'events': deepcopy(research.events),
                    'head_hash': research.head_hash, 'isolation': marker}
    if request['action'] == 'identity':
        fields(args, '')
        return marker
    require(request['action'] in ('workflow-state', 'workflow-context', 'workflow-commit', 'workflow-control'),
            'FORBIDDEN', 'Unknown benchmark service command')
    require(args.get('project_id') == marker['project_id'], 'ISOLATION', 'Project is outside this benchmark root')
    if request['action'] == 'workflow-control':
        require(args.get('session_id') == marker['session_id'] and args.get('command', {}).get('run_id') == marker['run_id'],
                'ISOLATION', 'Control is outside this benchmark run')
        require(args['command'].get('action') in ('read', 'reconcile', 'cancel', 'pause'), 'FORBIDDEN', 'No benchmark continuation or external preparation')
    else:
        require(args.get('run_id') == marker['run_id'], 'ISOLATION', 'Run is outside this benchmark root')
        if request['action'] == 'workflow-state':
            require(args.get('action') not in ('prepare', 'continue', 'queue', 'claim'), 'FORBIDDEN', 'No extra benchmark runs or queued calls')
    return workflow_service.dispatch_internal(root, request['action'], args)


if __name__ == '__main__':
    try:
        value = dispatch(sys.argv[1], json.load(sys.stdin))
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
    except (StoreError, ValueError, KeyError, TypeError, OSError, IndexError) as error:
        print(json.dumps({'ok': False, 'error': getattr(error, 'code', type(error).__name__), 'message': str(error)}, ensure_ascii=False))
        sys.exit(1)
