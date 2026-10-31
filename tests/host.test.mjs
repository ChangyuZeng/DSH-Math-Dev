import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import http from 'node:http';
import {apply} from '../host.mjs';

test('authenticated Web and native-shaped HTTP requests use the same real backend', async () => {
  const temp = await realpath(tmpdir());
  const root = await realpath(await mkdtemp(join(temp, 'dsh-math-host-')));
  assert.equal(dirname(root), temp); assert.ok(basename(root).startsWith('dsh-math-host-'));
  let handler;
  const server = http.createServer((req, res) => handler(req, res));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  // This double covers our admission wiring; real DSH cookie verification is an integration check.
  const browserPeer = {id: 'browser', signal: new AbortController().signal};
  const apiPeer = {id: 'api', principal: {kind: 'owner'}, interactions: 'answer'};
  const connection = {browserPeer, admit: req => req.headers.cookie === 'fixture-browser' ? {peer: browserPeer} :
    req.headers.cookie === 'fixture-api' ? {peer: apiPeer} : {rejection: 401}};
  apply({get: name => name === 'connection' ? connection : undefined, effect(fn) {fn();},
    webServer: {getOrigin: () => origin, register(route) {handler = route.handler; return () => {};}}},
  {root, python: process.env.DSH_MATH_PYTHON || 'python3'});
  const call = (action, args = {}, {native = false, headers = {}, body} = {}) => fetch(origin + '/dsh-math/api', {
    method: 'POST', headers: {...(!native && {origin}), 'content-type': 'application/json', 'x-dsh-math': 'human-ui-v1', cookie: 'fixture-browser', ...headers},
    body: body ?? JSON.stringify({action, args}),
  });
  try {
    assert.equal((await call('list', {}, {headers: {origin: 'https://example.com'}})).status, 403);
    assert.equal((await call('list', {}, {headers: {cookie: 'forged'}})).status, 401);
    assert.equal((await call('list', {}, {native: true, headers: {cookie: 'forged'}})).status, 401);
    assert.equal((await call('list', {}, {native: true, headers: {cookie: 'fixture-api'}})).status, 403);
    assert.deepEqual(await (await call('list')).json(), {ok: true, value: []});
    const problem = {title: 'HTTP fixture', statement: 'Known result', quantifiers: 'for all n', domain: 'integers', conventions: [], target_type: 'prove_or_disprove', parent: null, relation: '', completion_criteria: ['human review'], sources: ['synthetic fixture'], knowledge_status: 'known_result'};
    const created = await (await call('create', {session_id: 'session-http', problem}, {native: true})).json();
    assert.equal(created.ok, true);
    for (const native of [false, true]) {
      const opened = await (await call('overview', {project_id: created.value.id}, {native})).json();
      assert.equal(opened.ok, true);
      assert.equal(opened.value.project.session_id, 'session-http');
      assert.equal(opened.value.operation_model_requests, 0);
      assert.equal(opened.value.session_model_requests, null);
    }
    assert.equal((await (await call('list')).json()).value.length, 1);
    assert.equal((await call('list', {}, {body: '{'})).status, 400);
    assert.equal((await call('list', {}, {body: 'x'.repeat(1024 * 1024 + 1)})).status, 413);
    assert.equal((await fetch(origin + '/dsh-math/api')).status, 405);
  } finally {
    await new Promise(resolve => server.close(resolve));
    // root was resolved and verified as this test's own mkdtemp directory above.
    await rm(root, {recursive: true, force: true});
  }
});
