"""Small serial A/B driver reusing four-harness-v1. No replay or extra arms."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parents[1]
ADAPTER=SOURCE/'evaluation/four-harness-v1'
sys.path[:0]=[str(ADAPTER),str(HERE)]
from certificates import cards, canonical, check
from ledger import Ledger, OPTIMIZATION_BUDGET, POLICY, GateError, require
from gateway import Gateway, TOOL_NAMES, capability
from tools import CommonTools, ToolLimits
from state_guard import StateGuard
from transport import HostTransport
from native import run_process, isolation_probe
from x_live import prepare_m_optimization

ROOT=SOURCE.parent
NODE=Path(os.environ.get('DSH_MATH_NODE','/Users/zcy/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node')).resolve()

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
def emit(**value):print(json.dumps(value,ensure_ascii=False),flush=True)
def git(*args):return subprocess.check_output(['git',*args],cwd=SOURCE,text=True).strip()
def inventory():
    names=git('ls-files').splitlines()
    return {n:digest(SOURCE/n) for n in names if (SOURCE/n).is_file()}
def verify_freeze():
    p=json.loads((ROOT/'protocol.json').read_text())
    receipt=json.loads((ROOT/'protocol-freeze.json').read_text())
    require(receipt['sha256']==digest(ROOT/'protocol.json'),'PROTOCOL_DRIFT','Frozen protocol bytes changed')
    require(p['source_commit']==git('rev-parse','HEAD'),'SOURCE_DRIFT','Frozen source commit changed')
    require(p['source_inventory']==inventory(),'SOURCE_DRIFT','Tracked source changed')
    require(p['cards_sha256']==digest(ROOT/'tasks.json'),'TASK_DRIFT','Task cards changed')
    return p

def freeze():
    require(not git('status','--porcelain'),'DIRTY_SOURCE','Commit all protocol and source code before freeze')
    ts=cards();save(ROOT/'tasks.json',ts)
    rng=random.Random(91720261105);schedule=[]
    for split in ('dev','holdout'):
        blocks=[(t['id'],r) for t in ts if t['split']==split for r in range(1,4)]
        rng.shuffle(blocks)
        # Exactly nine AB and nine BA dev blocks. Holdout first-arm imbalance one block.
        orders=['AB','BA']*(len(blocks)//2)+(['AB'] if len(blocks)%2 else [])
        rng.shuffle(orders)
        for (task,repeat),order in zip(blocks,orders):
            for config in order:
                schedule.append({'ordinal':len(schedule)+1,'task':task,'split':split,'repeat':repeat,
                    'config':config,'run_id':f'{task}-r{repeat}-{config}','block_order':order})
    protocol={'version':1,'frozen_at_unix':time.time(),'source_commit':git('rev-parse','HEAD'),
       'baseline_A_commit':git('rev-parse','HEAD'),'initial_P0_anchor_commit':git('rev-parse','82e400b'),
       'baseline_A_definition':'Frozen source commit with full context policy; includes common P0 SSE evidence addendum before all solves.',
       'source_inventory':inventory(),'cards_sha256':digest(ROOT/'tasks.json'),
       'model':POLICY,'budget':OPTIMIZATION_BUDGET,'physical_request_limit':120,
       'time_limit':None,'cumulative_token_limit':None,'model_seed':None,'model_seed_reason':'Provider API exposes no supported model seed.',
       'generation_seed':20261105,'schedule_seed':91720261105,'serial':True,'clean_session_each_run':True,
       'A':{'context_policy':'full'},'B':{'context_policy':'omit-host-metrics'},
       'single_factor':'Remove only root host metrics from common-tool replies sent to model; retain full host logs and all mathematical results/errors.',
       'trigger_evidence':'Host tool reply always includes root metrics; frozen 80-step offline fixture demonstrates >16000 redundant context bytes without mathematical-field changes.',
       'candidate_gap_inspection':'Existing submit content hashes and native operation receipts already persist/reconcile; no second candidate mechanism added.',
       'schedule':schedule,'default_development_runs':36,'conditional_holdout_runs':18,'connectivity_limit':4,
       'promotion':{'require_equal_checks_and_isolation':True,'success_B_gte_A':True,'infra_B_lte_A':True,
          'benefit':'Strictly more formal successes, OR paired-success median token saving >=0.15 over >=3 tasks. No alternative trigger-error promotion for this telemetry-only B.',
          'require_B_trigger':True,'unknown_usage':'No imputation; cost criterion uses only fully accounted matched successes.',
          'tie_or_no_trigger':'Stop after 36. No tuning, replacement B, extra repeats or reserve consumption.'},
       'termination':['first independently valid explicit submit','native stop','120 physical reservations including failures/retries','unrecoverable error','observed violation','user cancel'],
       'success_policy':'check calls, final text and posthoc workspace constructions never count as formal submit success.',
       'no_replay':'Every started solve retained. A zero-dispatch infrastructure failure may be repaired in same logical run only with original timestamps/costs retained; default driver does not auto-repair.',
       'holdout_blinding':'No target positive or holdout solve log consumed by implementation; only aggregate gating and independent certificate results.',
       'reserve_policy':'Only pre-run invalidity replacement; no automatic replacement.',
       'dependencies':{'node':subprocess.check_output([str(NODE),'--version'],text=True).strip(),'python':sys.version,'lockfile_sha256':digest(SOURCE/'pnpm-lock.yaml')},
       'proof_scope':'Finite executable certificates for these synthetic tasks; no claim of general mathematical proof.'}
    save(ROOT/'protocol.json',protocol)
    save(ROOT/'protocol-freeze.json',{'sha256':digest(ROOT/'protocol.json'),'source_commit':protocol['source_commit'],'physical_requests':0})
    emit(frozen=True,source_commit=protocol['source_commit'],protocol_sha256=digest(ROOT/'protocol.json'))

def task_checker(card,dest):
    content=(HERE/'certificates.py').read_text()
    content=content.replace('TASK = None','TASK = '+repr(card),1)
    dest.write_text(content)
    return dest

def semantic_digest(card,candidate):
    normalized=candidate
    values=candidate.get('values') if isinstance(candidate,dict) else None
    if card['kind'] in ('projective_plane','domino') and isinstance(values,list) and len(values)%2==0 and all(type(x) is int for x in values):
        pairs=sorted(tuple(sorted(values[i:i+2])) for i in range(0,len(values),2))
        normalized={'values':[x for pair in pairs for x in pair]}
    return hashlib.sha256(canonical(normalized).encode()).hexdigest()

def bootstrap(card):
    statement=card['statement']+'\nPublic parameters: '+canonical(card['parameters'])+'\n'+card['certificate']
    statement+='\nUse the native tools. You must explicitly call submit with a complete candidate to obtain formal success. check only gives feedback. Save useful work in the workspace. Network and historical runs are unavailable.'
    problem={'title':card['id']+' finite certificate task','statement':statement,'quantifiers':card['quantifiers'],
       'domain':card['domain'],'conventions':[],'target_type':'construct','parent':None,'relation':'',
       'completion_criteria':['Submit an independently valid certificate meeting every stated condition.'],
       'sources':[card['source']],'knowledge_status':'synthetic_fixture'}
    return {'problem':problem,'stageGoal':'Construct and explicitly submit the finite certificate; retain intermediate work in the isolated workspace.'}

def execute(row,protocol, *, offline_responses=None):
    ident=row['run_id'];dest=ROOT/'runs'/ident
    require(not dest.exists(),'NO_REPLAY','Logical run already exists; do not reset or replay it')
    dest.mkdir(parents=True,mode=0o700)
    started=time.monotonic();started_unix=time.time()
    card=next(t for t in json.loads((ROOT/'tasks.json').read_text()) if t['id']==row['task'])
    root=Path(tempfile.mkdtemp(prefix='dshmath-'+ident+'-')).resolve()
    audit=dest/'audit';audit.mkdir(mode=0o700)
    checker=task_checker(card,dest/'checker.py')
    public=canonical({'id':card['id'],'statement':card['statement'],'parameters':card['parameters'],'certificate':card['certificate']}).encode()
    inputs=root/'inputs';inputs.mkdir();(inputs/'task.json').write_bytes(public)
    ledger=Ledger(dest/'ledger.sqlite');campaign='single-'+ident
    ledger.create_campaign(campaign,max_requests=120,max_tokens=None,scope='optimization')
    ledger.create_run(ident,campaign,'M',budget=OPTIMIZATION_BUDGET,frozen={'model':POLICY,'task_sha256':card['sha256'],'config':row['config']})
    host=CommonTools(ident,root/'common',materials={'task.json':{'path':inputs/'task.json','sha256':hashlib.sha256(public).hexdigest()}}, 
        limits=ToolLimits(cpu_seconds=None,timeout_seconds=None,artifact_bytes=1073741824),checker_path=checker)
    save(dest/'location.json',{'root':str(root),'row':row,'started_at_unix':started_unix,'started_monotonic':started,
        'task_sha256':card['sha256'],'checker_sha256':digest(checker),'source_commit':protocol['source_commit']})
    cancel=threading.Event();done=threading.Event();solved=threading.Event();lock=threading.RLock()
    candidates=[];tool_records=[];gw=None;plan=None;native=None;failure=None;isolated=False
    def stop(reason):
        cancel.set()
        if gw:gw.cancel_event.set()
        ledger.stop(ident,reason)
    guard=StateGuard([root,audit],max_bytes=1073741824,on_stop=stop,interval=.25)
    def audit_sink(kind,request_id,value):
        save(audit/(request_id+'.'+kind+'.json'),value)
        ledger.record(ident,'payload_audited',request_id=request_id,payload_kind=kind,sha256=hashlib.sha256(canonical(value).encode()).hexdigest())
    original=host.call
    def observed(self,name,args,**kwargs):
        stamp=time.time();elapsed=time.monotonic()-started
        reply=original(name,args,**kwargs)
        with lock:
            record={'tool':name,'arguments':args,'reply':reply,'elapsed_seconds':elapsed,'unix_time':stamp}
            tool_records.append(record);save(dest/'tools'/f'{len(tool_records):06d}.json',record)
            if name in ('check','submit'):
                raw=canonical(args.get('candidate')).encode();value=reply.get('result',{})
                verdict=value.get('independent_check',value)
                candidate={'source':name,'candidate':args.get('candidate'),'raw_sha256':hashlib.sha256(raw).hexdigest(),
                    'semantic_sha256':semantic_digest(card,args.get('candidate')),'checker_sha256':digest(checker),'check':verdict,
                    'raw_bytes':len(raw),'raw_encoding':'host canonical JSON; original model function-call argument bytes remain in audited provider response',
                    'tool_request_id':reply.get('metrics',{}).get('request_id'),
                    'receipt':value,'elapsed_seconds':time.monotonic()-started,'tool_started_elapsed_seconds':elapsed,
                    'unix_time':time.time(),'parent_request_id':kwargs.get('request_id')}
                candidates.append(candidate);save(dest/'candidates'/f'{len(candidates):06d}.json',candidate)
                # Only successful explicit submit with independent fresh-process receipt can stop successfully.
                if name=='submit' and reply.get('ok') and verdict.get('legal') is True and verdict.get('target_met') is True:
                    solved.set();ledger.stop(ident,'first_independent_submit_success')
        return reply
    host.call=types.MethodType(observed,host)
    def monitor():
        previous=0
        while not done.wait(.25):
            try:
                if solved.is_set():stop('first_independent_submit_success');return
                snap=ledger.snapshot(ident)
                if snap['terminal']:cancel.set();return
                elapsed=time.monotonic()-started
                if elapsed-previous>=30:
                    emit(run=ident,phase='running',requests=snap['requests'],known_tokens=snap['known_total_tokens'],total_tokens=snap['total_tokens'],elapsed=round(elapsed,1));previous=elapsed
            except Exception:stop('monitor_failure');return
    worker=threading.Thread(target=monitor,daemon=True)
    execution={'mode':'authorized_optimization','arm':'M','run_id':ident,'campaign_id':campaign,'model_requests':120,'campaign_max_requests':120}
    token=capability()
    try:
        if offline_responses is None:
            transport=HostTransport(profile='optimization',max_output_tokens=128000,response_timeout=180,
                evidence_sink=lambda value:ledger.record(ident,'transport_receipt',**value))
        else:
            from gateway import FixtureTransport
            transport=FixtureTransport(offline_responses)
        schema={'type':'object','properties':{'values':{'type':'array','items':{'type':'integer'}}},'required':['values'],'additionalProperties':False}
        gw=Gateway(ledger,transport=transport,bindings={token:{'run_id':ident,'arm':'M','role':'worker'}},
            tools={ident:host},allowed_tools={ident:set(TOOL_NAMES)},inject_output_cap=True,execution=execution if offline_responses is None else None,
            state_guards={ident:guard},frozen_materials={ident:host.material_inventory()},candidate_schema=schema,
            audit_sink=audit_sink,drain_timeout=30).start()
        plan=prepare_m_optimization(root/'native',node=NODE,python=Path(sys.executable).resolve(),gateway_url=gw.url,
            token=token,execution=execution,bootstrap=bootstrap(card),context_policy=protocol[row['config']]['context_policy'],offline=offline_responses is not None)
        plan['metadata']['state_bytes_limit']=1073741824
        isolation_probe(plan['command'][:3],node=NODE,env=plan['env'],work=plan['cwd']);isolated=True
        save(dest/'launch.json',{'metadata':plan['metadata'],'writable_roots':plan['writable_roots'],'task_sha256':card['sha256']})
        guard.start();worker.start();emit(run=ident,phase='native_started',config=row['config'])
        native=run_process(plan['command'],env=plan['env'],cwd=plan['cwd'],timeout=None,cancel_event=cancel,capture_dir=dest/'capture')
        native.pop('stdout',None);native.pop('stderr',None)
    except Exception as exc:
        failure={'code':getattr(exc,'code','HOST_FAILURE'),'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
        ledger.stop(ident,failure['code'])
    finally:
        done.set()
        if worker.is_alive():worker.join(2)
        current=ledger.snapshot(ident)
        if current['terminal'] is None:
            ledger.stop(ident,'physical_request_limit' if current['requests']>=120 else
                        'native_finished' if native and native['returncode']==0 else 'native_error')
        if gw:
            try:gw.close()
            except Exception as exc:
                failure=failure or {'code':'GATEWAY_CLEANUP','message':str(exc)}
        guard_report=guard.stop()
    snap=ledger.snapshot(ident);events=ledger.events();evidence=None
    if plan and Path(plan['evidence_path']).is_file():
        evidence=json.loads(Path(plan['evidence_path']).read_text());save(dest/'native-evidence.json',evidence)
    if snap['requests']==0 and native and native['returncode']!=0:
        failure=failure or {'code':(evidence or {}).get('error',{}).get('code','ZERO_REQUEST_NATIVE_FAILURE'),
            'message':'Native entry failed before its first physical reservation; preserve slot and halt pending runs.'}
    # Posthoc validity is reported separately and cannot promote formal success.
    posthoc=[];seen={c['raw_sha256'] for c in candidates}
    text_sources=[]
    for path in sorted(audit.glob('*.partial_response.json')):
        grouped={}
        for event in json.loads(path.read_text()).get('output_events',[]):
            if event['type'] in ('response.output_text.delta','response.function_call_arguments.delta'):
                key=(event['type'],event.get('item_id'),event.get('output_index'),event.get('content_index'))
                grouped[key]=grouped.get(key,'')+event.get('delta','')
            for field in ('text','arguments'):
                if isinstance(event.get(field),str):text_sources.append((str(path),event[field]))
            if isinstance(event.get('item'),dict):text_sources.append((str(path),canonical(event['item'])))
        text_sources.extend((str(path),text) for text in grouped.values())
    for path in sorted(audit.glob('*.response.json')):
        response=json.loads(path.read_text())
        for item in response.get('output',[]):
            if item.get('type')=='message':
                text_sources.extend((str(path),part.get('text','')) for part in item.get('content',[]) if part.get('type')=='output_text')
    for path in (root/'common'/'workspace').rglob('*.json'):
        if path.is_file() and path.stat().st_size<1048576:text_sources.append((str(path),path.read_text(errors='replace')))
    decoder=json.JSONDecoder()
    for source,text in text_sources:
        for pos,ch in enumerate(text):
            if ch!='{':continue
            try:value,_=decoder.raw_decode(text[pos:])
            except ValueError:continue
            if type(value) is not dict or set(value)!={'values'}:continue
            key=hashlib.sha256(canonical(value).encode()).hexdigest()
            if key in seen:continue
            seen.add(key)
            try:verdict=check(card,value)
            except (ValueError,TypeError,IndexError,KeyError):continue
            if verdict.get('target_met'):posthoc.append({'source':source,'candidate':value,'sha256':key,'check':verdict})
    save(dest/'posthoc.json',posthoc)
    usage=[json.loads(r['usage']) for r in snap['request_states'] if r['usage']]
    full=snap['accounting_complete'];tool_errors=sum(not r['reply'].get('ok') for r in tool_records)
    module_events=(evidence or {}).get('module_events',[])
    successes=[c for c in candidates if c['source']=='submit' and c['check'].get('legal') is True and c['check'].get('target_met') is True]
    terminal=snap['terminal'];infra=bool(failure) or terminal not in ('first_independent_submit_success','native_finished','native_error','physical_request_limit') and not solved.is_set()
    result={**row,'source_commit':protocol['source_commit'],'task_sha256':card['sha256'],'family':card['family'],
        'checker_sha256':digest(checker),'model':POLICY,'started_at_unix':started_unix,'ended_at_unix':time.time(),
        'wall_seconds':time.monotonic()-started,'formal_success':bool(successes),'posthoc_success':bool(posthoc),
        'construction_valid':bool(successes or posthoc or any(c['check'].get('target_met') for c in candidates)),
        'first_valid_submission_seconds':successes[0]['elapsed_seconds'] if successes else None,
        'infrastructure_interruption':infra,'mathematically_unsolved':not bool(successes or posthoc) and not infra,
        'input_tokens':sum(u['input_tokens'] for u in usage) if full else None,
        'output_tokens':sum(u['output_tokens'] for u in usage) if full else None,'total_tokens':snap['total_tokens'],
        'known_input_tokens':sum(u.get('input_tokens') or 0 for u in usage),'known_output_tokens':sum(u.get('output_tokens') or 0 for u in usage),
        'cached_input_tokens':sum(u['cached_input_tokens'] for u in usage) if full and all(u.get('cached_input_tokens') is not None for u in usage) else None,
        'reasoning_tokens':sum(u['reasoning_tokens'] for u in usage) if full and all(u.get('reasoning_tokens') is not None for u in usage) else None,
        'known_cached_input_tokens':sum(u.get('cached_input_tokens') or 0 for u in usage),
        'known_reasoning_tokens':sum(u.get('reasoning_tokens') or 0 for u in usage),
        'known_total_tokens':snap['known_total_tokens'],'unknown_usage_requests':sum(r['state']!='finished' or not r['usage'] for r in snap['request_states']),
        'logical_requests':sum(e['type']=='request_reserved' and not e.get('retry_of') for e in events),
        'physical_requests':snap['requests'],'http_dispatches':sum(e['type']=='request_dispatched' for e in events),
        'role_requests':{'worker':snap['requests']},'tool_calls':len(tool_records),'tool_errors':tool_errors,
        'candidate_checks':len(candidates),'unique_candidates':len({c['raw_sha256'] for c in candidates}),
        'semantic_unique_candidates':len({c['semantic_sha256'] for c in candidates}),
        'exact_duplicate_candidates':len(candidates)-len({c['raw_sha256'] for c in candidates}),
        'module_triggers':sum(e['type']=='context_projection' for e in module_events),
        'context_bytes_removed':sum(e.get('removed_bytes',0) for e in module_events if e['type']=='context_projection'),
        'retry_events':[e for e in events if e['type'] in ('retry_scheduled','retry_ready') or e['type']=='request_reserved' and e.get('retry_of')],
        'candidate_lineage':[{'raw_sha256':c['raw_sha256'],'tool_request_id':c['tool_request_id'],
            'parent_request_id':next((e['parent_request_id'] for e in events if e['type']=='native_tool_context' and e['tool_request_id']==c['tool_request_id']),None)} for c in candidates],
        'stop_reason':terminal,'fair_comparison_valid':isolated and guard_report['violation'] is None,
        'isolation_enforced':isolated,'state_guard':guard_report,'native':native,'failure':failure,'ledger':snap,
        'candidate_persistence':'content-addressed submit files and immutable independent receipts retained',
        'certificate_scope':'finite exact checker acceptance; no general proof claim'}
    save(dest/'events.json',events);save(dest/'result.json',result)
    emit(run=ident,formal_success=result['formal_success'],requests=result['physical_requests'],total_tokens=result['total_tokens'],stop_reason=terminal,failure=failure)
    return result

def connectivity():
    verify_freeze();dest=ROOT/'connectivity'
    require(not dest.exists(),'NO_REPLAY','Connectivity check already exists')
    dest.mkdir(mode=0o700)
    save(dest/'reservation.json',{'physical_requests':1,'maximum_authorized':4,'model':POLICY,'separate_from_solves':True,'started_at':time.time()})
    t=HostTransport(profile='optimization',max_output_tokens=128000,response_timeout=180)
    from concurrent.futures import TimeoutError
    payload={'model':'gpt-6-astra','instructions':'Perform only the connectivity check.', 'reasoning':{'effort':'max'},'input':[{'role':'user','content':'Connectivity check only. Reply with READY.'}],
             'max_output_tokens':128000,'store':False,'stream':True,'include':['reasoning.encrypted_content']}
    started=time.monotonic()
    try:
        response=t.start(payload,{'request_id':'optimization-connectivity-1','run_id':'connectivity','role':'worker','cancel_event':threading.Event()}).result()
        save(dest/'receipt.json',{'physical_requests':1,'response':response,'wall_seconds':time.monotonic()-started})
        require(response.get('status')=='completed' and response.get('model')=='gpt-6-astra' and response.get('reasoning',{}).get('effort')=='max','CONNECTIVITY','Unexpected upstream response')
        emit(connectivity='passed',physical_requests=1,usage=response.get('usage'))
    except Exception as exc:
        save(dest/'failure.json',{'physical_requests':1,'error':str(exc),'code':getattr(exc,'code',None),'usage':None});raise

def summarize():
    import statistics
    p=verify_freeze();results=[]
    for row in p['schedule']:
        path=ROOT/'runs'/row['run_id']/'result.json'
        if path.is_file():results.append(json.loads(path.read_text()))
    dev=[r for r in results if r['split']=='dev'];a=[r for r in dev if r['config']=='A'];b=[r for r in dev if r['config']=='B']
    paired=[]
    for x in a:
        y=next((y for y in b if (y['task'],y['repeat'])==(x['task'],x['repeat'])),None)
        if y and x['formal_success'] and y['formal_success'] and x['total_tokens'] and y['total_tokens'] is not None:
            paired.append({'task':x['task'],'repeat':x['repeat'],'saving':1-y['total_tokens']/x['total_tokens']})
    per_task={task:statistics.median(r['saving'] for r in paired if r['task']==task) for task in sorted({r['task'] for r in paired})}
    median=statistics.median(per_task.values()) if per_task else None
    benefit=sum(r['formal_success'] for r in b)>sum(r['formal_success'] for r in a) or (len(per_task)>=3 and median>=.15)
    eligible=len(dev)==36 and all(r['fair_comparison_valid'] for r in dev) and sum(r['formal_success'] for r in b)>=sum(r['formal_success'] for r in a) and sum(r['infrastructure_interruption'] for r in b)<=sum(r['infrastructure_interruption'] for r in a) and any(r['module_triggers'] for r in b) and benefit
    summary={'development_completed':len(dev),'holdout_completed':sum(r['split']=='holdout' for r in results),
        'A_formal_successes':sum(r['formal_success'] for r in a),'B_formal_successes':sum(r['formal_success'] for r in b),
        'A_infrastructure':sum(r['infrastructure_interruption'] for r in a),'B_infrastructure':sum(r['infrastructure_interruption'] for r in b),
        'paired_success_savings':paired,'task_median_savings':per_task,'median_task_saving':median,'promotion_eligible':eligible,
        'known_total_tokens':sum(r['known_total_tokens'] for r in results),'total_tokens':sum(r['total_tokens'] for r in results) if all(r['total_tokens'] is not None for r in results) else None,
        'unknown_usage_requests':sum(r['unknown_usage_requests'] for r in results),'physical_requests':sum(r['physical_requests'] for r in results),'runs':results}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    return summary

def campaign():
    p=verify_freeze()
    ping=ROOT/'connectivity/receipt.json';audited=ROOT/'connectivity-pre-audit-r1/accounting.json'
    audit_ok=False
    if audited.is_file():
        a=json.loads(audited.read_text());audit_ok=a.get('model')=='gpt-6-astra' and a.get('effort')=='max' and a.get('physical_requests')==1
    require(ping.is_file() or audit_ok,'CONNECTIVITY','Successful separately metered connectivity or qualified pre-run audit receipt required')
    for split in ('dev','holdout'):
        if split=='holdout' and not summarize()['promotion_eligible']:
            emit(holdout='not_consumed',reason='preregistered promotion gate not met');break
        for row in p['schedule']:
            if row['split']!=split:continue
            path=ROOT/'runs'/row['run_id']
            if path.exists():
                require((path/'result.json').is_file(),'INCOMPLETE_RUN','Preserve incomplete logical run and audit it before continuing')
                continue
            result=execute(row,p);summarize()
            require(result['fair_comparison_valid'],'FAIRNESS','Isolation/adapter validity failed; pending blocks stopped')
            require(not result['failure'] or result['physical_requests']>0,'STARTUP_FAILURE','Zero-call startup failure retained; repair/audit before pending blocks')
    s=summarize();emit(completed=s['development_completed'],holdout=s['holdout_completed'],promotion=s['promotion_eligible'])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['freeze','connectivity','campaign','summarize'])
    action=parser.parse_args().action
    if action=='freeze':freeze()
    elif action=='connectivity':connectivity()
    elif action=='campaign':campaign()
    else:
        s=summarize();emit(**{k:v for k,v in s.items() if k!='runs'})
