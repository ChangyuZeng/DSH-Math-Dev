import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const require = createRequire(import.meta.url);
const Comparison = require('../comparison-ui.cjs');
const reconstruct = (rows, side) => rows.map(row => row[side] ?? '').join('');
const h = (type, props, ...children) => ({type, props: props || {}, children: children.flat(Infinity)});
const nodes = node => !node || typeof node !== 'object' ? [] : [node, ...node.children.flatMap(nodes)];
const rowNodes = tree => nodes(tree).filter(node => node.props.className?.split(' ').includes('dm-comparison-row'));
const more = tree => nodes(tree).find(node => node.props.className?.split(' ').includes('dm-comparison-more'));
const count = tree => nodes(tree).find(node => node.props.className === 'dm-comparison-count').children[0];
// Exercise the production component and hook updater, not a pagination mock.
function renderer() {
  const slots = []; let cursor = 0;
  const React = {createElement: h, useState(initial) {
    const index = cursor++;
    if (!slots[index]) slots[index] = {value: typeof initial === 'function' ? initial() : initial};
    return [slots[index].value, next => { slots[index].value = typeof next === 'function' ? next(slots[index].value) : next; }];
  }};
  const ui = Comparison(React);
  return {ui, render(props) { cursor = 0; return ui.TextComparison(props); }};
}

test('comparison supports CommonJS and a standalone browser global', () => {
  assert.equal(typeof Comparison, 'function');
  const context = vm.createContext({});
  vm.runInContext(readFileSync(new URL('../comparison-ui.cjs', import.meta.url), 'utf8'), context);
  assert.equal(typeof context.DshMathComparisonUI, 'function');
  assert.equal(typeof context.DshMathComparisonUI({createElement: h}).TextComparison, 'function');
});

test('line comparison preserves exact Unicode, whitespace, endings and inserted alignment', () => {
  const local = 'same\r\n  $x\\le y$ 😀\nlast', remote = 'same\r\ninserted\n  $x\\le y$ 😀\nlast\n';
  const rows = Comparison.diffLines(local, remote);
  assert.equal(reconstruct(rows, 'local'), local); assert.equal(reconstruct(rows, 'remote'), remote);
  assert.ok(rows.some(row => row.kind === 'remote' && row.remote === 'inserted\n'));
  assert.ok(rows.some(row => row.kind === 'same' && row.local === '  $x\\le y$ 😀\n'));
  assert.deepEqual(Comparison.diffLines('', ''), []);
  for (const [left, right] of [['', '\n'], ['\r\n', '\n'], ['a\n\n', 'a\n'], [' a', 'a']]) {
    const diff = Comparison.diffLines(left, right);
    assert.equal(reconstruct(diff, 'local'), left); assert.equal(reconstruct(diff, 'remote'), right);
    assert.ok(diff.some(row => row.kind !== 'same'));
  }
});

test('large-document fallback remains bounded and never truncates either original', () => {
  const left = 'local\n'.repeat(700), right = 'remote\r\n'.repeat(800);
  const rows = Comparison.diffLines(left, right);
  assert.equal(rows.length, 800); assert.equal(reconstruct(rows, 'local'), left); assert.equal(reconstruct(rows, 'remote'), right);
});

test('fields retain order and show explicit unchanged/different labels, not raw HTML', () => {
  const fields = [{key: 'title', label: '标题'}, {key: 'sources', label: '来源'}];
  const local = {title: '<img src=x onerror=alert(1)>', sources: ['same', 'exact']}, remote = {title: 'server', sources: ['same', 'exact']};
  const compared = Comparison.compareFields(fields, local, remote);
  assert.deepEqual(compared.map(field => field.changed), [true, false]);
  assert.equal(compared[1].local, 'same\nexact');
  const view = renderer(), ui = view.ui;
  const tree = ui.FieldComparison({fields, local, remote, localVersion: 'v2', remoteVersion: 'v3'});
  assert.ok(nodes(tree).some(node => node.children.includes('标题 · 内容不同')));
  const text = view.render({local: local.title, remote: remote.title, localVersion: 'v2', remoteVersion: 'v3'});
  assert.ok(nodes(text).some(node => node.type === 'pre' && node.children.includes(local.title)));
  assert.ok(nodes(text).some(node => node.children.includes('我的内容 · v2')));
  assert.equal(nodes(text).some(node => node.props.dangerouslySetInnerHTML), false);
});

test('256KiB newline-heavy comparison mounts only 200 rows and preserves the complete diff', () => {
  const local = '\n'.repeat(256 * 1024), remote = '\r\n'.repeat(128 * 1024);
  const props = Object.freeze({local, remote}), view = renderer();
  let tree = view.render(props);
  assert.equal(rowNodes(tree).length, 200);
  assert.ok(nodes(tree).length < 2500, 'Only the visible row window creates elements');
  assert.match(count(tree), /已展示 200 \/ 262144 行比较/);
  assert.match(count(tree), /原文保持完整/);
  assert.ok(nodes(tree).some(node => node.children.includes('我的内容 · 第 1 行 · 不同 · LF 换行')));
  assert.ok(nodes(tree).some(node => node.children.includes('服务器内容 · 第 1 行 · 不同 · CRLF 换行')));
  assert.equal(more(tree).props.type, 'button');
  more(tree).props.onClick(); tree = view.render(props);
  assert.equal(rowNodes(tree).length, 400);
  assert.match(count(tree), /已展示 400 \/ 262144 行比较/);
  const rows = Comparison.diffLines(local, remote);
  assert.equal(rows.length, 262144);
  assert.equal(reconstruct(rows, 'local'), local);
  assert.equal(reconstruct(rows, 'remote'), remote);
  assert.equal(props.local, local); assert.equal(props.remote, remote);
});

test('incremental display reaches every exact row and resets on content or version changes', () => {
  const local = Array.from({length: 450}, (_, i) => `  😀 ${i}\r\n`).join('');
  const remote = local.replace('😀 449', '<script>alert(1)</script>');
  const props = Object.freeze({local, remote, localVersion: 'v1', remoteVersion: 'v2'}), view = renderer();
  const complete = Comparison.diffLines(local, remote);
  let tree = view.render(props);
  assert.equal(rowNodes(tree).length, 200);
  more(tree).props.onClick(); tree = view.render(props); assert.equal(rowNodes(tree).length, 400);
  more(tree).props.onClick(); tree = view.render(props);
  assert.equal(rowNodes(tree).length, complete.length);
  assert.equal(more(tree), undefined);
  assert.match(count(tree), new RegExp(`已展示 ${complete.length} / ${complete.length}`));
  for (const [side, index] of [['local', 0], ['remote', 1]]) {
    const displayed = rowNodes(tree).map(row => nodes(row.children[index]).filter(node => node.type === 'pre').map(node => node.children[0]).join('')).join('');
    assert.equal(displayed, props[side], 'Expansion never normalizes, drops or merges original text');
  }
  assert.equal(nodes(tree).some(node => node.props.dangerouslySetInnerHTML || node.type === 'script'), false);
  tree = view.render({...props, localVersion: 'v3'});
  assert.equal(rowNodes(tree).length, 200, 'A new revision resets the first render, not only a later effect');
  more(tree).props.onClick(); tree = view.render({...props, localVersion: 'v3'}); assert.equal(rowNodes(tree).length, 400);
  tree = view.render({...props, local: local + '\n'}); assert.equal(rowNodes(tree).length, 200);
  tree = view.render(props); assert.equal(rowNodes(tree).length, 200, 'Returning to old content does not restore a stale expanded limit');
  assert.deepEqual(Comparison.diffLines(local, remote), complete);
});

test('empty and short comparisons retain complete line-ending labels without more controls', () => {
  const view = renderer();
  let tree = view.render({});
  assert.equal(rowNodes(tree).length, 0); assert.equal(more(tree), undefined);
  assert.match(count(tree), /已展示 0 \/ 0/);
  tree = view.render({local: 'a\r\nb\nc\rd', remote: 'a\r\nb\nc\rd'});
  assert.equal(rowNodes(tree).length, 4); assert.equal(more(tree), undefined);
  for (const ending of ['CRLF 换行', 'LF 换行', 'CR 换行', '无末尾换行'])
    assert.ok(nodes(tree).some(node => node.children.some(value => typeof value === 'string' && value.endsWith(ending))));
});
