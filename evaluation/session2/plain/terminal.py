#!/usr/bin/env python3
"""Native ordinary DSH Session2 entry; offline fixture qualification only."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'evaluation' / 'four-harness-v1'))
from protocol import load_verified, claim_slot, exclusive_json
from workflow_runtime import certificate_contract, rendered_checker, CANDIDATE_SCHEMA
from gateway import Gateway, FixtureTransport, capability, responses_tools
from bridge import DirectCommonTools
from ledger import Ledger, OPTIMIZATION_BUDGET
from tools import CommonTools, ToolLimits
from native import isolated_environment, macos_fixture_profile, run_process
from state_guard import StateGuard

DSH = Path('/Users/zcy/.dsh/cli/lib/node_modules/@deepseek-ai/dsh')
MODULES = DSH / 'node_modules'
NODE = Path('/Users/zcy/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node').resolve()


def scoped(path, *, formal_output=False):
    path = Path(path)
    allowed = path.resolve().is_relative_to(HERE / '.offline')
    if formal_output:
        allowed = not path.resolve().is_relative_to(SOURCE)
    if not path.is_absolute() or path.is_symlink() or not allowed:
        raise ValueError('WRITE_SCOPE: use plain/ or lead-authorized session2/runs/')
    return path.resolve()


def save(path, value):
    exclusive_json(path, value)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance():
    paths = [DSH / 'package.json', HERE / 'runner.mjs', HERE / 'terminal.py']
    paths += [MODULES / '@deepseek-ai' / name / 'lib/index.js' for name in
        ('dsh-headless', 'dsh-agent', 'dsh-agent-loop', 'dsh-tools', 'dsh-llm',
         'dsh-llm-pi-ai', 'dsh-compaction-basic')]
    return [{'path': str(path), 'sha256': sha(path)} for path in paths]


def fixture_response(output=None, *, usage=True):
    value = {'id': 'fixture-response', 'object': 'response', 'model': 'gpt-6-astra',
             'status': 'completed', 'reasoning': {'effort': 'max'}, 'output': output or []}
    if usage:
        value['usage'] = {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110,
                          'input_tokens_details': {'cached_tokens': 20},
                          'output_tokens_details': {'reasoning_tokens': 4}}
    return value


def isolation_probe(prefix, env, work, outside):
    """Use a sibling canary within our write scope but outside child read roots."""
    outside.write_text('offline-canary-not-a-credential')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        code = """const fs=require('node:fs'),net=require('node:net');
let denied=false;try{fs.readFileSync(process.argv[1])}catch(e){denied=['EPERM','EACCES'].includes(e.code)}
if(!denied)process.exit(41);
const s=net.connect(Number(process.argv[2]),'127.0.0.1');
s.on('connect',()=>process.exit(42));s.on('error',e=>process.exit(['EPERM','EACCES'].includes(e.code)?0:43));
setTimeout(()=>process.exit(44),2000).unref();"""
        result = run_process([*prefix, NODE, '-e', code, outside, str(listener.getsockname()[1])],
                             env=env, cwd=work, timeout=5)
    return {'passed': result['returncode'] == 0, 'process': result,
            'file_denial': 'EPERM/EACCES required', 'network_denial': 'EPERM/EACCES required'}


def run_fixture(output, task_id, responses, *, registration_sha256=None, timeout=60, qualification_compact=False,
                live_transport=None, state_guard=None, slot_id=None):
    """Host-only primitives, no WorkflowRuntime or research state executor."""
    live = live_transport is not None
    output = scoped(output, formal_output=live)
    started_at = datetime.now(timezone.utc).isoformat()
    output.mkdir(mode=0o700)
    isolated = output / 'isolated'
    isolated.mkdir(mode=0o700)
    token, compaction_token = capability(), capability()
    env = isolated_environment(isolated, token=token)
    env['FOUR_HARNESS_COMPACTION_TOKEN'] = compaction_token
    env['DSH_HOME'] = str(isolated / 'home')
    card = certificate_contract(task_id)['task']
    public = {key: card[key] for key in ('id', 'statement', 'parameters', 'certificate')}
    inputs = output / 'inputs'
    inputs.mkdir()
    task_path = inputs / 'task.json'
    save(task_path, public)
    checker = output / 'checker.py'
    checker.write_bytes(rendered_checker(card))
    run_id = 'plain-' + output.name
    ledger = Ledger(output / 'ledger.sqlite')
    ledger.create_campaign(run_id, max_requests=120, max_tokens=None, scope='optimization')
    ledger.create_run(run_id, run_id, 'M', budget=OPTIMIZATION_BUDGET,
        frozen={'experiment_arm': 'plain', 'native_engine': 'DSH ReactLoopAgent',
                'registration_sha256': registration_sha256, 'task_id': task_id})
    host = CommonTools(run_id, output / 'common',
        materials={'task.json': {'path': task_path, 'sha256': sha(task_path)}}, checker_path=checker,
        limits=ToolLimits(cpu_seconds=None, timeout_seconds=None, artifact_bytes=1073741824))
    transport = live_transport if live else FixtureTransport(deepcopy(responses))
    audit = output / 'audit'
    audit.mkdir()
    def audit_sink(kind, request_id, value):
        save(audit / (request_id + '.' + kind + '.json'), value)
    gateway = Gateway(ledger, transport=transport,
        bindings={token: {'run_id': run_id, 'arm': 'M', 'role': 'worker'},
                  compaction_token: {'run_id': run_id, 'arm': 'M', 'role': 'compaction'}}, tools={run_id: host},
        allowed_tools={run_id: {'materials_list', 'materials_read', 'workspace_read', 'workspace_write', 'compute', 'check', 'submit'}},
        frozen_materials={run_id: host.material_inventory()}, candidate_schema=CANDIDATE_SCHEMA,
        inject_output_cap=True, audit_sink=audit_sink,
        execution=({'mode': 'authorized_optimization', 'arm': 'M', 'run_id': run_id,
                    'campaign_id': run_id, 'model_requests': 120, 'campaign_max_requests': 120} if live else None),
        state_guards=({run_id: state_guard} if live else {}))
    boundary_stop = threading.Event()
    def stop_boundary(reason):
        ledger.record(run_id, 'plain_state_stop', reason=reason)
        boundary_stop.set()
        gateway.cancel_event.set()
    artifact_guard = StateGuard([output], max_bytes=1073741824, on_stop=stop_boundary, interval=.25)
    registration_guard = state_guard
    class CombinedGuard:
        def check(self):
            artifact_guard.check()
            if registration_guard:
                registration_guard.check()
    gateway.state_guards[run_id] = CombinedGuard()
    artifact_guard.start()
    previous_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, lambda number, frame: stop_boundary('signal_' + str(number)))
    gateway.request_adapters[(run_id, 'worker')] = DirectCommonTools(gateway.manifest_tools(run_id))
    gateway.request_adapters[(run_id, 'compaction')] = DirectCommonTools(gateway.manifest_tools(run_id))
    gateway.start()
    route = gateway.url + '/M/worker'
    # Copy driver into the isolated readable root. Entire source/checker/ledger,
    # user's home and historical experiments remain outside that root.
    driver = isolated / 'runner.mjs'
    shutil.copyfile(HERE / 'runner.mjs', driver)
    tool_view = isolated / 'workflow_tool_view.mjs'
    shutil.copyfile(HERE.parents[2] / 'workflow_tool_view.mjs', tool_view)
    prompt = ('Construct and explicitly submit an exact certificate for the frozen public task. '
              'Preserve useful intermediate work in the isolated workspace.\n' + json.dumps(public, ensure_ascii=False))
    config = {'modules': str(MODULES), 'gateway': route, 'sessionId': run_id,
              'toolViewModule': tool_view.as_uri(),
              'events': str(isolated / 'native-events.jsonl'), 'prompt': prompt,
              'tools': [{k: v for k, v in tool.items() if k != 'type'} for tool in gateway.manifest_tools(run_id)],
              'qualificationCompact': qualification_compact}
    save(isolated / 'config.json', config)
    profile = macos_fixture_profile(isolated, readable=[MODULES, NODE.parent.parent], gateway_url=gateway.url)
    (isolated / 'sandbox.sb').write_text(profile)
    prefix = ['/usr/bin/sandbox-exec', '-f', str(isolated / 'sandbox.sb')]
    probe = isolation_probe(prefix, env, isolated, output / 'outside-canary')
    save(output / 'isolation-probe.json', probe)
    process = None
    started = time.monotonic()
    observer_done = threading.Event()
    def observe():
        last = 0
        next_time = started + 1800
        while not observer_done.wait(.25):
            count = ledger.snapshot(run_id)['requests']
            if count // 20 > last // 20 or time.monotonic() >= next_time:
                ledger.record(run_id, 'session2_observation', physical_requests=count,
                              elapsed_seconds=time.monotonic() - started, action='observe_only')
                last, next_time = count, time.monotonic() + 1800
    monitor = threading.Thread(target=observe, daemon=True)
    try:
        if not probe['passed']:
            raise RuntimeError('ISOLATION_PROBE_FAILED')
        monitor.start()
        process = run_process([*prefix, NODE, driver, isolated / 'config.json'], env=env, cwd=isolated,
            timeout=timeout, cancel_event=boundary_stop, capture_dir=output / 'captures')
        save(output / 'process.json', process)
    finally:
        observer_done.set()
        if monitor.is_alive():
            monitor.join(2)
        gateway.close()
        save(output / 'state-guard.json', artifact_guard.stop())
        save(output / 'ledger-snapshot.json', ledger.snapshot(run_id))
        save(output / 'ledger-events.json', ledger.events())
        save(output / 'physical-requests.json', getattr(transport, 'requests', []))
        save(output / 'provenance.json', provenance())
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    events_path = isolated / 'native-events.jsonl'
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    submitted, _, _ = host.submissions.inventory()
    receipts = [json.loads(host.submissions.read(name)) for name, _ in submitted if name.startswith('receipt-')]
    success = next((receipt for receipt in receipts if receipt.get('independent_check', {}).get('legal') is True
                    and receipt['independent_check'].get('target_met') is True
                    and receipt.get('verification_basis') == 'fresh_process_exact_audit_rerun'), None)
    result = {'independent_success': success, 'format': 'session2-plain-fixture-v1', 'experiment_arm': 'plain',
              'technical_ledger_arm': 'M', 'task_id': task_id, 'mode': 'real' if live else 'offline-fixture',
              'upstream_requests': ledger.snapshot(run_id)['requests'] if live else 0,
              'physical_fixture_requests': len(transport.requests) if not live else 0,
              'native_engine': next((e for e in events if e['type'] == 'engine_identity'), None),
              'native_terminal': next((e for e in reversed(events) if e['type'] == 'native_terminal'), None),
              'isolation_probe_passed': probe['passed'], 'returncode': process['returncode'] if process else None,
              'registration_sha256': registration_sha256, 'resource_scope': 'observed_not_equal_budget'}
    save(output / 'result.json', result)
    snapshot = ledger.snapshot(run_id)
    save(output / 'final.json', {'output': str(output), 'slot': slot_id, 'task': task_id,
         'arm': 'plain', 'identity': run_id, 'status': 'completed' if result['returncode'] == 0 else 'failed',
         'failure': None if result['returncode'] == 0 else result['native_terminal'],
         'start': started_at, 'finished': datetime.now(timezone.utc).isoformat(),
         'usage': snapshot, 'ledger_path': str(output / 'ledger.sqlite'),
         'result_path': str(output / 'result.json'), 'process_path': str(output / 'process.json')})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', required=True)
    parser.add_argument('--slot', required=True)
    parser.add_argument('--mode', choices=['fixture', 'real'], required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--responses')
    parser.add_argument('--authorize-upstream', action='store_true')
    args = parser.parse_args()
    live = args.mode == 'real'
    registration_path = Path(args.registration).resolve() if live else scoped(args.registration)
    output = scoped(args.output, formal_output=live)
    registration, slot, digest = load_verified(registration_path, args.slot, args.mode)
    if slot['arm'] != 'plain':
        parser.error('Expected a plain slot')
    if output.exists():
        parser.error('--output must be new')
    responses, transport, guard = [], None, None
    if live:
        if not args.authorize_upstream:
            parser.error('Real mode requires explicit --authorize-upstream')
        if args.responses:
            parser.error('Real mode rejects fixture responses')
        # Deliberately lazy: fixture execution never imports or reads credentials.
        from transport import HostTransport, load_host_credential
        credential = load_host_credential()  # fail before consuming the exclusive slot
        class RegistrationGuard:
            def check(self):
                _, _, current = load_verified(registration_path, args.slot, args.mode)
                if current != digest:
                    raise ValueError('REGISTRATION_DRIFT')
        guard = RegistrationGuard()
        guard.check()
        transport = HostTransport(auth_loader=lambda: credential, profile='workflow',
                                  timeout_profile='workflow', max_output_tokens=128000)
    else:
        if not args.responses:
            parser.error('--responses JSON array file is required for fixture mode')
        responses = json.loads(Path(args.responses).read_text())
        if type(responses) is not list:
            parser.error('Fixture responses must be a JSON array')
    claim_slot(registration_path, args.slot, digest, output)
    result = run_fixture(output, slot['task'], responses, registration_sha256=digest,
        live_transport=transport, state_guard=guard, slot_id=args.slot, timeout=None if live else 60)
    print(json.dumps(result))
    return 0 if result['returncode'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
