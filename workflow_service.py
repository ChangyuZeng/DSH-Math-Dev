"""Shared workflow transactions. Model proposals never acquire human authority.

All service operations take one project-level process lock before opening either
research or workflow stores. It spans research commit + workflow receipt, never
model transport. A crashed transaction is recovered only from its exact receipt.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import os
import stat

from backend import Projects
from research_store import safe_path, sync_directory
from schema import StoreError, canonical, fields, identifier, integer, ref, require, validate_data
from workflow_context import build_snapshot, completion_projection, freshness, head
from workflow_store import WorkflowStore
from workflow_strategy import derive_strategy

PUBLIC = {'list', 'read', 'prepare', 'start', 'pause', 'cancel', 'reconcile', 'continue', 'commit', 'grant_chat'}


@contextmanager
def transaction(root, project_id):
    # Validate the full project before creating any sidecar. Release research's
    # read lock before taking this lock: all callers share the same lock order.
    projects = Projects(root)
    with projects.open_project(project_id, require_writable=True):
        pass
    directory = projects.path(project_id)
    lock = safe_path(directory, '.workflow-service.lock')
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'UNSAFE_PATH', 'Unsafe workflow service lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StoreError('LOCKED', 'Workflow transaction in progress; retry the same operation') from error
        sync_directory(directory)
        yield
    finally:
        os.close(fd)


def control(root, project_id, session_id, command):
    require(type(command) is dict and command.get('action') in PUBLIC, 'FORBIDDEN', 'Workflow control action unavailable')
    identifier(session_id)
    projects = Projects(root)
    # Authorization precedes any workflow side effects, including lock creation.
    with projects.open_project(project_id, require_writable=True, session_id=session_id, chat=True):
        pass
    with transaction(root, project_id):
        if command['action'] == 'commit':
            fields(command, 'action run_id expected_rev action_id')
            return _commit_proposal(root, project_id, command['run_id'], command['expected_rev'], command['action_id'])
        request = {**command, 'project_id': project_id}
        if command['action'] == 'grant_chat':
            fields(command, 'action run_id expected_rev operation_id allowed')
            request['session_id'] = session_id
        if command['action'] == 'continue':
            with projects.open_project(project_id, require_writable=True) as (_, research):
                request['research_head'] = head(research)
        if command['action'] == 'reconcile':
            fields(command, 'action run_id expected_rev operation_id')
            run = WorkflowStore(root).dispatch({'action': 'read', 'project_id': project_id, 'run_id': command['run_id']})
            proposed = next((a for a in run['actions'] if a['status'] == 'proposed'), None)
            if proposed:
                return _commit_proposal(root, project_id, run['run_id'], command['expected_rev'], proposed['action_id'], receipt_only=True)
        result = _state(root, request)
        if command['action'] == 'read':
            # Public projections are response-only. Internal state, context hashes,
            # revision CAS and durable workflow events retain their exact schema.
            with projects.open_project(project_id, require_writable=True) as (_, research):
                return {**result, 'strategy': derive_strategy(result, research.records),
                        'completion_projection': completion_projection(research)}
        return result


def _state(root, request):
    # A durable research receipt must remain reachable after a sidecar failure.
    # Cancelling/failing that proposal would erase the recovery entry point.
    if request.get('action') in ('cancel', 'fail'):
        run = WorkflowStore(root).dispatch({'action': 'read', 'project_id': request['project_id'], 'run_id': request['run_id']})
        pending = next((a for a in run['actions'] if a['status'] == 'proposed'), None)
        if pending:
            with Projects(root).open_project(request['project_id'], require_writable=True) as (_, research):
                require('workflow-' + pending['action_id'] not in research.operations, 'RECONCILE_REQUIRED',
                        'Research receipt exists; archive the proposal before cancelling or failing it')
    return WorkflowStore(root).dispatch(request)


def context(root, project_id, run_id, kind, target_ref=None, dependency_refs=None, route_id=None):
    with transaction(root, project_id):
        run = WorkflowStore(root).dispatch({'action': 'read', 'project_id': project_id, 'run_id': run_id})
        return build_snapshot(root, project_id, run['spec'], kind, target_ref, dependency_refs, route_id)


def _proposal(run, attempt):
    proposal = attempt['proposal']
    fields(proposal, 'summary changes next_action next_target progress limitations')
    require(type(proposal['summary']) is str and type(proposal['limitations']) is list and
            all(type(value) is str for value in proposal['limitations']), 'INVALID_SCHEMA', 'Readable summary and limitations required')
    require(proposal['next_action'] in ('derive', 'check', 'revise', 'decompose', 'stop'), 'INVALID_SCHEMA', 'Invalid next action')
    require(proposal['progress'] in ('new_argument', 'new_obligation', 'new_evidence', 'no_progress'), 'INVALID_SCHEMA', 'Invalid progress classification')
    changes = proposal['changes']
    require(type(changes) is list and len(changes) <= 8 and len(canonical(changes)) <= 256 * 1024,
            'TOO_LARGE', 'Proposal permits at most eight bounded research changes')
    ids = set()
    for change in changes:
        fields(change, 'kind id expected_rev data')
        identifier(change['id'])
        integer(change['expected_rev'])
        require(change['id'] not in ids, 'INVALID_SCHEMA', 'Duplicate proposal object')
        ids.add(change['id'])
        require(change['kind'] in ('Note', 'Obligation'), 'FORBIDDEN', 'Workflow may propose Notes and Obligations only')
        validate_data(change['kind'], change['data'])
        require(change['data']['problem_ref'] == run['spec']['problem_ref'], 'TARGET_MISMATCH', 'Proposal changed the problem')
    if proposal['next_target'] is not None:
        ref(proposal['next_target'])
    return proposal, changes


def _receipt(research, operation_id, changes, origin):
    previous = research.operations.get(operation_id)
    if previous is None:
        return None
    prior_changes = [{'kind': row['kind'], 'id': row['id'], 'expected_rev': row['rev'] - 1, 'data': row['data']} for row in previous['changes']]
    require(prior_changes == changes and previous['origin'] == origin
            and previous['actor'] == {'kind': 'model', 'id': 'dsh-math-workflow'},
            'OPERATION_CONFLICT', 'Research commit differs from durable proposal')
    return deepcopy(previous['result'])


def _commit_proposal(root, project_id, run_id, expected_rev, action_id, *, receipt_only=False):
    identifier(run_id)
    identifier(action_id)
    integer(expected_rev, 1)
    workflows = WorkflowStore(root)
    run = workflows.dispatch({'action': 'read', 'project_id': project_id, 'run_id': run_id})
    attempt = next((row for row in run['actions'] if row['action_id'] == action_id), None)
    require(attempt is not None and attempt['status'] in ('proposed', 'committed'), 'INVALID_STATE', 'No proposal to commit')
    if attempt['status'] != 'committed':
        require(run['rev'] == expected_rev, 'VERSION_CONFLICT', 'Workflow changed before proposal commit')
    proposal, changes = _proposal(run, attempt)
    projects = Projects(root)
    metadata = projects.metadata(project_id)
    operation_id = 'workflow-' + action_id
    identifier(operation_id)
    identifier('receipt-' + action_id)
    origin = {'source': 'dsh-math-workflow', 'session_id': metadata['session_id'], 'run_id': run_id, 'tool_call_id': action_id}
    readonly = receipt_only or attempt['status'] == 'committed'
    with projects.open_project(project_id, writable=not readonly, require_writable=True,
                               actor={'kind': 'model', 'id': 'dsh-math-workflow'}, origin=origin) as (_, research):
        receipt = _receipt(research, operation_id, changes, origin)
        if receipt is None and not changes and attempt['status'] == 'committed':
            receipt = {'versions': []}
        if receipt is None:
            require(not changes or not readonly, 'RECONCILE_REQUIRED', 'No exact research receipt; proposal was not committed')
            require(not freshness(research.records, attempt['context_snapshot']), 'STALE_DEPENDENCY', 'Premise changed; proposal requires reassessment')
            target = proposal['next_target']
            if target is not None:
                proposed = next((c for c in changes if c['id'] == target['id'] and c['expected_rev'] + 1 == target['rev']), None)
                obj = proposed if proposed else research.get(target['id'], target['rev'])
                require(obj['kind'] in ('Problem', 'Note', 'Obligation', 'Evidence') and
                        ((obj['kind'] == 'Problem' and target == run['spec']['problem_ref']) or
                         obj['data'].get('problem_ref') == run['spec']['problem_ref']),
                        'TARGET_MISMATCH', 'Next target belongs to another problem')
            receipt = research.commit(operation_id, changes) if changes else {'versions': []}
    if attempt['status'] == 'committed':
        require(attempt['output_refs'] == receipt['versions'], 'OPERATION_CONFLICT', 'Workflow receipt differs from research receipt')
        return run
    # Failure here leaves the proposal recoverable. Never downgrade it to failed.
    return workflows.dispatch({'action': 'commit', 'project_id': project_id, 'run_id': run_id,
                               'expected_rev': expected_rev, 'operation_id': 'receipt-' + action_id,
                               'action_id': action_id, 'output_refs': receipt['versions']})


def commit_proposal(root, project_id, run_id, expected_rev, action_id):
    with transaction(root, project_id):
        return _commit_proposal(root, project_id, run_id, expected_rev, action_id)


def dispatch_internal(root, action, args):
    if action == 'workflow-control':
        return control(root, **args)
    if action == 'workflow-template':
        fields(args, 'project_id session_id problem_ref verification_policy')
        ref(args['problem_ref'])
        require(args['verification_policy'] in {'model-directed-v1', 'verify-revise-v1'}, 'INVALID_SPEC', 'Unknown verification policy')
        with Projects(root).open_project(args['project_id'], require_writable=True, session_id=args['session_id'], chat=True) as (_, research):
            problem = research.get(args['problem_ref']['id'], args['problem_ref']['rev'])
            from workflow_candidates import certificate_contract
            from workflow_runtime import qualified_spec
            for task_id in ('D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'H1', 'H2', 'H3'):
                contract = certificate_contract(task_id)
                if problem['kind'] == 'Problem' and all(problem['data'].get(k) == v for k, v in contract['problem_fields'].items()):
                    return qualified_spec(args['project_id'], task_id, args['verification_policy'], args['problem_ref'])
        require(False, 'RUNTIME_CAPABILITY', 'This exact problem has no qualified fixed certificate binding')
    if action == 'workflow-context':
        return context(root, **args)
    if action == 'workflow-commit':
        return commit_proposal(root, **args)
    if action == 'workflow-state':
        with transaction(root, args['project_id']):
            return _state(root, args)
    if action == 'workflow-tool':
        from workflow_tool_store import tool_receipt
        with transaction(root, args['project_id']):
            return tool_receipt(root, **args)
    if action in ('workflow-candidate', 'workflow-candidate-check'):
        from workflow_candidates import archive_candidate, archive_check
        with transaction(root, args['project_id']):
            return (archive_candidate if action == 'workflow-candidate' else archive_check)(root, **args)
    require(False, 'FORBIDDEN', 'Unknown workflow service entry')


if __name__ == '__main__':
    import json
    import sys
    try:
        request = json.load(sys.stdin)
        value = dispatch_internal(sys.argv[1], request['action'], request['args'])
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
    except (StoreError, ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': getattr(error, 'code', type(error).__name__), 'message': str(error)}, ensure_ascii=False))
        sys.exit(1)
