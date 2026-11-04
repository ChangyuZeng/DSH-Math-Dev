"""One-shot, human-authorized, non-target smoke. Formal execution is absent.

The caller freezes this source and offline evidence before run_campaign(). No
request is sent by import, freezing, construction, or plan preparation. A fresh
persistent output directory and a shared SQLite campaign prevent silent replay.
"""
from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import sys
import tempfile
import time

from cold_start import input_manifest, write_materials
from gateway import Gateway, capability, responses_tools, TOOL_NAMES
from ledger import Ledger, GateError, POLICY, ROLES, SMOKE_BUDGET, RESOURCE_POLICY, canonical, require
from native import run_process
from state_guard import StateGuard
from tools import CommonTools, ToolLimits
from transport import HostTransport

HERE = Path(__file__).resolve().parent
AUTHORIZATION = 'm02454'
CAMPAIGN = 'authorized-smoke-m02454'
AUTHORIZATION_ROOT = HERE.parents[1]/'dist'/'four-harness-authorizations'


def claim_authorization(manifest, output):
    # The authorization is session-global, independent of caller-selected output.
    # A crash or unknown receipt retains this marker; there is no reset command.
    AUTHORIZATION_ROOT.mkdir(parents=True,exist_ok=True)
    require(not any(p.is_symlink() for p in [AUTHORIZATION_ROOT,*AUTHORIZATION_ROOT.parents]),
            'ISOLATION','Authorization registry must not contain symlink ancestors')
    path=AUTHORIZATION_ROOT/(AUTHORIZATION+'.json')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        raise GateError('NO_REPLAY','This authorization was already claimed; a new output cannot reset its campaign') from None
    with os.fdopen(fd,'w') as stream:
        stream.write(canonical({'authorization':AUTHORIZATION,'campaign_id':CAMPAIGN,
            'manifest_sha256':hashlib.sha256(canonical(manifest).encode()).hexdigest(),
            'output':str(output),'claimed_at':time.time(),'automatic_replay_allowed':False})+'\n')
        stream.flush();os.fsync(stream.fileno())
    directory_fd=os.open(AUTHORIZATION_ROOT,os.O_RDONLY)
    try:os.fsync(directory_fd)
    finally:os.close(directory_fd)


def native_intrinsic_routes(arm, run_id, roles):
    """The same role-bound native X tools serve fixture and authorized startup."""
    if arm != 'X':
        return {}
    names = {'explorer': {'submit_notes', 'read_notes'},
             'coordinator': {'submit_coordination', 'read_notes'},
             'verifier': {'submit_verdict', 'submit_statement', 'submit_proof', 'read_notes'}}
    return {(run_id, role): set(names.get(role, set())) for role in roles}


def source_hashes():
    files = [p for p in HERE.iterdir() if p.is_file() and p.suffix in {'.py','.mjs'}]
    files += [p for p in (HERE/'patches').iterdir() if p.is_file()]
    return {str(p.relative_to(HERE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def execution_inventory(configuration):
    """Verify external executable/source/dependency bytes before any auth claim."""
    from native import source_identity,rethlas_mcp_dependencies
    from x_live import verify_x_build
    require(type(configuration) is dict and type(configuration.get('sources')) is dict,
            'CONFIGURATION','Complete pinned source configuration required')
    identities={arm:source_identity(Path(configuration['sources'][arm]),arm) for arm in ('C','X','R')}
    binaries={}
    for name in ('node','python','codex','bun'):
        path=Path(configuration[name]).resolve()
        require(path.is_file(),'RUNTIME','Frozen executable is absent')
        binaries[name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    cli=Path(configuration['codex']).resolve()
    binary=cli.parent.parent/'node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex'
    require(binary.is_file(),'RUNTIME','Frozen native Codex binary is absent')
    binaries['codex_native']={'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
    _,_,x_receipt=verify_x_build(Path(configuration['xean_build']))
    m_files=[HERE.parents[1]/name for name in ('schema.py','backend.py','workflow_runner.mjs','workflow_store.py',
                    'workflow_context.py','workflow_service.py','workflow_strategy.py')]
    m_files.extend(path for path in (HERE.parents[1]/'runtime/p2c').rglob('*')
                   if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc')
    require(all(path.is_file() and not path.is_symlink() for path in m_files),'SOURCE_DRIFT','M runtime source is absent or linked')
    return {'sources':identities,'binaries':binaries,'xean_receipt_sha256':x_receipt,
            'rethlas_dependencies':rethlas_mcp_dependencies(Path(configuration['python_site'])),
            'M_runtime_sources':{str(path.relative_to(HERE.parents[1])):hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted(m_files)}}


def freeze(*, offline_evidence, configuration):
    require(offline_evidence.get('passed') is True and offline_evidence.get('real_model_requests') == 0,
            'OFFLINE_GATES', 'Successful offline evidence is required before freezing smoke')
    return {'format':'four-harness-authorized-smoke-v1', 'scope':'smoke',
            'authorization':AUTHORIZATION, 'campaign_id':CAMPAIGN,
            'token_budget_authorization':'smoke_token_ceiling_revision:explicitly_approved',
             'model_policy':deepcopy(POLICY), 'per_run_budget':deepcopy(SMOKE_BUDGET),
            'max_requests':8, 'max_tokens':524288, 'arm_order':['C','X','R','M'],
            'replacement_runs':False, 'formal_execution_authorized':False,
            'cold_input':input_manifest('smoke'), 'resource_policy':deepcopy(RESOURCE_POLICY),
            'configuration':deepcopy(configuration), 'offline_evidence':deepcopy(offline_evidence),
            'source_hashes':source_hashes(),'execution_inventory':execution_inventory(configuration)}


def validate(manifest):
    require(manifest.get('format') == 'four-harness-authorized-smoke-v1' and
            manifest.get('scope') == 'smoke' and manifest.get('authorization') == AUTHORIZATION and
            manifest.get('campaign_id') == CAMPAIGN and manifest.get('model_policy') == POLICY and
            manifest.get('per_run_budget') == SMOKE_BUDGET and manifest.get('max_requests') == 8 and
            manifest.get('token_budget_authorization') == 'smoke_token_ceiling_revision:explicitly_approved' and
            manifest.get('max_tokens') == 524288 and manifest.get('arm_order') == ['C','X','R','M'] and
            manifest.get('replacement_runs') is False and manifest.get('formal_execution_authorized') is False,
            'SMOKE_FREEZE', 'Only the approved bounded smoke campaign is executable')
    require(manifest.get('cold_input') == input_manifest('smoke'), 'COLD_INPUT', 'Frozen smoke input changed')
    require(manifest.get('source_hashes') == source_hashes(), 'SOURCE_DRIFT', 'Smoke source changed after freezing')
    require(manifest.get('execution_inventory') == execution_inventory(manifest['configuration']),
            'RUNTIME_DRIFT','External runtime/source/dependency bytes changed after freezing')
    require(manifest.get('offline_evidence',{}).get('passed') is True and
            manifest['offline_evidence'].get('real_model_requests') == 0,
            'OFFLINE_GATES', 'Offline evidence is missing')


def write_json(path, value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(json.dumps(value, ensure_ascii=False,allow_nan=False,indent=2)+'\n')
        stream.flush();os.fsync(stream.fileno())


def json_objects(value):
    if isinstance(value,dict):
        yield value
        for item in value.values():yield from json_objects(item)
    elif isinstance(value,list):
        for item in value:yield from json_objects(item)
    elif isinstance(value,str) and value.lstrip().startswith(('{','[')):
        try:parsed=json.loads(value)
        except ValueError:return
        yield from json_objects(parsed)


def assess(native, failure, snapshot, events, guard_report, audit, native_evidence=None):
    def sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
    receipts={event['tool']['request_id']:{event['tool']['result_sha256']} for event in events
        if event['type']=='tool_finished' and event.get('tool',{}).get('ok') is True
        and event['tool'].get('tool') in {'materials.list','materials.read','workspace.read','workspace.write','compute'}
        and event['tool'].get('result_sha256')}
    for event in events:
        if (event['type']=='common_reply_projection' and event.get('full_result_sha256') in
                receipts.get(event.get('tool_request_id'),set())):
            receipts[event['tool_request_id']].add(event['sent_result_sha256'])
    returned=any(obj.get('metrics',{}).get('result_sha256') in
            receipts.get(obj.get('metrics',{}).get('request_id'),set()) and
            sha({key:value for key,value in obj.items() if key!='metrics'})==obj['metrics']['result_sha256']
        for payload in audit.get('request',{}).values() for obj in json_objects(payload.get('input',[]))
        if isinstance(obj.get('metrics'),dict))
    # Native MCP prefixes need not be JSON. Bind the already verified native
    # envelope to its exact call ID and output bytes in an audited continuation.
    returned=returned or any(event.get('output_receipt_verified') is True and
            event.get('model_tool_result_sha256') in receipts.get(event.get('tool_request_id'),set()) and
            any(item.get('type')=='function_call_output' and item.get('call_id')==event['call_id'] and
                sha(item.get('output'))==event['result_sha256']
                for payload in audit.get('request',{}).values() for item in payload.get('input',[]) if isinstance(item,dict))
        for event in events if event['type']=='native_function_output')
    bounded_stop=bool(native_evidence and native_evidence.get('arm')=='R' and
        native_evidence.get('bounded_stop_qualified') is True and native is not None and native.get('returncode')==1)
    checks={'native_exited_cleanly':native is not None and (native.get('returncode')==0 or bounded_stop) and
                not native.get('timed_out') and not native.get('cancelled'),
            'host_failure_absent':failure is None,'state_guard_clean':guard_report['violation'] is None,
            'run_not_stopped_before_close':snapshot['terminal'] is None,
            'provider_receipts_complete':snapshot['requests']>0 and snapshot['accounting_complete'] and snapshot['detail_complete'],
            'common_tool_succeeded':bool(receipts),'tool_receipt_in_native_continuation':returned}
    # Compatibility success and native task completion are explicit. Arithmetic
    # correctness is reviewed from the preserved native proposal/report; it is
    # never inferred from exit zero or model metadata containing a digit.
    return {'checks':checks,'compatibility_passed':all(checks.values()),
            'arithmetic_result_review':'required','smoke_passed':False,
            'status':'awaiting_result_review' if all(checks.values()) else 'incomplete'}


def run_campaign(manifest, output):
    validate(manifest)
    output = Path(output).absolute()
    require(not output.exists(), 'NO_REPLAY', 'Smoke output already exists; no automatic resume or replay')
    claim_authorization(manifest,output)
    output.mkdir(parents=True,mode=0o700)
    write_json(output/'frozen.json',manifest)
    ledger = Ledger((output/'ledger.sqlite').resolve())
    ledger.create_campaign(CAMPAIGN, max_requests=8, max_tokens=524288, scope='smoke')
    cfg = manifest['configuration']
    results = []
    for arm in manifest['arm_order']:
        run_id = 'smoke-'+arm
        try:
            ledger.create_run(run_id,CAMPAIGN,arm,budget=SMOKE_BUDGET,frozen={'manifest_sha256':hashlib.sha256(canonical(manifest).encode()).hexdigest()})
        except GateError as error:
            results.append({'arm':arm,'status':'not_started','reason':error.code})
            continue
        # Short fresh C/R paths avoid paying request context for long host paths.
        # They carry no reused state and remain protected by the same outer jail.
        if arm in {'C','R'} and Path('/private/tmp').is_dir():
            root=Path(tempfile.mkdtemp(prefix=arm+'-',dir='/private/tmp')).resolve()
        else:
            root=Path(tempfile.mkdtemp(prefix='dsh-four-smoke-'+arm+'-')).resolve()
        execution = dict(mode='authorized_smoke',arm=arm,run_id=run_id,campaign_id=CAMPAIGN,
                         model_requests=2,campaign_max_requests=8)
        materials = write_materials(root/'inputs',scope='smoke')
        tools = CommonTools(run_id,root/'common',materials=materials,
            limits=ToolLimits(timeout_seconds=10,artifact_bytes=16777216))
        cancelled=threading.Event()
        gateway=None
        def stop_state(reason,r=run_id):
            cancelled.set()
            if gateway is not None:gateway.cancel_event.set()
            ledger.stop(r,reason)
        audit_root=output/(arm+'-audit')
        audit_root.mkdir(mode=0o700)
        guard = StateGuard([root,audit_root],max_bytes=16777216,on_stop=stop_state)
        tokens = {role:capability() for role in sorted(ROLES[arm])}
        bindings = {token:{'run_id':run_id,'arm':arm,'role':role,
                          'require_native_metadata':arm in {'C','R'}} for role,token in tokens.items()}
        bridges = {}
        allowed = set(TOOL_NAMES)-{'check','submit'}
        if arm in {'C','R'}:
            from codex_bridge import NativeDirectBridge
            bridges = {(run_id,role):NativeDirectBridge(arm,role,responses_tools('smoke')) for role in tokens}
        intrinsic = native_intrinsic_routes(arm,run_id,tokens)
        intrinsic.update({(run_id,role):set(bridge.intrinsic_names) for (_,role),bridge in bridges.items()})
        def evidence_sink(evidence, rid=run_id):
            ledger.record(rid,'transport_receipt',**evidence)
        audit={'request':{},'response':{}}
        def audit_sink(kind,request_id,value):
            audit.setdefault(kind,{})[request_id]=value
            target=audit_root/(request_id+'.'+kind+'.json')
            with os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
                stream.write(canonical(value)+'\n');stream.flush();os.fsync(stream.fileno())
            guard.check()
            ledger.record(run_id,'payload_audited',request_id=request_id,payload_kind=kind,
                          sha256=hashlib.sha256(canonical(value).encode()).hexdigest())
        transport = HostTransport(evidence_sink=evidence_sink)
        gateway = None
        native = None
        plan = None
        native_evidence = None
        failure = None
        before_close = None
        started = time.monotonic()
        try:
            gateway = Gateway(ledger,transport=transport,bindings=bindings,tools={run_id:tools},
                allowed_tools={run_id:allowed},intrinsic_tools=intrinsic,request_adapters=bridges,
                inject_output_cap=True,execution=execution,state_guards={run_id:guard},audit_sink=audit_sink).start()
            if arm in {'C','R'}:
                from codex_bridge import prepare_codex_smoke
                plan = prepare_codex_smoke(root/'native',arm=arm,source_root=Path(cfg['sources'][arm]),
                    node=cfg['node'],codex=cfg['codex'],python=cfg['python'],gateway_url=gateway.url,
                    role_tokens=tokens,execution=execution,codex_source_root=Path(cfg['sources']['C']),
                    python_site=Path(cfg['python_site']) if cfg.get('python_site') else None)
                native_adapters=plan.get('request_adapters',{})
                native_intrinsics=plan.get('intrinsic_names',{})
                require(set(native_adapters)==set(tokens) and set(native_intrinsics)==set(tokens),
                        'ROLE_ROUTE','Native plan must bind every frozen role')
                gateway.request_adapters.update({(run_id,role):adapter for role,adapter in native_adapters.items()})
                gateway.intrinsic_tools.update({(run_id,role):set(names) for role,names in native_intrinsics.items()})
            else:
                from x_live import prepare_x_smoke, prepare_m_smoke
                if arm == 'X':
                    plan = prepare_x_smoke(root/'native',source_copy=Path(cfg['xean_build']),bun=cfg['bun'],
                        gateway_url=gateway.url,role_tokens={role:tokens[role] for role in ('explorer','coordinator','verifier')},execution=execution)
                else:
                    plan = prepare_m_smoke(root/'native',node=cfg['node'],python=cfg['python'],
                        gateway_url=gateway.url,token=tokens['worker'],execution=execution)
            require(all(Path(p).resolve().is_relative_to(root/'native') for p in plan['writable_roots']),
                    'ISOLATION','Native writable roots must exclude the host common tool state')
            safe_plan={key:plan[key] for key in ('metadata','catalog','source','config_sha256','sandbox_sha256',
                'runtime_version','budget','managed_preferences_probe','isolation_probe_passed',
                'native_loop_modified','native_termination_contract','writable_roots') if key in plan}
            write_json(audit_root/'launch.json',safe_plan)
            guard.start()
            remaining = SMOKE_BUDGET['wall_seconds']-(time.monotonic()-started)
            require(remaining>0,'WALL_BUDGET','Preparation exhausted the run wall clock')
            native = run_process(plan['command'],env=plan['env'],cwd=plan['cwd'],timeout=remaining,cancel_event=cancelled)
            if arm in {'C','R'}:
                from codex_bridge import collect_native_evidence
                native_evidence=collect_native_evidence(plan,native,ledger=ledger,run_id=run_id,
                    evidence_path=audit_root/'native-termination.json')
            guard.check()
        except Exception as error:
            failure = {'code':getattr(error,'code','HOST_FAILURE'),'type':type(error).__name__}
            ledger.stop(run_id,failure['code'])
        finally:
            before_close = ledger.snapshot(run_id)
            if gateway is not None: gateway.close()
            guard_report = guard.stop()
        events = [event for event in ledger.events() if event.get('run_id') == run_id]
        # All provider receipts and failed starts remain in the ledger. Unknowns
        # close the campaign, and every later arm is reported not_started.
        record = {'arm':arm,'run_id':run_id,'root':str(root),'scope':'smoke',
                  'native':native,'failure':failure,'before_close':before_close,
                  'ledger':ledger.snapshot(run_id),'events':events,'state_guard':guard_report,
                  'budgeted_requests':before_close['requests'],
                  'confirmed_http_dispatches':sum(event['type']=='transport_receipt' for event in events),
                  'unknown_usage_is_zero':False,
                  **assess(native,failure,before_close,events,guard_report,audit,native_evidence),
                   'native_evidence':native_evidence,
                  'formal_execution_authorized':False}
        # Keep host capability-bearing env and command out of persisted reports.
        if plan is not None:
            evidence_path = plan.get('evidence_path')
            if evidence_path and Path(evidence_path).is_file():
                try: record['native_evidence'] = json.loads(Path(evidence_path).read_text())
                except (OSError,ValueError): record['native_evidence_unreadable'] = True
        write_json(output/(arm+'.json'),record)
        results.append({key:record[key] for key in ('arm','status','budgeted_requests','confirmed_http_dispatches','failure')})
    summary = {'campaign_id':CAMPAIGN,'scope':'smoke','arms':results,
               'formal_execution_authorized':False,'no_automatic_replay':True}
    write_json(output/'summary.json',summary)
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        result=run_campaign(json.loads(Path(args.manifest).read_text()),args.output)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except GateError as error:
        print(json.dumps({'error':error.code,'message':str(error)}))
        return 2

if __name__=='__main__':raise SystemExit(main())
