"""Official project/run preparation for terminal.mjs; never contacts upstream."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback
import uuid

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from backend import Projects
from workflow_context import OUTPUT_CONTRACT
from workflow_runtime import certificate_contract, qualified_spec, save_json, tool_contracts
from workflow_service import dispatch_internal

STAGE_GOAL = """Use a short chain and very few model calls. First action: return a proposal containing one complete durable Note (use change_templates.Note), recording the construction plan and its gaps; set next_action=derive and next_target to that new Note's exact ID@rev. Do not compute or check in the first action. The next action must read that exact persisted Note from records, then execute Python using the production compute tool (the python_exec equivalent). For S1, construct Paley17 using quadratic residues modulo 17, in the public edge order; no precomputed answer is supplied. Intentionally check a clearly invalid candidate, inspect the returned error/failed-constraint feedback, correct it, check the correct candidate, then explicitly submit it. check alone does not submit. Keep calls few: combine computation/workspace persistence in one compute call where possible. Maximum three research actions and at most eight physical upstream attempts, including retries, apply externally; preserve useful partial work if stopped. Program acceptance is local finite certificate evidence, never a global proof or human acceptance. D7 is the separate 42-vertex no-monochromatic-K5 development task; Paley17 is applicable only to S1. Follow the frozen output_contract exactly."""


def source_inventory():
    ignored = {'.git', 'node_modules', 'dist', '__pycache__', '.pytest_cache'}
    rows = []
    for path in sorted(SOURCE.rglob('*')):
        if path.is_file() and not path.is_symlink() and not ignored.intersection(path.relative_to(SOURCE).parts):
            if path.suffix in {'.pyc', '.pyo'}:
                continue
            rows.append({'path': path.relative_to(SOURCE).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'root': str(SOURCE), 'sha256': digest, 'files': rows}


def prepare(output, mode, task, stage_goal=None):
    if mode == 'real':
        # Read-only admission before creating project/run/claim. The host manages
        # OAuth maintenance separately; experiment transport still never refreshes.
        from transport import load_host_credential
        try:
            credential = load_host_credential()
            del credential
        except Exception as error:
            save_json(output / 'credential-preflight.json', {'ready': False,
                'code': getattr(error, 'code', 'AUTH_INVALID'), 'network_requests': 0})
            raise
        save_json(output / 'credential-preflight.json', {'ready': True,
            'code': None, 'network_requests': 0})
    root = output / 'store'
    session = 'terminal-session1-' + str(uuid.uuid4())
    contract = certificate_contract(task)
    project = Projects(root).create(session, contract['problem'])
    spec = dispatch_internal(root, 'workflow-template', {'project_id': project['id'],
        'session_id': session, 'problem_ref': {'id': 'P', 'rev': 1},
        'verification_policy': 'model-directed-v1'})
    spec['stage_goal'] = stage_goal or STAGE_GOAL
    run_id = 'terminal-' + str(uuid.uuid4())
    def control(command):
        return dispatch_internal(root, 'workflow-control', {'project_id': project['id'],
            'session_id': session, 'command': command})
    run = control({'action': 'prepare', 'run_id': run_id, 'operation_id': str(uuid.uuid4()),
        'expected_rev': 0, 'spec': spec})
    run = control({'action': 'start', 'run_id': run_id, 'operation_id': str(uuid.uuid4()),
        'expected_rev': run['rev']})
    packet = {'root': str(root), 'projectId': project['id'], 'runId': run_id, 'sessionId': session, 'spec': spec}
    preflight = {'format': 'dsh-math-session1-preflight-v1', 'mode': mode, **packet,
        'source': source_inventory(), 'model_policy': spec['model_policy'], 'budget': spec['budget'],
        'context_budget': spec['context_budget'], 'output_contract': OUTPUT_CONTRACT,
        'external_boundary': {'max_actions': 3, 'max_physical_requests': 8,
            'gateway_max_attempts_per_logical_call': 3,
            'admission': 'Before each complete, require room for all 3 possible physical attempts; stop early if fewer than 3 remain. No quota rewrite, lineage replacement, or replay.',
            'upstream_authorized': mode == 'real'},
        'stop': ['first independent submit success', 'three actions', 'insufficient physical retry headroom', 'error or interruption'],
        'provenance': {'project_creation': 'backend.Projects(root).create(session, certificate_contract(task).problem)',
            'spec': 'workflow_runtime.qualified_spec; only stage_goal customized',
            'runtime': 'startWorkflowRuntime for real; explicit startOfflineWorkflowRuntime for fixture',
            'call': 'workflow_runtime.mjs:createWorkflowCall', 'executor': 'workflow_executor.mjs:executeToolStep',
            'fixture_answer': 'generated only inside fixture branch, absent from real stage_goal/context'},
        'task_card': contract['task'], 'checker': contract['checker'],
        'native_checker_sha256': contract['native_checker_sha256'], 'tools': tool_contracts()}
    save_json(output / 'preflight.json', preflight)
    return packet


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', required=True, choices=('fixture', 'real'))
    parser.add_argument('--task', default='S1')
    parser.add_argument('--stage-goal')
    parser.add_argument('--authorize-upstream', action='store_true')
    args = parser.parse_args()
    output = Path(args.output)
    try:
        if args.mode == 'real' and not args.authorize_upstream:
            raise ValueError('Real preparation requires --authorize-upstream; preparation itself makes no upstream call')
        if not output.is_absolute() or not output.is_dir():
            raise ValueError('Preparation requires an absolute directory created by terminal.mjs')
        if (output / 'preflight.json').exists() or (output / 'store').exists():
            raise ValueError('Existing preparation cannot be replayed')
        print(json.dumps(prepare(output, args.mode, args.task, args.stage_goal)))
    except BaseException as error:
        if output.is_absolute() and output.is_dir():
            save_json(output / 'prepare-error.json', {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()})
        traceback.print_exc()
        sys.exit(1)
