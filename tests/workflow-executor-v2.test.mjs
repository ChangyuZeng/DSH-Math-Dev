import test from 'node:test';
import assert from 'node:assert/strict';
import {executeToolStep, verificationInput} from '../workflow_executor.mjs';
import {parseExact, canonical, physicalProjection, createMeteredPorts, hash} from '../workflow_ports.mjs';

const clone = value => parseExact(JSON.stringify(value));
const problem = {id: 'P', rev: 2, kind: 'Problem', data: {statement: 'Frozen problem'}, origin: {secret: 'generation-secret'}};
const context = {problem_ref: {id: 'P', rev: 2},
  stage_goal: 'Retain every intermediate row and recovery.json with format dsh-math-workflow-recovery-v2, resume_index and ordered entries.', records: [
  {id: 'ancestor', rev: 1, kind: 'Problem', data: {statement: 'Wrong ancestor'}}, problem,
  {id: 'N', rev: 3, kind: 'Note', data: {body: 'Declared premise'}, origin: {secret: 'generation-secret'}},
  {id: 'unused', rev: 1, kind: 'Note', data: {body: 'prior-review-secret'}},
], directory: ['directory-secret'], route: 'route-secret', output_contract: {summary: 'text'}};
const proposal = (n = 1) => ({summary: 'Candidate ' + n, changes: [{kind: 'Note', id: 'C' + n,
  data: {uses: [{id: 'N', rev: 3}], body: 'Argument ' + n}}], next_action: 'stop', next_target: null,
  progress: 'new_candidate', limitations: []});
const response = value => ({model: 'gpt-6-astra', status: 'completed', output: [{type: 'message',
  content: [{type: 'output_text', text: JSON.stringify(value)}]}]});
const toolResponse = (name, callId, candidate = {values: [1]}) => ({model: 'gpt-6-astra', status: 'completed',
  output: [{type: 'function_call', name, call_id: callId, arguments: JSON.stringify(name === 'compute' ? {code: 'print(1)'} : {candidate})}]});

function fixture(responses, {policy = 'model-directed-v1', dispatch, commitFailure = false, snapshot = context} = {}) {
  const requests = [], effects = [], receipts = new Map(), candidateCalls = [];
  let state = {run_id: 'run', rev: 1, status: 'running', actions: [], execution_events: [], physical_requests: [],
    spec: {format: 'dsh-math-workflow-v2', problem_ref: context.problem_ref, model_policy: {provider: 'openai-codex', model: 'gpt-6-astra', effort: 'max'},
      execution: {verification_policy: policy, max_revisions: 2, checker: {code_sha256: 'a'.repeat(64)}}, budget: {requests: 120, max_output_tokens: 128000}}};
  const call = async (action, args) => {
    if (action === 'workflow-context') return {text: JSON.stringify(snapshot)};
    if (action === 'workflow-candidate') {
      candidateCalls.push(clone(args));
      return {candidate_sha256: hash(args.candidate), evidence_ref: {id: 'E', rev: 1}};
    }
    if (action === 'workflow-candidate-check') return {authority: 'program'};
    if (action === 'workflow-tool') {
      const prior = receipts.get(args.call_id);
      if (args.phase === 'claim') {
        if (prior) { assert.equal(prior.args_sha256, args.args_sha256); if (prior.status !== 'completed') throw Error('RECONCILE_REQUIRED'); return clone(prior); }
        receipts.set(args.call_id, {...clone(args), status: 'started'});
        return clone(receipts.get(args.call_id));
      }
      receipts.set(args.call_id, {...prior, status: 'completed', result: clone(args.result)});
      return clone(receipts.get(args.call_id));
    }
    if (action === 'workflow-commit') {
      if (commitFailure) throw Error('Commit receipt unavailable');
      state.actions.at(-1).status = 'committed'; state.rev++; return clone(state);
    }
    assert.equal(action, 'workflow-state');
    if (args.action === 'read') return clone(state);
    assert.equal(args.expected_rev, state.rev);
    if (args.action === 'reserve') state.actions.push({action_id: args.operation_id, status: 'running'});
    else if (args.action === 'event') state.execution_events.push(clone(args.event));
    else if (args.action === 'account') state.physical_requests.push(...clone(args.accounting.requests));
    else if (args.action === 'propose') Object.assign(state.actions.at(-1), {status: 'proposed', proposal: clone(args.proposal)});
    else if (args.action === 'fail') { state.actions.at(-1).status = 'unknown'; state.status = 'needs_reconcile'; }
    else if (args.action === 'pause') state.status = 'paused';
    else assert.fail(args.action);
    state.rev++; return clone(state);
  };
  const modelPort = {prepare: async () => ({tools: ['compute', 'check', 'submit'].map(name => ({name}))}),
    complete: async request => {
      requests.push(clone(request)); const value = responses.shift();
      if (value instanceof Error) throw value;
      assert.ok(value, 'Unexpected extra model call');
      const requestId = 'request-' + requests.length;
      return {requestId, response: value, accounting: {format: 'dsh-math-physical-accounting-v2', requests: [{request_id: requestId, role: request.role}]}};
    }};
  const toolPort = {dispatch: async (name, args, identity) => {
    assert.equal(receipts.get(identity.callId)?.status, 'started', 'Durable receipt must precede effect');
    effects.push({name, args: clone(args), identity});
    return dispatch ? dispatch(name, args, identity) : {ok: true, result: {legal: false, target_met: false}};
  }};
  return {requests, effects, receipts, candidateCalls, get state() { return state; },
    execute: () => executeToolStep({call, modelPort, toolPort, projectId: 'project', runId: 'run', expectedRev: state.rev, kind: 'derive'})};
}

test('exact numeric tokens survive and duplicate names are rejected at every depth', () => {
  const source = '{"values":[9007199254740993,-9007199254740993,1e3,0.10],"s":"escaped\\\"quote"}';
  assert.equal(JSON.stringify(parseExact(source)), source);
  assert.throws(() => parseExact('{"a":{"x":1,"x":2}}'), /Duplicate JSON key/);
  assert.throws(() => parseExact('{"a":1,"\\u0061":2}'), /Duplicate JSON key/);
});

test('fresh verification uses the exact problem and declared premises only', () => {
  const value = verificationInput(JSON.stringify(context), proposal());
  assert.equal(value.problem.rev, 2); assert.equal(value.problem.data.statement, 'Frozen problem');
  assert.deepEqual(value.declared_premises.map(item => item.id), ['N']);
  assert.doesNotMatch(JSON.stringify(value), /secret|Wrong ancestor|origin/);
});

test('program-triggered fresh revision retains the full frozen task requirements', async () => {
  const f = fixture([toolResponse('check', 'failed'), toolResponse('submit', 'final')], {
    policy: 'verify-revise-v1', dispatch: name => name === 'submit'
      ? {ok: true, result: {independent_check: {legal: true, target_met: true}}}
      : {ok: true, result: {legal: false, target_met: false}},
  });
  await f.execute();
  assert.deepEqual(f.requests.map(item => item.role), ['worker', 'reviser']);
  const initial = parseExact(f.requests[0].request.input[0].content[0].text);
  const input = f.requests[1].request.input;
  assert.equal(input.length, 1);
  const revised = parseExact(input[0].content[0].text);
  assert.equal(revised.stage_goal, initial.stage_goal);
  assert.deepEqual(revised.output_contract, initial.output_contract);
  assert.equal(revised.problem.rev, initial.problem_ref.rev);
  assert.doesNotMatch(JSON.stringify(input), /secret|Wrong ancestor|origin/);
});

test('advisory-triggered fresh revision retains the same task without generation history', async () => {
  const f = fixture([response(proposal()), response({verdict: 'fail', gaps: ['Specific public gap']}),
    response({...proposal(2), changes: []})], {policy: 'verify-revise-v1'});
  await f.execute();
  assert.deepEqual(f.requests.map(item => item.role), ['worker', 'verifier', 'reviser']);
  const initial = parseExact(f.requests[0].request.input[0].content[0].text);
  const input = f.requests[2].request.input;
  assert.equal(input.length, 1);
  const revised = parseExact(input[0].content[0].text);
  assert.equal(revised.stage_goal, initial.stage_goal);
  assert.deepEqual(revised.output_contract, initial.output_contract);
  assert.deepEqual(revised.declared_premises.map(item => item.id), ['N']);
  assert.doesNotMatch(JSON.stringify(input), /secret|Wrong ancestor|origin/);
});

test('missing stage requirements fail before dispatching a fresh reviser', async () => {
  const snapshot = clone(context);
  delete snapshot.stage_goal;
  const f = fixture([toolResponse('check', 'failed')], {policy: 'verify-revise-v1', snapshot});
  await assert.rejects(f.execute(), error => error.code === 'CONTEXT_STAGE_GOAL');
  assert.equal(f.requests.length, 1);
});

test('all physical retry rows remain visible and optional usage is nullable', () => {
  const value = physicalProjection({format: 'four-harness-accounting-v2', logical_request_id: 'one',
    request_ids: ['a', 'b'], requests: [{request_id: 'a', status: 'retryable', usage: null},
      {request_id: 'b', status: 'finished', usage: {input_tokens: 10, output_tokens: 2, total_tokens: 12, complete: true}}]}, 'reviser');
  assert.equal(value.requests.length, 2); assert.equal(value.requests[0].usage, null);
  assert.equal(value.requests[0].usage_complete, false); assert.equal(value.requests[1].usage_complete, true);
  const partial = physicalProjection({format: 'four-harness-accounting-v2', logical_request_id: 'partial',
    request_ids: ['p'], requests: [{request_id: 'p', status: 'unknown', usage: {
      input_tokens: 10, output_tokens: 0, total_tokens: 10, complete: false}}]}, 'worker');
  assert.equal(partial.requests[0].usage_complete, false); assert.equal(partial.requests[0].usage.total_tokens, 10);
  assert.equal(value.requests[1].usage.cached_input_tokens, null); assert.equal(value.requests[1].role, 'reviser');
  assert.throws(() => physicalProjection({format: 'four-harness-accounting-v2', request_ids: ['a'], requests: []}, 'worker'), /projection/);
});

test('A archives a proposal without invoking B or adding hidden model calls', async () => {
  const f = fixture([response(proposal())]); await f.execute();
  assert.equal(f.requests.length, 1); assert.equal(f.candidateCalls[0].candidate_kind, 'proposal');
  assert.equal(f.state.actions[0].status, 'committed');
});

test('B verifier context is fresh, tools absent, and uncertainty never becomes a pass', async () => {
  const f = fixture([response(proposal()), response({verdict: 'inconclusive', gaps: ['Missing premise']})], {policy: 'verify-revise-v1'});
  await f.execute();
  assert.deepEqual(f.requests.map(item => item.role), ['worker', 'verifier']);
  assert.deepEqual(f.requests[1].request.tools, []);
  const input = JSON.stringify(f.requests[1].request.input);
  assert.doesNotMatch(input, /secret|Wrong ancestor|route-secret/);
  assert.equal(f.state.actions[0].proposal.next_action, 'stop');
  assert.ok(f.state.actions[0].proposal.limitations.includes('Missing premise'));
});

test('B targeted revision stops when the identical candidate returns', async () => {
  const f = fixture([toolResponse('check', 'first'), toolResponse('check', 'second')], {policy: 'verify-revise-v1'});
  await f.execute();
  assert.deepEqual(f.requests.map(item => item.role), ['worker', 'reviser']);
  assert.equal(f.state.execution_events.filter(item => item.type === 'revision_triggered').length, 1);
  assert.ok(f.state.execution_events.some(item => item.reason === 'revision_no_growth'));
});

test('B program and prose revisions share a maximum of two in one chain', async () => {
  const fail = response({verdict: 'fail', gaps: ['Specific failed inference']});
  const f = fixture([toolResponse('check', 'first'), response(proposal(1)), fail, response(proposal(2)), fail], {policy: 'verify-revise-v1'});
  await f.execute();
  assert.equal(f.state.execution_events.filter(item => item.type === 'revision_triggered').length, 2);
  assert.equal(f.requests.length, 5); assert.equal(f.state.actions[0].proposal.next_action, 'stop');
});

test('identical native tool call reuses a completed receipt and does not repeat its effect', async () => {
  const f = fixture([toolResponse('compute', 'same'), toolResponse('compute', 'same'), response({...proposal(), changes: []})]);
  await f.execute(); assert.equal(f.effects.length, 1); assert.equal(f.receipts.get('same').status, 'completed');
});

test('unknown tool effect retains its claim and a proposed commit failure stays recoverable', async () => {
  const f = fixture([toolResponse('compute', 'unknown')], {dispatch: () => { throw Error('Interrupted after effect'); }});
  await assert.rejects(f.execute(), /Interrupted/); assert.equal(f.receipts.get('unknown').status, 'started');
  assert.equal(f.state.status, 'needs_reconcile');
  const committed = fixture([response(proposal())], {commitFailure: true});
  await assert.rejects(committed.execute(), /Commit receipt/);
  assert.equal(committed.state.actions[0].status, 'proposed');
});

test('first independently valid explicit submit stops before another B model call', async () => {
  const f = fixture([toolResponse('submit', 'final')], {policy: 'verify-revise-v1',
    dispatch: () => ({ok: true, result: {independent_check: {legal: true, target_met: true}}})});
  await f.execute(); assert.equal(f.requests.length, 1);
  assert.ok(f.state.execution_events.some(item => item.reason === 'first_independent_submit_success'));
});

test('ports reject external origins before using role credentials', () => {
  assert.throws(() => createMeteredPorts({gateway: 'https://example.com', bindings: {}}), /loopback/);
  assert.equal(canonical(parseExact('{"b":2,"a":9007199254740993}')), '{"a":9007199254740993,"b":2}');
});
