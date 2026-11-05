import test from 'node:test';
import assert from 'node:assert/strict';
import {parseToolArguments} from '../evaluation/four-harness-v1/m_adapter.mjs';

test('unsafe integer tool arguments retain exact provider numeric tokens', () => {
  const wire = '{"candidate":{"values":[9007199254740993,-9007199254740993,57896044618658097711785492504343953926634992332820282019728792003956564819987]}}';
  assert.equal(JSON.stringify(parseToolArguments(wire)), wire);
});

test('small integer arguments have identical values and wire serialization', () => {
  const wire = '{"candidate":{"values":[0,1,-1,256,9007199254740991]},"text":"9007199254740993","nested":{"a":true}}';
  assert.deepEqual(parseToolArguments(wire), JSON.parse(wire));
  assert.equal(JSON.stringify(parseToolArguments(wire)), JSON.stringify(JSON.parse(wire)));
  assert.throws(() => parseToolArguments('{broken'), SyntaxError);
});

test('fractional and exponent tokens remain numeric JSON, never string witnesses', () => {
  const wire = '{"values":[0.125,1e100,9007199254740993.0]}';
  assert.equal(JSON.stringify(parseToolArguments(wire)), wire);
});
