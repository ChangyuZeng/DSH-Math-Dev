import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, realpath, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {createMAdapter, createPythonWorkflowService, loadBenchmarkWorkflow, normalizeResponsesUsage,
  WORKFLOW_SOURCE_PIN, COMMON_TOOL_NAMES} from '../evaluation/four-harness-v1/m_adapter.mjs';
import * as nativeRunner from '../workflow_runner.mjs';
import {createGatewayBindings, runBridge, syntheticProblem, syntheticProposal} from '../evaluation/four-harness-v1/m_bridge.mjs';

const model = {provider: 'fixture', model: 'fixed-fixture', effort: 'max'};
const schemas = Object.keys(COMMON_TOOL_NAMES).map(name => ({type: 'function', name,
  description: 'Synthetic tool fixture', parameters: {type: 'object', properties: {}, additionalProperties: true}}));
const problem = {title: 'Synthetic addition fixture', statement: 'Compute 2 + 3 using only synthetic inputs.', quantifiers: 'one pair',
  domain: 'integers', conventions: [], target_type: 'engineering_fixture', parent: null, relation: '',
  completion_criteria: ['The synthetic result is five'], sources: ['new temporary fixture'], knowledge_status: 'synthetic_fixture'};
const budget = {requests: 6, total_tokens: 100_000, max_output_tokens: 100};
const usage = (extra = {}) => ({input_tokens: 20, input_tokens_details: {cached_tokens: 5}, output_tokens: 80,
  output_tokens_details: {reasoning_tokens: 10}, total_tokens: 100, ...extra});
const proposal = (extra = {}) => ({summary: 'Synthetic candidate only', changes: [], next_action: 'stop', next_target: null,
  progress: 'new_argument', limitations: ['Not human accepted'], ...extra});
const response = (value = proposal(), extra = {}) => ({id: 'response-fixture', status: 'completed', model: model.model,
  reasoning: {effort: model.effort}, usage: usage(), output: [{type: 'message', role: 'assistant',
    content: [{type: 'output_text', text: JSON.stringify(value)}]}], ...extra});
const toolResponse = () => response(null, {output: [{type: 'function_call', name: 'compute', call_id: 'compute-1',
  arguments: JSON.stringify({code: 'print(2 + 3)'})}]});
const noteData = () => ({note_type: 'argument', title: 'Addition candidate', summary: 'Two and three total five.', body: '2 + 3 = 5.',
  statement: 'The result is five.', assumptions: [], problem_ref: {id: 'P', rev: 1}, route_id: null, uses: [],
  candidate_for: [{id: 'O', rev: 1}], evidence: [], gaps: ['Needs review'], failure: null});
const obligationData = () => ({statement: 'Check the addition candidate.', scope: 'Only this synthetic input',
  completion_criteria: ['Verify the displayed arithmetic'], check_method: 'human', checker_id: null,
  problem_ref: {id: 'P', rev: 1}, uses: [{id: 'N', rev: 1}], candidates: [{id: 'N', rev: 1}], evidence: [],
  next_action: 'Check arithmetic', state: 'candidate', reason: 'Unreviewed synthetic candidate'});

async function temporary(t) {
  const parent = await realpath(tmpdir());
  const root = await mkdtemp(join(parent, 'four-harness-m-test-'));
  t.after(async () => {
    assert.equal(resolve(root), root);
    assert.equal(await realpath(root), root);
    assert.equal(root.startsWith(`${parent}/four-harness-m-test-`), true);
    await rm(root, {recursive: true});
  });
  return root;
}
async function fixture(t, {transport = async () => response(), tools = async () => ({stdout: '5\n', exit_code: 0}),
  resolvedModel = model, customBudget = budget} = {}) {
  const root = await temporary(t), service = await createPythonWorkflowService({root});
  const adapter = createMAdapter({service, transport, tools, toolSchemas: schemas, resolvedModel, contextWindow: 65_536});
  const initialized = await adapter.initialize({runId: 'synthetic-run', problem, modelPolicy: model, budget: customBudget,
    contextBudget: {max_tokens: 50_000}, stageGoal: 'Compute the synthetic addition and record an unreviewed candidate.'});
  return {root, service, adapter, initialized};
}

// Tests exercise the real Python service, transaction journal, exact context,
// and research commits. Only the Responses transport and common tools are fake.
test('M loads the pinned current runner with only its benchmark ACTION_SYSTEM sentence changed', async t => {
  const loaded = await loadBenchmarkWorkflow();
  assert.equal(loaded.module.executeLoop.toString(), nativeRunner.executeLoop.toString());
  assert.equal(loaded.module.executeStep.toString(), nativeRunner.executeStep.toString());
  assert.equal(nativeRunner.ACTION_SYSTEM.includes('No tool or network calls are available.'), true);
  assert.equal(loaded.module.ACTION_SYSTEM.includes('Only the supplied benchmark tools are available'), true);
  assert.equal(loaded.evidence.changes.length, 1);
  const source = await readFile(new URL('../workflow_runner.mjs', import.meta.url), 'utf8');
  assert.equal(createHash('sha256').update(source).digest('hex'), WORKFLOW_SOURCE_PIN.sha256);
  const root = await temporary(t), changed = join(root, 'workflow_runner.mjs');
  await writeFile(changed, `${source}\n// drift\n`);
  await assert.rejects(loadBenchmarkWorkflow({sourcePath: changed}), {code: 'SOURCE_DRIFT'});
});

test('M usage preserves nullable provider subfields and never double counts cached or reasoning tokens', () => {
  const normalized = normalizeResponsesUsage(usage());
  assert.deepEqual(normalized, {input_tokens: 20, cached_input_tokens: 5, output_tokens: 80,
    reasoning_tokens: 10, total_tokens: 100, source: 'responses.usage', complete: true});
  const absent = normalizeResponsesUsage({input_tokens: 20, output_tokens: 80, total_tokens: 100});
  assert.equal(absent.cached_input_tokens, null);
  assert.equal(absent.reasoning_tokens, null);
  assert.equal(absent.complete, false);
  assert.equal(normalizeResponsesUsage(usage({total_tokens: 110})).complete, false);
});

test('M actual executeLoop uses common tools and commits stable Note/Obligation exact revisions', async t => {
  const requests = [], toolCalls = [], events = [];
  const f = await fixture(t, {transport: async input => {
    requests.push(input);
    if (requests.length === 1) return {response: toolResponse(), requestId: 'physical-1'};
    if (requests.length === 2) return {response: response(proposal({changes: [
      {kind: 'Note', id: 'N', expected_rev: 0, data: noteData()},
      {kind: 'Obligation', id: 'O', expected_rev: 0, data: obligationData()},
    ], progress: 'new_obligation', next_action: 'check', next_target: {id: 'O', rev: 1}})), requestId: 'physical-2'};
    assert.equal(requests.length, 3);
    const snapshot = JSON.parse(input.request.input[0].content[0].text);
    assert.equal(snapshot.action.kind, 'check');
    assert.deepEqual(snapshot.action.target_ref, {id: 'O', rev: 1});
    assert.deepEqual(snapshot.records.map(row => `${row.id}@${row.rev}`).sort(), ['N@1', 'O@1', 'P@1']);
    return {response: response(proposal({changes: [{kind: 'Note', id: 'N', expected_rev: 1,
      data: {...noteData(), body: 'The synthetic tool reported five; this remains a candidate.'}}]})), requestId: 'physical-3'};
  }, tools: async tool => { toolCalls.push(tool); return {stdout: '5\n', exit_code: 0}; }});
  const result = await f.adapter.run({emit: event => events.push(event)});
  assert.equal(result.run.status, 'paused');
  assert.equal(result.run.scientific_status, 'candidate_pending_review');
  assert.equal(result.run.actions.length, 2);
  assert.deepEqual(result.run.usage, {requests: 3, total_tokens: 300, complete: true});
  assert.equal(result.run.actions[0].usage.output_tokens, 160); // aggregate exceeds physical cap legitimately
  assert.equal(result.run.actions[0].max_output_tokens, 100);
  assert.deepEqual(result.run.actions[0].output_refs, [{id: 'N', rev: 1}, {id: 'O', rev: 1}]);
  assert.deepEqual(result.run.actions[1].output_refs, [{id: 'N', rev: 2}]);
  assert.equal(requests[0].request.max_output_tokens, 100);
  assert.equal(requests[1].parentRequestId, 'physical-1');
  assert.equal(requests[2].parentRequestId, null); // fresh native context for each action
  assert.equal(requests[1].request.input.at(-1).output, JSON.stringify({stdout: '5\n', exit_code: 0}));
  assert.equal(toolCalls[0].name, 'compute');
  assert.deepEqual(toolCalls[0].arguments, {code: 'print(2 + 3)'});
  assert.equal(events.filter(event => event.type === 'tool_finished').length, 1);
  assert.match(events.find(event => event.type === 'tool_finished').result_sha256, /^[0-9a-f]{64}$/);
  const inspected = await f.service.inspect();
  assert.equal(inspected.metadata.execution_authorized, false);
  assert.equal(inspected.objects.N.length, 2);
  assert.equal(inspected.objects.O[0].data.state, 'candidate');
  assert.equal(inspected.events.slice(1).every(event => event.actor.kind === 'model'), true);
  assert.equal(Object.values(inspected.objects).flat().some(row => row.kind === 'Review'), false);
  assert.deepEqual(result.run.actions[0].benchmark_accounting.requests[0].raw_usage, usage());
  const fresh = await createPythonWorkflowService({root: f.root});
  assert.deepEqual(await fresh.inspect(), inspected);
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(requests.length, 3);
});

test('M resolved model drift fails before start, reservation, or transport', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => { calls++; return response(); }, resolvedModel: {...model, model: 'drift'}});
  await assert.rejects(f.adapter.run(), {code: 'MODEL_DRIFT'});
  assert.equal(calls, 0);
  const run = await f.adapter.read();
  assert.equal(run.status, 'prepared');
  assert.equal(run.actions.length, 0);
});

test('M provider model drift after dispatch retains usage and refuses another call', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => { calls++; return response(proposal(), {model: 'other-model'}); }});
  await assert.rejects(f.adapter.run(), {code: 'MODEL_DRIFT'});
  const run = await f.adapter.read();
  assert.equal(run.usage.requests, 1);
  assert.equal(run.usage.total_tokens, 100);
  assert.equal(run.usage.complete, false);
  assert.equal(run.actions[0].status, 'failed');
  assert.equal((await f.service.inspect()).events.length, 1);
  await f.adapter.reconcile();
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(calls, 1);
});

test('M failed provider response retains known usage without committing its proposal or replaying', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => {
    calls++;
    return response(syntheticProposal(), {status: 'failed',
      error: {code: 'synthetic_failure', message: 'Synthetic provider failure'}});
  }});
  await assert.rejects(f.adapter.run(), {code: 'RESPONSE_INCOMPLETE'});
  const run = await f.adapter.read();
  assert.deepEqual(run.usage, {requests: 1, total_tokens: 100, complete: false});
  assert.equal(run.actions[0].status, 'failed');
  const receipt = run.actions[0].benchmark_accounting.requests[0];
  assert.equal(receipt.status, 'finished');
  assert.deepEqual(receipt.raw_usage, usage());
  assert.equal(receipt.usage.complete, true);
  const inspected = await f.service.inspect();
  assert.deepEqual(Object.keys(inspected.objects), ['P']);
  assert.equal(inspected.events.length, 1);
  await f.adapter.reconcile();
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(calls, 1);
});

test('M gateway error with raw usage retains costs and server identity without commits or replay', async t => {
  let calls = 0;
  const binding = createGatewayBindings({gateway: 'http://127.0.0.1:12345', token: 'fixture-capability',
    fetchImpl: async url => {
      if (url.pathname.endsWith('/manifest')) {
        return new Response(JSON.stringify({offline: true, model_policy: model, tools: schemas, context_window: 65536}));
      }
      assert.equal(url.pathname, '/M/worker/v1/responses');
      calls++;
      return new Response(JSON.stringify({error: {code: 'TRANSPORT_ERROR', message: 'Synthetic gateway failure',
        dispatched: true, request_id: 'synthetic-physical-1', usage: usage()}}),
      {status: 409, headers: {'X-Four-Request-Id': 'synthetic-physical-1'}});
    }});
  await binding.preflight();
  const f = await fixture(t, {transport: binding.transport});
  await assert.rejects(f.adapter.run(), {code: 'TRANSPORT_ERROR', dispatched: true, request_id: 'synthetic-physical-1'});
  const run = await f.adapter.read();
  assert.deepEqual(run.usage, {requests: 1, total_tokens: 100, complete: false});
  assert.equal(run.actions[0].status, 'failed');
  const receipt = run.actions[0].benchmark_accounting.requests[0];
  assert.equal(receipt.request_id, 'synthetic-physical-1');
  assert.equal(receipt.status, 'unknown');
  assert.deepEqual(receipt.raw_usage, usage());
  assert.equal(receipt.usage.complete, true);
  const inspected = await f.service.inspect();
  assert.deepEqual(Object.keys(inspected.objects), ['P']);
  assert.equal(inspected.events.length, 1);
  await f.adapter.reconcile();
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(calls, 1);
});

test('M absent usage leaves a durable unknown request and reconciliation never replays', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => { calls++; return response(proposal(), {usage: undefined}); }});
  await assert.rejects(f.adapter.run(), {code: 'USAGE_MISSING'});
  const run = await f.adapter.read();
  assert.equal(run.status, 'needs_reconcile');
  assert.equal(run.actions[0].status, 'unknown');
  assert.equal(run.usage.requests, 1);
  assert.equal(run.usage.complete, false);
  const accounting = run.actions[0].benchmark_accounting.requests[0];
  assert.equal(accounting.status, 'unknown');
  assert.equal(accounting.usage.total_tokens, null);
  assert.equal(accounting.raw_usage, null);
  const reconciled = await f.adapter.reconcile();
  assert.equal(reconciled.dispatched, false);
  assert.deepEqual(reconciled.run, run);
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(calls, 1);
});

test('M missing usage on a tool followup retains known subtotal and both physical requests', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => ++calls === 1 ? toolResponse() : response(proposal(), {usage: undefined})});
  await assert.rejects(f.adapter.run(), {code: 'USAGE_MISSING'});
  const run = await f.adapter.read();
  assert.equal(run.usage.requests, 2);
  assert.equal(run.usage.total_tokens, 100);
  assert.equal(run.usage.complete, false);
  assert.deepEqual(run.actions[0].benchmark_accounting.requests.map(row => row.status), ['finished', 'unknown']);
  assert.equal(run.actions[0].benchmark_accounting.requests[1].usage.output_tokens, null);
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(calls, 2);
});

test('M interrupted dispatch stays unknown and cancellation stops without replay', async t => {
  let calls = 0;
  const controller = new AbortController();
  const f = await fixture(t, {transport: async ({signal}) => {
    calls++;
    controller.abort();
    assert.equal(signal.aborted, true);
    throw Object.assign(Error('Fixture interrupted in flight'), {dispatched: true});
  }});
  await assert.rejects(f.adapter.run({signal: controller.signal}), /interrupted in flight/);
  const run = await f.adapter.read();
  assert.equal(run.status, 'needs_reconcile');
  assert.equal(run.actions[0].status, 'unknown');
  assert.equal(run.usage.requests, 1);
  assert.equal(run.usage.complete, false);
  await f.adapter.reconcile();
  assert.equal(calls, 1);
});

test('M cancellation after a complete provider receipt preserves costs despite native usage chunk rejection', async t => {
  let calls = 0;
  const controller = new AbortController();
  const f = await fixture(t, {transport: async () => {
    calls++;
    controller.abort();
    return response();
  }});
  await assert.rejects(f.adapter.run({signal: controller.signal}), /Workflow interrupted/);
  const run = await f.adapter.read();
  assert.equal(run.status, 'needs_reconcile');
  assert.equal(run.actions[0].status, 'unknown');
  assert.deepEqual(run.usage, {requests: 1, total_tokens: 100, complete: false});
  assert.deepEqual(run.actions[0].usage, {total_tokens: 100, output_tokens: 80});
  assert.equal(run.actions[0].benchmark_accounting.requests[0].status, 'finished');
  assert.equal((await f.service.inspect()).events.length, 1);
  await f.adapter.reconcile();
  assert.equal(calls, 1);
});

test('M pre-cancel never starts or reserves a physical request', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => { calls++; return response(); }});
  const controller = new AbortController(); controller.abort();
  await assert.rejects(f.adapter.run({signal: controller.signal}), {code: 'CANCELLED'});
  const run = await f.adapter.read();
  assert.equal(run.status, 'prepared');
  assert.equal(run.actions.length, 0);
  assert.equal(calls, 0);
});

test('M physical request cap includes tool iterations while native loop stops on actual aggregate budget', async t => {
  let calls = 0;
  const f = await fixture(t, {customBudget: {...budget, requests: 2}, transport: async () => {
    calls++;
    assert.ok(calls <= 2, 'native loop must not start a third request');
    return calls === 1 ? toolResponse() : response(proposal({next_action: 'derive'}));
  }});
  const {run} = await f.adapter.run();
  assert.equal(run.usage.requests, 2);
  assert.equal(run.usage.total_tokens, 200);
  assert.equal(run.reason, 'budget_exhausted');
  assert.equal(run.actions.length, 1);
  assert.equal(run.actions[0].status, 'committed');
});

test('M central predispatch rejection is distinguished from a charged unknown request', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => { calls++; throw Object.assign(Error('Central budget rejected'), {dispatched: false}); }});
  await assert.rejects(f.adapter.run(), /Central budget rejected/);
  const run = await f.adapter.read();
  assert.equal(run.usage.requests, 0);
  assert.equal(run.actions[0].benchmark_accounting.requests[0].status, 'not_dispatched');
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(calls, 1);
});

test('M missing cache and reasoning details stops while retaining known core usage', async t => {
  let calls = 0;
  const f = await fixture(t, {transport: async () => {
    calls++;
    return response(proposal(), {usage: {input_tokens: 20, output_tokens: 80, total_tokens: 100}});
  }});
  await assert.rejects(f.adapter.run(), {code: 'USAGE_MISSING'});
  const run = await f.adapter.read();
  assert.deepEqual(run.usage, {requests: 1, total_tokens: 100, complete: false});
  const raw = run.actions[0].benchmark_accounting.requests[0];
  assert.equal(raw.usage.cached_input_tokens, null);
  assert.equal(raw.usage.reasoning_tokens, null);
  assert.equal(raw.status, 'unknown');
  await assert.rejects(f.adapter.run(), {code: 'NO_REPLAY'});
  assert.equal(calls, 1);
});

test('M gateway bindings obtain shared schemas and preserve tool JSON, parents, and physical IDs', async () => {
  const calls = [], toolValue = {ok: true, result: {materials: []}, metrics: {wall_seconds: 0.001}};
  const binding = createGatewayBindings({gateway: 'http://127.0.0.1:12345', token: 'fixture-capability',
    fetchImpl: async (url, options) => {
      calls.push({url, options, body: JSON.parse(options.body)});
      const value = url.pathname.endsWith('/manifest') ? {offline: true, model_policy: model, tools: schemas, context_window: 65536}
        : url.pathname.endsWith('/tools') ? toolValue : response();
      return new Response(JSON.stringify(value), {headers: {'X-Four-Request-Id': 'central-physical-2'}});
    }});
  await assert.rejects(binding.transport({request: {}}), {code: 'PREFLIGHT_REQUIRED', dispatched: false});
  const manifest = await binding.preflight();
  assert.deepEqual(manifest.tools, schemas);
  assert.deepEqual(await binding.tools({name: 'materials.list', arguments: {}}), toolValue);
  const received = await binding.transport({request: {model: model.model, stream: false}, parentRequestId: 'central-physical-1'});
  assert.equal(received.requestId, 'central-physical-2');
  assert.deepEqual(calls.map(call => call.url.pathname), ['/M/worker/manifest', '/M/worker/tools', '/M/worker/v1/responses']);
  assert.deepEqual(calls[1].body, {name: 'materials_list', arguments: {}});
  assert.equal(calls[2].options.headers['X-Four-Parent-Request-Id'], 'central-physical-1');
  assert.equal(calls.every(call => call.options.headers.Authorization === 'Bearer fixture-capability'), true);
  assert.equal(calls.every(call => call.options.redirect === 'error'), true);
});

test('M gateway bindings fail closed for live gateways and preserve unknown versus rejected dispatch', async () => {
  let calls = 0;
  const live = createGatewayBindings({gateway: 'http://127.0.0.1:12345', token: 'fixture', fetchImpl: async () => {
    calls++;
    return new Response(JSON.stringify({offline: false, model_policy: model, tools: schemas, context_window: 65536}));
  }});
  await assert.rejects(live.preflight(), {code: 'LIVE_DISABLED'});
  await assert.rejects(live.transport({request: {}}), {code: 'PREFLIGHT_REQUIRED'});
  assert.equal(calls, 1);
  assert.throws(() => createGatewayBindings({gateway: 'https://upstream.invalid', token: 'fixture'}), {code: 'GATEWAY_ENDPOINT'});
  let mode = 'manifest';
  const binding = createGatewayBindings({gateway: 'http://127.0.0.1:12345', token: 'fixture', fetchImpl: async () => {
    calls++;
    if (mode === 'manifest') return new Response(JSON.stringify({offline: true, model_policy: model, tools: schemas, context_window: 65536}));
    if (mode === 'unknown') throw Error('Network reset');
    return new Response(JSON.stringify({error: {code: 'BUDGET', message: 'Rejected before transport', dispatched: false}}), {status: 409});
  }});
  await binding.preflight();
  mode = 'unknown';
  await assert.rejects(binding.transport({request: {}}), {code: 'GATEWAY_CONNECTION', dispatched: true});
  mode = 'rejected';
  await assert.rejects(binding.transport({request: {}}), {code: 'BUDGET', dispatched: false});
  assert.equal(calls, 4); // one request per invocation, no automatic retry
  await assert.rejects(runBridge([]), {code: 'LIVE_DISABLED'});
});

test('M exported synthetic fixture builders produce native model-only Note and Obligation commits', async t => {
  assert.deepEqual(syntheticProblem(), problem);
  const f = await fixture(t, {transport: async () => response(syntheticProposal())});
  const {run} = await f.adapter.run();
  assert.equal(run.actions[0].status, 'committed');
  assert.deepEqual(run.actions[0].output_refs, [{id: 'N', rev: 1}, {id: 'O', rev: 1}]);
  const inspected = await f.service.inspect();
  assert.equal(inspected.events.slice(1).every(event => event.actor.kind === 'model'), true);
  assert.equal(Object.values(inspected.objects).flat().some(row => row.kind === 'Review'), false);
});

test('M Python bridge rejects unrelated roots, nonempty initialization, and foreign project identity', async t => {
  const root = await temporary(t);
  await writeFile(join(root, 'preexisting.txt'), 'untouched');
  const service = await createPythonWorkflowService({root});
  await assert.rejects(service.initialize({run_id: 'r', problem, model_policy: model, budget,
    context_budget: {max_tokens: 50_000}, stage_goal: 'fixture', tools_sha256: '0'.repeat(64)}), {code: 'ISOLATION'});
  assert.equal(await readFile(join(root, 'preexisting.txt'), 'utf8'), 'untouched');
  const f = await fixture(t);
  await assert.rejects(f.service.call('workflow-state', {action: 'read', project_id: 'foreign', run_id: 'synthetic-run'}), {code: 'ISOLATION'});
  await assert.rejects(f.service.call('workflow-state', {action: 'continue', project_id: f.initialized.isolation.project_id,
    run_id: 'synthetic-run'}), {code: 'FORBIDDEN'});
});
