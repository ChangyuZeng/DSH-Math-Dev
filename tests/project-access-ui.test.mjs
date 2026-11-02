// Synthetic hook checks only; these do not establish installed-browser acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {webcrypto} from 'node:crypto';

const require = createRequire(import.meta.url);
const ProjectAccess = require('../project-access-ui.cjs');
if (!globalThis.crypto) globalThis.crypto = webcrypto;

function harness() {
  const slots = [], effects = [];
  let position = 0, mounted = true, staleUpdates = 0;
  const React = {
    createElement(type, props, ...children) {
      return {type, props: props || {}, children: children.flat(Infinity).filter(value => value !== false && value != null)};
    },
    useState(initial) {
      const index = position++;
      if (!slots[index]) slots[index] = {value: typeof initial === 'function' ? initial() : initial};
      return [slots[index].value, value => {
        if (!mounted) staleUpdates++;
        slots[index].value = typeof value === 'function' ? value(slots[index].value) : value;
      }];
    },
    useRef(initial) {
      const index = position++;
      return slots[index] ||= {current: initial};
    },
    useEffect(effect, deps) {
      const index = position++, previous = slots[index];
      if (!previous || deps.length !== previous.deps.length || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        slots[index] = {deps, cleanup: previous?.cleanup};
        effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); });
      }
    },
  };
  const Component = ProjectAccess(React);
  return {
    render(props) {
      assert.equal(mounted, true, 'cannot render an unmounted instance');
      position = 0;
      const tree = Component(props);
      while (effects.length) effects.shift()();
      return tree;
    },
    unmount() {
      for (const slot of slots) slot.cleanup?.();
      mounted = false;
    },
    get staleUpdates() { return staleUpdates; },
  };
}

function nodes(tree, predicate) {
  const result = [];
  function visit(value) {
    if (!value || typeof value !== 'object') return;
    if (predicate(value)) result.push(value);
    for (const child of value.children || []) visit(child);
  }
  visit(tree);
  return result;
}
const text = tree => tree == null ? '' : typeof tree === 'object' ? tree.children.map(text).join(' ') : String(tree);
function button(tree, caption) {
  const found = nodes(tree, node => node.type === 'button' && text(node) === caption)[0];
  assert.ok(found, `Missing button ${caption}`);
  return found;
}
const checkbox = tree => nodes(tree, node => node.type === 'input' && node.props.type === 'checkbox')[0];
const associate = '在当前会话继续此项目';
const retry = '重试保存会话设置';
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
function fixture(respond = async () => ({rev: 8, current_project_id: 'P', auto_record: true})) {
  const calls = [], changes = [], h = harness();
  const props = {
    sessionId: 'synthetic-session', project: {id: 'P', title: '合成项目'},
    context: {rev: 7, current_project_id: null, auto_record: true}, busy: false,
    request(action, args) {
      calls.push({action, args: structuredClone(args)});
      return respond(action, args, calls.length);
    },
    onChange: value => changes.push(value),
  };
  return {h, props, calls, changes, render: () => h.render(props)};
}

function assertContext(call, sessionId = 'synthetic-session') {
  assert.deepEqual(call, {action: 'project-context', args: {session_id: sessionId, query: '', offset: 0, limit: 50}});
}

function assertMutation(call, action, extra, rev = 7) {
  assert.equal(call.action, action);
  assert.match(call.args.operation_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.deepEqual(call.args, {session_id: 'synthetic-session', expected_rev: rev, operation_id: call.args.operation_id, ...extra});
}

test('passive render and browsing project/context changes never associate or write preferences', () => {
  const f = fixture();
  assert.match(text(f.render()), /当前会话尚未选定研究项目/);
  f.props.project = {id: 'Q', title: '另一个项目'};
  f.props.context = {...f.props.context, current_project_id: 'P', rev: 9};
  assert.match(text(f.render()), /正在浏览其他项目/);
  f.props.project = {id: 'P', title: '已关联项目'};
  assert.match(text(f.render()), /当前会话研究项目：已关联项目/);
  f.props.context = {...f.props.context, auto_record: false};
  assert.equal(checkbox(f.render()).props.checked, false);
  assert.deepEqual(f.calls, []);
  assert.deepEqual(f.changes, []);
});

test('explicit association sends session, project, expected_rev and operation ID; pending blocks duplicate writes', async () => {
  const gate = deferred(), f = fixture(() => gate.promise);
  const initial = f.render(), click = button(initial, associate).props.onClick;
  const saving = click();
  await click(); // The original handler can fire twice before React renders pending state.
  assert.equal(f.calls.length, 1);
  assertMutation(f.calls[0], 'project-associate', {project_id: 'P'});
  const pending = f.render();
  assert.equal(button(pending, associate).props.disabled, true);
  assert.equal(checkbox(pending).props.disabled, true);
  assert.match(text(pending), /正在保存会话设置/);
  assert.deepEqual(f.changes, []);
  const receipt = {rev: 8, current_project_id: 'P', auto_record: true};
  gate.resolve(receipt);
  await saving;
  assert.equal(f.calls.length, 2);
  assertContext(f.calls[1]);
  assert.deepEqual(f.changes, [receipt]);
  assert.equal(button(f.render(), associate).props.disabled, false);
  assert.equal(nodes(f.render(), node => node.props.role === 'alert').length, 0);
});

test('explicit disassociation uses current revision and project identity', async () => {
  const f = fixture();
  f.props.context = {rev: 12, current_project_id: 'P', auto_record: true};
  const tree = f.render();
  assert.equal(nodes(tree, node => node.type === 'button' && text(node) === associate).length, 0);
  await button(tree, '取消当前会话关联').props.onClick();
  assertMutation(f.calls[0], 'project-disassociate', {project_id: 'P'}, 12);
  assert.equal(f.calls.length, 2);
  assertContext(f.calls[1]);
  assert.equal(f.changes.length, 1);
});

test('unchecking automatic recording sends literal false and rendering saved false does not write', async () => {
  const receipt = {rev: 8, current_project_id: null, auto_record: false};
  const f = fixture(async () => receipt);
  assert.equal(checkbox(f.render()).props.checked, true);
  await checkbox(f.render()).props.onChange({target: {checked: false}});
  assertMutation(f.calls[0], 'project-preferences', {auto_record: false});
  assert.deepEqual(f.changes, [receipt]);
  f.props.context = receipt;
  assert.equal(checkbox(f.render()).props.checked, false);
  assert.equal(f.calls.length, 2);
  assertContext(f.calls[1]);
});

test('uncertain association retry preserves original operation and content across changed props', async () => {
  const receipt = {rev: 8, current_project_id: 'P', auto_record: true};
  const f = fixture(async (action, args, count) => {
    if (count === 1) throw Error('response lost');
    return receipt;
  });
  await button(f.render(), associate).props.onClick();
  assert.deepEqual(f.changes, []);
  f.props.sessionId = 'replacement-session';
  f.props.project = {id: 'Q', title: '另一个项目'};
  f.props.context = {rev: 99, current_project_id: null, auto_record: false};
  const uncertain = f.render();
  assert.match(text(uncertain), /response lost/);
  assert.equal(button(uncertain, associate).props.disabled, true);
  assert.equal(checkbox(uncertain).props.disabled, true);
  await button(uncertain, retry).props.onClick();
  assert.equal(f.calls.length, 3);
  assert.deepEqual(f.calls[1], f.calls[0]);
  assertMutation(f.calls[1], 'project-associate', {project_id: 'P'});
  assertContext(f.calls[2], 'replacement-session');
  assert.deepEqual(f.changes, [receipt]);
  assert.equal(nodes(f.render(), node => node.type === 'button' && text(node) === retry).length, 0);
});

test('uncertain preference retry retains false even when refreshed context says true', async () => {
  const f = fixture(async (action, args, count) => {
    if (count === 1) throw Object.assign(Error('transport timeout'), {code: 'NETWORK_ERROR'});
    return {rev: 8, auto_record: false};
  });
  await checkbox(f.render()).props.onChange({target: {checked: false}});
  f.props.context = {...f.props.context, rev: 40, auto_record: true};
  await button(f.render(), retry).props.onClick();
  assert.deepEqual(f.calls[1], f.calls[0]);
  assert.equal(f.calls[1].args.auto_record, false);
  assert.equal(f.calls[1].args.expected_rev, 7);
  assert.equal(f.calls.length, 3);
  assertContext(f.calls[2]);
  assert.deepEqual(f.changes, [{rev: 8, auto_record: false}]);
});

for (const code of ['VERSION_CONFLICT', 'INVALID_SCHEMA', 'OPERATION_CONFLICT', 'PROJECT_NOT_FOUND']) {
  test(`${code} reloads context and clears the old attempt before a fresh explicit write`, async () => {
    const fresh = {rev: 21, current_project_id: null, auto_record: false};
    const f = fixture(async (action, args, count) => {
      if (count === 1) throw Object.assign(Error(`known ${code}`), {code});
      if (action === 'project-context') return fresh;
      return {rev: 22, current_project_id: 'P', auto_record: false};
    });
    await button(f.render(), associate).props.onClick();
    assert.deepEqual(f.calls[1], {action: 'project-context', args: {session_id: 'synthetic-session', query: '', offset: 0, limit: 50}});
    assert.deepEqual(f.changes, [fresh]);
    f.props.context = fresh;
    const reloaded = f.render();
    assert.match(text(reloaded), new RegExp(`known ${code}`));
    assert.equal(nodes(reloaded, node => node.type === 'button' && text(node) === retry).length, 0);
    assert.equal(button(reloaded, associate).props.disabled, false);
    await button(reloaded, associate).props.onClick();
    assertMutation(f.calls[2], 'project-associate', {project_id: 'P'}, 21);
    assert.notEqual(f.calls[2].args.operation_id, f.calls[0].args.operation_id);
    assert.equal(f.calls.length, 4);
    assertContext(f.calls[3]);
    assert.deepEqual(f.changes, [fresh, fresh]);
  });
}

test('failed conflict reload preserves the original error and permits a new operation', async () => {
  const f = fixture(async (action, args, count) => {
    if (count === 1) throw Object.assign(Error('stale revision'), {code: 'VERSION_CONFLICT'});
    throw Error('reload unavailable');
  });
  await button(f.render(), associate).props.onClick();
  const tree = f.render();
  assert.match(text(tree), /stale revision/);
  assert.doesNotMatch(text(tree), /reload unavailable/);
  assert.deepEqual(f.changes, []);
  assert.equal(f.calls.length, 2);
  assert.equal(button(tree, associate).props.disabled, false);
  assert.equal(nodes(tree, node => node.type === 'button' && text(node) === retry).length, 0);
});

for (const outcome of ['success', 'conflict']) {
  test(`unmount before mutation ${outcome} prevents stale callback, state updates and reload`, async () => {
    const gate = deferred(), f = fixture(() => gate.promise);
    const saving = button(f.render(), associate).props.onClick();
    f.h.unmount();
    if (outcome === 'success') gate.resolve({rev: 8, current_project_id: 'P'});
    else gate.reject(Object.assign(Error('stale revision'), {code: 'VERSION_CONFLICT'}));
    await saving;
    assert.deepEqual(f.changes, []);
    assert.equal(f.calls.length, 1);
    assert.equal(f.h.staleUpdates, 0);
  });
}

test('unmount while conflict context reload is pending prevents applying its stale result', async () => {
  const gate = deferred(), started = deferred();
  const f = fixture(async action => {
    if (action === 'project-associate') throw Object.assign(Error('stale revision'), {code: 'VERSION_CONFLICT'});
    started.resolve();
    return gate.promise;
  });
  const saving = button(f.render(), associate).props.onClick();
  await started.promise;
  f.h.unmount();
  gate.resolve({rev: 30, current_project_id: 'elsewhere'});
  await saving;
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.changes, []);
  assert.equal(f.h.staleUpdates, 0);
});

for (const scenario of [
  {name: 'association revoked after receipt', action: 'project-associate', fresh: {rev: 40, current_project_id: null, auto_record: true}},
  {name: 'disassociation superseded after receipt', action: 'project-disassociate', fresh: {rev: 40, current_project_id: 'P', auto_record: false}},
  {name: 'preference changed after receipt', action: 'project-preferences', fresh: {rev: 40, current_project_id: null, auto_record: true}},
]) {
  test(`${scenario.name}: applies revision 40 context rather than revision 8 receipt`, async () => {
    const receipt = {rev: 8, current_project_id: scenario.action === 'project-associate' ? 'P' : null, auto_record: false};
    const f = fixture(async action => action === 'project-context' ? scenario.fresh : receipt);
    if (scenario.action === 'project-disassociate') f.props.context = {...f.props.context, current_project_id: 'P'};
    const tree = f.render();
    if (scenario.action === 'project-preferences') await checkbox(tree).props.onChange({target: {checked: false}});
    else await button(tree, scenario.action === 'project-associate' ? associate : '取消当前会话关联').props.onClick();
    assertMutation(f.calls[0], scenario.action, scenario.action === 'project-preferences' ? {auto_record: false} : {project_id: 'P'});
    assert.equal(f.calls.length, 2);
    assertContext(f.calls[1]);
    assert.deepEqual(f.changes, [scenario.fresh]);
    f.props.context = f.changes[0];
    const refreshed = f.render();
    assert.equal(checkbox(refreshed).props.checked, scenario.fresh.auto_record);
    assert.match(text(refreshed), scenario.fresh.current_project_id === 'P' ? /当前会话研究项目：合成项目/ : /当前会话尚未选定研究项目/);
    assert.equal(nodes(refreshed, node => node.type === 'button' && text(node) === retry).length, 0);
  });
}

test('successful write with failed reload retains the operation until retry reload succeeds', async () => {
  const refresh = deferred(), started = deferred();
  const receipt = {rev: 8, current_project_id: 'P', auto_record: true};
  const fresh = {rev: 40, current_project_id: null, auto_record: false};
  const f = fixture(async (action, args, count) => {
    if (action !== 'project-context') return receipt;
    if (count === 2) throw Error('post-save reload unavailable');
    started.resolve();
    return refresh.promise;
  });
  await button(f.render(), associate).props.onClick();
  assert.equal(f.calls.length, 2);
  assertContext(f.calls[1]);
  assert.deepEqual(f.changes, []);
  const uncertain = f.render();
  assert.match(text(uncertain), /post-save reload unavailable/);
  assert.equal(button(uncertain, associate).props.disabled, true);
  assert.equal(checkbox(uncertain).props.disabled, true);
  const saving = button(uncertain, retry).props.onClick();
  await started.promise;
  assert.deepEqual(f.calls[2], f.calls[0]);
  assertContext(f.calls[3]);
  assert.deepEqual(f.changes, []);
  const pending = f.render();
  assert.match(text(pending), /正在保存会话设置/);
  assert.equal(button(pending, associate).props.disabled, true);
  assert.equal(checkbox(pending).props.disabled, true);
  refresh.resolve(fresh);
  await saving;
  assert.equal(f.calls.length, 4);
  assert.deepEqual(f.changes, [fresh]);
  const recovered = f.render();
  assert.equal(button(recovered, associate).props.disabled, false);
  assert.equal(nodes(recovered, node => node.type === 'button' && text(node) === retry).length, 0);
  assert.equal(nodes(recovered, node => node.props.role === 'alert').length, 0);
});

for (const code of ['VERSION_CONFLICT', 'INVALID_SCHEMA', 'OPERATION_CONFLICT', 'PROJECT_NOT_FOUND']) {
  test(`full HTTP ${code} code reloads context and permits a new operation`, async () => {
    const fresh = {rev: 40, current_project_id: null, auto_record: false};
    const message = `${code}: request rejected by server`;
    const f = fixture(async (action, args, count) => {
      if (count === 1) throw Object.assign(Error(`HTTP 409: ${message}`), {code: message});
      return action === 'project-context' ? fresh : {rev: 8, current_project_id: 'P', auto_record: true};
    });
    await button(f.render(), associate).props.onClick();
    assert.equal(f.calls.length, 2);
    assertContext(f.calls[1]);
    assert.deepEqual(f.changes, [fresh]);
    f.props.context = fresh;
    const recovered = f.render();
    assert.match(text(recovered), new RegExp(message));
    assert.equal(button(recovered, associate).props.disabled, false);
    assert.equal(nodes(recovered, node => node.type === 'button' && text(node) === retry).length, 0);
    await button(recovered, associate).props.onClick();
    assertMutation(f.calls[2], 'project-associate', {project_id: 'P'}, 40);
    assert.notEqual(f.calls[2].args.operation_id, f.calls[0].args.operation_id);
    assert.equal(f.calls.length, 4);
    assertContext(f.calls[3]);
    assert.deepEqual(f.changes, [fresh, fresh]);
  });
}

test('unmount after successful write while context refresh is pending prevents stale updates', async () => {
  const refresh = deferred(), started = deferred();
  const f = fixture(async action => {
    if (action !== 'project-context') return {rev: 8, current_project_id: 'P', auto_record: true};
    started.resolve();
    return refresh.promise;
  });
  const saving = button(f.render(), associate).props.onClick();
  await started.promise;
  assert.equal(f.calls.length, 2);
  assertContext(f.calls[1]);
  assert.deepEqual(f.changes, []);
  f.h.unmount();
  refresh.resolve({rev: 40, current_project_id: null, auto_record: false});
  await saving;
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.changes, []);
  assert.equal(f.h.staleUpdates, 0);
});

test('busy and missing context guard stale event handlers as well as disabled controls', async () => {
  for (const patch of [{busy: true}, {context: null}]) {
    const f = fixture();
    Object.assign(f.props, patch);
    const tree = f.render();
    assert.equal(button(tree, associate).props.disabled, true);
    assert.equal(checkbox(tree).props.disabled, true);
    await button(tree, associate).props.onClick();
    await checkbox(tree).props.onChange({target: {checked: false}});
    assert.deepEqual(f.calls, []);
    assert.deepEqual(f.changes, []);
  }
});
