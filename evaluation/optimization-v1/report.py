#!/usr/bin/env python3
"""Export existing frozen results without new solves or model requests."""
import csv
import hashlib
import json
from pathlib import Path
import statistics
from collections import Counter

ROOT=Path(__file__).resolve().parents[3]
def dump_csv(name,rows):
    if not rows:return
    with (ROOT/name).open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
def total(rows,key):
    values=[r[key] for r in rows]
    return sum(values) if all(v is not None for v in values) else None

def main():
    protocol=json.loads((ROOT/'protocol.json').read_text());summary=json.loads((ROOT/'summary.json').read_text())
    results=summary['runs'];rows=[]
    scalar=['ordinal','run_id','task','family','split','repeat','config','block_order','source_commit','task_sha256',
        'checker_sha256','started_at_unix','ended_at_unix','wall_seconds','first_valid_submission_seconds',
        'formal_success','construction_valid','posthoc_success','mathematically_unsolved','infrastructure_interruption',
        'stop_reason','fair_comparison_valid','input_tokens','output_tokens','total_tokens','known_input_tokens',
        'known_output_tokens','known_total_tokens','cached_input_tokens','reasoning_tokens','known_cached_input_tokens',
        'known_reasoning_tokens','unknown_usage_requests','physical_requests','http_dispatches','logical_requests',
        'tool_calls','tool_errors','candidate_checks','unique_candidates','semantic_unique_candidates',
        'exact_duplicate_candidates','module_triggers','context_bytes_removed']
    for result in results:
        row={k:result.get(k) for k in scalar}
        folder=ROOT/'runs'/result['run_id']
        candidates=[json.loads(p.read_text()) for p in (folder/'candidates').glob('*.json')]
        cpu=[c['receipt'].get('checker_cpu_seconds') for c in candidates]
        wall=[c['receipt'].get('checker_wall_seconds') for c in candidates]
        row.update(checker_cpu_seconds=sum(cpu) if all(x is not None for x in cpu) else None,
            known_checker_cpu_seconds=sum(x for x in cpu if x is not None),unknown_checker_cpu_count=sum(x is None for x in cpu),
            checker_wall_seconds=sum(wall) if all(x is not None for x in wall) else None,
            known_checker_wall_seconds=sum(x for x in wall if x is not None),unknown_checker_wall_count=sum(x is None for x in wall),
            retry_attempts=sum(e['type']=='request_reserved' for e in result['retry_events']),
            dollar_cost=None,dollar_cost_reason='Provider pricing not available; do not impute.')
        rows.append(row)
    dump_csv('results.csv',rows)
    groups=[]
    for family in sorted({r['family'] for r in results}):
        for config in ('A','B'):
            values=[r for r in results if r['family']==family and r['config']==config]
            groups.append({'family':family,'config':config,'runs':len(values),'formal_successes':sum(r['formal_success'] for r in values),
                'construction_valid':sum(r['construction_valid'] for r in values),'posthoc_successes':sum(r['posthoc_success'] for r in values),
                'infrastructure_interruptions':sum(r['infrastructure_interruption'] for r in values),
                'total_tokens':total(values,'total_tokens'),'known_total_tokens':sum(r['known_total_tokens'] for r in values),
                'unknown_usage_requests':sum(r['unknown_usage_requests'] for r in values)})
    dump_csv('family-summary.csv',groups)
    pairs=[]
    for task in sorted({r['task'] for r in results}):
        a=[r for r in results if r['task']==task and r['config']=='A'];b=[r for r in results if r['task']==task and r['config']=='B']
        savings=[]
        for x in a:
            y=next((y for y in b if y['repeat']==x['repeat']),None)
            if y and x['formal_success'] and y['formal_success'] and x['total_tokens'] and y['total_tokens'] is not None:
                savings.append(1-y['total_tokens']/x['total_tokens'])
        pairs.append({'task':task,'split':(a+b)[0]['split'],'A_runs':len(a),'B_runs':len(b),
            'A_formal_successes':sum(r['formal_success'] for r in a),'B_formal_successes':sum(r['formal_success'] for r in b),
            'paired_fully_accounted_successes':len(savings),'paired_median_token_saving':statistics.median(savings) if savings else None,
            'A_total_tokens':total(a,'total_tokens'),'B_total_tokens':total(b,'total_tokens')})
    dump_csv('task-pairs.csv',pairs)
    modes=Counter((r['config'],r['stop_reason'],r['infrastructure_interruption']) for r in results)
    dump_csv('failure-modes.csv',[{'config':a,'stop_reason':b,'infrastructure':c,'runs':n} for (a,b,c),n in sorted(modes.items())])
    separate=[]
    for path in ROOT.glob('*/accounting.json'):
        value=json.loads(path.read_text());usage=value.get('usage') or {}
        separate.append({'purpose':value['purpose'],'physical_requests':value.get('physical_requests'),
            'input_tokens':usage.get('input_tokens'),'output_tokens':usage.get('output_tokens'),'total_tokens':usage.get('total_tokens'),
            'elapsed_seconds':value.get('elapsed_seconds'),'receipt':str(path.relative_to(ROOT))})
    dump_csv('separate-model-costs.csv',separate)
    lines=['# dsh-math optimization result','',f"Frozen source: `{protocol['source_commit']}`.",
        f"Protocol SHA-256: `{hashlib.sha256((ROOT/'protocol.json').read_bytes()).hexdigest()}`.",'',
        f"Development solves: {summary['development_completed']}/36; holdout solves: {summary['holdout_completed']}/18 conditional.",
        f"Development formal success A/B: {summary['A_formal_successes']}/18 and {summary['B_formal_successes']}/18.",
        f"Development infrastructure interruptions A/B: {summary['A_infrastructure']} and {summary['B_infrastructure']}.",
        f"Preregistered promotion gate: {'PASS' if summary['promotion_eligible'] else 'NOT MET'}.",
        f"Median of task-level paired-success token savings: {summary['median_task_saving']}.",'',
        '| Task | A formal | B formal | Accounted successful pairs | Median token saving |',
        '|---|---:|---:|---:|---:|']
    for p in pairs:
        v=p['paired_median_token_saving'];v='unknown' if v is None else f'{v:.1%}'
        lines.append(f"| {p['task']} | {p['A_formal_successes']}/{p['A_runs']} | {p['B_formal_successes']}/{p['B_runs']} | {p['paired_fully_accounted_successes']} | {v} |")
    lines+=['',f"Solve physical requests: {summary['physical_requests']}; known token subtotal: {summary['known_total_tokens']}; full total: {summary['total_tokens']}; unknown-usage requests: {summary['unknown_usage_requests']}.",'',
        'The statistical unit is the task. Repeats are paired within task; task medians, rather than 18 allegedly independent tasks, determine the cost gate. Cached input and reasoning are subsets of input/output, never added again. Missing counters remain unknown. Prices were unavailable, so currency costs remain unknown. Connectivity/audit requests are listed separately.', '',
        'B removes only root host telemetry from serialized tool replies. It remains off by default. A positive offline byte fixture establishes a trigger, not a success or cost improvement. If the gate is not met, preserve the experimental switch without promoting it or consuming holdout. No tuning or extra repeats follow.', '',
        'Formal success requires explicit submit and a fresh-process exact checker receipt. Valid check-only or posthoc candidates are separately recorded. Certificate acceptance covers these finite synthetic tasks and is not a general mathematical proof.', '',
        'See results.csv, task-pairs.csv, family-summary.csv, failure-modes.csv, separate-model-costs.csv, independent-artifact-audit.json and independent-post-audit for full evidence. Original model payloads, tool replies, candidates, fresh-process receipts, hash-chained ledger events, native evidence and pre-cleanup stdout/stderr remain per run.']
    path=ROOT/'RESULTS.md'
    with path.open('x') as output:output.write('\n'.join(lines)+'\n')
    print(json.dumps({'run_rows':len(rows),'task_rows':len(pairs),'promotion':summary['promotion_eligible']}))

if __name__=='__main__':main()
