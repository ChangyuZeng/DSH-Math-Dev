import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {projectToolResult} from '../workflow_tool_view.mjs';
import {executeToolStep} from '../workflow_executor.mjs';
import {hash, parseExact} from '../workflow_ports.mjs';

const clone = value => parseExact(JSON.stringify(value));
const metrics = () => ({request_id: 'tool-1', model_request_id: 'request-1', run_id: 'run', tool: 'compute',
  params_sha256: 'a'.repeat(64), result_sha256: 'b'.repeat(64), started_at: '2026-11-13T05:00:00.000000+00:00', finished_at: '2026-11-13T05:00:03.500000+00:00',
  wall_seconds: 3.5, parent_cpu_seconds: 0.1, child_cpu_seconds: null, known_child_cpu_seconds: 2,
  cpu_seconds: null, known_cpu_seconds: 2.1, cpu_accounting_complete: false,
  resource_scope: 'observed_tool_cost', resource_mode: 'observed', child_cpu_observation_source: 'wait4_rusage',
  peak_memory_bytes: null, known_peak_memory_bytes: 4096, memory_accounting_complete: false,
  future_metric: {samples: [1, 2], scope: 'unknown-do-not-drop'}});
const freeze = value => { if (value && typeof value === 'object') { Object.freeze(value); Object.values(value).forEach(freeze); } return value; };
const names = ['materials_list', 'materials_read', 'workspace_write', 'workspace_read', 'compute', 'check', 'submit'];

for (const name of names) test(`${name}: preserves full payload, identity, unknowns and incomplete resource scope`, () => {
  const payload = {text: 'material and code\n' + 'x'.repeat(24000), stdout: 'exact stdout\n', stderr: 'exact stderr\n',
    returncode: -9, timed_out: true, output_truncated: true, stdout_bytes: 90000, stderr_bytes: 13,
    timeout_seconds: 2, legal: false, target_met: false, candidate_sha256: 'c'.repeat(64), checker_sha256: 'd'.repeat(64),
    independent_check: {legal: false, target_met: false, errors: ['D7: 41 vertices, expected 42', 'D9: wrong certificate length']},
    receipt_id: 'receipt-1', problem_ref: {id: 'P', rev: 2}, authority: 'local_claim', global_proof: false,
    metrics: {mathematical_nested_metrics: 'preserve'}, future_result: [1, 2]};
  const full = freeze({ok: true, result: payload, metrics: metrics(), research_receipt: {id: 'E', rev: 1}, future_root: {keep: true}});
  const before = JSON.stringify(full), ref = freeze({call_id: 'call-1'});
  const view = projectToolResult(name, full, ref);
  assert.deepEqual(view.result, full.result);
  assert.deepEqual(view.research_receipt, full.research_receipt);
  assert.deepEqual(view.future_root, full.future_root);
  const omitted = ['started_at', 'finished_at', 'parent_cpu_seconds', 'child_cpu_seconds', 'known_child_cpu_seconds'];
  for (const key of Object.keys(full.metrics).filter(key => !omitted.includes(key)))
    assert.deepEqual(view.metrics[key], full.metrics[key], key);
  for (const key of omitted) assert.equal(Object.hasOwn(view.metrics, key), false);
  assert.equal(view.tool_result_projection.metrics_hash_scope, 'original_tool_reply_before_metrics_and_host_enrichment');
  assert.deepEqual(view.tool_result_projection.host_audit_ref, ref);
  assert.ok(Buffer.byteLength(JSON.stringify(view), 'utf8') < Buffer.byteLength(before, 'utf8'));
  view.result.independent_check.legal = true;
  view.metrics.future_metric.samples.push(3);
  view.tool_result_projection.host_audit_ref.call_id = 'mutated';
  assert.equal(JSON.stringify(full), before); assert.equal(ref.call_id, 'call-1');
});

test('errors, unknown formats, metadata collisions and exact numbers survive conservatively', () => {
  for (const full of [null, ['unknown'], {ok: false, error: {code: 'timeout', message: 'failed', result: {stdout: 'partial'}}},
    {ok: false, error: {code: 'host_error'}, metrics: 'future-format'},
    {ok: true, metrics: {...metrics(), resource_scope: 'future-wrapper'}},
    {ok: false, error: {code: 'accounting_failed'}, metrics: {started_at: 'only-start', parent_cpu_seconds: 2}},
    {ok: true, metrics: metrics(), tool_result_projection: {future: true}}]) {
    assert.deepEqual(projectToolResult('unknown', freeze(full)), full);
  }
  const error = {ok: false, error: {code: 'timeout', message: 'not a mathematical failure'}, metrics: metrics()};
  assert.deepEqual(projectToolResult('compute', error).error, error.error);
  const full = parseExact('{"ok":true,"result":{"values":[9007199254740993,0.10]},"metrics":{"wall_seconds":0.10,"started_at":"t"}}');
  assert.equal(JSON.stringify(projectToolResult('future_tool', full).result), '{"values":[9007199254740993,0.10]}');
  assert.equal(JSON.stringify(full.metrics), '{"wall_seconds":0.10,"started_at":"t"}');
});

test('short receipts and long UTF-8 audit references fall back to a complete independent copy', () => {
  for (const [full, ref] of [
    [{ok: true, result: {text: '短回执', stdout: '5\n'}, metrics: {resource_scope: 'observed_tool_cost', wall_seconds: 1, started_at: 's'}}, {request_id: 'r'}],
    [{ok: false, error: {code: 'timeout'}, metrics: metrics()}, {host_audit_id: '仅宿主可读'.repeat(200)}],
  ]) {
    const before = JSON.stringify(full);
    freeze(full); freeze(ref);
    const view = projectToolResult('compute', full, ref);
    assert.deepEqual(view, full);
    assert.notEqual(view, full); assert.notEqual(view.metrics, full.metrics);
    assert.ok(Buffer.byteLength(JSON.stringify(view), 'utf8') <= Buffer.byteLength(before, 'utf8'));
    view.metrics.wall_seconds = 999;
    assert.equal(JSON.stringify(full), before);
  }
});

test('unknown or invalid elapsed totals retain original timestamp observations', () => {
  for (const wall_seconds of [null, -1, '3.5']) {
    const full = freeze({ok: true, result: {}, metrics: {...metrics(), wall_seconds}});
    const view = projectToolResult('compute', full);
    assert.equal(view.metrics.started_at, full.metrics.started_at);
    assert.equal(view.metrics.finished_at, full.metrics.finished_at);
    assert.equal(view.metrics.wall_seconds, wall_seconds);
  }
});

// Small host fixture: model requests are captured after durable receipt publication.
async function executeFixture(toolCalls, results) {
  const requests = [], receipts = new Map(), order = [], rawUsage = {input_tokens: 17, output_tokens: 5, total_tokens: 22};
  let state = {rev: 1, status: 'running', actions: [], execution_events: [], accounting: [],
    spec: {format: 'dsh-math-workflow-v2', model_policy: {model: 'fixture'}, problem_ref: {id: 'P', rev: 1},
      budget: {max_output_tokens: 50}, execution: {verification_policy: 'model-directed-v1', checker: {code_sha256: 'checker'}}}};
  const call = async (name, args) => {
    if (name === 'workflow-context') return {text: '{"output_contract":{}}'};
    if (name === 'workflow-candidate') { order.push('candidate'); return {candidate_sha256: hash(args.candidate)}; }
    if (name === 'workflow-candidate-check') { order.push('archive_check'); assert.deepEqual(args.result.metrics, clone(metrics())); return {authority: 'local_claim'}; }
    if (name === 'workflow-tool') {
      if (args.phase === 'claim') return receipts.has(args.call_id) ? clone(receipts.get(args.call_id)) : {status: 'started'};
      order.push('finish'); receipts.set(args.call_id, {status: 'completed', result: clone(args.result)}); return {};
    }
    if (name === 'workflow-commit') { state.actions.at(-1).status = 'committed'; return clone(state); }
    assert.equal(name, 'workflow-state');
    if (args.action === 'read') return clone(state);
    assert.equal(args.expected_rev, state.rev);
    if (args.action === 'reserve') { order.push('reserve'); state.reservation = args.max_output_tokens; state.actions.push({action_id: args.operation_id, status: 'running'}); }
    if (args.action === 'event') state.execution_events.push(clone(args.event));
    if (args.action === 'account') { order.push('account'); state.accounting.push(clone(args.accounting)); }
    if (args.action === 'propose') state.actions.at(-1).proposal = args.proposal;
    if (args.action === 'pause') state.status = 'paused';
    state.rev++; return clone(state);
  };
  const modelPort = {prepare: async () => ({tools: names.map(name => ({name}))}), complete: async ({request}) => {
    requests.push(clone(request));
    if (requests.length > 1) assert.ok(receipts.size, 'Full receipt must be published before projected model input');
    const item = toolCalls.shift();
    return {requestId: `request-${requests.length}`, accounting: {requests: [{request_id: `request-${requests.length}`, raw_usage: rawUsage}]},
      response: {model: 'fixture', status: 'completed', output: item ? [{type: 'function_call', name: item.name, call_id: item.id,
        arguments: '{"candidate":{"values":[1]}}'}] : [{type: 'message', content: [{type: 'output_text',
        text: '{"summary":"done","changes":[],"next_action":"stop"}'}]}]}};
  }};
  let effects = 0;
  await executeToolStep({call, modelPort, toolPort: {dispatch: async () => { effects++; order.push('dispatch'); return clone(results.shift()); }},
    projectId: 'project', runId: 'run', expectedRev: 1, kind: 'derive'});
  return {state, requests, receipts, order, effects, rawUsage};
}

test('executor archives full receipt before projection, reuses original receipt, preserves usage and reservation', async () => {
  const original = {ok: true, result: {stdout: 'original', legal: true, target_met: true}, metrics: metrics()};
  const f = await executeFixture([{name: 'check', id: 'same'}, {name: 'check', id: 'same'}], [original]);
  assert.equal(f.effects, 1); assert.equal(f.requests.length, 3, 'Successful check never substitutes for submit');
  assert.deepEqual(f.order.slice(0, 5), ['reserve', 'account', 'candidate', 'dispatch', 'archive_check']);
  assert.ok(f.order.indexOf('finish') > f.order.indexOf('archive_check'));
  assert.deepEqual(f.receipts.get('same').result.metrics, clone(original.metrics));
  assert.deepEqual(f.receipts.get('same').result.research_receipt, {authority: 'local_claim'});
  const views = f.requests.slice(1).map(request => JSON.parse(request.input.find(item => item.type === 'function_call_output').output));
  assert.deepEqual(views[0], views[1]);
  assert.equal(views[0].metrics.started_at, undefined);
  assert.deepEqual(views[0].result, original.result);
  assert.deepEqual(views[0].tool_result_projection.host_audit_ref, {call_id: 'same'});
  assert.equal(f.state.reservation, 50);
  assert.equal(f.state.accounting.length, 3);
  assert.ok(f.state.accounting.every(item => JSON.stringify(item.requests[0].raw_usage) === JSON.stringify(f.rawUsage)));
  assert.ok(f.state.execution_events.some(event => event.type === 'tool_finished' && event.result_sha256 === hash(f.receipts.get('same').result)));
});

test('only independently legal successful submit triggers native stop; illegal candidates and errors continue', async () => {
  const calls = [{name: 'submit', id: 'D7'}, {name: 'submit', id: 'D9'}, {name: 'submit', id: 'error'}, {name: 'submit', id: 'final'}];
  const results = [
    {ok: true, result: {independent_check: {legal: false, target_met: false, errors: ['41 points, expected 42']}}},
    {ok: true, result: {independent_check: {legal: false, target_met: false, errors: ['wrong format length']}}},
    {ok: false, error: {code: 'timeout'}, result: {independent_check: {legal: true, target_met: true}}},
    {ok: true, result: {independent_check: {legal: true, target_met: true}}},
  ].map(result => ({...result, metrics: metrics()}));
  const f = await executeFixture(calls, results);
  assert.equal(f.requests.length, 4); assert.equal(f.effects, 4);
  for (const [index, id] of ['D7', 'D9', 'error'].entries()) {
    const view = JSON.parse(f.requests[index + 1].input.find(item => item.call_id === id && item.type === 'function_call_output').output);
    assert.deepEqual(view.result, f.receipts.get(id).result.result);
    assert.deepEqual(view.error, f.receipts.get(id).result.error);
  }
  assert.equal(f.state.execution_events.filter(event => event.type === 'native_stop').length, 1);
  assert.equal(f.state.execution_events.find(event => event.type === 'native_stop').call_id, 'final');
});

test('plain native binding renders the shared view but returns and stops from the full receipt', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tool-view-plain-'));
  try {
    const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
    const full = {ok: true, result: {status: 'completed', verification_basis: 'fresh_process_exact_audit_rerun',
      independent_check: {legal: true, target_met: true}, stdout: 'unaltered'}, metrics: metrics()};
    const fixturePath = path.join(dir, 'fixture.json'), output = path.join(dir, 'output.json');
    fs.writeFileSync(fixturePath, JSON.stringify(full));
    const modules = path.join(dir, 'modules');
    const plugins = ['cordis', 'dsh-llm', 'dsh-session', 'dsh-agent', 'dsh-system-prompt', 'dsh-tools',
      'dsh-session-projection', 'dsh-agent-loop', 'dsh-token-meter', 'dsh-compaction-basic', 'dsh-llm-pi-ai'];
    for (const name of plugins) {
      const target = path.join(modules, '@deepseek-ai', name, 'lib'); fs.mkdirSync(target, {recursive: true});
      fs.writeFileSync(path.join(target, 'index.js'), name === 'cordis' ? `
        const fs = require('node:fs');
        exports.Context = class {
          constructor() {
            this.tools = {register: tool => {this.tool = tool;}};
            this.agents = {create: async () => ({dispose: async () => {}, agent: {
              constructor: {name: 'FixtureAgent'}, ctx: {tools: {schemas: () => [this.tool]}},
              followup: () => {}, session: {seq: 1, eventAt: () => ({type: 'turn/end', data: {reason: {kind: 'completed'}}})},
              whenIdle: async () => {
                // Seed the native parent through the real runner stream listener.
                const stream = this.streamListener({provider:'openai-codex', model:'gpt-6-astra', maxTokens:128000, reasoningEffort:'max'}, async function* () {
                  await globalThis.fetch('http://127.0.0.1:1/M/worker/v1/responses', {method:'POST', body:'{}'});
                  yield {type:'block-end', block:{type:'tool-call', id:'plain-call'}};
                });
                for await (const item of stream) {}
                let concluded = 0;
                const value = await this.tool.execute({}, {callId: 'plain-call', concludeTurn: () => concluded++});
                const before = JSON.stringify(value);
                const rendered = this.tool.output.render({}, value);
                fs.writeFileSync(${JSON.stringify(output)}, JSON.stringify({value, rendered, concluded, unchanged: JSON.stringify(value) === before}));
              }
            }})};
          }
          async plugin() {}
          on(name, listener) { if (name === 'llm/stream') this.streamListener = listener; }
        };
      ` : name === 'dsh-llm' ? 'exports.createUserMessage = x => x; exports.isAgentLoopRequest = () => true;' : 'exports.default = () => {};');
    }
    const preload = path.join(dir, 'preload.mjs');
    fs.writeFileSync(preload, `import fs from 'node:fs'; globalThis.fetch = async () => ({ok: true, status: 200, headers: new Headers({'X-Four-Request-Id':'parent'}), json: async () => JSON.parse(fs.readFileSync(${JSON.stringify(fixturePath)}, 'utf8'))});`);
    const config = path.join(dir, 'config.json');
    fs.writeFileSync(config, JSON.stringify({modules, gateway: 'http://127.0.0.1:1/M/worker', sessionId: 'run',
      tools: [{name: 'submit'}], events: path.join(dir, 'events.jsonl'), prompt: 'offline fixture',
      toolViewModule: path.join(root, 'workflow_tool_view.mjs')}));
    const result = spawnSync(process.execPath, ['--import', preload, path.join(root, 'evaluation/session2/plain/runner.mjs'), config], {encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
    const observed = JSON.parse(fs.readFileSync(output, 'utf8'));
    assert.deepEqual(observed.value, full); assert.equal(observed.unchanged, true); assert.equal(observed.concluded, 1);
    const view = JSON.parse(observed.rendered[0].text);
    assert.deepEqual(view, projectToolResult('submit', full, {request_id: 'tool-1'}));
  } finally {
    // dir is the exact mkdtemp-created fixture root above.
    assert.ok(path.resolve(dir).startsWith(path.join(os.tmpdir(), 'tool-view-plain-')));
    fs.rmSync(dir, {recursive: true, force: true});
  }
});
