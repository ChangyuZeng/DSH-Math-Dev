import test from 'node:test';
import assert from 'node:assert/strict';
import {humanApiRejection, isLoopbackAddress} from '../human-api.mjs';
import {apply} from '../host.mjs';

// Admission doubles test OUR policy, not DSH's signature/HMAC implementation.
function fixture() {
  const browserPeer = {id: 'browser', principal: {kind: 'owner'}, interactions: 'answer', signal: new AbortController().signal};
  const calls = [];
  const connection = {browserPeer, admit: request => { calls.push(request); return {peer: browserPeer}; }};
  const ctx = {get: () => connection, webServer: {getOrigin: () => 'http://127.0.0.1:3210'}};
  const request = {socket: {remoteAddress: '127.0.0.1'}, headers: {
    host: '127.0.0.1:3210', origin: 'http://127.0.0.1:3210',
    'content-type': 'application/json', 'x-dsh-math': 'human-ui-v1', cookie: 'fixture-only',
  }};
  return {ctx, request, connection, browserPeer, calls};
}

test('authenticated browser admission accepts exact Web origin or absent native origin', () => {
  for (const address of ['127.0.0.1', '127.42.0.2', '::1', '::ffff:127.0.0.1']) {
    for (const native of [false, true]) {
      const f = fixture(); f.request.socket.remoteAddress = address;
      if (native) delete f.request.headers.origin;
      f.request.headers['sec-fetch-site'] = 'same-origin';
      assert.equal(humanApiRejection(f.ctx, f.request), undefined);
      assert.equal(f.calls[0], f.request, 'Connection must receive the unmodified request');
      assert.equal(f.calls[0].headers.cookie, 'fixture-only');
      assert.equal(f.calls[0].headers['sec-fetch-site'], 'same-origin');
      assert.equal(f.calls[0].headers.origin, native ? undefined : 'http://127.0.0.1:3210');
    }
  }
});

test('present Origin values never bypass the exact original origin fence', () => {
  for (const origin of ['', 'null', 'dsh-app://app', 'https://evil.invalid', 'https://127.0.0.1:3210',
    'http://127.0.0.1:3211', 'http://localhost:3210', ['http://127.0.0.1:3210'], 'http://127.0.0.1:3210, null']) {
    const f = fixture(); f.request.headers.origin = origin;
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 403, String(origin));
    assert.equal(f.calls.length, 0);
  }
});

test('loopback binding, actual socket, and exact Host all remain mandatory', () => {
  for (const origin of ['http://0.0.0.0:3210', 'http://[::]:3210', 'http://example.com:3210']) {
    const f = fixture(); f.ctx.webServer.getOrigin = () => origin;
    f.request.headers.host = new URL(origin).host; f.request.headers.origin = origin;
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 403);
  }
  for (const address of [undefined, '', 'localhost', '127.evil', '127.999.0.1', '192.168.1.1', '::ffff:192.168.1.1']) {
    assert.equal(isLoopbackAddress(address), false);
    const f = fixture(); f.request.socket.remoteAddress = address;
    f.request.headers['x-forwarded-for'] = '127.0.0.1';
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 403);
  }
  for (const host of [undefined, '', 'localhost:3210', '127.0.0.1:3211', ['127.0.0.1:3210']]) {
    const f = fixture(); f.request.headers.host = host;
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 403);
  }
});

test('intent markers, JSON and fetch-site do not replace authentication', () => {
  for (const [key, value] of [['x-dsh-math', undefined], ['x-dsh-math', 'model'],
    ['x-dsh-math', ['human-ui-v1']], ['content-type', undefined], ['content-type', 'text/plain'],
    ['content-type', 'application/json-invalid'], ['content-type', ['application/json']], ['sec-fetch-site', 'cross-site']]) {
    const f = fixture(); delete f.request.headers.origin; f.request.headers[key] = value;
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 403);
    assert.equal(f.calls.length, 0);
  }
  for (const origin of [undefined, 'http://127.0.0.1:3210']) {
    const f = fixture(); f.request.headers.origin = origin;
    delete f.request.headers.cookie;
    f.request.headers.authorization = 'Bearer not-browser-authority';
    f.connection.admit = () => ({rejection: 401});
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 401);
  }
});

test('API owner/answer peers cannot use the human API', () => {
  const f = fixture(); delete f.request.headers.origin;
  const apiPeer = {...f.browserPeer, id: 'api'};
  f.connection.admit = () => ({peer: apiPeer});
  assert.equal(humanApiRejection(f.ctx, f.request)?.status, 403);
});

test('connection errors, missing contracts and invalid admission all fail closed', () => {
  for (const admission of [undefined, null, {}, {peer: null}, {rejection: 500}, {rejection: undefined}]) {
    const f = fixture(); f.connection.admit = () => admission;
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 503);
  }
  for (const connection of [undefined, {}, {browserPeer: {}, admit: null}, {admit: () => ({})}]) {
    const f = fixture(); f.ctx.get = () => connection;
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, 503);
  }
  const f = fixture(); f.connection.admit = () => { throw Error('not available'); };
  assert.equal(humanApiRejection(f.ctx, f.request)?.status, 503);
  for (const rejection of [401, 403]) {
    f.connection.admit = () => ({rejection});
    assert.equal(humanApiRejection(f.ctx, f.request)?.status, rejection);
  }
  const controller = new AbortController(); controller.abort();
  f.browserPeer.signal = controller.signal; f.connection.admit = () => ({peer: f.browserPeer});
  assert.equal(humanApiRejection(f.ctx, f.request)?.status, 401);
});

test('a rejected request never reads its body or reaches the backend queue', async () => {
  const f = fixture(); let handler, status, bodyReads = 0;
  f.connection.admit = () => ({rejection: 401});
  f.ctx.effect = fn => fn();
  f.ctx.webServer.register = route => { handler = route.handler; return () => {}; };
  apply(f.ctx, {root: '/nonexistent/dsh-math-rejection-must-not-write', chatPreparation: false});
  const request = {...f.request, method: 'POST', async *[Symbol.asyncIterator]() { bodyReads++; throw Error('body must not be read'); }};
  await handler(request, {writeHead: code => { status = code; }, end: () => {}});
  assert.equal(status, 401); assert.equal(bodyReads, 0);
  request.method = 'GET';
  await handler(request, {writeHead: code => { status = code; }, end: () => {}});
  assert.equal(status, 405); assert.equal(bodyReads, 0);
});
