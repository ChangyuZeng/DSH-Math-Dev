"""Explicit serial campaign driver. No work runs at import or without --authorize-upstream.

The campaign and UI use workflow_runtime + workflow_executor unchanged. The only
extra operations here are frozen scheduling, evidence capture and comparison.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1]
sys.path.insert(0, str(SOURCE))
from backend import Projects
from research_store import read_regular
from schema import require
from workflow_runtime import certificate_contract, qualified_spec, save_json
from workflow_v2 import MODEL
from workflow_service import dispatch_internal
from campaign import Campaign, unknown_result, usage_projection
from protocol import DEFAULT_ROOT, campaign_root, digest, freeze, verify_freeze
from recovery import verify_recovery


def prepare_slot(directory, slot, protocol):
    """New actual ResearchStore project, session and runtime per consumed slot."""
    folder = Path(directory) / 'runs' / slot['slot_id']
    require(not folder.exists(), 'NO_REPLAY', 'A started slot cannot be prepared again')
    folder.mkdir(parents=True, mode=0o700)
    root = folder / 'store'
    contract = certificate_contract(slot['task'])
    session = 'campaign-' + str(uuid.uuid4())
    project = Projects(root).create(session, contract['problem'])
    run_id = str(uuid.uuid4())
    spec = qualified_spec(project['id'], slot['task'], protocol['arms'][slot['arm']])
    run = dispatch_internal(root, 'workflow-state', {'action': 'prepare', 'project_id': project['id'],
        'run_id': run_id, 'operation_id': str(uuid.uuid4()), 'expected_rev': 0, 'spec': spec})
    run = dispatch_internal(root, 'workflow-state', {'action': 'start', 'project_id': project['id'],
        'run_id': run_id, 'operation_id': str(uuid.uuid4()), 'expected_rev': run['rev']})
    request = {'root': str(root), 'projectId': project['id'], 'runId': run_id,
               'output': str(folder / 'process.json'), 'python': sys.executable}
    save_json(folder / 'launch.json', {'slot': slot, 'request': request, 'source_sha256': protocol['source']['sha256']})
    return request


def offline_preflight(directory, document, node='node'):
    """Repeat the frozen offline tests, then recheck every frozen input."""
    records = []
    for command in document['protocol']['offline_preflight_commands']:
        executable = sys.executable if command[0] == 'python' else node
        run = subprocess.run([executable, *command[1:]], cwd=SOURCE, capture_output=True, timeout=300,
                             env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        records.append({'command': command, 'returncode': run.returncode,
            'stdout': run.stdout.decode('utf8', 'replace'), 'stderr': run.stderr.decode('utf8', 'replace')})
    save_json(Path(directory) / 'preflight' / (str(uuid.uuid4()) + '.json'), {'commands': records})
    require(all(row['returncode'] == 0 for row in records), 'PREFLIGHT_FAILED', 'Required offline tests failed')
    verify_freeze(directory, node=node)
    return digest(document['protocol']['qualification'])


def physical_probe(directory, reservation):
    """Exactly one non-target physical attempt, with no Gateway/SDK retry loop."""
    from transport import HostTransport
    from ledger import normalize_usage
    folder = Path(directory) / 'connectivity' / reservation['probe_id']
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    save_json(folder / 'reservation.json', reservation)
    evidence = []
    def audited(value):
        evidence.append(value)
        save_json(folder / 'transport.json', evidence)
    transport = HostTransport(profile='workflow', max_output_tokens=128000, evidence_sink=audited)
    payload = {'model': 'gpt-6-astra', 'reasoning': {'effort': 'max'}, 'max_output_tokens': 128000,
        'instructions': 'Connectivity only. Return READY.', 'input': [{'role': 'user', 'content': 'Reply READY.'}],
        'tools': [], 'tool_choice': 'none', 'store': False, 'stream': True}
    start, response, failure = time.monotonic(), None, None
    try:
        response = transport.start(payload, {'request_id': reservation['probe_id'], 'role': 'worker',
            'run_id': 'workflow-connectivity', 'cancel_event': threading.Event()}).result()
    except Exception as error:
        response = getattr(error, 'observed_response', None)
        failure = {'code': getattr(error, 'code', 'CONNECTIVITY')}
    usage = normalize_usage(response.get('usage') if response else None, terminal=failure is None and bool(response))
    reasoning = (response or {}).get('reasoning')
    success = bool(failure is None and response and response.get('status') == 'completed' and
                   response.get('model') == 'gpt-6-astra' and usage['complete'] and
                   (reasoning is None or isinstance(reasoning, dict) and reasoning.get('effort') in (None, 'max')))
    receipt = {'physical_requests': 1, 'success': success, 'response': response, 'failure': failure,
               'usage': usage,
               'wall_seconds': time.monotonic() - start}
    save_json(folder / 'receipt.json', receipt)
    return receipt


def _ledger_evidence(directory):
    """Read crashed reservations and the verified event chain without repairing it."""
    ledger = Path(directory) / 'ledger.sqlite'
    if not ledger.exists():
        return [], [], False
    db = sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows, events, previous = [], [], '0' * 64
        for row in db.execute('SELECT * FROM events ORDER BY seq'):
            event = json.loads(row['event'])
            require(event['seq'] == row['seq'] and event['previous_hash'] == previous and digest(event) == row['hash'],
                    'ACCOUNTING', 'Gateway event chain cannot be reconciled')
            events.append(event)
            previous = row['hash']
        for row in db.execute('SELECT * FROM requests ORDER BY rowid'):
            rows.append({'request_id': row['id'], 'parent_request_id': row['parent'], 'role': row['role'],
                'status': 'finished' if row['state'] == 'finished' else 'unknown', 'gateway_state': row['state'],
                'usage': json.loads(row['usage']) if row['usage'] else None,
                'raw_usage': json.loads(row['raw_usage']) if row['raw_usage'] else None})
        return rows, events, True
    finally:
        db.close()


def immediate_failure(code, events):
    """Use host transport evidence as well as the sanitized executor error code."""
    evidence = [row.get('evidence', {}) for row in events if row.get('type') == 'transport_evidence']
    if (code or '').startswith('AUTH_') or any(row.get('http_status') in (401, 403) for row in evidence):
        return 'auth'
    if (code == 'MODEL_DRIFT' or any(row.get('anomaly') in ('response_model_drift', 'response_effort_drift') or
            row.get('actual_model') is not None and row['actual_model'] != MODEL['model'] or
            row.get('actual_effort') is not None and row['actual_effort'] != MODEL['effort'] for row in events) or
            any(row.get('error_model_drift') is True or row.get('error_effort_drift') is True for row in evidence)):
        return 'model_drift'
    protocol_codes = {'PROTOCOL_DRIFT', 'CHECKER_DRIFT', 'TASK_DRIFT', 'TOOL_CAPABILITY', 'GATEWAY_MANIFEST',
        'ACCOUNTING', 'RUNTIME_BUDGET', 'RUNTIME_CAPTURE_MISSING', 'RUNTIME_CLEANUP', 'GATEWAY_RECEIPT', 'RESPONSE_SCHEMA', 'SSE_SCHEMA', 'SSE_DUPLICATE',
        'UPSTREAM_JSON', 'UPSTREAM_DECODE', 'UPSTREAM_TYPE', 'CONTEXT_EVIDENCE_DRIFT', 'NATIVE_LINEAGE_DRIFT'}
    if code in protocol_codes or any(row.get('rejected_parameter') is not None or row.get('error_schema_invalid') is True for row in evidence):
        return 'protocol_drift'
    return None


def collect_slot(directory, slot, protocol):
    folder = Path(directory) / 'runs' / slot['slot_id']
    result = unknown_result(slot)
    if not (folder / 'launch.json').exists():
        return result
    launch = json.loads(read_regular(folder / 'launch.json'))
    request = launch['request']
    runtime_dir = Path(request['root']) / 'projects' / request['projectId'] / 'workflow-runtime' / request['runId']
    process = json.loads(read_regular(folder / 'process.json')) if (folder / 'process.json').exists() else None
    rows, gateway_events, known = _ledger_evidence(runtime_dir)
    usage = usage_projection(rows) if known else {key: None for key in ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens')}
    result.update(physical_requests=rows, physical_count_known=known, usage=usage,
                  total_tokens=usage['total_tokens'], runtime_directory=str(runtime_dir),
                  immediate=immediate_failure(None, gateway_events))
    if not process:
        if runtime_dir.exists():
            result.update(immediate=result['immediate'] or 'protocol_drift', failure='RUNTIME_CAPTURE_MISSING')
        save_json(folder / 'result.json', result)
        return result
    run, failure = process['run'] or {}, process['failure']
    events = run.get('execution_events', [])
    success_event = any(event.get('type') == 'native_stop' and event.get('reason') == 'first_independent_submit_success' for event in events)
    # Preserve the immutable independent receipt; posthoc checks never set success.
    accepted = []
    submissions = runtime_dir / 'isolated' / 'common' / '.host' / 'submissions'
    if submissions.is_dir():
        for path in sorted(submissions.glob('receipt-*.json')):
            receipt = json.loads(read_regular(path))
            check = receipt.get('independent_check', {})
            if (receipt.get('verification_basis') == 'fresh_process_exact_audit_rerun' and check.get('legal') is True
                    and check.get('target_met') is True and receipt.get('run_id') == request['runId']
                    and receipt.get('checker_sha256') == run.get('spec', {}).get('execution', {}).get('checker', {}).get('code_sha256')):
                candidate_bytes = read_regular(submissions / (receipt['candidate_sha256'] + '.json'))
                require(hashlib.sha256(candidate_bytes).hexdigest() == receipt['candidate_sha256'],
                        'ACCOUNTING', 'Independent candidate bytes changed')
                accepted.append(receipt)
    formal = success_event and bool(accepted) and failure is None
    code = failure['code'] if failure else None
    terminals = [row for row in gateway_events if row.get('type') in ('request_finished', 'request_unknown')]
    provider_failed = bool(terminals and terminals[-1].get('finish_reason') in ('failed', 'cancelled'))
    math_stop = code in {'REQUEST_BUDGET', 'TOKEN_BUDGET', 'CAMPAIGN_BUDGET', 'INVALID_PROPOSAL'} or (
        code == 'MODEL_INCOMPLETE' and not provider_failed)
    infra = bool(failure) and not math_stop
    final = json.loads(read_regular(runtime_dir / 'final.json')) if (runtime_dir / 'final.json').exists() else None
    if not final:
        formal, infra, code = False, True, 'RUNTIME_CAPTURE_MISSING'
    if not known or len(rows) > 120 or (final and final.get('ledger', {}).get('requests') != len(rows)):
        formal, infra, code = False, True, 'ACCOUNTING'
    immediate = immediate_failure(code, gateway_events)
    guard_ok = final is not None and final['state_guard'] is not None and not final['state_guard'].get('violation')
    task = next(row for row in protocol['tasks'] if row['id'] == slot['task'])
    checks_ok = (run.get('spec', {}).get('execution', {}).get('checker') == task['checker'] and
                 run.get('spec', {}).get('tools') == protocol['tool_contracts'])
    if not guard_ok or not checks_ok:
        formal, infra = False, True
    result.update(formal_success=formal, infra_terminated=infra,
        termination='formal_success' if formal else 'infrastructure' if infra else 'math_unsolved',
        failure=code, immediate=immediate, wall_seconds=process['wall_seconds'], execution_events=events,
        independent_receipts=accepted, comparable={'checks': checks_ok, 'source': launch['source_sha256'] == protocol['source']['sha256'],
            'isolation': guard_ok and protocol['qualification']['checks'].get('isolated_compute_denials') is True},
        differential_defect=False, posthoc=None)
    if slot['task'] in ('D5', 'D6'):
        task = next(row['card'] for row in protocol['tasks'] if row['id'] == slot['task'])
        candidate = json.loads(read_regular(submissions / (accepted[0]['candidate_sha256'] + '.json'))) if accepted else None
        result['workflow_recovery'] = verify_recovery(task, runtime_dir / 'isolated' / 'common' / 'workspace', candidate)
    save_json(folder / 'result.json', result)
    return result


def solve_slot(directory, slot, protocol, node, driver_fd):
    request = prepare_slot(directory, slot, protocol)
    request['ownerFd'] = driver_fd
    folder = Path(directory) / 'runs' / slot['slot_id']
    with (folder / 'process.stdout').open('xb') as stdout, (folder / 'process.stderr').open('xb') as stderr:
        # The child inherits the driver lock, so a killed parent cannot start a
        # second driver while its already-dispatched Node process is still alive.
        child = subprocess.Popen([node, str(HERE / 'run-slot.mjs')], cwd=SOURCE, stdin=subprocess.PIPE,
                                 stdout=stdout, stderr=stderr, pass_fds=(driver_fd,))
        save_json(folder / 'process-owner.json', {'pid': child.pid})
        try:
            child.communicate(json.dumps(request).encode())
        except BaseException:
            child.terminate()
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            raise
    return collect_slot(directory, slot, protocol)


def run_campaign(directory=DEFAULT_ROOT, *, authorize_upstream=False, recover=False, node='node'):
    require(authorize_upstream is True, 'AUTHORIZATION_REQUIRED', 'Real campaign/probes require explicit execution authorization')
    directory = campaign_root(directory)
    document = json.loads(read_regular(directory / 'protocol.json'))
    queue = Campaign(directory, document)
    with queue.driver_lock() as driver_fd:
        state = queue.read()
        if state['status'] in {'stopped', 'completed'}:
            return state
        try:
            verify_freeze(directory, node=node)
        except Exception:
            queue.pause('protocol_drift')
            return queue.read()
        if state['active_slot']:
            queue.reconcile_interrupted(collect_slot(directory, state['active_slot'], document['protocol']))
        elif state['active_probe']:
            queue.reconcile_interrupted()
        state = queue.read()
        if state['status'] == 'paused' and not recover:
            return state
        if state['status'] in {'stopped', 'completed'}:
            return state
        try:
            checked = offline_preflight(directory, document, node)
        except Exception as error:
            queue.pause('protocol_drift' if getattr(error, 'code', '') in {'PROTOCOL_DRIFT', 'DIRTY_SOURCE'} else 'offline_preflight_failed')
            return queue.read()
        if recover:
            # A killed runtime can leave an uncertain tool/process lifetime. A new
            # connectivity phase cannot substitute for its durable drained capture.
            for result in queue.read()['results']:
                previous = result.get('runtime_directory')
                require(not previous or not Path(previous).exists() or (Path(previous) / 'final.json').is_file(),
                        'RECONCILE_REQUIRED', 'Previous owned runtime has no drained final capture')
            queue.begin_recovery(checked)
        while True:
            state = queue.read()
            if state['status'] in {'paused', 'stopped', 'completed'}:
                return state
            # Every dispatch, including a recovery probe, verifies the same freeze.
            try:
                verify_freeze(directory, node=node)
            except Exception:
                queue.pause('protocol_drift')
                return queue.read()
            if state['status'] in {'initial_connectivity', 'recovery_connectivity'}:
                probe = queue.claim_probe()
                try:
                    receipt = physical_probe(directory, probe)
                except Exception as error:
                    receipt = {'physical_requests': 1, 'success': False, 'usage': None, 'failure': {'code': getattr(error, 'code', 'PROBE_FAILED')}}
                queue.finish_probe(probe['probe_id'], receipt)
            elif state['status'] == 'awaiting_gate':
                queue.evaluate()
            else:
                slot = queue.claim_slot()
                try:
                    result = solve_slot(directory, slot, document['protocol'], node, driver_fd)
                except Exception as error:
                    result = collect_slot(directory, slot, document['protocol'])
                    result['failure'] = getattr(error, 'code', 'SLOT_STARTUP')
                queue.finish_slot(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'status', 'run', 'recover'))
    parser.add_argument('--root', default=str(DEFAULT_ROOT))
    parser.add_argument('--node', default='node')
    parser.add_argument('--qualification')
    parser.add_argument('--authorize-upstream', action='store_true')
    args = parser.parse_args()
    if args.action == 'freeze':
        require(args.qualification is not None, 'PREFLIGHT_REQUIRED', 'Qualification file is required')
        value = freeze(args.root, json.loads(read_regular(Path(args.qualification))), node=args.node)
    elif args.action == 'status':
        document = json.loads(read_regular(Path(args.root) / 'protocol.json'))
        value = Campaign(args.root, document).read()
    else:
        value = run_campaign(args.root, authorize_upstream=args.authorize_upstream, recover=args.action == 'recover', node=args.node)
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
