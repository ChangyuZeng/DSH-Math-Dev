import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import http from 'node:http';
import {createHash} from 'node:crypto';
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
  const chatTools = new Map();
  apply({get: name => name === 'connection' ? connection : undefined, effect(fn) {fn();},
    tools: {register(definition) {chatTools.set(definition.name, definition);}},
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
    const bytes = Buffer.from('Material fixture only.\n'.repeat(60000));
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    const begin = await (await call('material-begin', {session_id: 'session-http', project_id: created.value.id,
      operation_id: 'http-upload', files: [{name: 'large.txt', bytes: bytes.length, sha256}]})).json();
    assert.equal(begin.ok, true);
    const upload = begin.value;
    for (let offset = 0, index = 0; offset < bytes.length; offset += 512 * 1024, index++) {
      const response = await (await call('material-chunk', {session_id: 'session-http', upload_id: upload.upload_id,
        file_id: upload.files[0].file_id, index, base64: bytes.subarray(offset, offset + 512 * 1024).toString('base64')})).json();
      assert.equal(response.ok, true);
    }
    const preview = await (await call('material-preview', {session_id: 'session-http', upload_id: upload.upload_id})).json();
    assert.equal(preview.ok, true);
    const confirmed = await (await call('material-confirm', {session_id: 'session-http', project_id: created.value.id,
      upload_id: upload.upload_id, operation_id: 'http-confirm', preview_hash: preview.value.preview_hash})).json();
    assert.equal(confirmed.ok, true);
    assert.equal(confirmed.value.materials[0].status, 'imported');
    const ref = confirmed.value.materials[0].ref;
    const first = await (await call('material-bytes', {session_id: 'session-http', project_id: created.value.id, ref, offset: 0, limit: 100})).json();
    assert.equal(first.ok, true); assert.equal(Buffer.from(first.value.base64, 'base64').length, 100);
    const denied = await (await call('material-read', {session_id: 'another-session', project_id: created.value.id, ref})).json();
    assert.equal(denied.ok, false);
    assert.equal((await call('list', {}, {body: '{'})).status, 400);
    assert.equal((await call('list', {}, {body: 'x'.repeat(1024 * 1024 + 1)})).status, 413);
    assert.equal((await fetch(origin + '/dsh-math/api')).status, 405);
  } finally {
    await new Promise(resolve => server.close(resolve));
    // root was resolved and verified as this test's own mkdtemp directory above.
    await rm(root, {recursive: true, force: true});
  }
});
