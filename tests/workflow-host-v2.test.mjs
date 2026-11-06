import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, realpathSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, join, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createWorkflowV2Host} from '../workflow_host.mjs';

const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const setup = `
import json,sys
from backend import Projects
from workflow_store import WorkflowStore
from workflow_runtime import qualified_spec
from workflow_candidates import certificate_contract
root=sys.argv[1]; projects=Projects(root); store=WorkflowStore(root); result=[]
for i in range(2):
 session='session-'+str(i)
 project=projects.create(session,certificate_contract('D1')['problem'])['id']
 spec=qualified_spec(project,'D1')
 run=store.dispatch(dict(action='prepare',project_id=project,run_id='run',expected_rev=0,operation_id='prepare',spec=spec))
 run=store.dispatch(dict(action='start',project_id=project,run_id='run',expected_rev=run['rev'],operation_id='start'))
 result.append(dict(project_id=project,session_id=session,run_id='run',expected_rev=run['rev'],kind='derive',mode='step'))
print(json.dumps(result))
`;
function harness(t, {enabled = true} = {}) {
  const temp = realpathSync(tmpdir()), root = realpathSync(mkdtempSync(join(temp, 'dsh-workflow-v2-host-')));
  const runs = JSON.parse(execFileSync('python3', ['-B', '-c', setup, root], {cwd: repo, encoding: 'utf8'}));
  const policy = {mode: 'danger-full-access', workspaceRoot: root}, plan = {active: false, pending: false};
  const tools = new Map(), requests = [], starts = [], closes = [], effects = [], hooks = {};
  const agents = runs.map(({session_id: id}) => ({id, session: {id, header: {id, isSeeded: false}}, ctx: {}}));
  const ctx = {agents: {get: id => agents.find(agent => agent.id === id)}, tools: {register: tool => tools.set(tool.name, tool)},
    on() {}, get(name) {
      if (name === 'planMode') return {get: () => plan};
      if (name === 'sandboxPolicy') return {resolve: ({session}) => ({...policy, sessionId: session.id})};
      if (name === 'sandbox') return {confine: async argv => ({argv, enforcement: 'full'})};
      return undefined;
    }};
  const host = createWorkflowV2Host({ctx, root, config: {workflowV2: {enabled}}, startRuntime: async options => {
    starts.push(options);
    return {modelPort: {prepare: async () => ({tools: hooks.tool ? [{name: 'workspace_write', type: 'function'}] : []}), complete: async request => {
      requests.push(request);
      const id = 'physical-' + requests.length, usage = {input_tokens: 12, output_tokens: 8, total_tokens: 20, cached_input_tokens: null, reasoning_tokens: null};
      const output = hooks.tool && requests.length === 1
        ? [{type: 'function_call', name: 'workspace_write', call_id: 'write-fixture', arguments: '{"path":"probe.txt","text":"fixture"}'}]
        : [{type: 'message', content: [{type: 'output_text', text: JSON.stringify({summary: 'Offline no-progress fixture', changes: [], next_action: 'stop', next_target: null, progress: 'no_progress', limitations: []})}]}];
      await hooks.afterResponse?.(requests.length);
      return {requestId: id, accounting: {format: 'dsh-math-physical-accounting-v2', requests: [{request_id: id,
        logical_request_id: id, parent_request_id: null, role: request.role, status: 'finished', usage_complete: true, usage, raw_usage: usage}]},
        response: {model: 'gpt-6-astra', status: 'completed', output}};
    }}, toolPort: {dispatch(name) { effects.push(name); return {ok: true, result: {path: 'probe.txt'}, metrics: {}}; }},
      close: async () => closes.push(options.runId)};
  }});
  host.registerChat(async (request, exec) => host.execute(request.args, {exec, signal: exec.signal, continuous: request.action === 'workflow-run'}));
  const execution = index => ({agent: agents[index], callId: 'ordinary-call', signal: new AbortController().signal});
  const grant = async (index, allowed = true) => {
    const args = runs[index];
    const value = await host.call('workflow-control', {project_id: args.project_id, session_id: args.session_id,
      command: {action: 'grant_chat', run_id: args.run_id, expected_rev: args.expected_rev, operation_id: 'grant-' + args.expected_rev, allowed}});
    args.expected_rev = value.rev; return value;
  };
  t.after(async () => { await host.dispose();
    assert.ok(root.startsWith(temp + sep) && basename(root).startsWith('dsh-workflow-v2-host-'));
    rmSync(root, {recursive: true, force: true});
  });
  return {host, ctx, runs, agents, policy, plan, tools, requests, starts, closes, execution, grant, effects, hooks};
}

test('v2 is disabled by default and cannot register execution tools when disabled', async t => {
  const f = harness(t, {enabled: false}); assert.equal(f.tools.size, 0);
  await assert.rejects(f.host.execute(f.runs[0]), {code: 'RUNTIME_CAPABILITY'}); assert.equal(f.starts.length, 0);
});

test('PLAN, pending plan, delegated identity and filesystem policy deny before runtime creation', async t => {
  const f = harness(t), args = f.runs[0];
  for (const key of ['active', 'pending']) {
    f.plan[key] = true; await assert.rejects(f.host.execute(args), {code: 'PLAN_MODE'}); f.plan[key] = false;
  }
  f.agents[0].session.header.origin = 'subagent';
  await assert.rejects(f.host.execute(args), {code: 'CHAT_SCOPE_REQUIRED'}); delete f.agents[0].session.header.origin;
  f.policy.mode = 'read-only'; await assert.rejects(f.host.execute(args), {code: 'READ_ONLY'});
  f.policy.mode = 'workspace-write'; await assert.rejects(f.host.execute(args), {code: 'RUNTIME_CONFINEMENT'});
  assert.equal(f.starts.length, 0); assert.equal(f.requests.length, 0);
});

test('ordinary chat requires an exact per-run grant and cannot mint one through tool arguments', async t => {
  const f = harness(t), args = f.runs[0], exec = f.execution(0), tool = f.tools.get('math_workflow_execute');
  const toolArgs = () => ({project_id: args.project_id, run_id: args.run_id, expected_rev: args.expected_rev, mode: 'step', kind: 'derive'});
  await assert.rejects(tool.execute({...toolArgs(), allowed: true}, exec), {code: 'INVALID_ARGUMENTS'});
  await assert.rejects(tool.execute(toolArgs(), exec), {code: 'WORKFLOW_GRANT_REQUIRED'});
  assert.equal(f.starts.length, 0);
  await f.grant(0);
  const result = JSON.parse(await tool.execute(toolArgs(), exec));
  assert.equal(result.status, 'paused'); assert.equal(result.usage.requests, 1);
  assert.equal(result.usage.total_tokens, 20); assert.equal(f.starts.length, 1); assert.equal(f.closes.length, 1);
});

test('revocation and stale revisions cannot execute or recreate a runtime', async t => {
  const f = harness(t), args = f.runs[0], exec = f.execution(0);
  await f.grant(0); const old = {...args}; await f.grant(0, false);
  await assert.rejects(f.host.execute(old, {exec}), {code: 'REVISION_CONFLICT'});
  await assert.rejects(f.host.execute(args, {exec}), {code: 'WORKFLOW_GRANT_REQUIRED'});
  assert.equal(f.starts.length, 0);
});

test('UI and granted chat invoke the same executor and accounting path', async t => {
  const f = harness(t);
  const ui = await f.host.execute(f.runs[0]);
  await f.grant(1); const chat = await f.host.execute(f.runs[1], {exec: f.execution(1)});
  assert.deepEqual(f.requests.map(value => value.role), ['worker', 'worker']);
  assert.equal(f.requests[0].request.instructions, f.requests[1].request.instructions);
  assert.deepEqual(f.requests[0].request.tools, f.requests[1].request.tools);
  assert.deepEqual(ui.usage, chat.usage); assert.equal(ui.status, 'paused'); assert.equal(chat.status, 'paused');
});

test('explicit v2 stop pauses without constructing a runtime or charging a request', async t => {
  const f = harness(t), run = await f.host.execute({...f.runs[0], kind: 'stop'});
  assert.equal(run.status, 'paused'); assert.equal(run.usage.requests, 0); assert.equal(f.starts.length, 0);
});

for (const change of ['PLAN', 'read-only']) test(`live ${change} revocation prevents the next effect and model call`, async t => {
  const f = harness(t); f.hooks.tool = true;
  f.hooks.afterResponse = () => { if (change === 'PLAN') f.plan.active = true; else f.policy.mode = 'read-only'; };
  await assert.rejects(f.host.execute(f.runs[0]), {code: change === 'PLAN' ? 'PLAN_MODE' : 'READ_ONLY'});
  assert.equal(f.requests.length, 1); assert.deepEqual(f.effects, []);
  const run = await f.host.call('workflow-control', {project_id: f.runs[0].project_id, session_id: f.runs[0].session_id,
    command: {action: 'read', run_id: f.runs[0].run_id}});
  assert.equal(run.usage.total_tokens, 20); assert.equal(run.usage.requests, 1);
  assert.equal(run.usage.requests_complete, true); assert.equal(f.closes.length, 1);
});

for (const change of ['missing', 'replaced']) test(`granted chat stops when its live agent is ${change}`, async t => {
  const f = harness(t), args = f.runs[0], exec = f.execution(0);
  await f.grant(0); f.hooks.tool = true;
  f.hooks.afterResponse = () => { f.ctx.agents.get = () => change === 'missing' ? undefined : {...exec.agent}; };
  const tool = f.tools.get('math_workflow_execute');
  await assert.rejects(tool.execute({project_id: args.project_id, run_id: args.run_id,
    expected_rev: args.expected_rev, mode: 'step', kind: 'derive'}, exec), {code: 'SESSION_UNAVAILABLE'});
  assert.equal(f.requests.length, 1); assert.deepEqual(f.effects, []);
  const run = await f.host.call('workflow-control', {project_id: args.project_id, session_id: args.session_id,
    command: {action: 'read', run_id: args.run_id}});
  assert.equal(run.usage.total_tokens, 20); assert.equal(run.usage.requests, 1);
  assert.equal(run.usage.requests_complete, true); assert.equal(f.closes.length, 1);
});

test('missing live session and cross-session identity are rejected before data mutation', async t => {
  const f = harness(t);
  await assert.rejects(f.host.execute({...f.runs[0], session_id: 'missing'}), {code: 'SESSION_UNAVAILABLE'});
  await assert.rejects(f.host.execute(f.runs[0], {exec: f.execution(1)}), {code: 'CHAT_SCOPE_REQUIRED'});
  assert.equal(f.starts.length, 0);
});
