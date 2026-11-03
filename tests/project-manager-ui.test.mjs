import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import UI from '../project-manager-ui.cjs';

let serial = 0;
const project = {id: 'project-a', title: '积分研究', schema_version: 4};
const another = {id: 'project-b', title: '极限研究', schema_version: 4};
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
function server() {
  const calls = [], completed = new Map();
  let rev = 0, removed = false, commits = 0;
  return {calls, get commits() { return commits; }, bump() { rev++; }, async request(action, args) {
    calls.push({action, args});
    if (action === 'list') return [project, another];
    if (action === 'library-read') return {item: {entry: {kind: 'project', id: args.project_id}, metadata_rev: rev, removed}};
    if (completed.has(args.operation_id)) return completed.get(args.operation_id);
    if (args.expected_rev !== rev) throw Object.assign(Error('changed'), {code: 'VERSION_CONFLICT'});
    removed = action === 'library-remove'; rev++; commits++;
    const result = {metadata: {metadata_rev: rev, removed}}; completed.set(args.operation_id, result); return result;
  }};
}
function control(t, s, options = {}) {
  const c = UI.createProjectManagerController({request: s.request, sessionId: `project-manager-${++serial}`, uuid: () => `op-${++serial}`, ...options});
  t.after(() => c.dispose()); return c;
}
test('publishes browser factory without changing its component API', () => {
  const context = {}; vm.runInNewContext(readFileSync(new URL('../project-manager-ui.cjs', import.meta.url), 'utf8'), context);
  assert.equal(typeof context.DshMathProjectManagerUI, 'function');
  assert.equal(typeof UI.createProjectManagerController, 'function');
});
test('lost response locks selection and retains exact immutable retry across unmount', async t => {
  const s = server(); let lose = true;
  const request = async (action, args) => { const value = await s.request(action, args); if (action === 'library-remove' && lose) { lose = false; throw Error('response lost'); } return value; };
  const sessionId = `lost-${++serial}`, c = control(t, s, {request, sessionId});
  await c.load(); await c.choose(project); assert.equal(await c.save(), false);
  const attempt = c.get().attempt;
  assert.ok(Object.isFrozen(attempt)); assert.ok(Object.isFrozen(attempt.args)); assert.ok(Object.isFrozen(attempt.args.entry));
  assert.throws(() => { attempt.args.expected_rev = 999; }, TypeError);
  const calls = s.calls.length; assert.equal(await c.choose(another), false); c.cancel(); assert.equal(s.calls.length, calls);
  assert.equal(c.get().selected.project.id, project.id); c.dispose();
  const remount = control(t, s, {request, sessionId});
  assert.equal(remount.get().attempt, attempt); assert.equal(await remount.choose(another), false);
  assert.equal(await remount.save(), true);
  const writes = s.calls.filter(x => x.action === 'library-remove');
  assert.equal(writes.length, 2); assert.equal(writes[0].args, writes[1].args); assert.equal(s.commits, 1);
  assert.equal(remount.get().attempt, null); assert.equal(remount.get().selected, null);
  assert.equal(await remount.choose(another), true);
});
test('synchronous shared lock prevents double dispatch and switches while request is in flight', async t => {
  const s = server(), held = deferred(); let writes = 0, notifications = 0;
  const sessionId = `inflight-${++serial}`, request = (action, args) => { if (action === 'library-remove') { writes++; return held.promise; } return s.request(action, args); };
  const c = control(t, s, {request, sessionId, notify: () => notifications++});
  await c.choose(project); const pending = c.save();
  assert.equal(await c.save(), false); assert.equal(await c.choose(another), false); assert.equal(writes, 1);
  c.dispose(); const before = notifications;
  const remount = control(t, s, {request, sessionId});
  assert.equal(remount.get().busy, true); assert.equal(await remount.save(), false);
  held.reject(Error('offline')); assert.equal(await pending, false);
  assert.equal(notifications, before); assert.equal(remount.get().busy, false); assert.ok(remount.get().attempt);
});
test('CAS conflict requires explicit reread and a new confirmation with current revision', async t => {
  const s = server(), c = control(t, s); await c.choose(project); s.bump();
  assert.equal(await c.save(), false); assert.equal(c.get().attempt, null); assert.equal(c.get().conflict, true);
  const before = s.calls.length;
  assert.equal(await c.save(), false); assert.equal(await c.choose(another), false); assert.equal(s.calls.length, before);
  assert.equal(await c.reread(), true); assert.equal(c.get().conflict, false); assert.equal(c.get().selected.item.metadata_rev, 1);
  assert.equal(s.commits, 0, 'reread never submits a write');
  assert.equal(await c.save(), true);
  const writes = s.calls.filter(x => x.action === 'library-remove');
  assert.notEqual(writes[0].args.operation_id, writes[1].args.operation_id); assert.equal(writes[1].args.expected_rev, 1);
});
test('session isolation and refresh failures never turn an acknowledged write into uncertain retry', async t => {
  const s = server(); let refreshFail = false;
  const c = control(t, s, {request: (action, args) => { if (action === 'list' && refreshFail) throw Error('list unavailable'); return s.request(action, args); }, onChange: () => { throw Error('render unavailable'); }});
  await c.choose(project); refreshFail = true; assert.equal(await c.save(), true);
  assert.equal(c.get().attempt, null); assert.equal(c.get().busy, false); assert.match(c.get().error, /操作已保存/);
  const other = control(t, s); assert.equal(other.get().selected, null); assert.equal(other.get().attempt, null); assert.equal(other.get().error, '');
});

function hooks() {
  const cells = [], effects = []; let cursor = 0;
  return {createElement(type, props, ...children) { return {type, props: props || {}, children: children.flat(Infinity).filter(v => v != null && v !== false)}; },
    useState(initial) { const i = cursor++; if (!(i in cells)) cells[i] = initial; return [cells[i], value => { cells[i] = value; }]; },
    useRef(initial) { const i = cursor++; return cells[i] ||= {current: initial}; },
    useEffect(fn, deps) { const i = cursor++, old = cells[i]; if (!old || deps.some((v, n) => v !== old.deps[n])) { old?.cleanup?.(); cells[i] = {deps}; effects.push(() => { cells[i].cleanup = fn(); }); } },
    render(component, props) { cursor = 0; const tree = component(props); effects.splice(0).forEach(fn => fn()); return tree; },
    dispose() { for (const cell of cells) cell?.cleanup?.(); }};
}
const nodes = tree => typeof tree === 'object' && tree ? [tree, ...tree.children.flatMap(nodes)] : [];
const text = tree => typeof tree === 'object' && tree ? tree.children.map(text).join(' ') : String(tree);
const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && text(node) === label);
const tick = () => new Promise(resolve => setImmediate(resolve));
test('component remount offers original retry and disables project choices and cancellation', async t => {
  const s = server(); let lose = true;
  const props = {sessionId: `render-${++serial}`, request: async (action, args) => {
    const value = await s.request(action, args); if (action === 'library-remove' && lose) { lose = false; throw Error('lost reply'); } return value;
  }};
  const first = hooks(), Component = UI(first).ProjectManager; first.render(Component, props); await tick();
  let tree = first.render(Component, props); await button(tree, '删除').props.onClick(); tree = first.render(Component, props);
  await button(tree, '确认删除').props.onClick(); first.dispose();
  const second = hooks(), Remounted = UI(second).ProjectManager; t.after(() => second.dispose()); second.render(Remounted, props); await tick(); tree = second.render(Remounted, props);
  assert.equal(button(tree, '删除').props.disabled, true); assert.equal(button(tree, '取消').props.disabled, true);
  assert.equal(button(tree, '重试原操作').props.disabled, false); await button(tree, '重试原操作').props.onClick();
  assert.equal(s.commits, 1); tree = second.render(Remounted, props); assert.equal(button(tree, '重试原操作'), undefined);
});
test('component exposes explicit conflict reread before renewed confirmation', async t => {
  const s = server(), react = hooks(), Component = UI(react).ProjectManager;
  const props = {request: s.request, sessionId: `conflict-ui-${++serial}`}; t.after(() => react.dispose()); react.render(Component, props); await tick();
  let tree = react.render(Component, props); await button(tree, '删除').props.onClick(); s.bump(); tree = react.render(Component, props);
  await button(tree, '确认删除').props.onClick(); tree = react.render(Component, props);
  assert.equal(button(tree, '确认删除').props.disabled, true); assert.equal(button(tree, '删除').props.disabled, true);
  await button(tree, '读取当前状态').props.onClick(); tree = react.render(Component, props);
  assert.equal(button(tree, '读取当前状态'), undefined); assert.equal(button(tree, '确认删除').props.disabled, false); assert.equal(s.commits, 0);
});
