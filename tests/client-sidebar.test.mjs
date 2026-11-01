import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import Forms from '../forms.cjs';

const source = readFileSync(new URL('../client-main.cjs', import.meta.url), 'utf8');
const graphSource = readFileSync(new URL('../process-graph.cjs', import.meta.url), 'utf8');
const graphUISource = readFileSync(new URL('../graph-ui.cjs', import.meta.url), 'utf8');

// Hook/element contracts only, not a DOM renderer or visual acceptance evidence.
// Function children stay opaque unless a test explicitly calls that component.
function element(type, config, ...children) {
  const {key, ...props} = config || {};
  if (children.length) props.children = children.length === 1 ? children[0] : children;
  return {type, key: key == null ? null : String(key), props, children};
}
function hooks({effects = false} = {}) {
  let cells = [], cursor = 0, mounted = true;
  const writesAfterUnmount = [];
  const slot = (kind, create) => {
    assert.ok(mounted, 'Render requires a mounted hook frame');
    const index = cursor++;
    if (!(index in cells)) cells[index] = {kind, ...create()};
    assert.equal(cells[index].kind, kind, 'Hook order changed');
    return cells[index];
  };
  const cleanupEffects = () => {
    for (const cell of cells) if (cell.kind === 'effect' && cell.cleanup) {
      cell.cleanup(); cell.cleanup = undefined;
    }
  };
  const react = {
    Fragment: Symbol('Fragment'),
    createElement: element,
    reset: () => { cursor = 0; },
    writesAfterUnmount,
    useState(initial) {
      const cell = slot('state', () => ({value: typeof initial === 'function' ? initial() : initial}));
      const frame = cells;
      return [cell.value, next => {
        if (!mounted || frame !== cells) { writesAfterUnmount.push(next); return; }
        cell.value = typeof next === 'function' ? next(cell.value) : next;
      }];
    },
    useRef(initial) {
      return slot('ref', () => ({value: {current: initial}})).value;
    },
    useEffect(callback, deps) {
      const cell = slot('effect', () => ({initialized: false}));
      const changed = !cell.initialized || deps === undefined || cell.deps === undefined ||
        deps.length !== cell.deps.length || deps.some((value, i) => !Object.is(value, cell.deps[i]));
      cell.initialized = true; cell.deps = deps; cell.callback = callback;
      if (effects && changed) cell.pending = true;
    },
    flushEffects() {
      for (const cell of cells) if (cell.kind === 'effect' && cell.pending) {
        cell.pending = false;
        cell.cleanup?.();
        cell.cleanup = cell.callback();
        assert.ok(cell.cleanup === undefined || typeof cell.cleanup === 'function');
      }
    },
    // Models cleanup/setup replay with the same refs (e.g. Strict Mode), not a remount.
    restartEffects() {
      assert.ok(effects && mounted);
      cleanupEffects();
      for (const cell of cells) if (cell.kind === 'effect') cell.pending = true;
      react.flushEffects();
    },
    unmount() { cleanupEffects(); mounted = false; },
    mount() {
      assert.equal(mounted, false, 'Unmount the previous hook frame first');
      cells = []; cursor = 0; mounted = true;
    },
  };
  return react;
}

function loadPlugin({react = hooks(), globals = {}} = {}) {
  let registration;
  const context = {
    DshMathForms: {},
    DshMathFormUI: () => ({}),
    DshMathPreview: () => ({Preview: function Preview() {}}),
    DshMathProjectForm: () => ({ProjectForm: function ProjectForm() {}}),
    DshMathWorkbenchUI: () => ({Scratchpad: function Scratchpad() {}, LegacyImport: function LegacyImport() {}}),
    DshMathRecordUI: () => ({RecordReader: function RecordReader() {}, RecordBrowser: function RecordBrowser() {}, ReviewSummary: function ReviewSummary() {}, ExportReceipt: function ExportReceipt() {}, ContextPack: function ContextPack() {}}),
    DshMathTheme: '.dsh-math-view{}',
    document: undefined,
    ...globals,
  };
  context.window = context;
  context.window.__ModuleLoader__ = {load: value => { registration = value; }};
  vm.createContext(context);
  vm.runInContext(graphSource, context);
  vm.runInContext(graphUISource, context);
  vm.runInContext(source, context);
  assert.equal(typeof context.DshMathProcessGraph.build, 'function');
  assert.equal(typeof context.DshMathGraphUI, 'function');
  return registration.factory(name => {
    assert.equal(name, 'react');
    return react;
  });
}

// Only the Cordis contracts consumed here: dependency-scoped effects are disposed
// before a service replacement, and a parent disposal tears down its child scope.
function contextWith(initialSidebar) {
  const slots = [], disposers = [], injections = [];
  let sidebar = initialSidebar, disposed = false;
  const effect = (owner, callback) => {
    const cleanup = callback();
    let active = true;
    const dispose = () => { if (active) { active = false; cleanup?.(); } };
    owner.push(dispose);
    return dispose;
  };
  const activate = injection => {
    if (!sidebar) return;
    const service = sidebar, childDisposers = [];
    injection.cleanup = () => {
      for (const dispose of [...childDisposers].reverse()) dispose();
      injection.cleanup = undefined;
    };
    injection.callback({
      get: name => name === 'betterSidebar' ? service : undefined,
      effect: callback => effect(childDisposers, callback),
    });
  };
  return {
    uiConversation: {views: {register: () => {}}},
    uiWorkspace: {openSession: () => {}},
    get: name => name === 'betterSidebar' ? sidebar : undefined,
    effect: callback => effect(disposers, callback),
    inject(names, callback) {
      assert.deepEqual(Array.from(names), ['betterSidebar']);
      const injection = {callback};
      injections.push(injection);
      disposers.push(() => injection.cleanup?.());
      activate(injection);
    },
    setSidebar(next) {
      assert.equal(disposed, false);
      if (sidebar === next) return;
      for (const injection of injections) injection.cleanup?.();
      sidebar = next;
      for (const injection of injections) activate(injection);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const dispose of [...disposers].reverse()) dispose();
    },
    slots: {
      rows: slots,
      inject: (_name, callback) => callback(),
      register: (descriptor, component) => { slots.push({descriptor, component}); },
    },
  };
}
function sidebarStub() {
  const tabs = new Map(), descriptors = [], opened = [];
  const controls = {enabled: true, disposed: 0};
  return {
    tabs, descriptors, opened, controls,
    registerTab(descriptor) {
      assert.equal(tabs.has(descriptor.id), false, 'Duplicate active tab registration');
      descriptors.push(descriptor); tabs.set(descriptor.id, descriptor);
      let active = true;
      return () => {
        assert.equal(active, true, 'Tab disposer called twice');
        active = false; controls.disposed++; tabs.delete(descriptor.id);
      };
    },
    getTab: id => tabs.get(id),
    isTabEnabled: id => controls.enabled && tabs.has(id),
    openTab: (seed, scope) => { opened.push({seed, scope}); },
  };
}
function entryTest(sidebar, {react = hooks(), globals = {}} = {}) {
  const plugin = loadPlugin({react, globals}), ctx = contextWith(sidebar);
  plugin.apply(ctx);
  const entry = ctx.slots.rows.find(row => row.descriptor.id === 'math-projects');
  assert.ok(entry);
  // Keep the original injection: availability must be checked at click time.
  const injected = entry.descriptor.inject();
  const render = (sessionId = 'session-current') => {
    react.reset(); return entry.component({sessionId, ...injected});
  };
  return {ctx, render};
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...nodes(tree.children || [])];
}
function visibleNodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(visibleNodes);
  if (!tree || typeof tree !== 'object' || tree.props?.hidden) return [];
  return [tree, ...visibleNodes(tree.children || [])];
}
const component = (tree, name) => nodes(tree).find(node => node.type?.name === name);
const visibleComponent = (tree, name) => visibleNodes(tree).find(node => node.type?.name === name);
const panel = (tree, id) => nodes(tree).find(node => node.props.role === 'tabpanel' && node.props.id.endsWith(`-${id}-panel`));
const readActions = ['list', 'overview', 'project-draft-read'];
function button(tree, label) {
  const found = visibleNodes(tree).find(node => (node.children.includes(label) || node.props['aria-label'] === label) && typeof node.props.onClick === 'function');
  assert.ok(found, `Missing button: ${label}`);
  return found;
}
const byTitle = (tree, title) => visibleNodes(tree).find(node => node.props.title === title);
const byClass = (tree, className) => visibleNodes(tree).find(node => node.props.className?.split(' ').includes(className));
const welcome = tree => nodes(tree).find(node => node.props['aria-label'] === '研究起点');
const picker = tree => nodes(tree).find(node => node.props['aria-label'] === '选择研究项目');
const alerts = tree => nodes(tree).filter(node => node.props.role === 'alert');
const dialog = tree => nodes(tree).find(node => node.props.role === 'dialog');
const settle = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

// All fixtures and API replies are in-memory. No real fetch, project registration,
// database write, replay, or export is allowed by this test harness.
function overview(id) {
  const problem = {id: `P-${id}`, rev: 1, kind: 'Problem', data: {title: id}};
  const obligation = {id: `O-${id}`, rev: 1, kind: 'Obligation', data: {statement: '待审核', problem_ref: {id: problem.id, rev: 1}, candidates: [{id: `N-${id}`, rev: 1}]}};
  const note = {id: `N-${id}`, rev: 1, kind: 'Note', data: {title: '候选', problem_ref: {id: problem.id, rev: 1}}};
  for (const object of [problem, obligation, note]) object.schema_version = 4;
  return {project: {id, campaign_id: id, schema_version: 4, mode: 'daily_research'}, snapshot: {usable: true, state: {seq: 3}}, objects: [problem, obligation, note], links: [],
    groups: {targets: [{object: problem}], accepted: [], candidates: [], obligations: [{object: obligation}], failed_routes: [], notes: [{object: note}]}};
}
// Synthetic projection inputs only. Hash-shaped constants model an already-checked
// backend reply; they are not evidence, real research, or a cryptographic chain test.
function processFixture(id, {history = false} = {}) {
  const view = overview(id);
  if (history) {
    const previous = view.objects.find(object => object.kind === 'Note');
    const current = {...previous, rev: 2, data: {...previous.data, title: '修订候选'}};
    view.objects.push(current); view.groups.notes.push({object: current});
  }
  for (const object of view.objects) Object.assign(object, {schema_version: 4,
    created_at: '2000-01-01T00:00:00Z', actor: {kind: 'human', id: 'synthetic-fixture'},
    origin: {source: 'dsh-math-human', session_id: `session-${id}`, run_id: null, tool_call_id: null}});
  view.project.title = `Synthetic project ${id}`;
  const hash = seq => seq.toString(16).padStart(64, '0');
  const events = view.objects.map((object, index) => {
    const seq = index + 1, event_hash = hash(seq);
    const data = {schema_version: 4, campaign_id: id, event_id: `synthetic-${id}-${seq}`,
      seq, kind: 'commit', actor: object.actor, origin: object.origin, changes: [object], event_hash, previous_hash: hash(seq - 1)};
    return {source: 'research/events.jsonl', seq, hash: event_hash, kind: 'research.commit', data};
  });
  const objects = {};
  for (const object of view.objects) (objects[object.id] ||= []).push(object);
  view.snapshot = {usable: true, state: {campaign_id: id, seq: events.length, head_hash: hash(events.length), objects}};
  const replay = {format: 'dsh-math-replay-v1', project_id: id, research_head: hash(events.length), sources: [], events};
  return {view, replay};
}
const processNode = tree => nodes(tree).find(node => typeof node.type === 'function' && node.type.name === 'ProcessGraph');
const projects = ['a', 'b'].map(id => ({id, title: id, session_id: `session-${id}`}));
function viewTest({effects = false, handle, globals = {}} = {}) {
  const sessions = new Set();
  const react = hooks({effects}), requests = [], controls = {failure: null, handle, apiFailure: null};
  const Editor = function Editor() {}, Refs = function Refs() {}, Select = function Select() {};
  const plugin = loadPlugin({react, globals: {
    ...globals,
    DshMathForms: Forms, DshMathFormUI: () => ({Editor, Refs, Select}), crypto: {randomUUID: () => 'test-uuid'},
    fetch: async (url, request) => {
      assert.equal(url, '/dsh-math/api');
      assert.equal(request.method, 'POST');
      const body = JSON.parse(request.body); requests.push(body);
      if (body.action === 'project-draft-read') {
        assert.deepEqual(Object.keys(body.args), ['session_id']);
        assert.ok(sessions.has(body.args.session_id), 'Draft summaries may read only an explicitly rendered session');
        assert.equal(body.args.session_id, session, 'Every summary dispatch must use the currently bound session, never scan past sessions');
      }
      if (controls.apiFailure?.action === body.action) return {json: async () => ({ok: false, error: controls.apiFailure.code, message: 'synthetic precommit rejection'})};
      if (controls.failure && body.action === 'overview') return {json: async () => ({ok: false, error: controls.failure})};
      const intercepted = controls.handle?.(body);
      if (intercepted !== undefined) return {json: async () => ({ok: true, value: await intercepted})};
      if (body.action === 'project-draft-read') return {json: async () => ({ok: true, value: {draft: null, validation: null}})};
      assert.ok(['list', 'overview'].includes(body.action), `Unexpected write API: ${body.action}`);
      return {json: async () => ({ok: true, value: body.action === 'list' ? projects : overview(body.args.project_id)})};
    },
  }});
  const ctx = contextWith(undefined); plugin.apply(ctx);
  const View = ctx.slots.rows.find(row => row.descriptor.id === 'math').component;
  let session = 'test-session', previous, sidebar = true;
  const wrap = sessionId => View({sessionId, sidebar});
  // Model two independently mounted surface lifetimes sharing one plugin module's retained buffers.
  const switchSurface = value => { react.unmount(); react.mount(); previous = undefined; sidebar = value; };
  const render = (sessionId = session, {flushEffects = true} = {}) => {
    session = sessionId; sessions.add(sessionId);
    const child = wrap(sessionId);
    assert.equal(typeof child.type, 'function', 'MathView must return its keyed ProjectView');
    if (previous && (previous.type !== child.type || previous.key !== child.key)) {
      react.unmount(); react.mount();
    }
    previous = child;
    react.reset();
    const tree = child.type(child.props);
    if (flushEffects) react.flushEffects();
    return tree;
  };
  const choose = async id => {
    let tree = render();
    // A zero-project render has no picker. Populate it through the real read-only
    // load path, not by seeding hook state or bypassing the visible control.
    if (!picker(tree)) {
      if (effects) await settle();
      else await button(tree, '刷新').props.onClick();
      tree = render();
    }
    const control = picker(tree);
    assert.ok(control, 'Project choices require a completed nonempty list');
    assert.equal(control.props.disabled, false, 'Do not select while a load is pending');
    assert.ok(nodes(control).some(node => node.type === 'option' && node.props.value === id));
    return control.props.onChange({target: {value: id}});
  };
  return {render, wrap, choose, requests, controls, react, Editor, Refs, Select, ctx, switchSurface};
}
function processTest({effects = false, history = false, handle} = {}) {
  const fixtures = {a: processFixture('a', {history}), b: processFixture('b')};
  const replayFor = body => ({...fixtures[body.args.project_id].replay, sources: body.args.sources.map(source => ({
    ...source, integrity: 'verified_chain', sha256: 'e'.repeat(64), bytes: 0,
  }))});
  const t = viewTest({effects, handle: body => {
    const intercepted = handle?.(body);
    if (intercepted !== undefined) return intercepted;
    if (body.action === 'overview') return fixtures[body.args.project_id].view;
    if (body.action === 'replay') return replayFor(body);
  }});
  return {...t, fixtures, replayFor};
}

test('registers a singleton math tab in Better Sidebar', () => {
  const sidebar = sidebarStub(), plugin = loadPlugin(), ctx = contextWith(sidebar);
  plugin.apply(ctx);
  assert.equal(sidebar.descriptors.length, 1);
  const [descriptor] = sidebar.descriptors;
  assert.equal(descriptor.id, 'dsh-math:projects');
  assert.equal(descriptor.title, '数学研究');
  assert.equal(descriptor.single, true);
  const rendered = descriptor.component({scope: {sessionId: 'session-math'}});
  assert.equal(rendered.props.sessionId, 'session-math');
  assert.equal(rendered.props.sidebar, true);
  ctx.dispose();
  assert.equal(sidebar.controls.disposed, 1);
});

test('header entry dispatches to the ready sidebar instead of opening a modal', () => {
  const sidebar = sidebarStub(), t = entryTest(sidebar);
  button(t.render(), '数学项目').props.onClick();
  assert.equal(sidebar.opened.length, 1);
  assert.equal(sidebar.opened[0].seed.type, 'dsh-math:projects');
  assert.equal(sidebar.opened[0].seed.title, '数学研究');
  assert.equal(sidebar.opened[0].scope.sessionId, 'session-current');
  assert.equal(dialog(t.render()), undefined);
  assert.equal(alerts(t.render()).length, 0);
  // openTab is void: this asserts dispatch only, never visible-open success.
});

test('keeps the conversation entries when Better Sidebar is absent', () => {
  const plugin = loadPlugin(), ctx = contextWith(undefined);
  assert.doesNotThrow(() => plugin.apply(ctx));
  assert.equal(ctx.slots.rows.filter(row => row.descriptor.id === 'math-projects').length, 2);
});

test('late sidebar arrival, unload, and reload register exactly once per service lifetime', () => {
  const t = entryTest(undefined), first = sidebarStub(), replacement = sidebarStub();
  button(t.render(), '数学项目').props.onClick();
  assert.ok(dialog(t.render()));
  dialog(t.render()).children[0].props.onClose();
  t.ctx.setSidebar(first); t.ctx.setSidebar(first);
  assert.equal(first.descriptors.length, 1);
  assert.equal(first.tabs.size, 1);
  button(t.render(), '数学项目').props.onClick();
  assert.equal(first.opened.length, 1);
  assert.equal(dialog(t.render()), undefined);

  t.ctx.setSidebar(undefined); t.ctx.setSidebar(undefined);
  assert.equal(first.controls.disposed, 1);
  assert.equal(first.tabs.size, 0);
  button(t.render(), '数学项目').props.onClick();
  assert.ok(dialog(t.render()));
  assert.equal(first.opened.length, 1);
  dialog(t.render()).children[0].props.onClose();

  t.ctx.setSidebar(first); t.ctx.setSidebar(first);
  assert.equal(first.descriptors.length, 2);
  assert.equal(first.tabs.size, 1);
  t.ctx.setSidebar(replacement); t.ctx.setSidebar(replacement);
  assert.equal(first.controls.disposed, 2);
  assert.equal(first.tabs.size, 0);
  assert.equal(replacement.descriptors.length, 1);
  button(t.render(), '数学项目').props.onClick();
  assert.equal(replacement.opened.length, 1);
  assert.equal(first.opened.length, 1);
  assert.equal(dialog(t.render()), undefined);
  t.ctx.dispose(); t.ctx.dispose();
  assert.equal(replacement.controls.disposed, 1);
  assert.equal(replacement.tabs.size, 0);
});

test('missing or disabled tab and missing session show errors without pretending to open', async t => {
  const cases = [
    {name: 'missing registered tab', alter: sidebar => sidebar.tabs.clear(), message: '数学标签页尚未就绪，请重新打开侧栏。'},
    {name: 'disabled tab', alter: sidebar => { sidebar.controls.enabled = false; }, message: '数学标签页已禁用，请在 Better Sidebar 设置中启用。'},
    {name: 'missing session', session: '', message: '请先打开一个会话，再使用数学项目。'},
    {name: 'missing getTab method', alter: sidebar => { sidebar.getTab = undefined; }, pattern: /getTab/},
  ];
  for (const scenario of cases) await t.test(scenario.name, () => {
    const sidebar = sidebarStub(), entry = entryTest(sidebar);
    // Change readiness after injection, proving it is not cached by the entry.
    scenario.alter?.(sidebar);
    button(entry.render(scenario.session), '数学项目').props.onClick();
    const tree = entry.render(scenario.session), errors = alerts(tree);
    assert.equal(sidebar.opened.length, 0);
    assert.equal(dialog(tree), undefined);
    assert.equal(errors.length, 1);
    if (scenario.message) assert.ok(errors[0].children.includes(scenario.message));
    else assert.match(errors[0].children.join(''), scenario.pattern);
  });
});

test('sidebar dispatch failure is surfaced and a later click clears the error', () => {
  const sidebar = sidebarStub(), t = entryTest(sidebar), openTab = sidebar.openTab;
  sidebar.openTab = () => { throw Error('mock dispatch failed'); };
  button(t.render(), '数学项目').props.onClick();
  assert.equal(sidebar.opened.length, 0);
  assert.equal(dialog(t.render()), undefined);
  assert.ok(alerts(t.render())[0].children.includes('mock dispatch failed'));
  sidebar.openTab = openTab;
  button(t.render(), '数学项目').props.onClick();
  assert.equal(sidebar.opened.length, 1);
  assert.equal(alerts(t.render()).length, 0);
  assert.equal(dialog(t.render()), undefined);
});

test('theme disposal removes only the style node owned by that plugin generation', () => {
  const children = [];
  const document = {
    createElement(tag) {
      assert.equal(tag, 'style');
      return {id: '', textContent: '', removed: 0, remove() {
        this.removed++;
        const index = children.indexOf(this);
        if (index >= 0) children.splice(index, 1);
      }};
    },
    head: {appendChild(node) { children.push(node); }},
    getElementById: id => children.find(node => node.id === id),
  };
  const foreign = document.createElement('style');
  foreign.id = 'dsh-math-theme'; foreign.textContent = 'foreign style';
  document.head.appendChild(foreign);
  const plugin = loadPlugin({globals: {document}}), first = contextWith(), second = contextWith();
  plugin.apply(first); plugin.apply(second);
  assert.equal(children.length, 3);
  const [, ownedFirst, ownedSecond] = children;
  assert.notEqual(ownedFirst, ownedSecond);
  for (const owned of [ownedFirst, ownedSecond]) {
    assert.equal(owned.id, 'dsh-math-theme');
    assert.equal(owned.textContent, '.dsh-math-view{}');
  }
  first.dispose(); first.dispose();
  assert.deepEqual(children, [foreign, ownedSecond]);
  assert.equal(ownedFirst.removed, 1);
  assert.equal(ownedSecond.removed, 0);
  second.dispose();
  assert.deepEqual(children, [foreign]);
  assert.equal(ownedSecond.removed, 1);
  assert.equal(foreign.removed, 0);
  assert.equal(foreign.textContent, 'foreign style');
});

test('an empty workspace has one primary create action and a secondary import modal entry', async () => {
  const t = viewTest({effects: true, handle: body => body.action === 'list' ? [] : undefined});
  t.render('session-empty'); await settle();
  const tree = t.render(), start = welcome(tree), header = nodes(tree).find(node => node.type === 'header');
  const primary = nodes(tree).filter(node => node.props.variant === 'primary');
  assert.equal(tree.props['aria-busy'], false);
  assert.equal(picker(tree), undefined);
  assert.equal(byClass(tree, 'dm-project-picker'), undefined);
  assert.ok(start);
  assert.equal(primary.length, 1);
  assert.equal(primary[0], button(start, '新建项目'));
  assert.equal(nodes(header).some(node => node.children.includes('新建项目')), false);
  assert.equal(nodes(tree).filter(node => node.children.includes('新建项目') && node.props.onClick).length, 1);
  assert.ok(nodes(start).some(node => node.children.includes('新建与浏览不调用模型，也不会自动开始研究。')));

  const legacy = byClass(tree, 'dm-import');
  assert.equal(legacy.type, 'section');
  assert.equal(legacy.props['aria-label'], '导入旧研究库（只读副本）');
  assert.equal(button(legacy, '导入旧研究库').props.variant, undefined, 'Import stays secondary to project creation');
  assert.equal(button(legacy, '导入旧研究库').props.title, '导入旧研究库（只读副本）');
  assert.equal(byClass(legacy, 'dm-import-mark'), undefined, 'Import does not duplicate the page icon');
  assert.ok(nodes(legacy).some(node => node.children.includes('只读副本')));
  assert.ok(nodes(legacy).some(node => node.children.includes('原库不变；不自动开始研究。')));
  assert.equal(byClass(legacy, 'dm-import-hint'), undefined, 'Safety note is stated once, beside the action');
  assert.equal(nodes(tree).some(node => node.type?.name === 'LegacyImport'), false);
  await button(legacy, '导入旧研究库').props.onClick();
  const imported = nodes(t.render()).find(node => node.type?.name === 'LegacyImport');
  assert.equal(imported.props.sessionId, 'session-empty');
  imported.props.close();
  assert.equal(nodes(t.render()).some(node => node.type?.name === 'LegacyImport'), false);

  button(start, '新建项目').props.onClick();
  const creating = t.render(), form = nodes(creating).find(node => node.type?.name === 'ProjectForm');
  assert.equal(welcome(creating), undefined);
  assert.equal(picker(creating), undefined);
  assert.ok(byTitle(creating, '项目准备'));
  assert.equal(form.props.sessionId, 'session-empty');
  assert.equal(typeof form.props.request, 'function');
  assert.equal(typeof form.props.registerGuard, 'function');
  const cancel = button(nodes(creating).find(node => node.type === 'header'), '取消新建');
  assert.equal(cancel.props.variant, 'ghost');
  await cancel.props.onClick();
  assert.ok(welcome(t.render()));
  assert.equal(byTitle(t.render(), '项目准备'), undefined);
  assert.equal(nodes(t.render()).filter(node => node.props.variant === 'primary').length, 1);
  assert.deepEqual(t.requests.map(request => request.action), ['list', 'project-draft-read'], 'Opening and cancelling forms must not create research data');
});

test('overview leads with the goal and actions, while records keeps every group', async () => {
  const snapshot = overview('a'), goalObject = snapshot.groups.targets[0].object;
  goalObject.data.statement = '合成目标第一行\n合成目标第二行';
  const t = viewTest({handle: body => body.action === 'overview' ? snapshot : undefined});
  await t.choose('a');
  const tree = t.render(), flat = nodes(tree), goal = byClass(tree, 'dm-goal');
  const groupTitles = ['当前目标', '已接受 / 已关闭', '候选结论', '证明义务', '失败路线', '研究笔记'];
  assert.ok(goal);
  assert.equal(welcome(tree), undefined);
  assert.equal(button(nodes(tree).find(node => node.type === 'header'), '新建项目').props.variant, 'ghost');
  assert.equal(byClass(goal, 'dm-goal-title').children[0], goalObject.data.title);
  assert.equal(component(byClass(goal, 'dm-goal-statement'), 'Preview').props.text, goalObject.data.statement);
  assert.equal(nodes(tree).filter(node => node.children.includes('P-a@1') && node.props.onClick).length, 1, 'Do not duplicate the target in the digest');
  assert.deepEqual(flat.filter(node => groupTitles.includes(node.props.title)).map(node => node.props.title), ['证明义务', '研究笔记']);
  const firstGroup = byTitle(tree, '证明义务');
  assert.ok(flat.indexOf(goal) < flat.indexOf(firstGroup));
  for (const label of ['登记笔记', '登记义务', '登记证据']) assert.ok(flat.indexOf(button(goal, label)) < flat.indexOf(firstGroup));
  assert.ok(nodes(goal).some(node => node.children.includes('登记的是研究记录，不是证明。程序检查不替代人工审核。')));
  const details = byClass(tree, 'dm-project-details');
  assert.equal(details.type, 'details');
  assert.ok(details.props.open === undefined || details.props.open === false);
  assert.ok(flat.indexOf(details) > flat.indexOf(byTitle(tree, '研究笔记')));
  assert.ok(nodes(details).some(node => node.children.includes('开放性与数学结论不由软件自动判定；当前状态由版本化记录推导。')));

  button(tree, '记录与审核').props.onClick();
  const records = t.render();
  assert.equal(button(records, '记录与审核').props['data-active'], true);
  assert.equal(panel(records, 'overview').props.hidden, true);
  const browser = visibleComponent(records, 'RecordBrowser');
  assert.equal(browser.props.view, snapshot, 'Complete groups and exact objects reach the read-only browser');
  assert.deepEqual(Object.keys(browser.props.view.groups), ['targets', 'accepted', 'candidates', 'obligations', 'failed_routes', 'notes']);
  assert.equal(typeof browser.props.inspect, 'function');
  browser.props.inspect(goalObject);
  assert.equal(visibleComponent(t.render(), 'RecordReader').props.object, goalObject);
  assert.ok(t.requests.every(request => readActions.includes(request.action)));
});

test('overview truncation still reaches all records by title and exact historical reference', async () => {
  const snapshot = overview('a'), problem = {id: 'P-a', rev: 1};
  const notes = [
    ...[1, 2, 3].map(index => ({id: `N-digest-${index}`, rev: 1, kind: 'Note', data: {title: `合成记录 ${index}`, problem_ref: problem}})),
    ...[1, 2].map(rev => ({id: 'N-history', rev, kind: 'Note', data: {title: '同名历史记录', statement: `合成版本 ${rev}`, problem_ref: problem}})),
  ];
  snapshot.groups.notes = notes.map(object => ({object}));
  snapshot.groups.accepted = [{object: snapshot.objects.find(object => object.id === 'N-a'), assessment: {state: 'closed', reasons: ['仅合成局部检查']}}];
  snapshot.objects.push(...notes);
  const t = viewTest({handle: body => body.action === 'overview' ? snapshot : undefined});
  await t.choose('a');
  const digest = t.render(), notePanel = byTitle(digest, '研究笔记');
  const rows = panel => nodes(panel).filter(node => node.props.className === 'dm-record');
  assert.equal(rows(notePanel).length, 3);
  assert.deepEqual(rows(notePanel).map(node => node.key), notes.slice(0, 3).map(object => `${object.id}@${object.rev}`));
  assert.equal(nodes(notePanel).some(node => node.children.includes('N-history@1') || node.children.includes('N-history@2')), false);
  const disclaimer = '按记录状态归类。程序检查通过仅限对应检查范围，不代表全局证明或 R1/R2 已通过。';
  assert.ok(nodes(byTitle(digest, '已接受 / 已关闭')).some(node => node.children.includes(disclaimer)));
  assert.equal(byTitle(digest, '已接受事实'), undefined);
  button(notePanel, '查看全部 5 条').props.onClick();
  const records = t.render(), browser = visibleComponent(records, 'RecordBrowser');
  assert.equal(button(records, '记录与审核').props['data-active'], true);
  assert.equal(picker(records).props.value, 'a');
  assert.equal(panel(records, 'overview').props.hidden, true);
  assert.deepEqual(browser.props.view.groups.notes.map(row => row.object), notes, 'Browser receives every historical revision, not the truncated digest');
  assert.equal(visibleNodes(records).some(node => node.children.includes('查看全部 5 条')), false);
  const before = t.requests.length;
  for (const object of notes) {
    const key = `${object.id}@${object.rev}`;
    browser.props.inspect(object);
    const detail = byTitle(t.render(), key), reader = visibleComponent(detail, 'RecordReader');
    assert.ok(reader, 'The browser callback opens the selected exact revision');
    assert.equal(reader.props.object, object);
    reader.props.onClose();
    assert.equal(byTitle(t.render(), key), undefined);
  }
  // Digest title and exact-reference controls still share that same navigation contract.
  button(t.render(), '研究概览').props.onClick();
  const row = rows(byTitle(t.render(), '研究笔记'))[0];
  for (const opener of [byClass(row, 'dm-record-title'), button(row, `${notes[0].id}@${notes[0].rev}`)]) {
    assert.equal(opener.type, 'button'); opener.props.onClick();
    const reader = visibleComponent(t.render(), 'RecordReader'); assert.equal(reader.props.object, notes[0]); reader.props.onClose();
  }
  assert.equal(t.requests.length, before, 'Opening already-loaded records is local, not a write or a new research request');
});

test('overview registration opens the record editor before the record lists', async () => {
  for (const [label, kind] of [['登记笔记', 'Note'], ['登记义务', 'Obligation'], ['登记证据', 'Evidence']]) {
    const t = viewTest(); await t.choose('a');
    button(t.render(), label).props.onClick();
    const tree = t.render(), flat = nodes(tree), editor = flat.find(node => node.type === t.Editor);
    assert.equal(button(tree, '记录与审核').props['data-active'], true);
    assert.equal(editor.props.initial.kind, kind);
    assert.equal(editor.props.initial.data.problem_ref.id, 'P-a');
    assert.ok(flat.indexOf(byTitle(tree, '编辑研究记录')) < flat.indexOf(visibleComponent(tree, 'RecordBrowser')));
    assert.ok(t.requests.every(request => readActions.includes(request.action)));
  }
});

test('refresh retains a draft and sources but project change clears transient state', async () => {
  const t = viewTest(); await t.choose('a');
  button(t.render(), 'O-a@1').props.onClick();
  nodes(t.render()).find(node => node.type === t.Refs).props.change([{id: 'N-a', rev: 1}]);
  button(t.render(), '笔记').props.onClick();
  const draft = nodes(t.render()).find(node => node.type === t.Editor).props.initial;
  button(t.render(), '回放与导出').props.onClick();
  button(t.render(), '添加回放来源').props.onClick();
  nodes(t.render()).find(node => node.props.label === '来源 1 完整路径').props.change('/test/a.jsonl');
  await button(t.render(), '刷新').props.onClick();
  assert.equal(nodes(t.render()).find(node => node.props.label === '来源 1 完整路径').props.value, '/test/a.jsonl');
  button(t.render(), '记录与审核').props.onClick();
  assert.equal(nodes(t.render()).find(node => node.type === t.Editor).props.initial, draft);
  const nextList = deferred(); t.controls.handle = body => body.action === 'list' ? nextList.promise : undefined;
  const pending = t.choose('b'); await settle();
  assert.equal(picker(t.render()).props.disabled, true);
  assert.equal(t.render().props['aria-busy'], true);
  assert.ok(nodes(t.render()).some(node => node.props.role === 'status'));
  nextList.resolve(projects); await pending;
  assert.equal(picker(t.render()).props.value, 'b');
  assert.equal(nodes(t.render()).some(node => node.type === t.Editor), false);
  const browserB = visibleComponent(t.render(), 'RecordBrowser');
  browserB.props.inspect(browserB.props.view.objects.find(object => object.id === 'O-b' && object.rev === 1));
  assert.equal(nodes(t.render()).find(node => node.type === t.Refs).props.value.length, 0);
  button(t.render(), '回放与导出').props.onClick();
  assert.equal(nodes(t.render()).some(node => node.props.label === '来源 1 完整路径'), false);
});

test('selecting no project clears its view, draft, candidate and replay sources', async () => {
  const t = viewTest(); await t.choose('a');
  button(t.render(), 'O-a@1').props.onClick();
  nodes(t.render()).find(node => node.type === t.Refs).props.change([{id: 'N-a', rev: 1}]);
  button(t.render(), '笔记').props.onClick();
  button(t.render(), '回放与导出').props.onClick();
  button(t.render(), '添加回放来源').props.onClick();
  nodes(t.render()).find(node => node.props.label === '来源 1 完整路径').props.change('/test/a.jsonl');
  const before = t.requests.length;
  await t.choose('');
  assert.deepEqual(t.requests.slice(before).map(request => request.action), ['list']);
  assert.equal(picker(t.render()).props.value, '');
  assert.equal(t.render().props['aria-busy'], false);
  assert.ok(welcome(t.render()));
  assert.equal(byTitle(t.render(), '联合只读回放与导出'), undefined);
  assert.equal(byTitle(t.render(), '当前目标'), undefined);
  assert.equal(byClass(t.render(), 'dm-goal'), undefined);
  await t.choose('a');
  assert.equal(nodes(t.render()).some(node => node.props.label === '来源 1 完整路径'), false);
  button(t.render(), '记录与审核').props.onClick();
  assert.equal(nodes(t.render()).some(node => node.type === t.Editor), false);
  assert.equal(byTitle(t.render(), 'O-a@1'), undefined);
  const browser = visibleComponent(t.render(), 'RecordBrowser');
  browser.props.inspect(browser.props.view.objects.find(object => object.id === 'O-a' && object.rev === 1));
  assert.equal(nodes(t.render()).find(node => node.type === t.Refs).props.value.length, 0);
});

test('failed project load keeps the current draft and reports the error', async () => {
  const t = viewTest(); await t.choose('a'); button(t.render(), '登记笔记').props.onClick();
  const draft = nodes(t.render()).find(node => node.type === t.Editor).props.initial;
  t.controls.failure = 'UNUSABLE'; await t.choose('b');
  const tree = t.render();
  assert.equal(picker(tree).props.value, 'a');
  assert.equal(picker(tree).props.disabled, false);
  assert.equal(nodes(tree).find(node => node.type === t.Editor).props.initial, draft);
  assert.ok(alerts(tree).some(node => node.children.includes('UNUSABLE')));
});

test('session key remounts isolate all local state and initialize the matching project once', async () => {
  const t = viewTest({effects: true});
  assert.equal(t.wrap('session-a').key, 'session-a');
  assert.equal(t.wrap('session-b').key, 'session-b');
  assert.equal(t.wrap(undefined).key, '');
  t.render('session-a'); await settle();
  assert.equal(picker(t.render()).props.value, 'a');
  assert.deepEqual(t.requests.map(request => request.action), ['list', 'project-draft-read', 'overview']);
  button(t.render(), '登记笔记').props.onClick();
  button(t.render(), '新建项目').props.onClick();
  assert.equal(nodes(t.render()).find(node => node.type?.name === 'ProjectForm').key, 'session-a');
  button(t.render(), '回放与导出').props.onClick();
  button(t.render(), '添加回放来源').props.onClick();
  const empty = t.render('session-b');
  assert.equal(picker(empty), undefined, 'New scope must not expose the previous project choices');
  assert.ok(welcome(empty));
  assert.equal(byTitle(empty, '项目准备'), undefined);
  assert.equal(byTitle(empty, '当前目标'), undefined);
  assert.equal(byClass(empty, 'dm-goal'), undefined);
  await settle();
  assert.equal(picker(t.render()).props.value, 'b');
  assert.equal(button(t.render(), '研究概览').props['data-active'], true);
  assert.deepEqual(t.requests.map(request => request.action), ['list', 'project-draft-read', 'overview', 'list', 'project-draft-read', 'overview']);
  assert.deepEqual(t.requests.filter(request => request.action === 'overview').map(request => request.args.project_id), ['a', 'b']);
  button(t.render(), '记录与审核').props.onClick();
  assert.equal(nodes(t.render()).some(node => node.type === t.Editor), false);
  button(t.render(), '回放与导出').props.onClick();
  assert.equal(nodes(t.render()).some(node => node.props.label === '来源 1 完整路径'), false);
  button(t.render(), '新建项目').props.onClick();
  assert.equal(nodes(t.render()).find(node => node.type?.name === 'ProjectForm').key, 'session-b');
  assert.equal(nodes(t.render()).find(node => node.type?.name === 'ProjectForm').props.sessionId, 'session-b');
  assert.equal(t.requests.length, 6, 'Rerenders must not restart initialization');
  assert.deepEqual(t.requests.filter(body => body.action === 'project-draft-read').map(body => body.args.session_id), ['session-a', 'session-b']);
});

test('switching to a session without a project clears the previous scope', async () => {
  const t = viewTest({effects: true});
  t.render('session-a'); await settle();
  assert.equal(picker(t.render()).props.value, 'a');
  button(t.render(), '登记笔记').props.onClick();
  const before = t.requests.length;
  t.render('session-without-project'); await settle();
  const tree = t.render();
  assert.equal(picker(tree).props.value, '');
  assert.equal(tree.props['aria-busy'], false);
  assert.ok(welcome(tree));
  assert.equal(byTitle(tree, '当前目标'), undefined);
  assert.equal(byClass(tree, 'dm-goal'), undefined);
  assert.equal(nodes(tree).some(node => node.type === t.Editor), false);
  assert.deepEqual(t.requests.slice(before).map(request => request.action), ['list', 'project-draft-read']);
  assert.equal(t.requests.at(-1).args.session_id, 'session-without-project');
});

test('a late successful or failed A response cannot contaminate a completed B session', async testContext => {
  for (const outcome of ['resolve', 'reject']) await testContext.test(outcome, async () => {
    const a = deferred(), b = deferred();
    const t = viewTest({effects: true, handle: body => body.action === 'overview' ? (body.args.project_id === 'a' ? a : b).promise : undefined});
    t.render('session-a'); await settle();
    assert.equal(t.requests.at(-1).args.project_id, 'a');
    t.render('session-b'); await settle();
    assert.equal(t.requests.at(-1).args.project_id, 'b');
    b.resolve(overview('b')); await settle();
    assert.equal(picker(t.render()).props.value, 'b');
    button(t.render(), '登记笔记').props.onClick();
    const draft = nodes(t.render()).find(node => node.type === t.Editor).props.initial;
    const refreshing = deferred();
    t.controls.handle = body => body.action === 'list' ? refreshing.promise : undefined;
    const refresh = button(t.render(), '刷新').props.onClick();
    assert.equal(t.render().props['aria-busy'], true);
    const beforeLate = t.requests.length;
    assert.equal(beforeLate, 8, 'Two initializations and one explicit refresh include only their scoped draft reads');
    if (outcome === 'resolve') a.resolve(overview('a'));
    else a.reject(Error('late A failure'));
    await settle();
    const tree = t.render();
    assert.equal(picker(tree).props.value, 'b');
    assert.equal(tree.props['aria-busy'], true, 'Old finally must not clear B busy');
    assert.equal(picker(tree).props.disabled, true);
    assert.equal(nodes(tree).find(node => node.type === t.Editor).props.initial, draft);
    assert.equal(alerts(tree).length, 0);
    assert.equal(t.react.writesAfterUnmount.length, 0, 'Old success, catch and finally must not update unmounted state');
    assert.equal(t.requests.length, beforeLate, 'Old A must not trigger any follow-up request');
    refreshing.resolve(projects); await refresh;
    assert.equal(t.render().props['aria-busy'], false);
    assert.equal(picker(t.render()).props.value, 'b');
    assert.equal(nodes(t.render()).find(node => node.type === t.Editor).props.initial, draft);
  });
});

test('effect cleanup/setup keeps a newer operation busy when an older finally finishes', async () => {
  const oldList = deferred(), newList = deferred(), lists = [oldList, newList];
  const t = viewTest({effects: true, handle: body => body.action === 'list' ? lists.shift().promise : undefined});
  t.render('session-a');
  assert.equal(t.render().props['aria-busy'], true);
  t.react.restartEffects();
  assert.equal(t.requests.length, 4);
  oldList.resolve(projects); await settle();
  assert.equal(t.render().props['aria-busy'], true);
  assert.equal(picker(t.render()), undefined, 'Superseded initialization must not publish project choices');
  assert.equal(button(t.render(), '刷新').props.disabled, true);
  assert.equal(alerts(t.render()).length, 0);
  assert.deepEqual(t.requests.map(request => request.action), ['list', 'project-draft-read', 'list', 'project-draft-read'], 'Superseded initialization must not request overview');
  newList.resolve(projects); await settle();
  assert.equal(picker(t.render()).props.value, 'a');
  assert.equal(t.render().props['aria-busy'], false);
  assert.deepEqual(t.requests.map(request => request.action), ['list', 'project-draft-read', 'list', 'project-draft-read', 'overview']);
});

test('double save callbacks dispatch only one mocked commit before the disabled render', async () => {
  const pending = deferred(), t = viewTest({handle: body => body.action === 'commit' ? pending.promise : undefined});
  await t.choose('a'); button(t.render(), '登记笔记').props.onClick();
  const editor = nodes(t.render()).find(node => node.type === t.Editor);
  const request = {operation_id: 'synthetic-only', changes: []};
  const first = editor.props.save(request), second = editor.props.save(request);
  assert.equal(t.requests.filter(body => body.action === 'commit').length, 1);
  assert.equal(t.requests.at(-1).args.project_id, 'a');
  assert.deepEqual(t.requests.at(-1).args.request, request);
  const busyTree = t.render(), busyEditor = nodes(busyTree).find(node => node.type === t.Editor);
  assert.equal(busyTree.props['aria-busy'], true);
  assert.equal(busyEditor.props.busy, true);
  const third = busyEditor.props.save(request);
  assert.equal(t.requests.filter(body => body.action === 'commit').length, 1, 'Pending ref must survive a rerender');
  await Promise.all([second, third]);
  assert.equal(t.render().props['aria-busy'], true);
  pending.resolve({}); await first;
  assert.equal(t.render().props['aria-busy'], false);
  assert.equal(nodes(t.render()).some(node => node.type === t.Editor), false);
  assert.deepEqual(t.requests.map(body => body.action), ['project-draft-read', 'list', 'list', 'overview', 'commit', 'list', 'overview']);
});

test('an unmounted save completion cannot dispatch a refresh in the new session', async () => {
  const pending = deferred(), t = viewTest({effects: true, handle: body => body.action === 'commit' ? pending.promise : undefined});
  t.render('session-a'); await settle();
  button(t.render(), '登记笔记').props.onClick();
  const save = nodes(t.render()).find(node => node.type === t.Editor).props.save;
  const operation = save({operation_id: 'synthetic-only', changes: []});
  t.render('session-b'); await settle();
  assert.equal(picker(t.render()).props.value, 'b');
  const before = t.requests.length;
  pending.resolve({}); await assert.rejects(operation, /View changed/);
  assert.equal(t.requests.length, before);
  assert.equal(t.react.writesAfterUnmount.length, 0);
  assert.equal(picker(t.render()).props.value, 'b');
  assert.equal(t.render().props['aria-busy'], false);
  assert.equal(alerts(t.render()).length, 0);
  await save({operation_id: 'stale-callback', changes: []});
  assert.equal(t.requests.length, before, 'A stale callback must not dispatch after unmount');
});

test('process entry reads only project replay and selected sources require an explicit load', async () => {
  const t = processTest(); await t.choose('a');
  const before = t.requests.length;
  button(t.render(), '回放与导出').props.onClick();
  button(t.render(), '添加回放来源').props.onClick();
  nodes(t.render()).find(node => node.props.label === '来源 1 格式').props.change('ledger-py-v1');
  nodes(t.render()).find(node => node.props.label === '来源 1 完整路径').props.change('/synthetic/first.jsonl');
  assert.equal(t.requests.length, before, 'Selecting a source must not read it');
  const enter = button(t.render(), '研究过程').props.onClick;
  const first = enter(), duplicate = enter();
  assert.deepEqual(t.requests.slice(before), [{action: 'replay', args: {project_id: 'a', sources: []}}]);
  await Promise.all([first, duplicate]);
  const initial = processNode(t.render()), frozen = JSON.stringify(initial.props.graph);
  assert.equal(initial.props.graph.format, 'dsh-math-process-graph-v1');
  assert.equal(initial.props.graph.counts.records, 3, 'Load the real projection, not a fake Graph factory');
  assert.deepEqual(Array.from(initial.props.graph.sources, source => source.path), ['research/events.jsonl']);
  assert.equal(alerts(t.render()).length, 0);

  button(t.render(), '选择工具账本').props.onClick();
  assert.equal(button(t.render(), '回放与导出').props['data-active'], true);
  nodes(t.render()).find(node => node.props.label === '来源 1 完整路径').props.change('/synthetic/second.jsonl');
  await button(t.render(), '研究过程').props.onClick();
  assert.equal(t.requests.length, before + 1, 'Returning to a frozen graph must not reload sources');
  assert.equal(processNode(t.render()).props.graph, initial.props.graph);
  assert.equal(processNode(t.render()).key, initial.key);
  assert.equal(JSON.stringify(initial.props.graph), frozen);
  const add = button(t.render(), '加入所选来源（1）');
  assert.equal(add.props.disabled, false);
  await add.props.onClick();
  assert.deepEqual(t.requests.at(-1), {action: 'replay', args: {project_id: 'a', sources: [{adapter: 'ledger-py-v1', path: '/synthetic/second.jsonl'}]}});
  const loaded = processNode(t.render());
  assert.deepEqual(Array.from(loaded.props.graph.sources, source => source.path), ['research/events.jsonl', '/synthetic/second.jsonl']);
  assert.notEqual(loaded.key, initial.key, 'A changed source snapshot must own a fresh graph UI lifetime');
  button(t.render(), '选择工具账本').props.onClick();
  button(t.render(), '移除来源').props.onClick();
  await button(t.render(), '研究过程').props.onClick();
  assert.equal(processNode(t.render()).props.graph, loaded.props.graph, 'Removing a selection does not mutate an already-read snapshot');
  assert.equal(button(t.render(), '加入所选来源（0）').props.disabled, true);
  assert.equal(t.requests.length, before + 2);
  await button(t.render(), '读取本项目记录').props.onClick();
  assert.deepEqual(t.requests.at(-1), {action: 'replay', args: {project_id: 'a', sources: []}});
  assert.deepEqual(Array.from(processNode(t.render()).props.graph.sources, source => source.path), ['research/events.jsonl']);
  assert.ok(t.requests.every(request => [...readActions, 'replay'].includes(request.action)), 'No model, mutation, audit export, or source discovery request');
});

test('a stale process head reports an error without replacing a frozen snapshot', async testContext => {
  for (const existing of [false, true]) await testContext.test(existing ? 'existing graph' : 'first load', async () => {
    const t = processTest(); await t.choose('a');
    if (existing) await button(t.render(), '研究过程').props.onClick();
    const previous = processNode(t.render());
    const fallback = t.controls.handle;
    t.controls.handle = body => body.action === 'replay' ? {...t.fixtures.a.replay, research_head: 'f'.repeat(64)} : fallback(body);
    await button(t.render(), existing ? '读取本项目记录' : '研究过程').props.onClick();
    const message = '过程图：回放与概览的研究 head 不一致，请刷新后重试。';
    assert.deepEqual(alerts(t.render()).map(node => node.children[0]), [message]);
    assert.equal(processNode(t.render())?.props.graph, previous?.props.graph);
    assert.equal(t.render().props['aria-busy'], false);
    const before = t.requests.length;
    t.render(); await settle(); t.render();
    assert.deepEqual(alerts(t.render()).map(node => node.children[0]), [message], 'Rerenders must not silently clear a rejected snapshot error');
    assert.equal(t.requests.length, before, 'No implicit retry or source discovery');
  });
});

test('refresh, project changes and no-project selection clear the process snapshot', async () => {
  const t = processTest(); await t.choose('a');
  await button(t.render(), '研究过程').props.onClick();
  const previous = processNode(t.render()).props.graph, before = t.requests.length;
  await button(t.render(), '刷新').props.onClick();
  assert.equal(processNode(t.render()), undefined);
  assert.deepEqual(t.requests.slice(before).map(request => request.action), ['project-draft-read', 'list', 'overview']);
  assert.equal(button(t.render(), '研究过程').props['data-active'], true);
  await button(t.render(), '读取本项目记录').props.onClick();
  assert.notEqual(processNode(t.render()).props.graph, previous);
  await t.choose('b');
  assert.equal(processNode(t.render()), undefined);
  await button(t.render(), '读取本项目记录').props.onClick();
  assert.equal(processNode(t.render()).props.graph.project_id, 'b');
  await t.choose('');
  assert.equal(processNode(t.render()), undefined);
  assert.ok(welcome(t.render()));
  await t.choose('a');
  assert.equal(processNode(t.render()), undefined, 'Reselecting a project must not resurrect its old graph');
  assert.equal(t.requests.filter(request => request.action === 'replay').length, 3);
});

test('process references open the exact historical revision in the records tab', async () => {
  const t = processTest({history: true}); await t.choose('a');
  await button(t.render(), '研究过程').props.onClick();
  const graph = processNode(t.render()), before = t.requests.length;
  assert.deepEqual(Array.from(graph.props.graph.nodes.filter(node => node.ref?.id === 'N-a'), node => node.ref.rev), [1, 2]);
  graph.props.inspect({id: 'N-a', rev: 99});
  assert.equal(button(t.render(), '研究过程').props['data-active'], true, 'Never substitute the latest revision for a missing exact ref');
  for (const rev of [1, 2]) {
    graph.props.inspect({id: 'N-a', rev});
    assert.equal(button(t.render(), '记录与审核').props['data-active'], true);
    const detail = byTitle(t.render(), `N-a@${rev}`);
    assert.ok(detail);
    assert.equal(visibleComponent(detail, 'RecordReader').props.object, t.fixtures.a.view.objects.find(object => object.id === 'N-a' && object.rev === rev));
    button(t.render(), '关闭详情').props.onClick();
    await button(t.render(), '研究过程').props.onClick();
    assert.equal(processNode(t.render()).props.graph, graph.props.graph);
  }
  assert.equal(t.requests.length, before, 'Inspecting a captured reference is local and read-only');
});

test('late process replies cannot publish state or clear a newer session operation', async testContext => {
  for (const outcome of ['resolve', 'reject']) await testContext.test(outcome, async () => {
    const a = deferred(), t = processTest({effects: true, handle: body => body.action === 'replay' && body.args.project_id === 'a' ? a.promise : undefined});
    t.render('session-a'); await settle();
    const stale = button(t.render(), '研究过程').props.onClick, operation = stale();
    assert.equal(t.render().props['aria-busy'], true);
    t.render('session-b'); await settle();
    assert.equal(processNode(t.render()), undefined);
    await button(t.render(), '研究过程').props.onClick();
    const b = processNode(t.render()).props.graph;
    assert.equal(b.project_id, 'b');
    const newer = deferred(), fallback = t.controls.handle;
    t.controls.handle = body => body.action === 'replay' ? newer.promise : fallback(body);
    const pending = button(t.render(), '读取本项目记录').props.onClick(), before = t.requests.length;
    if (outcome === 'resolve') a.resolve(t.fixtures.a.replay);
    else a.reject(Error('late graph A failure'));
    await operation;
    assert.equal(processNode(t.render()).props.graph, b);
    assert.equal(picker(t.render()).props.value, 'b');
    assert.equal(t.render().props['aria-busy'], true, 'A finally must not clear B busy');
    assert.equal(button(t.render(), '读取本项目记录').props.disabled, true);
    assert.equal(alerts(t.render()).length, 0);
    assert.equal(t.react.writesAfterUnmount.length, 0);
    assert.equal(t.requests.length, before);
    await stale();
    assert.equal(t.requests.length, before, 'An unmounted process callback cannot dispatch');
    newer.resolve(t.fixtures.b.replay); await pending;
    assert.equal(t.render().props['aria-busy'], false);
    assert.equal(processNode(t.render()).props.graph.project_id, 'b');
    assert.equal(alerts(t.render()).length, 0);
  });
});

test('closed records are not labelled accepted mathematical facts', async () => {
  const fixture = overview('a');
  fixture.groups.accepted = [{object: fixture.objects[2], assessment: {state: 'closed', reasons: ['synthetic local check only']}}];
  const t = viewTest({handle: body => body.action === 'overview' ? fixture : undefined}); await t.choose('a');
  const accepted = byTitle(t.render(), '已接受 / 已关闭'); assert.ok(accepted);
  assert.ok(nodes(accepted).some(node => node.children.some(text => typeof text === 'string' && text.includes('不代表全局证明或 R1/R2 已通过'))));
  assert.equal(byTitle(t.render(), '已接受事实'), undefined);
  button(t.render(), '记录与审核').props.onClick();
  assert.equal(visibleComponent(t.render(), 'RecordBrowser').props.view.groups.accepted, fixture.groups.accepted, 'Reader receives the actual scoped assessment, not a manufactured mathematical verdict');
});

test('fallback modal still opens and closes without Better Sidebar', () => {
  const t = entryTest(undefined);
  button(t.render(), '数学项目').props.onClick();
  assert.ok(dialog(t.render()));
  dialog(t.render()).children[0].props.onClose();
  assert.equal(dialog(t.render()), undefined);
});

test('fallback uses the native top layer and Escape reuses the guarded close action', () => {
  const react = hooks({effects: true});
  let focused = 0, shown = 0, closed = 0, clicked = 0, prevented = 0;
  const trigger = {isConnected: true, focus() { focused++; }};
  const document = {activeElement: trigger, createElement: () => ({remove() {}}), head: {appendChild() {}}};
  const t = entryTest(undefined, {react, globals: {document}});
  button(t.render(), '数学项目').props.onClick();
  const modal = dialog(t.render());
  assert.equal(modal.type, 'dialog');
  assert.equal(modal.props['aria-modal'], true);
  const native = {open: false, showModal() { this.open = true; shown++; }, close() { this.open = false; closed++; },
    querySelector(selector) { assert.equal(selector, 'button[title="关闭数学工作台"]'); return {click() { clicked++; }}; }};
  modal.props.ref.current = native;
  react.flushEffects();
  assert.equal(shown, 1);
  modal.props.onCancel({preventDefault() { prevented++; }});
  assert.equal(prevented, 1); assert.equal(clicked, 1);
  modal.props.onCancel({target: {}, currentTarget: {}, preventDefault() { prevented++; }});
  assert.equal(prevented, 1); assert.equal(clicked, 1, 'Nested import cancel must not close the workbench');
  assert.ok(dialog(t.render()), 'Escape waits for the draft-flushing close action, not a direct unmount');
  modal.children[0].props.onClose();
  assert.equal(dialog(t.render()), undefined);
  react.flushEffects();
  assert.equal(closed, 1); assert.equal(focused, 1);
  react.unmount();
  assert.equal(react.writesAfterUnmount.length, 0);
});

test('fallback reports unsupported native dialog without leaving an invisible open view', () => {
  const react = hooks({effects: true});
  const document = {activeElement: null, createElement: () => ({remove() {}}), head: {appendChild() {}}};
  const t = entryTest(undefined, {react, globals: {document}});
  button(t.render(), '数学项目').props.onClick();
  dialog(t.render()).props.ref.current = {};
  react.flushEffects();
  assert.equal(dialog(t.render()), undefined);
  assert.ok(alerts(t.render()).some(node => node.children.join('').includes('不支持原生模态窗口')));
});

test('form presentation preserves historical exact references and blocks busy saves', async () => {
  const react = hooks(), context = {};
  vm.runInNewContext(readFileSync(new URL('../form-ui.cjs', import.meta.url), 'utf8'), context);
  const {Text, Refs} = context.DshMathFormUI(react, Forms);
  const text = Text({label: '标题', value: '保留文本', change: () => {}});
  assert.equal(text.props.className, 'dm-field');
  assert.equal(text.children[1].props.className, 'dm-input');
  const problem = {id: 'P', rev: 1}, objects = [1, 2].map(rev => ({id: 'N', rev, kind: 'Note', data: {problem_ref: problem, title: '历史引用'}}));
  react.reset();
  const refs = Refs({label: '前提', objects, kinds: ['Note'], problem, value: [{id: 'N', rev: 1}], change: () => {}});
  assert.equal(refs.props.className, 'dm-fieldset');
  assert.ok(nodes(refs).some(node => node.type === 'span' && node.children[0].includes('N@1')));
  const draft = Forms.draft('Note', problem, () => 'stable-id');
  Object.assign(draft.data, {title: '合成笔记', summary: '仅测试', body: '合成正文', statement: '合成主张'});
  const fresh = hooks(), ui = context.DshMathFormUI(fresh, Forms);
  let saved;
  const editor = ui.Editor({initial: draft, objects: [], busy: true, save: request => { saved = request; }, cancel: () => {}});
  assert.equal(editor.props.className, 'dm-editor');
  assert.equal(nodes(editor).find(node => node.type === 'h3').props.tabIndex, -1);
  assert.equal(nodes(editor).find(node => node.props.type === 'submit').props.disabled, true);
  await editor.props.onSubmit({preventDefault() {}});
  assert.equal(saved, undefined, 'A stale submit callback cannot ignore busy');
  fresh.reset();
  await ui.Editor({initial: draft, objects: [], busy: false, save: request => { saved = request; }, cancel: () => {}}).props.onSubmit({preventDefault() {}});
  assert.equal(saved.operation_id, draft.operation_id);
  assert.equal(saved.changes[0].expected_rev, 0);
  assert.equal(saved.changes[0].data.problem_ref.rev, 1);
});

test('short Field and Text use inputs but preserve legacy LF and CR values in textareas', async () => {
  const t = viewTest(); await t.choose('a'); button(t.render(), '回放与导出').props.onClick();
  button(t.render(), '添加回放来源').props.onClick();
  const fields = [nodes(t.render()).find(node => node.props.label === '来源 1 完整路径')];
  assert.ok(fields.every(field => field && field.props.multiline === false));
  const context = {};
  vm.runInNewContext(readFileSync(new URL('../form-ui.cjs', import.meta.url), 'utf8'), context);
  const {Text} = context.DshMathFormUI(hooks(), Forms);
  const components = [...fields.map(field => ({render: field.type, props: field.props})), {render: Text, props: {label: '短字段', multi: false}}];
  for (const component of components) for (const value of ['', '  单行 Ω  ', '第一行\n第二行\n', '第一行\r\n第二行', '第一行\r第二行']) {
    let changed;
    const tree = component.render({...component.props, value, change: next => { changed = next; }});
    const control = tree.children[1], multiline = /[\r\n]/.test(value);
    assert.equal(control.type, multiline ? 'textarea' : 'input');
    assert.equal(control.props.rows, multiline ? 3 : undefined);
    assert.equal(control.props.value, value);
    control.props.onChange({target: {value}});
    assert.equal(changed, value, 'Change handlers must not trim, split or flatten historical text');
  }
  // Exercise the real parent callbacks too, without submitting to any API.
  const historical = '  标题第一行\n标题第二行\n';
  fields[0].props.change(historical);
  const field = nodes(t.render()).find(node => node.props.label === '来源 1 完整路径');
  assert.equal(field.props.value, historical);
  const fieldControl = field.type(field.props).children[1];
  assert.equal(fieldControl.type, 'textarea');
  fieldControl.props.onChange({target: {value: historical + '末行'}});
  assert.equal(nodes(t.render()).find(node => node.props.label === '来源 1 完整路径').props.value, historical + '末行');
  const react = hooks(), ui = context.DshMathFormUI(react, Forms);
  const initial = Forms.draft('Note', {id: 'P-synthetic', rev: 1}, () => 'text-fixture');
  Object.assign(initial.data, {title: historical, summary: '合成摘要', body: '合成正文', statement: '合成主张'});
  let saved;
  const renderEditor = () => { react.reset(); return ui.Editor({initial, objects: [], save: request => { saved = request; }, cancel: () => {}}); };
  const title = nodes(renderEditor()).find(node => node.props.label === '笔记标题');
  assert.equal(title.props.multi, false);
  const titleControl = title.type(title.props).children[1];
  assert.equal(titleControl.type, 'textarea');
  assert.equal(titleControl.props.value, historical);
  titleControl.props.onChange({target: {value: historical + '末行'}});
  await renderEditor().props.onSubmit({preventDefault() {}});
  assert.equal(saved.changes[0].data.title, historical + '末行');
});

test('five compact tabs retain the same scratchpad component and next actions preserve exact refs', async () => {
  const fixture = overview('a'); fixture.groups.obligations[0].object.data.next_action = '先检查合成条件\n再登记结果';
  const t = viewTest({handle: body => body.action === 'overview' ? fixture : undefined}); await t.choose('a');
  const scratch = tree => nodes(tree).find(node => node.type?.name === 'Scratchpad');
  const first = scratch(t.render()); assert.equal(first.props.active, false);
  assert.equal(nodes(t.render()).filter(node => node.props.className === 'dm-tab').length, 5);
  const digest = byClass(t.render(), 'dm-next-action-list'); assert.ok(digest); assert.ok(nodes(digest).some(node => node.children.includes('先检查合成条件\n再登记结果')));
  button(digest, 'O-a@1').props.onClick(); assert.equal(visibleComponent(byTitle(t.render(), 'O-a@1'), 'RecordReader').props.object.kind, 'Obligation');
  button(t.render(), '项目草稿本').props.onClick(); assert.equal(scratch(t.render()).key, first.key); assert.equal(scratch(t.render()).props.active, true);
  button(t.render(), '研究概览').props.onClick(); assert.equal(scratch(t.render()).key, first.key); assert.equal(scratch(t.render()).props.active, false);
});

test('legacy readonly view suppresses every research mutation but retains explicit audit export', async () => {
  const fixture = overview('a'); fixture.project.schema_version = 3; fixture.project.mode = 'legacy_readonly_copy'; fixture.objects.forEach(object => { object.schema_version = 3; });
  const t = viewTest({handle: body => body.action === 'overview' ? fixture : body.action === 'export' ? {path: '/synthetic/derived-audit', exported: true} : undefined}); await t.choose('a');
  assert.equal(nodes(t.render()).find(node => node.type?.name === 'Scratchpad').props.writable, false);
  for (const text of ['登记笔记', '登记义务', '登记证据']) assert.equal(nodes(t.render()).some(node => node.children.includes(text)), false);
  button(t.render(), 'O-a@1').props.onClick();
  for (const text of ['笔记', '义务', '证据', '修订此记录', '人工审核预览', '本人提交审核记录']) assert.equal(nodes(t.render()).some(node => node.children.includes(text)), false);
  assert.equal(t.requests.some(body => body.action === 'export'), false); button(t.render(), '回放与导出').props.onClick();
  assert.equal(t.requests.some(body => body.action === 'export'), false); await button(t.render(), '生成本地审核包').props.onClick();
  assert.equal(visibleComponent(t.render(), 'ExportReceipt').props.receipt.path, '/synthetic/derived-audit');
  assert.deepEqual(t.requests.filter(body => !readActions.includes(body.action)).map(body => body.action), ['export']);
});

test('project switches await child flush and failure keeps the active project', async () => {
  const t = viewTest(); await t.choose('a'); const gate = deferred(); let allowed = false;
  const child = nodes(t.render()).find(node => node.type?.name === 'Scratchpad');
  const unregister = child.props.registerGuard('synthetic-scratch', async () => { await gate.promise; return allowed; });
  const pending = t.choose('b'); await settle(); assert.equal(picker(t.render()).props.value, 'a'); gate.resolve(); await pending;
  assert.equal(picker(t.render()).props.value, 'a'); assert.ok(alerts(t.render()).some(node => node.children.join('').includes('未保存')));
  allowed = true; await t.choose('b'); assert.equal(picker(t.render()).props.value, 'b'); unregister();
});

test('promotion links the exact committed revision and retry never duplicates the formal commit', async () => {
  const commit = deferred(); let failLink = true;
  const t = viewTest({handle: body => {
    if (body.action === 'commit') return commit.promise;
    if (body.action === 'scratchpad-promote-link') { if (failLink) { failLink = false; return Promise.reject(Error('synthetic link offline')); } return {linked: true, target: body.args.target}; }
  }}); await t.choose('a'); const pad = {project_id: 'a', rev: 7, title: '合成草稿', body: '精确内容\n'};
  nodes(t.render()).find(node => node.type?.name === 'Scratchpad').props.onPromote('Note', pad);
  let editor = nodes(t.render()).find(node => node.type === t.Editor); assert.ok(editor); assert.equal(editor.props.initial.data.body, pad.body);
  assert.equal(editor.props.initial.data.summary, ''); assert.equal(editor.props.initial.promotion.scratchpad_rev, 7);
  const request = Forms.request(editor.props.initial), save = editor.props.save(request);
  assert.equal(t.requests.filter(body => body.action === 'scratchpad-promote-link').length, 0);
  commit.resolve({versions: [{id: 'unrelated', rev: 99}, {id: editor.props.initial.id, rev: 4}]}); await assert.rejects(save, /不要重复保存记录/);
  assert.equal(nodes(t.render()).some(node => node.type === t.Editor), false); assert.ok(alerts(t.render()).some(node => node.children.join('').includes('不要重复保存记录')));
  const firstLink = t.requests.find(body => body.action === 'scratchpad-promote-link');
  assert.deepEqual(firstLink.args.target, {id: editor.props.initial.id, rev: 4}); assert.equal(firstLink.args.scratchpad_rev, 7);
  assert.equal(firstLink.args.project_id, 'a'); assert.equal(firstLink.args.session_id, 'test-session');
  await t.choose('b'); assert.equal(picker(t.render()).props.value, 'a', 'Unfinished source link blocks in-UI project switch');
  await button(t.render(), '重试来源链接').props.onClick();
  assert.equal(t.requests.filter(body => body.action === 'commit').length, 1); const links = t.requests.filter(body => body.action === 'scratchpad-promote-link');
  assert.equal(links.length, 2); assert.deepEqual(links[0], links[1]); assert.equal(nodes(t.render()).some(node => node.children.includes('重试来源链接')), false);
});

test('promotion completion after session unmount never dispatches a stale source link', async () => {
  const commit = deferred(); const t = viewTest({effects: true, handle: body => body.action === 'commit' ? commit.promise : undefined});
  t.render('session-a'); await settle();
  nodes(t.render()).find(node => node.type?.name === 'Scratchpad').props.onPromote('Obligation', {project_id: 'a', rev: 3, title: 'synthetic', body: 'synthetic'});
  const editor = nodes(t.render()).find(node => node.type === t.Editor), pending = editor.props.save(Forms.request(editor.props.initial));
  t.render('session-b'); await settle(); commit.resolve({versions: [{id: editor.props.initial.id, rev: 1}]}); await assert.rejects(pending, /View changed/);
  assert.equal(t.requests.some(body => body.action === 'scratchpad-promote-link'), false); assert.equal(t.react.writesAfterUnmount.length, 0);
  assert.equal(picker(t.render()).props.value, 'b');
});

const editNode = (t, tree = t.render()) => nodes(tree).find(node => node.type === t.Editor);
const plain = value => JSON.parse(JSON.stringify(value));
const writeRequests = t => t.requests.filter(body => !readActions.includes(body.action));
const unsaved = t => component(t.render(), 'UnsavedPrompt');
function changeFormalDraft(t, body = '  exact local\r\n$math$ 😀\n') {
  const editor = editNode(t), edited = {...editor.props.initial, data: {...editor.props.initial.data, title: 'local title', summary: 'local summary', body}};
  editor.props.onDraftChange(edited, {dirty: true}); return edited;
}
const reviewFixture = () => ({eligible_for_pass: true, required_scope: 'local_claim', basis_hash: 'synthetic-basis',
  basis: {target: {id: 'O-a', rev: 1}, candidate: {id: 'N-a', rev: 1}}, reasons: []});
async function openReview(t) {
  await t.choose('a'); button(t.render(), 'O-a@1').props.onClick();
  nodes(t.render()).find(node => node.type === t.Refs).props.change([{id: 'N-a', rev: 1}]);
  await button(t.render(), '人工审核预览').props.onClick();
  const tree = t.render();
  visibleNodes(tree).find(node => node.type === 'select' && node.props.value === '' && !node.props['aria-label']).props.onChange({target: {value: 'inconclusive'}});
  nodes(t.render()).find(node => node.props.label === '本人审核发现（每行一项）').props.change('  finding one\n精确发现 Ω\n');
}

test('dirty formal editor stays mounted across every tab with stable key and no leave dialog or implicit write', async () => {
  const t = processTest(); await t.choose('a'); button(t.render(), '登记笔记').props.onClick();
  const original = editNode(t); assert.equal(original.props.initialDirty, false, 'A blank document does not inherit a dirty flag');
  const edited = changeFormalDraft(t);
  for (const tab of ['研究概览', '项目草稿本', '研究过程', '回放与导出', '记录与审核']) {
    await button(t.render(), tab).props.onClick(); const tree = t.render(), editor = editNode(t, tree);
    assert.equal(editor.type, original.type); assert.equal(editor.key, original.key); assert.equal(editor.props.initial, edited);
    assert.equal(editor.props.initialDirty, true); assert.equal(unsaved(t), undefined);
    assert.equal(panel(tree, 'records').props.hidden, tab !== '记录与审核');
  }
  assert.deepEqual(t.requests.filter(body => ![...readActions, 'replay'].includes(body.action)), []);
  assert.equal(t.requests.filter(body => body.action === 'replay').length, 1, 'Only explicit process viewing requests replay');
});

test('project switching protects dirty documents with cancel, keep and discard; current project owns the saved bytes', async () => {
  const t = viewTest(); await t.choose('a'); button(t.render(), '登记笔记').props.onClick();
  const edited = changeFormalDraft(t), key = editNode(t).key;
  let navigation = t.choose('b'); await settle();
  assert.equal(picker(t.render()).props.value, 'a'); assert.equal(unsaved(t).props.decision.keep, true);
  unsaved(t).props.finish('cancel'); await navigation;
  assert.equal(picker(t.render()).props.value, 'a'); assert.equal(editNode(t).props.initial, edited);
  navigation = t.choose('b'); await settle(); unsaved(t).props.finish('keep'); await navigation;
  assert.equal(picker(t.render()).props.value, 'b'); assert.equal(editNode(t), undefined);
  await t.choose('a'); assert.equal(editNode(t).key, key); assert.deepEqual(plain(editNode(t).props.initial), plain(edited));
  assert.equal(editNode(t).props.initialDirty, true);
  navigation = t.choose('b'); await settle(); unsaved(t).props.finish('discard'); await navigation;
  await t.choose('a'); assert.equal(editNode(t), undefined); assert.equal(writeRequests(t).length, 0);
});

test('editor busy/unknown callbacks gate departure and original requests survive keep without allowing replacement', async () => {
  let lose = true;
  const t = viewTest({handle: body => body.action === 'commit' ? (lose ? (lose = false, Promise.reject(Error('unknown transport receipt'))) : {}) : undefined});
  await t.choose('a'); button(t.render(), '登记笔记').props.onClick(); changeFormalDraft(t);
  let editor = editNode(t); editor.props.onBusyChange(true);
  const before = t.requests.length; await t.choose('b'); assert.equal(picker(t.render()).props.value, 'a'); assert.equal(unsaved(t), undefined); assert.equal(t.requests.length, before);
  editor.props.onBusyChange(false);
  const attempt = Forms.request(editor.props.initial); editor.props.onAttemptChange(attempt);
  await assert.rejects(editor.props.save(attempt), /unknown transport receipt/);
  assert.deepEqual(plain(editNode(t).props.initialAttempt), plain(attempt));
  const cancelEditor = editNode(t).props.cancel(); await settle();
  assert.equal(unsaved(t).props.decision.keep, false); assert.equal(unsaved(t).props.decision.uncertain, true);
  unsaved(t).props.finish('cancel'); await cancelEditor; assert.ok(editNode(t));
  const switchProject = t.choose('b'); await settle(); assert.equal(unsaved(t).props.decision.uncertain, true);
  unsaved(t).props.finish('keep'); await switchProject; assert.equal(editNode(t), undefined);
  await t.choose('a'); editor = editNode(t); assert.deepEqual(plain(editor.props.initialAttempt), plain(attempt));
  await editor.props.save(editor.props.initialAttempt);
  const commits = t.requests.filter(body => body.action === 'commit'); assert.equal(commits.length, 2); assert.deepEqual(commits[0], commits[1]);
  assert.equal(editNode(t), undefined);
});

test('cross-session remount retains only the matching session/project document and ignores stale child callbacks', async () => {
  const t = viewTest({effects: true}); t.render('session-a'); await settle();
  button(t.render(), '登记笔记').props.onClick(); const edited = changeFormalDraft(t, 'session A exact\r\n');
  const stale = editNode(t); stale.props.onAttemptChange(Forms.request(edited));
  const empty = t.render('session-b'); assert.equal(editNode(t, empty), undefined); await settle();
  assert.equal(picker(t.render()).props.value, 'b'); assert.equal(editNode(t), undefined);
  await t.choose('a'); assert.equal(editNode(t), undefined, 'The same project viewed from session B cannot inherit session A draft');
  stale.props.onDraftChange({...edited, data: {...edited.data, body: 'stale overwrite'}}, {dirty: true}); stale.props.onBusyChange(true); stale.props.onAttemptChange(null);
  assert.equal(editNode(t), undefined); assert.equal(t.react.writesAfterUnmount.length, 0);
  t.render('session-a'); await settle();
  assert.equal(editNode(t).props.initial.data.body, edited.data.body); assert.ok(editNode(t).props.initialAttempt); assert.equal(editNode(t).props.initialDirty, true);
  assert.equal(writeRequests(t).length, 0);
  assert.deepEqual(t.requests.filter(body => body.action === 'project-draft-read').map(body => body.args.session_id), ['session-a', 'session-b', 'session-a']);
});

test('beforeunload observes a blank editor busy callback even without a retained dirty draft', async testContext => {
  const listeners = new Map(), document = {createElement: () => ({remove() {}}), head: {appendChild() {}}};
  const t = viewTest({effects: true, globals: {document,
    addEventListener: (name, callback) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener: (name, callback) => listeners.get(name)?.delete(callback)}});
  testContext.after(() => { t.react.unmount(); t.ctx.dispose(); });
  const unload = () => { const event = {prevented: false, preventDefault() { this.prevented = true; }}; for (const callback of listeners.get('beforeunload') || []) callback(event); return event; };
  t.render('session-a'); await settle(); button(t.render(), '登记笔记').props.onClick();
  const editor = editNode(t); assert.equal(editor.props.initialDirty, false); assert.equal(unload().prevented, false);
  editor.props.onBusyChange(true); const event = unload(); assert.equal(event.prevented, true); assert.equal(event.returnValue, '');
  assert.equal(editor.props.initialDirty, false, 'Busy protection must not manufacture a dirty edit or commit');
  editor.props.onBusyChange(false); assert.equal(unload().prevented, false); assert.equal(writeRequests(t).length, 0);
  t.react.unmount(); assert.equal(unload().prevented, false, 'Unmount removes the active getter');
  t.ctx.dispose(); assert.equal(listeners.get('beforeunload').size, 0, 'Plugin cleanup unregisters its listener');
});

test('sidebar and conversation buffers remain separate and a clean surface cannot delete the other retained draft', async () => {
  const t = viewTest({effects: true}); t.render('session-a'); await settle();
  button(t.render(), '登记笔记').props.onClick(); const sidebarDraft = changeFormalDraft(t, 'sidebar-only exact\r\n');
  t.switchSurface(false); t.render('session-a'); await settle();
  assert.equal(editNode(t), undefined, 'Conversation starts with no sidebar draft');
  button(t.render(), '登记笔记').props.onClick(); assert.equal(editNode(t).props.initialDirty, false);
  t.switchSurface(true); t.render('session-a'); await settle();
  assert.equal(editNode(t).props.initialDirty, true); assert.deepEqual(plain(editNode(t).props.initial), plain(sidebarDraft), 'Clean conversation teardown cannot remove sidebar content');
  t.switchSurface(false); t.render('session-a'); await settle(); button(t.render(), '登记笔记').props.onClick();
  const conversationDraft = changeFormalDraft(t, 'conversation-only exact\n');
  t.switchSurface(true); t.render('session-a'); await settle();
  assert.equal(editNode(t).props.initial.data.body, sidebarDraft.data.body); assert.equal(editNode(t).props.initialDirty, true);
  const cancelling = editNode(t).props.cancel(); await settle(); unsaved(t).props.finish('discard'); await cancelling; assert.equal(editNode(t), undefined);
  t.switchSurface(false); t.render('session-a'); await settle();
  assert.equal(editNode(t).props.initialDirty, true); assert.deepEqual(plain(editNode(t).props.initial), plain(conversationDraft), 'Discard is scoped to its surface');
  assert.equal(writeRequests(t).length, 0); t.react.unmount();
});

test('review findings survive tab changes and refresh but require an explicit fresh basis before submission', async () => {
  let previews = 0;
  const t = viewTest({handle: body => body.action === 'review-preview' ? {...reviewFixture(), basis_hash: `basis-${++previews}`} : body.action === 'review' ? {} : undefined});
  await openReview(t); const before = t.requests.length;
  button(t.render(), '研究概览').props.onClick(); assert.equal(unsaved(t), undefined); button(t.render(), '记录与审核').props.onClick();
  assert.equal(t.requests.length, before); assert.equal(nodes(t.render()).find(node => node.props.label === '本人审核发现（每行一项）').props.value, '  finding one\n精确发现 Ω\n');
  await button(t.render(), '刷新').props.onClick();
  assert.equal(button(t.render(), '本人提交审核记录').props.disabled, true);
  assert.equal(component(t.render(), 'ReviewSummary').props.preview.basis_hash, 'basis-1');
  assert.equal(nodes(t.render()).find(node => node.props.label === '本人审核发现（每行一项）').props.value, '  finding one\n精确发现 Ω\n');
  assert.equal(t.requests.some(body => body.action === 'review'), false);
  await button(t.render(), '重新核对审核依据').props.onClick(); assert.equal(component(t.render(), 'ReviewSummary').props.preview.basis_hash, 'basis-2');
  assert.equal(button(t.render(), '本人提交审核记录').props.disabled, false);
  await button(t.render(), '本人提交审核记录').props.onClick();
  const writes = t.requests.filter(body => body.action === 'review'); assert.equal(writes.length, 1);
  assert.equal(writes[0].args.request.basis_hash, 'basis-2'); assert.equal(writes[0].args.request.scope, 'local_claim');
  assert.deepEqual(writes[0].args.request.target, {id: 'O-a', rev: 1}); assert.deepEqual(writes[0].args.request.candidate, {id: 'N-a', rev: 1});
  assert.deepEqual(writes[0].args.request.findings, ['  finding one', '精确发现 Ω']);
});

test('unsubmitted review findings can be kept per scope, but inspect/cancel never silently replaces them', async () => {
  const t = viewTest({handle: body => body.action === 'review-preview' ? reviewFixture() : undefined}); await openReview(t);
  const reader = visibleComponent(t.render(), 'RecordReader'), original = reader.props.object;
  const next = reader.props.view.objects.find(object => object.id === 'N-a');
  const inspecting = reader.props.inspect(next); await settle();
  assert.equal(unsaved(t).props.decision.keep, false); unsaved(t).props.finish('cancel'); await inspecting;
  assert.equal(visibleComponent(t.render(), 'RecordReader').props.object, original);
  const navigation = t.choose('b'); await settle(); unsaved(t).props.finish('keep'); await navigation;
  assert.equal(component(t.render(), 'ReviewSummary'), undefined); assert.equal(visibleComponent(t.render(), 'RecordReader'), undefined);
  await t.choose('a');
  assert.equal(nodes(t.render()).find(node => node.props.label === '本人审核发现（每行一项）').props.value, '  finding one\n精确发现 Ω\n');
  assert.equal(visibleComponent(t.render(), 'RecordReader').props.object.id, 'O-a');
  assert.equal(button(t.render(), '本人提交审核记录').props.disabled, true, 'Restored preview must be treated as stale');
  const closing = visibleComponent(t.render(), 'RecordReader').props.onClose(); await settle(); unsaved(t).props.finish('discard'); await closing;
  assert.equal(component(t.render(), 'ReviewSummary'), undefined); assert.equal(byClass(t.render(), 'dm-record-detail'), undefined);
  assert.deepEqual(writeRequests(t).map(body => body.action), ['review-preview']);
});

test('review unknown receipt preserves the exact payload across refresh and only explicit retry resubmits', async () => {
  let lose = true;
  const t = viewTest({handle: body => body.action === 'review-preview' ? reviewFixture() : body.action === 'review' ? (lose ? (lose = false, Promise.reject(Error('lost review receipt'))) : {}) : undefined});
  await openReview(t); await button(t.render(), '本人提交审核记录').props.onClick();
  const original = t.requests.find(body => body.action === 'review'); assert.ok(original);
  assert.equal(button(t.render(), '人工审核预览').props.disabled, true);
  await button(t.render(), '刷新').props.onClick();
  assert.equal(t.requests.filter(body => body.action === 'review').length, 1);
  assert.equal(button(t.render(), '重试原审核提交').props.disabled, false);
  const fieldset = nodes(t.render()).find(node => node.props.className === 'dm-editor-fields'); assert.equal(fieldset.props.disabled, true);
  await button(t.render(), '重试原审核提交').props.onClick();
  const attempts = t.requests.filter(body => body.action === 'review'); assert.equal(attempts.length, 2); assert.deepEqual(attempts[1], original);
});

test('definitive backend review conflicts preserve findings but clear retry identity and demand a fresh preview', async testContext => {
  for (const code of ['VERSION_CONFLICT', 'REVIEW_CONFLICT', 'REVIEW_BLOCKED', 'SCOPE_MISMATCH']) await testContext.test(code, async () => {
    const t = viewTest({handle: body => body.action === 'review-preview' ? reviewFixture() : undefined}); await openReview(t);
    t.controls.apiFailure = {action: 'review', code}; await button(t.render(), '本人提交审核记录').props.onClick();
    assert.equal(button(t.render(), '本人提交审核记录').props.disabled, true);
    assert.equal(visibleNodes(t.render()).some(node => node.children.includes('重试原审核提交')), false);
    assert.equal(nodes(t.render()).find(node => node.props.label === '本人审核发现（每行一项）').props.value, '  finding one\n精确发现 Ω\n');
    assert.ok(alerts(t.render()).some(node => node.children.join('').includes(code)));
    assert.equal(t.requests.filter(body => body.action === 'review').length, 1);
    await button(t.render(), '重新核对审核依据').props.onClick(); assert.equal(button(t.render(), '本人提交审核记录').props.disabled, false);
  });
});

test('current-session draft summary has absent, incomplete, complete, committed and pending-confirmation states without writes', async testContext => {
  const cases = [
    ['absent', {draft: null}, '新建项目'],
    ['incomplete', {draft: {id: 'd', rev: 2, status: 'draft', fields: {title: 'synthetic draft'}}, validation: {complete: false, missing_fields: ['statement', 'domain']}}, '继续准备 · 还缺 2 项'],
    ['complete', {draft: {id: 'd', rev: 3, status: 'draft'}, validation: {complete: true}}, '查看并确认草稿'],
    ['committed', {draft: {id: 'd', rev: 4, status: 'committed'}}, '新建项目'],
    ['pending', {draft: {id: 'd', rev: 5, status: 'draft'}, pending_finalization: {expected_rev: 5, operation_id: 'pending-confirm'}}, '查看待恢复确认'],
  ];
  for (const [name, summary, label] of cases) await testContext.test(name, async () => {
    const session = `summary-${name}`;
    const t = viewTest({effects: true, handle: body => body.action === 'list' ? [] : body.action === 'project-draft-read' ? summary : undefined});
    t.render(session); await settle(); const tree = t.render();
    assert.equal(nodes(tree).filter(node => node.props.variant === 'primary').length, 1); button(welcome(tree), label).props.onClick();
    assert.equal(component(t.render(), 'ProjectForm').props.sessionId, session);
    assert.deepEqual(t.requests, [{action: 'list', args: {}}, {action: 'project-draft-read', args: {session_id: session}}]);
  });
});

test('late draft summaries never expose another session and failed reads remain read-only retryable', async () => {
  const pending = deferred();
  const t = viewTest({effects: true, handle: body => body.action === 'list' ? [] : body.action === 'project-draft-read' && body.args.session_id === 'summary-a' ? pending.promise : undefined});
  t.render('summary-a'); t.render('summary-b'); await settle();
  pending.resolve({draft: {id: 'private-a', status: 'draft', rev: 1, fields: {title: 'must not leak'}}, validation: {complete: true}}); await settle();
  assert.ok(button(welcome(t.render()), '新建项目')); assert.equal(nodes(t.render()).some(node => node.children.join('').includes('must not leak')), false);
  assert.equal(t.react.writesAfterUnmount.length, 0); assert.equal(writeRequests(t).length, 0);
  t.controls.apiFailure = {action: 'project-draft-read', code: 'SUMMARY_UNAVAILABLE'};
  await button(t.render(), '刷新').props.onClick(); await settle(); assert.ok(nodes(t.render()).some(node => node.children.join('').includes('SUMMARY_UNAVAILABLE')));
  assert.equal(writeRequests(t).length, 0);
});

test('tab ARIA relationships, roving focus and keyboard navigation keep one visible panel without mutation', async () => {
  const t = viewTest(); await t.choose('a'); let focused = '';
  const check = () => {
    const tree = t.render(), tabs = nodes(tree).filter(node => node.props.role === 'tab'), panels = nodes(tree).filter(node => node.props.role === 'tabpanel');
    assert.equal(tabs.length, 5); assert.equal(panels.length, 5); assert.equal(panels.filter(node => !node.props.hidden).length, 1);
    assert.equal(tabs.filter(node => node.props.tabIndex === 0).length, 1);
    for (const tab of tabs) { const related = panels.find(node => node.props.id === tab.props['aria-controls']); assert.ok(related); assert.equal(related.props['aria-labelledby'], tab.props.id); assert.equal(tab.props['aria-selected'], !related.props.hidden); }
    return {tree, tabs};
  };
  for (const [key, start, expected] of [['ArrowRight', 0, 'scratchpad'], ['End', 1, 'replay'], ['Home', 4, 'overview'], ['ArrowLeft', 0, 'replay']]) {
    const {tree, tabs} = check(), nav = nodes(tree).find(node => node.props.role === 'tablist');
    const targets = tabs.map(node => ({focus() { focused = node.key; }})); let prevented = false;
    nav.props.onKeyDown({key, target: targets[start], currentTarget: {querySelectorAll: selector => { assert.equal(selector, '[role="tab"]'); return targets; }}, preventDefault() { prevented = true; }});
    assert.equal(prevented, true); assert.equal(focused, expected); assert.equal(panel(t.render(), expected).props.hidden, false);
  }
  check(); assert.equal(writeRequests(t).length, 0);
});

test('reader pin, exact historical context package and closing callbacks stay local and scoped to the selected project', async () => {
  const t = processTest({history: true}); await t.choose('a'); button(t.render(), '记录与审核').props.onClick();
  const browser = visibleComponent(t.render(), 'RecordBrowser'), object = browser.props.view.objects.find(item => item.id === 'N-a' && item.rev === 1);
  const before = t.requests.length; browser.props.inspect(object);
  let reader = visibleComponent(t.render(), 'RecordReader'); assert.equal(reader.props.object, object); assert.equal(reader.props.view, browser.props.view);
  reader.props.onPin(object); assert.equal(component(byClass(t.render(), 'dm-pinned-record'), 'RecordReader').props.object, object);
  button(t.render(), '准备上下文包').props.onClick(); const pack = visibleComponent(t.render(), 'ContextPack');
  assert.equal(pack.props.selected, object); assert.equal(pack.props.view, browser.props.view); assert.equal(pack.key, 'a:N-a@1');
  pack.props.onClose(); assert.equal(component(t.render(), 'ContextPack'), undefined);
  reader = visibleComponent(t.render(), 'RecordReader'); reader.props.inspect(browser.props.view.objects.find(item => item.id === 'N-a' && item.rev === 2));
  assert.equal(visibleComponent(t.render(), 'RecordReader').props.object.rev, 2);
  assert.equal(component(byClass(t.render(), 'dm-pinned-record'), 'RecordReader').props.object.rev, 1, 'Pinned reference is not auto-upgraded');
  assert.equal(component(byClass(t.render(), 'dm-pinned-record'), 'RecordReader').props.onClose, undefined, 'Unpin has its own explicit action, not a misleading detail-close callback');
  visibleComponent(t.render(), 'RecordReader').props.onClose(); assert.equal(byClass(t.render(), 'dm-record-detail'), undefined);
  button(byClass(t.render(), 'dm-pinned-record'), '取消固定参考').props.onClick(); assert.equal(byClass(t.render(), 'dm-pinned-record'), undefined);
  assert.equal(t.requests.length, before); assert.equal(writeRequests(t).length, 0);
  await t.choose('b'); assert.equal(component(t.render(), 'ContextPack'), undefined); assert.equal(byClass(t.render(), 'dm-pinned-record'), undefined);
});

test('closing detail restores focus only after records DOM commit, with search fallback for hidden or disconnected origins', async testContext => {
  for (const destination of ['original', 'hidden-origin', 'disconnected-origin']) await testContext.test(destination, async () => {
    let listVisible = true; const focuses = [], queries = [];
    const origin = {isConnected: true, getClientRects: () => listVisible ? [{}] : [], focus: options => focuses.push({target: 'original', options})};
    const fallback = {focus: options => focuses.push({target: 'search', options})};
    const document = {activeElement: origin, createElement: () => ({remove() {}}), head: {appendChild() {}}};
    const t = viewTest({effects: true, globals: {document}});
    t.render('session-a'); await settle(); button(t.render(), '记录与审核').props.onClick();
    const tree = t.render(), root = {scrollTop: 137, querySelector: selector => { queries.push(selector); return fallback; }};
    tree.props.ref.current = root;
    const browser = visibleComponent(tree, 'RecordBrowser'); browser.props.inspect(browser.props.view.objects.find(object => object.id === 'N-a'));
    const reader = visibleComponent(t.render(), 'RecordReader');
    listVisible = false; root.scrollTop = 12;
    reader.props.onClose();
    assert.deepEqual(focuses, [], 'The close callback must not focus the still-hidden list synchronously');
    assert.equal(root.scrollTop, 12, 'Scroll restoration also waits until the post-commit effect');
    const closing = t.render(undefined, {flushEffects: false});
    assert.equal(byClass(closing, 'dm-record-detail'), undefined); assert.equal(panel(closing, 'records').props.hidden, false);
    assert.deepEqual(focuses, [], 'Rendering the next tree alone does not perform focus side effects');
    // Model the committed narrow layout: reveal the list before React effects run.
    listVisible = destination !== 'hidden-origin'; origin.isConnected = destination !== 'disconnected-origin';
    t.react.flushEffects();
    assert.equal(root.scrollTop, 137); assert.deepEqual(plain(focuses), [{target: destination === 'original' ? 'original' : 'search', options: {preventScroll: true}}]);
    assert.deepEqual(queries, destination === 'original' ? [] : ['.dm-record-browser input']);
    t.render(); t.react.flushEffects(); assert.equal(focuses.length, 1, 'Restoration runs once, not on unrelated rerenders');
    assert.equal(writeRequests(t).length, 0); t.react.unmount(); t.ctx.dispose(); assert.equal(t.react.writesAfterUnmount.length, 0);
  });
});

test('detail focus restoration waits until records is visible and cannot run after the view unmounts', async () => {
  let focused = 0; const origin = {isConnected: true, getClientRects: () => [{}], focus: () => { focused++; }};
  const document = {activeElement: origin, createElement: () => ({remove() {}}), head: {appendChild() {}}};
  const t = viewTest({effects: true, globals: {document}}); t.render('session-a'); await settle();
  button(t.render(), '记录与审核').props.onClick(); let tree = t.render();
  const root = {scrollTop: 55, querySelector: () => null}; tree.props.ref.current = root;
  let browser = visibleComponent(tree, 'RecordBrowser'); browser.props.inspect(browser.props.view.objects[2]);
  visibleComponent(t.render(), 'RecordReader').props.onClose();
  // Switch away before committing the close; a hidden records panel must not receive focus.
  button(tree, '研究概览').props.onClick(); t.render(); assert.equal(focused, 0);
  button(t.render(), '记录与审核').props.onClick(); tree = t.render(undefined, {flushEffects: false}); assert.equal(focused, 0);
  t.react.flushEffects(); assert.equal(focused, 1);
  browser = visibleComponent(tree, 'RecordBrowser'); browser.props.inspect(browser.props.view.objects[2]);
  visibleComponent(t.render(), 'RecordReader').props.onClose(); t.react.unmount(); await settle();
  assert.equal(focused, 1, 'An uncommitted close does not focus after unmount'); assert.equal(t.react.writesAfterUnmount.length, 0);
  assert.equal(writeRequests(t).length, 0); t.ctx.dispose();
});

test('narrow layout follows the math container and uses inspected theme aliases', () => {
  const context = {};
  vm.runInNewContext(readFileSync(new URL('../client-theme.cjs', import.meta.url), 'utf8'), context);
  const css = context.DshMathTheme;
  assert.match(css, /container: dsh-math \/ inline-size/);
  assert.match(css, /@container dsh-math \(max-width: 520px\)/);
  assert.match(css, /\.dm-ref \{[^}]*overflow-wrap: anywhere/);
  assert.match(css, /\.dm-fieldset \{[^}]*min-inline-size: 0/);
  assert.match(css, /var\(--dsw-alias-bg-base\)/);
  assert.match(css, /var\(--dsw-alias-label-primary\)/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}\b/i);
});
