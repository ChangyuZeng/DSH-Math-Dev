import test from 'node:test';
import assert from 'node:assert/strict';
import {executeStep, executeLoop, workflowCapability} from '../workflow_runner.mjs';

function fixture({chunks, commitError = false} = {}) {
  let run = {rev: 1, status: 'running', actions: [], spec: {model_policy: {provider: 'fixture', model: 'fixed', effort: 'high'}, budget: {max_output_tokens: 100}}};
  const calls = [];
  const proposal = {summary: 'candidate', changes: [], next_action: 'stop', next_target: null, progress: 'no_progress', limitations: []};
  const call = async (action, args) => {
    calls.push([action, args]);
    if (action === 'workflow-context') return {input_token_bound: 50, text: '{}'};
    if (action === 'workflow-commit') { if (commitError) throw Error('receipt unavailable'); run.actions.at(-1).status = 'committed'; return run; }
    if (args.action === 'reserve') { run.rev++; run.actions.push({action_id: args.operation_id, status: 'running'}); }
    if (args.action === 'propose') { run.rev++; run.actions.at(-1).status = 'proposed'; run.actions.at(-1).proposal = args.proposal; }
    if (args.action === 'pause') { run.rev++; run.status = 'paused'; }
    if (args.action === 'fail') { run.rev++; run.actions[0].status = 'unknown'; }
    return structuredClone(run);
  };
  const llm = {prepareCall: async config => ({config, context: {contextWindow: 1000}, stream: async function* () {
    for (const chunk of chunks ?? [{type: 'text-delta', text: JSON.stringify(proposal)}, {type: 'usage', usage: {inputTokens: 20, outputTokens: 10}}, {type: 'finish', reason: {kind: 'stop'}}]) {
      if (chunk instanceof Error) throw chunk;
      yield chunk;
    }
  }})};
  return {calls, call, llm, options: {call, llm, projectId: 'P', runId: 'R', expectedRev: 1, kind: 'derive'}};
}
test('disabled unless explicitly enabled and service exists', () => {
  assert.equal(workflowCapability({}, {}).enabled, false);
  assert.equal(workflowCapability({enabled: true}, {}).enabled, true);
});
test('one reserved request and accounted proposal before research commit', async () => {
  const f = fixture(); await executeStep(f.options);
  assert.deepEqual(f.calls.map(([action, a]) => action === 'workflow-state' ? a.action : action), ['read', 'workflow-context', 'reserve', 'propose', 'workflow-commit']);
  assert.equal(f.calls[3][1].usage.total_tokens, 30);
});
test('uncertain commit keeps proposal recoverable without rerunning model', async () => {
  const f = fixture({commitError: true}); await assert.rejects(executeStep(f.options), /receipt unavailable/);
  assert.equal(f.calls.some(([, a]) => a.action === 'fail'), false);
  assert.equal(f.calls.filter(([, a]) => a.action === 'reserve').length, 1);
});
test('stream error without accounting becomes unknown, never retried', async () => {
  const f = fixture({chunks: [Error('stream lost')]}); await assert.rejects(executeStep(f.options), /stream lost/);
  assert.equal(f.calls.filter(([, a]) => a.action === 'fail').length, 1);
});
test('partial text survives interruption with unknown usage and no replay', async () => {
  const f = fixture({chunks: [{type: 'text-delta', text: '{"candidate":"durable"'}, Error('stream lost')]});
  await assert.rejects(executeStep(f.options), /stream lost/);
  const saved = f.calls.find(([, a]) => a.action === 'propose')?.[1];
  assert.equal(saved?.proposal.invalid_output, '{"candidate":"durable"');
  assert.equal(saved?.usage, null);
  assert.equal(f.calls.filter(([, a]) => a.action === 'reserve').length, 1);
  assert.equal(f.calls.some(([action]) => action === 'workflow-commit'), false);
});
test('partial known usage is retained but marked incomplete', async () => {
  const f = fixture({chunks: [{type: 'usage', usage: {inputTokens: 20, outputTokens: 3}}, Error('stream lost')]});
  await assert.rejects(executeStep(f.options), /stream lost/);
  assert.equal(f.calls.find(([, a]) => a.action === 'propose')[1].usage.total_tokens, 23);
  assert.equal(f.calls.find(([, a]) => a.action === 'fail')[1].accounting_complete, false);
});
test('serial loop stops after a committed stop recommendation', async () => {
  const f = fixture(); const result = await executeLoop(f.options);
  assert.equal(result.status, 'paused');
  assert.equal(f.calls.filter(([, a]) => a.action === 'reserve').length, 1);
});
test('serial loop reassesses after two no-progress outcomes', async () => {
  const f = fixture({chunks: [{type: 'text-delta', text: JSON.stringify({summary: 'no new result', changes: [], next_action: 'derive', next_target: null, progress: 'no_progress', limitations: []})}, {type: 'usage', usage: {inputTokens: 20, outputTokens: 10}}, {type: 'finish', reason: {kind: 'stop'}}]});
  const result = await executeLoop(f.options);
  assert.equal(result.status, 'paused');
  assert.equal(f.calls.filter(([, a]) => a.action === 'reserve').length, 2);
});
test('resolved model drift rejects before claiming or charging a request', async () => {
  const f = fixture(); f.llm.prepareCall = async config => ({config: {...config, model: 'other'}});
  await assert.rejects(executeStep(f.options), /differ/);
  assert.equal(f.calls.some(([, a]) => a.action === 'reserve'), false);
});
