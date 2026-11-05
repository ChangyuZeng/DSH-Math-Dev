#!/usr/bin/env python3
"""One separately metered Astra/max review; no solving or hidden positive examples."""
import json
from pathlib import Path
import sys
import time
import threading
from experiment import ROOT,SOURCE,ADAPTER,save as write,git
from transport import HostTransport


def main():
    phase=sys.argv[1] if len(sys.argv)>1 else 'pre'
    if phase not in {'pre','post'}:raise SystemExit('pre or post required')
    folder=ROOT/('connectivity-pre-audit-r1' if phase=='pre' else 'independent-post-audit')
    folder.mkdir(exist_ok=False)
    paths=['workflow_runner.mjs','workflow_store.py','workflow_context.py',
        'evaluation/optimization-v1/experiment.py','evaluation/optimization-v1/certificates.py',
        'evaluation/four-harness-v1/m_adapter.mjs','evaluation/four-harness-v1/m_bridge.mjs',
        'evaluation/four-harness-v1/gateway.py','evaluation/four-harness-v1/ledger.py',
        'evaluation/four-harness-v1/native.py','evaluation/four-harness-v1/tools.py',
        'evaluation/four-harness-v1/x_live.py','evaluation/four-harness-v1/transport.py']
    contents={p:(SOURCE/p).read_text() for p in paths}
    contents['requested_plan']=(ROOT.parent/'dsh-math-optimization-plan.md').read_text()
    for name in ('protocol.json','promotion.json','results.csv'):
        if (ROOT/name).exists():contents[name]=(ROOT/name).read_text()
    instructions=('You are an independent implementation and experiment integrity auditor in a fresh context. '
        'Inspect supplied code/protocol against requested plan. No target solving, no positive witnesses, no web or tools. '
        'Focus on physical-request accounting, isolation/leakage, explicit submit success, durability, one-factor fairness, '
        'unknown usage, task/certificate soundness, stopping/promotion, hidden holdout boundaries. '
        'Return concise JSON {verdict: pass|concerns|fail, critical_findings:[{issue,evidence,impact,fix}], '
        'noncritical_findings:[], strengths:[], limits:[]}. Distinguish demonstrated bugs from hypotheses. '
        'Do not call this review a mathematical proof. Do not propose extra arms/repeats or changed thresholds.')
    payload={'model':'gpt-6-astra','reasoning':{'effort':'max'},'instructions':instructions,
        'input':[{'role':'user','content':json.dumps({'phase':phase,'source_commit':git('rev-parse','HEAD'),'evidence':contents},ensure_ascii=False)}],
        'max_output_tokens':128000,'stream':True,'store':False,'tools':[],'truncation':'disabled'}
    write(folder/'request.json',payload)
    started=time.time(); receipts=[]
    try:
        response=HostTransport(profile='optimization',max_output_tokens=128000,response_timeout=180,
            evidence_sink=lambda e:receipts.append(e)).start(payload,{'request_id':f'independent-{phase}-audit-1',
                'cancel_event':threading.Event()}).result()
        write(folder/'response.json',response)
        texts=[part.get('text','') for item in response.get('output',[]) for part in item.get('content',[]) if part.get('type')=='output_text']
        text='\n'.join(texts);(folder/'review.txt').write_text(text)
        usage=response.get('usage')
        record={'purpose':'connectivity_and_pre_run_audit' if phase=='pre' else 'independent_final_audit',
            'physical_requests':1,'model':response.get('model'),'effort':response.get('reasoning',{}).get('effort'),
            'usage':usage,'usage_unknown':usage is None,'elapsed_seconds':time.time()-started,'transport_receipts':receipts}
        write(folder/'accounting.json',record)
        print(json.dumps(record),flush=True)
        if response.get('model')!='gpt-6-astra' or response.get('reasoning',{}).get('effort')!='max':raise RuntimeError('audit model drift')
    except Exception as exc:
        write(folder/'interruption.json',{'type':type(exc).__name__,'code':getattr(exc,'code',None),
            'elapsed_seconds':time.time()-started,'transport_receipts':receipts,'usage_unknown':True})
        raise

if __name__=='__main__':main()
