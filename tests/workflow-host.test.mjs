import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, realpathSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, join, sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {apply} from '../host.mjs';

const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const fixture = `
import json, sys
from backend import Projects
from workflow_store import WorkflowStore
root=sys.argv[1]
problem={"title":"Fixture", "statement":"Candidate", "quantifiers":"all x", "domain":"integers", "conventions":[], "target_type":"exploratory", "parent":None, "relation":"", "completion_criteria":["human review"], "sources":["fixture"], "knowledge_status":"unverified_open"}
result=[]
for index in range(2):
 session='session-'+str(index)
 project=Projects(root).create(session,problem)['id']
 spec={"format":"dsh-math-workflow-v1", "project_id":project, "problem_ref":{"id":"P","rev":1}, "model_policy":{"provider":"fixture","model":"fixture","effort":"high"}, "budget":{"requests":3,"total_tokens":50000,"max_output_tokens":1000}, "tools":[], "context_budget":{"max_tokens":20000}, "stage_goal":"Candidate research"}
 store=WorkflowStore(root)
 run=store.dispatch(dict(action='prepare',project_id=project,run_id='shared-run',operation_id='prepare',expected_rev=0,spec=spec))
 run=store.dispatch(dict(action='start',project_id=project,run_id='shared-run',operation_id='start',expected_rev=run['rev']))
 result.append(dict(project_id=project,session_id=session,run=run))
print(json.dumps(result))
`;

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

function host(t, {enabled = true} = {}) {
  const temp = realpathSync(tmpdir());
  const root = realpathSync(mkdtempSync(join(temp, 'dsh-workflow-host-')));
  const projects = JSON.parse(execFileSync('python3', ['-B', '-c', fixture, root], {cwd: repo, encoding: 'utf8'}));
  const streams = [];
  const waiters = [];
  let prepares = 0;
  const llm = {async prepareCall(config) {
    prepares++;
    return {config, context: {contextWindow: 100000}, async *stream(options) {
      const pending = deferred();
      const stream = {signal: options.signal, pending};
      streams.push(stream);
      for (const wake of waiters.splice(0)) wake();
      if (options.signal.aborted) throw Error('aborted');
      const abort = () => pending.resolve();
      options.signal.addEventListener('abort', abort, {once: true});
      try { await pending.promise; throw Error('aborted'); }
      finally { options.signal.removeEventListener('abort', abort); }
    }};
  }};
  const peer = {signal: new AbortController().signal};
  const cleanup = [];
  let handler;
  const ctx = {
    effect(factory) { const dispose = factory(); if (typeof dispose === 'function') cleanup.push(dispose); },
    webServer: {getOrigin: () => 'http://127.0.0.1:43875', register(route) { handler = route.handler; return () => {}; }},
    get(name) {
      if (name === 'llm') return llm;
      if (name === 'connection') return {browserPeer: peer, admit: request => request.headers.cookie === 'fixture-auth'
        ? {peer} : {rejection: 401}};
      return null;
    },
  };
  apply(ctx, {root, chatPreparation: false, workflowExecution: {enabled}, modelRuns: []});
  const dispose = () => { for (const fn of cleanup.splice(0)) fn(); };
  t.after(() => {
    dispose();
    // This is the exact temporary test fixture created above, never user data.
    assert.ok(root.startsWith(temp + sep) && basename(root).startsWith('dsh-workflow-host-'));
    rmSync(root, {recursive: true, force: true});
  });
  const api = async (action, args, headers = {}) => {
    const request = {method: 'POST', socket: {remoteAddress: '127.0.0.1'},
      headers: {host: '127.0.0.1:43875', origin: 'http://127.0.0.1:43875', 'content-type': 'application/json',
        'x-dsh-math': 'human-ui-v1', cookie: 'fixture-auth', ...headers},
      async *[Symbol.asyncIterator]() { yield JSON.stringify({action, args}); }};
    let status, body;
    await handler(request, {writeHead(code) { status = code; }, end(value) { body = JSON.parse(value); }});
    return {status, ...body};
  };
  const args = p => ({project_id: p.project_id, session_id: p.session_id, run_id: 'shared-run', expected_rev: p.run.rev, kind: 'derive'});
  const read = async p => {
    const response = await api('workflow', {project_id: p.project_id, session_id: p.session_id, command: {action: 'read', run_id: 'shared-run'}});
    assert.equal(response.ok, true, JSON.stringify(response));
    return response.value.run;
  };
  const pause = (p, rev) => api('workflow', {project_id: p.project_id, session_id: p.session_id,
    command: {action: 'pause', run_id: 'shared-run', expected_rev: rev, operation_id: 'pause-' + rev}});
  const waitStreams = async count => {
    while (streams.length < count) await new Promise(resolve => waiters.push(resolve));
  };
  return {projects, api, args, read, pause, streams, waitStreams, dispose, get prepares() { return prepares; }};
}

test('browser and project authorization precede abort of an active action', {timeout: 15000}, async t => {
  const h = host(t), p = h.projects[0];
  const rejected = await h.api('workflow-run', h.args(p), {cookie: 'invalid'});
  assert.equal(rejected.status, 401);
  assert.equal(h.prepares, 0);
  const running = h.api('workflow-run', h.args(p));
  await h.waitStreams(1);
  const current = await h.read(p);
  const unauthorized = await h.api('workflow', {project_id: p.project_id, session_id: 'unrelated-session',
    command: {action: 'pause', run_id: 'shared-run', expected_rev: current.rev, operation_id: 'unauthorized-pause'}});
  assert.equal(unauthorized.ok, false);
  assert.equal(h.streams[0].signal.aborted, false);
  const stale = await h.pause(p, 1);
  assert.equal(stale.ok, false);
  assert.equal(h.streams[0].signal.aborted, false);
  const paused = await h.pause(p, current.rev);
  assert.equal(paused.ok, true, JSON.stringify(paused));
  assert.equal(paused.value.run.status, 'needs_reconcile');
  assert.equal((await running).ok, false);
  assert.equal(h.prepares, 1);
});

test('parallel pause settles one request and never replays its model call', {timeout: 15000}, async t => {
  const h = host(t), p = h.projects[0];
  const running = h.api('workflow-run', h.args(p));
  await h.waitStreams(1);
  const current = await h.read(p);
  const replies = await Promise.all([h.pause(p, current.rev), h.pause(p, current.rev)]);
  assert.ok(replies.some(reply => reply.ok), JSON.stringify(replies));
  const final = await h.read(p);
  assert.equal(final.status, 'needs_reconcile');
  assert.equal(final.actions[0].status, 'unknown');
  assert.equal(final.usage.requests, 1);
  assert.equal(final.usage.complete, false);
  assert.equal((await running).ok, false);
  assert.equal(h.prepares, 1);
});

test('equal run ids in two projects have separate controllers; disposal aborts remaining work', {timeout: 15000}, async t => {
  const h = host(t), [first, second] = h.projects;
  const one = h.api('workflow-run', h.args(first));
  await h.waitStreams(1);
  const two = h.api('workflow-run', h.args(second));
  await h.waitStreams(2);
  const current = await h.read(first);
  assert.equal((await h.pause(first, current.rev)).ok, true);
  assert.equal(h.streams[0].signal.aborted, true);
  assert.equal(h.streams[1].signal.aborted, false);
  assert.equal((await one).ok, false);
  h.dispose();
  assert.equal(h.streams[1].signal.aborted, true);
  assert.equal((await two).ok, false);
  assert.equal((await h.read(second)).status, 'needs_reconcile');
  assert.equal(h.prepares, 2);
});

test('parallel execution attempts preserve the active controller and single claim', {timeout: 15000}, async t => {
  const h = host(t), p = h.projects[0];
  const one = h.api('workflow-run', h.args(p));
  const two = h.api('workflow-run', h.args(p));
  await h.waitStreams(1);
  const current = await h.read(p);
  assert.equal((await h.pause(p, current.rev)).ok, true);
  const results = await Promise.all([one, two]);
  assert.ok(results.every(result => result.ok === false));
  assert.equal(h.prepares, 1);
  assert.equal((await h.read(p)).usage.requests, 1);
});

test('explicit stop pauses without preparing a model; disabled execution rejects run', {timeout: 15000}, async t => {
  const h = host(t), p = h.projects[0];
  const stopped = await h.api('workflow-run', {...h.args(p), kind: 'stop'});
  assert.equal(stopped.ok, true, JSON.stringify(stopped));
  assert.equal(stopped.value.run.status, 'paused');
  assert.equal(stopped.value.run.scientific_status, 'candidate_pending_review');
  assert.equal(h.prepares, 0);
  const disabled = host(t, {enabled: false});
  const rejected = await disabled.api('workflow-run', disabled.args(disabled.projects[0]));
  assert.equal(rejected.ok, false);
  assert.equal(disabled.prepares, 0);
});
