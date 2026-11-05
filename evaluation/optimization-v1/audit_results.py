#!/usr/bin/env python3
"""Read-only reconciliation, independent of the execution driver's functions.

Does not display candidate values or holdout solver logs. Checks persisted bytes,
ledger hash chains, physical-role quota, formal-submit receipts and transcript policy.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(value):return hashlib.sha256(value).hexdigest()

def main():
    failures=[];checks=0;runs=[]
    def need(condition,issue):
        nonlocal checks
        checks+=1
        if not condition:failures.append(issue)
    protocol=json.loads((ROOT/'protocol.json').read_text());freeze=json.loads((ROOT/'protocol-freeze.json').read_text())
    need(sha((ROOT/'protocol.json').read_bytes())==freeze['sha256'],'protocol hash mismatch')
    previous_end=None
    for row in protocol['schedule']:
        folder=ROOT/'runs'/row['run_id']
        if not folder.exists():continue
        result=json.loads((folder/'result.json').read_text());label=row['run_id']
        need(all(result[k]==v for k,v in row.items()),label+': schedule mismatch')
        need(result['physical_requests']<=120,label+': quota exceeded')
        need(result['model']=={'provider':'openai-codex','model':'gpt-6-astra','effort':'max'},label+': model policy changed')
        if previous_end is not None:need(result['started_at_unix']>=previous_end,label+': serial overlap')
        previous_end=result['ended_at_unix']
        db=sqlite3.connect('file:'+str(folder/'ledger.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        previous='0'*64;events=[]
        for record in db.execute('SELECT seq,event,hash FROM events ORDER BY seq'):
            event=json.loads(record['event'])
            need(event['previous_hash']==previous and event['seq']==record['seq'],label+': event predecessor mismatch')
            need(sha(canonical(event).encode())==record['hash'],label+': event hash mismatch')
            previous=record['hash'];events.append(event)
        requests=list(db.execute('SELECT * FROM requests'));db.close()
        need(len(requests)==result['physical_requests'],label+': request count mismatch')
        need(sum(e['type']=='request_reserved' for e in events)==len(requests),label+': reservation mismatch')
        need(all(r['role']=='worker' for r in requests),label+': unreported role')
        if any(r['state']!='finished' or not r['usage'] or not json.loads(r['usage'])['complete'] for r in requests):
            need(result['total_tokens'] is None,label+': unknown usage imputed')
        for path in (folder/'audit').glob('*.request.json'):
            request=json.loads(path.read_text())
            need(request.get('model')=='gpt-6-astra' and request.get('reasoning',{}).get('effort')=='max',label+': wire model drift')
            for item in request.get('input',[]):
                if item.get('type')!='function_call_output':continue
                value=json.loads(item['output'])
                need(('metrics' in value)==(row['config']=='A'),label+': context policy mismatch')
        candidates=[json.loads(p.read_text()) for p in (folder/'candidates').glob('*.json')]
        official=[];location=json.loads((folder/'location.json').read_text());runtime=Path(location['root'])
        for candidate in candidates:
            need(sha(canonical(candidate['candidate']).encode())==candidate['raw_sha256'],label+': candidate bytes changed')
            need(candidate['checker_sha256']==result['checker_sha256'],label+': checker differs')
            if candidate['source']=='submit' and candidate['check'].get('legal') is True and candidate['check'].get('target_met') is True:
                official.append(candidate)
                saved=runtime/'common/.host/submissions'/(candidate['raw_sha256']+'.json')
                need(saved.is_file() and sha(saved.read_bytes())==candidate['raw_sha256'],label+': durable submission missing')
        need(bool(official)==result['formal_success'],label+': formal success not supported by submit receipt')
        need(sha((folder/'checker.py').read_bytes())==result['checker_sha256'],label+': frozen checker changed')
        need((folder/'capture/stdout.log').is_file() and (folder/'capture/stderr.log').is_file(),label+': pre-cleanup logs missing')
        runs.append({'run_id':label,'passed':not any(f.startswith(label+':') for f in failures)})
    report={'auditor':'stdlib-only read-only reconciliation; no executor helper imports','checks':checks,
        'runs':runs,'failures':failures,'passed':not failures,'audited_at_unix':time.time(),
        'limits':['Checks finite certificate receipts and artifact integrity; not a general mathematical proof.',
                  'Program audit complements the separately metered fresh-context Astra review.']}
    path=ROOT/'independent-artifact-audit.json'
    if path.exists():raise RuntimeError('Audit artifact already exists; preserve it')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':not failures,'checks':checks,'run_count':len(runs),'failures':failures}))
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
