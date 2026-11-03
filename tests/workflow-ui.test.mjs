import assert from 'node:assert/strict';
import test from 'node:test';
import {webcrypto} from 'node:crypto';
import UI from '../workflow-ui.cjs';
if (!globalThis.crypto) globalThis.crypto = webcrypto;
function hooks() {
  const cells = [], effects = []; let cursor = 0;
  return {Fragment: 'fragment', createElement(type, props, ...children) { return {type, props: props || {}, children: children.flat(Infinity).filter(v => v != null && v !== false)}; },
    useState(initial) { const i = cursor++; if (!(i in cells)) cells[i] = typeof initial === 'function' ? initial() : initial; return [cells[i], value => { cells[i] = typeof value === 'function' ? value(cells[i]) : value; }]; },
    useRef(initial) { const i = cursor++; return cells[i] ||= {current: initial}; },
    useEffect(fn, deps) { const i = cursor++, old = cells[i]; if (!old || deps.some((v, n) => v !== old.deps[n])) { old?.cleanup?.(); cells[i] = {deps}; effects.push(() => { cells[i].cleanup = fn(); }); } },
    render(component, props) { cursor = 0; const tree = component(props); effects.splice(0).forEach(fn => fn()); return tree; },
    dispose() { for (const cell of cells) cell?.cleanup?.(); }};
}
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...tree.children.flatMap(nodes)] : [];
const text = tree => tree && typeof tree === 'object' ? tree.children.map(text).join(' ') : String(tree);
const button = (tree, label) => nodes(tree).find(n => n.type === 'button' && text(n) === label);
const tick = () => new Promise(resolve => setImmediate(resolve));
const reference = {id: 'problem', rev: 1};
function harness(request, extra = {}) {
  const react = hooks(), Wrapper = UI(react).WorkflowPanel;
  const props = {request, projectId: 'p', sessionId: 's', problemRef: reference, writable: true, ...extra};
  const Component = Wrapper(props).type;
  return {react, props, render: () => react.render(Component, props)};
}
function fill(c) {
  for (const [name, value] of Object.entries({provider: 'provider', model: 'fixed', effort: 'high', stage_goal: 'test goal', requests: '3', total_tokens: '300', max_output_tokens: '100', max_tokens: '1000'})) {
    nodes(c.render()).find(n => n.props.name === name).props.onChange({target: {value}});
  }
}
const prepared = {run_id: 'run', project_id: 'p', rev: 1, status: 'prepared', scientific_status: 'candidate_pending_review', spec: {budget: {requests: 3, total_tokens: 300}}, usage: {requests: 0, total_tokens: 0, complete: true}, actions: []};
test('mount only reads; explicit prepare freezes exact spec and disabled capability denies start', async () => {
  const calls = []; let run;
  const c = harness(async (action, args) => {
    calls.push({action, args});
    if (args.command.action === 'list') return {runs: run ? [run] : [], capability: {enabled: false, reason: 'disabled by config'}};
    run = {...prepared, run_id: args.command.run_id, spec: args.command.spec}; return {run, capability: {enabled: false, reason: 'disabled by config'}};
  });
  c.render(); await tick(); assert.deepEqual(calls.map(c => c.args.command.action), ['list']);
  fill(c); await button(c.render(), '冻结并准备方案').props.onClick();
  const cmd = calls.find(c => c.args.command.action === 'prepare').args.command;
  assert.equal(cmd.expected_rev, 0); assert.match(cmd.run_id, /^[\da-f-]{36}$/); assert.match(cmd.operation_id, /^[\da-f-]{36}$/);
  assert.deepEqual(cmd.spec.problem_ref, reference); assert.equal(cmd.spec.format, 'dsh-math-workflow-v1'); assert.deepEqual(cmd.spec.tools, []);
  assert.equal(button(c.render(), '启用运行').props.disabled, true);
  await button(c.render(), '启用运行').props.onClick(); assert.equal(calls.some(c => c.args.command.action === 'start'), false); c.react.dispose();
});
test('step is explicit, double clicks dispatch once, and missing usage remains visible', async () => {
  let release; const held = new Promise(resolve => { release = resolve; }); const calls = [];
  const run = {...prepared, status: 'running', usage: {requests: 1, total_tokens: null, complete: false}};
  const c = harness(async (action, args) => { calls.push({action, args}); if (action === 'workflow-step') return held; return args.command.action === 'list' ? {runs: [run], capability: {enabled: true}} : {run, capability: {enabled: true}}; });
  c.render(); await tick(); let tree = c.render(); assert.match(text(tree), /usage 不完整/);
  assert.equal(calls.some(c => c.action === 'workflow-step'), false);
  const first = button(tree, '执行一步').props.onClick(); await button(tree, '执行一步').props.onClick();
  assert.equal(calls.filter(c => c.action === 'workflow-step').length, 1);
  assert.equal(calls.at(-1).args.expected_rev, 1); release({run, capability: {enabled: true}}); await first; c.react.dispose();
});
test('failed mutation preserves input, never retries, requires explicit read reconciliation', async () => {
  const calls = []; const c = harness(async (_, args) => { calls.push(args.command); if (args.command.action === 'prepare') throw Error('reply lost'); return {runs: [], capability: {enabled: false}}; });
  c.render(); await tick(); fill(c); await button(c.render(), '冻结并准备方案').props.onClick();
  assert.equal(nodes(c.render()).find(n => n.props.name === 'stage_goal').props.value, 'test goal');
  assert.equal(button(c.render(), '冻结并准备方案').props.disabled, true);
  await button(c.render(), '冻结并准备方案').props.onClick(); assert.equal(calls.filter(c => c.action === 'prepare').length, 1); c.react.dispose();
});
test('known backend rejection keeps the editable draft and no automatic retry', async () => {
  let writes = 0;
  const c = harness(async (_, args) => {
    if (args.command.action === 'prepare') { writes++; throw Object.assign(Error('invalid spec'), {code: 'INVALID_SCHEMA'}); }
    return {runs: []};
  });
  c.render(); await tick(); fill(c); await button(c.render(), '冻结并准备方案').props.onClick();
  assert.equal(writes, 1); assert.equal(button(c.render(), '冻结并准备方案').props.disabled, false);
  assert.equal(nodes(c.render()).find(n => n.props.name === 'stage_goal').props.value, 'test goal'); c.react.dispose();
});
test('explicit proposed archive sends CAS commit without requesting a model', async () => {
  let run = {...prepared, actions: [{action_id: 'action', kind: 'derive', status: 'proposed'}]};
  const calls = []; const c = harness(async (action, args) => {
    calls.push({action, args});
    if (args.command.action === 'list') return {runs: [run], capability: {enabled: false}};
    if (args.command.action === 'commit') run = {...run, rev: 2, actions: [{...run.actions[0], status: 'committed'}]};
    return {run, capability: {enabled: false}};
  });
  c.render(); await tick(); assert.equal(button(c.render(), '重试归档').props.disabled, false);
  await button(c.render(), '重试归档').props.onClick();
  const command = calls.find(call => call.args.command.action === 'commit').args.command;
  assert.equal(command.run_id, 'run'); assert.equal(command.action_id, 'action'); assert.equal(command.expected_rev, 1);
  assert.equal(calls.some(call => call.action === 'workflow-step'), false);
  assert.equal(button(c.render(), '重试归档'), undefined); c.react.dispose();
});
test('continuous execution is explicit and pause reads current revision while loop waits', async () => {
  const run = {...prepared, status: 'running'}; const calls = []; let release;
  const pending = new Promise(resolve => { release = resolve; });
  const c = harness(async (action, args) => {
    calls.push({action, args});
    if (action === 'workflow-run') return pending;
    if (args.command.action === 'list') return {runs: [run], capability: {enabled: true}};
    if (args.command.action === 'pause') return {run: {...run, status: 'paused', rev: 5}, capability: {enabled: true}};
    return {run: {...run, rev: 3}, capability: {enabled: true}};
  });
  c.render(); await tick(); assert.equal(calls.some(call => call.action === 'workflow-run'), false);
  const request = button(c.render(), '按预算连续执行').props.onClick();
  assert.equal(button(c.render(), '暂停').props.disabled, false);
  await button(c.render(), '暂停').props.onClick();
  assert.equal(calls.find(call => call.args.command?.action === 'pause').args.command.expected_rev, 3);
  release({run: {...run, rev: 4}, capability: {enabled: true}}); await request;
  assert.match(text(c.render()), /状态：paused/); assert.equal(calls.filter(call => call.action === 'workflow-run').length, 1);
  c.react.dispose();
});
test('strategy projections stay advisory and do not change selection or execute', async () => {
  const calls = [];
  const suggestion = {kind: 'stop', target_ref: null, reason: 'two_committed_no_progress_results_require_reassessment'};
  const run = {...prepared, status: 'running', strategy: {next_action: suggestion, routes: [
    {route_id: 'route-a', state: 'needs_recheck', consecutive_no_progress: 0, technical_errors: 3, committed_actions: 1, next_action: {kind: 'check', rebuild_context_required: true}},
    {route_id: 'route-b', state: 'needs_reassessment', consecutive_no_progress: 2, technical_errors: 1, committed_actions: 2, next_action: suggestion},
    {route_id: null, state: 'active', consecutive_no_progress: 0, technical_errors: 0, committed_actions: 0},
  ]}};
  const c = harness(async (action, args) => { calls.push({action, args}); return args.command.action === 'list' ? {runs: [run], capability: {enabled: true}} : {run, capability: {enabled: true}}; });
  c.render(); await tick(); const tree = c.render();
  assert.match(text(tree), /需重查/); assert.match(text(tree), /需重评/); assert.match(text(tree), /active（进行中）/);
  assert.match(text(tree), /连续无进展：2 次；技术错误：1 次/);
  assert.equal(nodes(tree).find(n => n.type === 'select' && n.props.value === 'derive').props.value, 'derive');
  assert.deepEqual(calls.map(call => call.args.command.action), ['list', 'read']);
  assert.doesNotMatch(text(tree), /记录完成度投影（只读）/); c.react.dispose();
});
test('scope key changes and disposed async result cannot notify new project', async () => {
  let release, notified = 0; const pending = new Promise(resolve => { release = resolve; });
  const c = harness(async (_, args) => args.command.action === 'prepare' ? pending : {runs: []}, {onChanged: () => notified++});
  const Wrapper = UI(c.react).WorkflowPanel;
  assert.notEqual(Wrapper(c.props).props.key, Wrapper({...c.props, projectId: 'other'}).props.key);
  c.render(); await tick(); fill(c); const promise = button(c.render(), '冻结并准备方案').props.onClick(); c.react.dispose();
  release({run: prepared, capability: {enabled: true}}); await promise; assert.equal(notified, 0);
});
