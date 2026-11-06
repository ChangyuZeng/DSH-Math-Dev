import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createWorkflowCall, startWorkflowRuntime, startOfflineWorkflowRuntime} from '../workflow_runtime.mjs';
import {executeToolLoop} from '../workflow_executor.mjs';
import {parseExact} from '../workflow_ports.mjs';
import {runOfflineSlot} from '../evaluation/workflow-v2/run-slot.mjs';

const source = fileURLToPath(new URL('..', import.meta.url));
const python = process.env.PYTHON ?? 'python3';
const stop = {summary: 'Offline invalid-certificate fixture ended.', changes: [], next_action: 'stop',
  next_target: null, progress: 'no_progress', limitations: ['No target solved.']};
const response = output => ({id: 'offline-response', model: 'gpt-6-astra', reasoning: {effort: 'max'},
  status: 'completed', output, usage: {input_tokens: 10, output_tokens: 4, total_tokens: 14}});
const text = () => response([{type: 'message', role: 'assistant', content: [{type: 'output_text', text: JSON.stringify(stop)}]}]);
const invalid = (tool = 'check') => response([{type: 'function_call', name: tool, call_id: 'invalid-certificate',
  arguments: '{"candidate":{"values":[9007199254740993,-9007199254740993]}}'}]);

function prepared(t, policy = 'model-directed-v1') {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'workflow-runtime-v2-')));
  const cleanup = [];
  t.after(async () => {
    for (const close of cleanup) await close();
    assert.equal(realpathSync(directory), directory);
    rmSync(directory, {recursive: true});
  });
  const code = `import json,sys
from backend import Projects
from workflow_runtime import qualified_spec,certificate_contract
from workflow_service import dispatch_internal
root,policy=sys.argv[1:]
project=Projects(root).create('offline-session',certificate_contract('D1')['problem'])['id']
spec=qualified_spec(project,'D1',policy)
run=dispatch_internal(root,'workflow-state',{'action':'prepare','project_id':project,'run_id':'offline-run','operation_id':'offline-prepare','expected_rev':0,'spec':spec})
run=dispatch_internal(root,'workflow-state',{'action':'start','project_id':project,'run_id':'offline-run','operation_id':'offline-start','expected_rev':run['rev']})
print(json.dumps({'root':root,'projectId':project,'runId':'offline-run','spec':spec}))`;
  const result = spawnSync(python, ['-B', '-c', code, join(directory, 'store'), policy], {cwd: source, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  return {...parseExact(result.stdout), python, directory, cleanup};
}

function files(directory) {
  return readdirSync(directory, {withFileTypes: true}).flatMap(item => {
    const path = join(directory, item.name);
    return item.isDirectory() ? files(path) : [path];
  });
}

test('product entry cannot accidentally select fixtures', () => {
  assert.throws(() => startWorkflowRuntime({root: '/unused', projectId: 'x', runId: 'x', responses: []}), /test transport/);
  assert.throws(() => startWorkflowRuntime({root: '/unused', projectId: 'x', runId: 'x', transport: 'fixture'}), /test transport/);
});

test('shared runtime uses real executor, Gateway receipts and ResearchStore without patching', async t => {
  const preparedRun = prepared(t);
  const call = createWorkflowCall(preparedRun);
  const runtime = await startOfflineWorkflowRuntime({...preparedRun, responses: [invalid(), text()]});
  preparedRun.cleanup.push(() => runtime.close());
  assert.equal(runtime.metadata.mode, 'offline-fixture');
  assert.equal(runtime.metadata.capabilities.qualified_certificates_only, true);
  assert.equal(Object.hasOwn(runtime, 'bindings'), false);
  assert.equal(Object.hasOwn(runtime, 'gateway'), false);
  assert.equal(Object.hasOwn(runtime.metadata, 'bindings'), false);
  const run = await call('workflow-state', {action: 'read', project_id: preparedRun.projectId, run_id: preparedRun.runId});
  const ended = await executeToolLoop({call, modelPort: runtime.modelPort, toolPort: runtime.toolPort,
    projectId: preparedRun.projectId, runId: preparedRun.runId, expectedRev: run.rev, kind: 'derive'});
  assert.equal(ended.usage.requests, 2);
  assert.equal(ended.physical_requests.length, 2);
  assert.equal(ended.physical_requests[0].usage.cached_input_tokens, null);
  assert.equal(ended.physical_requests[0].usage.total_tokens, 14);
  assert.ok(ended.execution_events.some(row => row.type === 'candidate_archived'));
  assert.ok(ended.execution_events.some(row => row.type === 'check_completed'));
  assert.ok(!ended.execution_events.some(row => row.type === 'revision_triggered'));
  assert.ok(!ended.execution_events.some(row => row.type === 'native_stop' && row.reason === 'first_independent_submit_success'));
  await runtime.close();
  const evidence = files(runtime.metadata.directory);
  assert.ok(evidence.some(path => path.includes('.tool_request.')));
  assert.ok(evidence.some(path => path.includes('.tool_response.')));
  assert.ok(evidence.some(path => path.endsWith('final.json')));
  const exact = evidence.filter(path => path.endsWith('.json')).map(path => readFileSync(path, 'utf8')).join('\n');
  assert.match(exact, /9007199254740993/);
  assert.doesNotMatch(exact, /9007199254740992/);
  const replay = () => startOfflineWorkflowRuntime({...preparedRun, responses: [text()]});
  await assert.rejects(replay, error => ['RECONCILE_REQUIRED', 'RUNTIME_STATE'].includes(error.code));
  assert.throws(() => runtime.modelPort.complete({}), error => error.code === 'RUNTIME_CLOSED');
});

test('campaign slot uses the same runtime and B revision policy with an invalid certificate', async t => {
  const preparedRun = prepared(t, 'verify-revise-v1');
  const output = join(preparedRun.directory, 'slot.json');
  const record = await runOfflineSlot({...preparedRun, output, responses: [invalid('submit'), text()]});
  assert.equal(record.failure, null, JSON.stringify(record.failure));
  assert.equal(record.run.usage.requests, 2);
  assert.deepEqual(record.run.physical_requests.map(row => row.role), ['worker', 'reviser']);
  assert.ok(record.run.execution_events.some(row => row.type === 'revision_triggered'));
  assert.ok(record.run.execution_events.some(row => row.type === 'submit_completed'));
  assert.ok(!record.run.execution_events.some(row => row.type === 'native_stop' && row.reason === 'first_independent_submit_success'));
  assert.equal(parseExact(readFileSync(output, 'utf8')).format, 'dsh-math-workflow-slot-v2');
  const independent = files(record.runtime.directory).filter(path => /receipt-.*\.json$/.test(path));
  assert.equal(independent.length, 1);
  assert.equal(parseExact(readFileSync(independent[0], 'utf8')).independent_check.target_met, false);
  const collected = spawnSync(python, ['-B', '-c', `import json,sys
from pathlib import Path
sys.path.insert(0,'evaluation/workflow-v2')
from driver import collect_slot
from protocol import public_tasks
from workflow_runtime import save_json,tool_contracts
folder=Path(sys.argv[1]); packet=json.loads(sys.argv[2]); record=json.loads(Path(sys.argv[3]).read_text())
slot={'slot_id':'dev-D1-r1-B','pair_id':'dev-D1-r1','task':'D1','rep':1,'arm':'B','phase':'dev','order':['A','B']}
base=folder/'runs'/slot['slot_id']
save_json(base/'launch.json',{'request':packet,'source_sha256':'offline-fixture'})
save_json(base/'process.json',record)
protocol={'source':{'sha256':'offline-fixture'},'tasks':public_tasks(),'tool_contracts':tool_contracts(),
          'qualification':{'checks':{'isolated_compute_denials':False}}}
result=collect_slot(folder,slot,protocol)
print(json.dumps(result))`, preparedRun.directory, JSON.stringify({root: preparedRun.root,
      projectId: preparedRun.projectId, runId: preparedRun.runId}), output], {cwd: source, encoding: 'utf8'});
  assert.equal(collected.status, 0, collected.stderr);
  const evidence = parseExact(collected.stdout);
  assert.equal(evidence.physical_count_known, true);
  assert.equal(evidence.usage.physical_requests, 2);
  assert.equal(evidence.total_tokens, 28);
  assert.equal(evidence.usage.cached_input_tokens, null);
  assert.equal(evidence.formal_success, false);
  assert.equal(evidence.infra_terminated, false);
  assert.equal(evidence.termination, 'math_unsolved');
  assert.equal(evidence.comparable.isolation, false);
});

test('even an unused claimed lineage cannot silently obtain a new ledger', async t => {
  const preparedRun = prepared(t);
  const runtime = await startOfflineWorkflowRuntime({...preparedRun, responses: []});
  await runtime.close();
  await assert.rejects(startOfflineWorkflowRuntime({...preparedRun, responses: []}), error => error.code === 'RECONCILE_REQUIRED');
  const final = parseExact(readFileSync(join(runtime.metadata.directory, 'final.json'), 'utf8'));
  assert.equal(final.ledger.requests, 0);
});
