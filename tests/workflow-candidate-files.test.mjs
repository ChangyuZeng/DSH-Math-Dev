import test from 'node:test';
import assert from 'node:assert/strict';
import {executeToolStep} from '../workflow_executor.mjs';
import {canonical, hash, parseExact} from '../workflow_ports.mjs';

const copy = value => parseExact(JSON.stringify(value));
const source = ' { "candidate_file": { "path": "certificate.json", "sha256": "' + 'b'.repeat(64) + '", "size": 123 } } ';
const pinned = parseExact('{"values":[9007199254740993]}');
const proposal = {summary: 'offline stop', changes: [], next_action: 'stop', next_target: null, progress: 'no_progress', limitations: []};
function fixture({policy = 'model-directed-v1', prepareFailure = false, preparation = true, submit = false} = {}) {
  let state = {run_id: 'run', rev: 1, status: 'running', actions: [], execution_events: [], physical_requests: [],
    spec: {format: 'dsh-math-workflow-v2', problem_ref: {id: 'P', rev: 1}, budget: {requests: 120, max_output_tokens: 128000},
      model_policy: {provider: 'openai-codex', model: 'gpt-6-astra', effort: 'max'},
      execution: {verification_policy: policy, max_revisions: 2, checker: {code_sha256: 'a'.repeat(64)}}}};
  const receipts = new Map(), order = [], archived = [], requests = [];
  let models = 0;
  const call = async (action, args) => {
    if (action === 'workflow-context') return {text: JSON.stringify({problem_ref: {id: 'P', rev: 1}, stage_goal: 'frozen task',
      records: [{kind: 'Problem', id: 'P', rev: 1, data: {statement: 'public fixture'}}], output_contract: {}})};
    if (action === 'workflow-tool') {
      if (args.phase === 'claim') {
        if (receipts.has(args.call_id)) return copy(receipts.get(args.call_id));
        receipts.set(args.call_id, {status: 'started'}); order.push('claim'); return {status: 'started'};
      }
      receipts.set(args.call_id, {status: 'completed', result: copy(args.result)}); order.push('finish'); return {};
    }
    if (action === 'workflow-candidate') {
      order.push('archive'); archived.push(copy(args)); return {candidate_sha256: hash(args.candidate), evidence_ref: {id: 'E', rev: 1}};
    }
    if (action === 'workflow-candidate-check') {order.push('review'); return {result_status: 'fail'};}
    if (action === 'workflow-commit') {state.actions.at(-1).status = 'committed'; state.rev++; return copy(state);}
    assert.equal(action, 'workflow-state');
    if (args.action === 'read') return copy(state);
    assert.equal(args.expected_rev, state.rev);
    if (args.action === 'reserve') state.actions.push({action_id: args.operation_id, status: 'running'});
    else if (args.action === 'event') state.execution_events.push(copy(args.event));
    else if (args.action === 'account') state.physical_requests.push(...copy(args.accounting.requests));
    else if (args.action === 'propose') Object.assign(state.actions.at(-1), {proposal: copy(args.proposal), status: 'proposed'});
    else if (args.action === 'pause') state.status = 'paused';
    else if (args.action === 'fail') state.status = 'needs_reconcile';
    else assert.fail(args.action);
    state.rev++; return copy(state);
  };
  const modelPort = {prepare: async () => ({tools: ['check', 'submit'].map(name => ({name}))}), complete: async request => {
    requests.push(copy(request)); models++;
    const output = models === 1 ? [{type: 'function_call', name: submit ? 'submit' : 'check', call_id: 'file', arguments: source}]
      : [{type: 'message', content: [{type: 'output_text', text: JSON.stringify(proposal)}]}];
    return {requestId: 'physical-' + models, response: {model: 'gpt-6-astra', status: 'completed', output},
      accounting: {format: 'dsh-math-physical-accounting-v2', requests: [{request_id: 'physical-' + models, role: request.role}]}};
  }};
  const toolPort = {dispatch: async (name, args, options) => {
    order.push('dispatch'); assert.equal(options.preparedCandidate, 'host-token');
    assert.equal(canonical(args), canonical(parseExact(source))); assert.equal(archived.length, 1);
    assert.equal(receipts.get('file').status, 'started');
    return {ok: true, result: {candidate_sha256: hash(pinned), ...(submit ? {independent_check: {legal: true, target_met: true}} : {legal: false, target_met: false})}};
  }};
  if (preparation) toolPort.prepareCandidate = async (name, args, options) => {
    order.push('prepare'); assert.equal(options.callId, 'file'); assert.equal(options.parentRequestId, 'physical-1');
    assert.equal(receipts.get('file').status, 'started');
    return prepareFailure ? {ok: false, error: {code: 'invalid_candidate_json', message: 'strict JSON required'}, metrics: {phase: 'candidate_preparation'}}
      : {prepare_id: 'host-token', candidate: pinned, candidate_sha256: hash(pinned), candidate_file: args.candidate_file};
  };
  return {order, archived, receipts, requests, state: () => state,
    execute: () => executeToolStep({call, modelPort, toolPort, projectId: 'project', runId: 'run', expectedRev: state.rev, kind: 'derive'})};
}

test('both policies claim, prepare, archive resolved exact candidate, dispatch pinned token, and retain original source', async () => {
  for (const policy of ['model-directed-v1', 'verify-revise-v1']) {
    const f = fixture({policy}); await f.execute();
    assert.deepEqual(f.order.slice(0, 6), ['claim', 'prepare', 'archive', 'dispatch', 'review', 'finish']);
    const archived = f.archived[0];
    assert.equal(archived.source_arguments, source);
    assert.equal(archived.raw_arguments, canonical({candidate: pinned}));
    assert.equal(canonical(archived.candidate), canonical(pinned));
    assert.equal(f.receipts.get('file').status, 'completed');
    if (policy === 'verify-revise-v1') {
      assert.equal(f.requests[1].role, 'reviser');
      assert.match(JSON.stringify(f.requests[1].request.input), /9007199254740993/);
    }
  }
});

test('failed preparation retains a completed failure receipt and never archives or dispatches a different candidate', async () => {
  const f = fixture({prepareFailure: true}); await f.execute();
  assert.deepEqual(f.order.filter(item => ['prepare', 'dispatch', 'review'].includes(item)), ['prepare']);
  assert.equal(f.receipts.get('file').result.error.code, 'invalid_candidate_json');
  assert.equal(f.receipts.get('file').status, 'completed');
  assert.equal(f.archived.filter(item => item.candidate_kind !== 'proposal').length, 0);
});

test('missing host prepare port fails closed without dispatch', async () => {
  const f = fixture({preparation: false});
  await assert.rejects(f.execute(), error => error.code === 'CANDIDATE_PREPARE_REQUIRED');
  assert.equal(f.order.includes('dispatch'), false);
});

test('an explicit pinned file submit with a fresh success stops before any extra model request', async () => {
  const f = fixture({submit: true, policy: 'verify-revise-v1'}); await f.execute();
  assert.equal(f.requests.length, 1);
  assert.ok(f.state().execution_events.some(event => event.reason === 'first_independent_submit_success'));
});
