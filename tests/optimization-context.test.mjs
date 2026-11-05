import test from 'node:test';
import assert from 'node:assert/strict';
import {serializeToolResult, createMAdapter} from '../evaluation/four-harness-v1/m_adapter.mjs';

test('single context factor removes host telemetry while retaining every result and error field', () => {
  const result = {ok: true, result: {stdout: 'candidate: [2,3,5]', stderr: '', status: 'completed',
    metrics: {mathematical: 'a nested field is retained'}}, metrics: {cpu_seconds: 0.1, wall_seconds: 0.3,
    result_sha256: 'a'.repeat(64), rss_watchdog: {samples: 50}, run_id: 'isolated'}};
  const before = structuredClone(result);
  const a = serializeToolResult(result);
  const b = serializeToolResult(result, 'omit-host-metrics');
  assert.equal(a.output, JSON.stringify(result));
  assert.deepEqual(JSON.parse(b.output), {ok: true, result: result.result});
  assert.deepEqual(result, before);
  assert.equal(b.triggered, true);
  assert.equal(b.removed_bytes, Buffer.byteLength(a.output) - Buffer.byteLength(b.output));
  const error = {ok: false, error: {code: 'compute_error', message: 'candidate rejected'}, metrics: {x: 1}};
  assert.deepEqual(JSON.parse(serializeToolResult(error, 'omit-host-metrics').output), {ok: false, error: error.error});
});

test('long repeated tool fixture demonstrates context trigger with exact mathematical preservation', () => {
  let a = '', b = '', removed = 0;
  for (let i = 0; i < 80; i++) {
    const r = {ok: true, result: {stdout: `step ${i}: ${i*i}`, path: `checkpoint-${i}.json`},
      metrics: {request_id: 'a'.repeat(36), result_sha256: 'b'.repeat(64), started_at: '2026-11-05T00:00:00Z',
        cpu_seconds: 0.2, wall_seconds: 0.5, peak_memory_bytes: 10000000}};
    const x = serializeToolResult(r), y = serializeToolResult(r, 'omit-host-metrics');
    a += x.output; b += y.output; removed += y.removed_bytes;
    assert.deepEqual(JSON.parse(y.output).result, r.result);
  }
  assert.equal(Buffer.byteLength(a) - Buffer.byteLength(b), removed);
  assert.ok(removed > 16000);
});

test('switch defaults off, refuses unknown policy and non-experiment activation', () => {
  assert.equal(serializeToolResult({ok: true}).triggered, false);
  assert.throws(() => serializeToolResult({}, 'other'), {code: 'CONTEXT_POLICY'});
  assert.throws(() => createMAdapter({contextPolicy: 'omit-host-metrics'}), {code: 'CONTEXT_POLICY'});
});
