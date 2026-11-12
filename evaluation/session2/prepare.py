"""Prepare a preregistered Session2 math-current run using production interfaces."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import traceback
import uuid

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1]
sys.path.insert(0, str(SOURCE))
from backend import Projects
from workflow_context import OUTPUT_CONTRACT
from workflow_runtime import save_json
from workflow_service import dispatch_internal
from protocol import claim_slot, load_verified


def prepare(output, mode, registration_path, slot_id):
    frozen, slot, registration_hash = load_verified(registration_path, slot_id, mode)
    if slot['arm'] != 'math-current':
        raise ValueError('This preparation entry only admits math-current; plain must use native DSH')
    if mode == 'real':
        from transport import load_host_credential
        try:
            credential = load_host_credential()
            del credential
        except Exception as error:
            save_json(output / 'credential-preflight.json', {'ready': False,
                'code': getattr(error, 'code', 'AUTH_INVALID'), 'network_requests': 0})
            raise
        save_json(output / 'credential-preflight.json', {'ready': True, 'code': None, 'network_requests': 0})
    claim_slot(registration_path, slot_id, registration_hash, output)
    root = output / 'store'
    session = 'terminal-session2-' + str(uuid.uuid4())
    task = frozen['tasks'][slot['task']]
    from workflow_runtime import certificate_contract
    contract = certificate_contract(slot['task'])
    # Source citations are host calibration metadata, not shared solver input.
    # Sanitize only this new evaluation project; preserve the frozen contract.
    problem = deepcopy(contract['problem'])
    # Official Problem schema requires nonempty provenance; this neutral
    # placeholder contains no bibliographic or construction-method clue.
    problem['sources'] = ['Frozen public task statement supplied for this evaluation.']
    project = Projects(root).create(session, problem)
    spec = dispatch_internal(root, 'workflow-template', {'project_id': project['id'],
        'session_id': session, 'problem_ref': {'id': 'P', 'rev': 1}, 'verification_policy': 'model-directed-v1'})
    if spec['stage_goal'] != task['stage_goal']:
        raise ValueError('Frozen production stage goal differs')
    for key in ('model_policy', 'budget', 'context_budget', 'tools'):
        if spec[key] != frozen[key]:
            raise ValueError(f'Production {key} differs from preregistration')
    run_id = 'session2-' + str(uuid.uuid4())
    def control(command):
        return dispatch_internal(root, 'workflow-control', {'project_id': project['id'],
            'session_id': session, 'command': command})
    run = control({'action': 'prepare', 'run_id': run_id, 'operation_id': str(uuid.uuid4()),
        'expected_rev': 0, 'spec': spec})
    control({'action': 'start', 'run_id': run_id, 'operation_id': str(uuid.uuid4()), 'expected_rev': run['rev']})
    packet = {'root': str(root), 'projectId': project['id'], 'runId': run_id, 'sessionId': session, 'spec': spec}
    save_json(output / 'preflight.json', {'format': 'dsh-math-session2-preflight-v1', 'mode': mode,
        'arm': slot['arm'], 'slot': slot, 'registration': str(registration_path),
        'registration_sha256': registration_hash, **packet,
        'source': frozen['source'], 'model_policy': spec['model_policy'], 'budget': spec['budget'],
        'context_budget': spec['context_budget'], 'output_contract': OUTPUT_CONTRACT,
        'external_boundary': {'max_actions': None, 'max_physical_requests': 120,
            'upstream_authorized': mode == 'real', 'gateway_max_attempts_per_logical_call': 3,
            'admission': 'Native ledger per-physical-request reservation; no Session1 headroom cap'},
        'stop': frozen['stop'], 'provenance': {
            'project_creation': 'backend.Projects.create; official workflow-template/prepare/start',
            'runtime': 'startWorkflowRuntime; explicit startOfflineWorkflowRuntime only for fixture',
            'executor': 'workflow_executor.mjs:executeToolLoop (unmodified production)',
            'policy': 'model-directed-v1; unmodified production stage_goal; no Session1 diagnostic actions'},
        'host_problem_sources': deepcopy(contract['problem']['sources']),
        'solver_input_policy': 'Fresh evaluation Problem sources contain only a neutral provenance placeholder required by official schema; citations and task metadata stay host-only',
        'task_card': task['task'], 'checker': task['checker'],
        'native_checker_sha256': task['native_checker_sha256'], 'tools': spec['tools']})
    return packet


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', required=True, choices=('fixture', 'real'))
    parser.add_argument('--registration', required=True)
    parser.add_argument('--slot', required=True)
    parser.add_argument('--authorize-upstream', action='store_true')
    args = parser.parse_args()
    output = Path(args.output)
    try:
        if args.mode == 'real' and not args.authorize_upstream:
            raise ValueError('Real preparation requires explicit --authorize-upstream')
        if not output.is_absolute() or not output.is_dir() or output.is_symlink():
            raise ValueError('Preparation requires an absolute new regular directory created by terminal')
        if (output / 'preflight.json').exists() or (output / 'store').exists():
            raise ValueError('Existing preparation cannot be replayed')
        print(json.dumps(prepare(output, args.mode, args.registration, args.slot)))
    except BaseException as error:
        if output.is_absolute() and output.is_dir() and not output.is_symlink():
            save_json(output / 'prepare-error.json', {'type': type(error).__name__,
                'message': str(error), 'traceback': traceback.format_exc()})
        traceback.print_exc()
        sys.exit(1)
