import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createRequire} from 'node:module';
import {webcrypto, createHash} from 'node:crypto';
const require = createRequire(import.meta.url);
const factory = require('../materials-ui.cjs');
const {createMaterialsController, organizePrompt, downloadOriginal} = factory;
if (!globalThis.crypto) globalThis.crypto = webcrypto;
let sequence = 0;
function file(name = 'synthetic.txt', text = '合成材料 only', size = null) {
  const value = new Blob([text]);
  return {name, size: size ?? value.size, arrayBuffer: () => value.arrayBuffer(), slice: (...args) => value.slice(...args)};
}
function fixture(options = {}) {
  const calls = [], notifications = [], sessionId = options.sessionId || `synthetic-session-${++sequence}`;
  const request = async (action, args) => {
    calls.push({action, args: structuredClone(args)});
    const special = await options.intercept?.(action, args, calls);
    if (special !== undefined) return special;
    if (action === 'material-begin') return {upload_id: `upload-${sessionId}`, files: args.files.map((x, i) => ({...x, file_id: `file-${i}`}))};
    if (action === 'material-preview') return {preview_hash: 'fixed-preview', files: [{name: 'synthetic.txt', file_id: 'file-0', status: 'ready'}]};
    if (action === 'material-confirm') return {project_id: args.project_id, materials: [{file_id: 'file-0', name: 'synthetic.txt', status: 'imported', ref: {id: 'E', rev: 1}, sha256: 'synthetic'}]};
    return {ok: true};
  };
  const config = {request, sessionId, uuid: () => `operation-${++sequence}`, notify: state => notifications.push(state)};
  return {control: createMaterialsController(config), config, calls, notifications};
}

test('select runs upload internally and lost chunk replay preserves file/index/bytes', async () => {
  let lost = true;
  const f = fixture({intercept(action) { if (action === 'material-chunk' && lost) { lost = false; throw Error('lost response'); } }});
  const source = file('synthetic.txt', 'x'.repeat(600 * 1024));
  assert.equal(await f.control.select([source], 'P'), false);
  assert.equal(f.control.get().busy, false);
  assert.match(f.control.get().error, /lost response/);
  assert.equal(f.calls.filter(x => x.action === 'material-begin').length, 1);
  assert.equal((await f.control.retry()).preview_hash, 'fixed-preview');
  const chunks = f.calls.filter(x => x.action === 'material-chunk');
  assert.deepEqual(chunks[0], chunks[1]);
  assert.equal(chunks[2].args.index, 1);
  assert.equal(Buffer.from(chunks[1].args.base64, 'base64').length, 512 * 1024);
  assert.equal(Buffer.from(chunks[2].args.base64, 'base64').length, 88 * 1024);
  assert.equal(f.control.get().files[0], source);
  assert.equal(f.control.get().busy, false);
  f.control.dispose();
});

test('upload obeys server-advertised exact chunk byte contract', async () => {
  const f = fixture({intercept(action, args) {
    if (action === 'material-begin') return {upload_id: 'custom-chunks', limits: {chunk_bytes: 4}, files: args.files.map(x => ({...x, file_id: 'f'}))};
    if (action === 'material-chunk') {
      const length = Buffer.from(args.base64, 'base64').length;
      assert.equal(length, args.index === 2 ? 2 : 4);
    }
  }});
  assert.ok(await f.control.select([file('synthetic.txt', '0123456789')], 'P'));
  assert.deepEqual(f.calls.filter(x => x.action === 'material-chunk').map(x => x.args.index), [0, 1, 2]);
  f.control.dispose();
});

test('uncertain confirm freezes operation/project/duplicate setting until receipt', async () => {
  let lost = true;
  const f = fixture({intercept(action) { if (action === 'material-confirm' && lost) { lost = false; throw Error('confirmation response lost'); } }});
  await f.control.select([file()], 'P');
  assert.equal(await f.control.confirm('P', false), false);
  assert.ok(f.control.get().attempt);
  assert.equal(await f.control.cancel(), false);
  assert.match(f.control.get().error, /结果未知/);
  assert.equal(await f.control.select([file('other.txt')], 'Q'), false);
  assert.equal((await f.control.confirm('Q', true)).project_id, 'P');
  const requests = f.calls.filter(x => x.action === 'material-confirm');
  assert.deepEqual(requests[0].args, requests[1].args);
  assert.equal(requests[1].args.skip_duplicates, false);
  assert.equal(f.control.get().attempt, null);
  await f.control.confirm('Q', true);
  assert.equal(f.calls.filter(x => x.action === 'material-confirm').length, 2);
  f.control.dispose();
});

test('bound batch rejects project switching; projectless staging binds on explicit confirmation', async () => {
  const bound = fixture();
  await bound.control.select([file()], 'P');
  assert.equal(await bound.control.confirm('Q'), false);
  assert.match(bound.control.get().error, /绑定原项目/);
  assert.equal(bound.calls.filter(x => x.action === 'material-confirm').length, 0);
  bound.control.dispose();
  const unbound = fixture();
  await unbound.control.select([file()], null);
  assert.equal(unbound.calls[0].args.project_id, null);
  assert.equal(await unbound.control.confirm(null), false);
  assert.equal((await unbound.control.confirm('new-project', true)).project_id, 'new-project');
  assert.equal(unbound.calls.at(-1).args.skip_duplicates, true);
  unbound.control.dispose();
});

test('failed begin cannot silently discard unknown upload; retry and cancel reuse operation', async () => {
  let lost = true;
  const f = fixture({intercept(action) { if (action === 'material-begin' && lost) { lost = false; throw Error('lost begin'); } }});
  await f.control.select([file()], 'P');
  const original = structuredClone(f.control.get().begin);
  assert.equal(await f.control.select([file('replacement.txt')], 'P'), false);
  assert.equal(f.calls.length, 1);
  assert.equal(await f.control.cancel(), true);
  assert.deepEqual(f.calls[1].args, original);
  assert.equal(f.calls[2].action, 'material-cleanup');
  assert.equal(f.control.get().begin, null);
  assert.ok(await f.control.select([file('replacement.txt')], 'P'));
  f.control.dispose();
});

test('same-session remount retains files and joins pending upload; dispose does not notify or cross sessions', async () => {
  let unblock, began;
  const gate = new Promise(resolve => { unblock = resolve; });
  const started = new Promise(resolve => { began = resolve; });
  const f = fixture({intercept: async action => { if (action === 'material-chunk') { began(); await gate; } }});
  const source = file();
  const uploading = f.control.select([source], 'P');
  await started;
  f.control.dispose();
  const oldNotifications = f.notifications.length;
  const restoredNotifications = [];
  const restored = createMaterialsController({...f.config, notify: value => restoredNotifications.push(value)});
  assert.equal(restored.get().busy, true);
  assert.equal(restored.get().files[0], source);
  const joined = restored.retry();
  const other = fixture();
  assert.deepEqual(other.control.get().files, []);
  assert.equal(other.control.get().upload, null);
  unblock();
  assert.ok(await uploading);
  assert.ok(await joined);
  assert.equal(f.calls.filter(x => x.action === 'material-chunk').length, 1);
  assert.equal(f.notifications.length, oldNotifications);
  assert.ok(restoredNotifications.some(x => x.preview));
  assert.equal(restored.get().busy, false);
  assert.equal(await f.control.confirm('P'), false);
  assert.ok(f.calls.every(x => x.args.session_id === f.config.sessionId));
  restored.dispose(); other.control.dispose();
});

test('beforeunload listener is removed by dispose and blocks uncertain confirmation', async () => {
  const previous = globalThis.window, listeners = new Set();
  globalThis.window = {addEventListener: (name, fn) => { assert.equal(name, 'beforeunload'); listeners.add(fn); }, removeEventListener: (name, fn) => { assert.equal(name, 'beforeunload'); listeners.delete(fn); }};
  try {
    const f = fixture({intercept(action) { if (action === 'material-confirm') throw Error('unknown result'); }});
    assert.equal(listeners.size, 1);
    await f.control.select([file()], 'P');
    await f.control.confirm('P');
    let prevented = false;
    const event = {preventDefault() { prevented = true; }};
    [...listeners][0](event);
    assert.equal(prevented, true);
    assert.equal(event.returnValue, '');
    f.control.dispose();
    assert.equal(listeners.size, 0);
  } finally { if (previous === undefined) delete globalThis.window; else globalThis.window = previous; }
});

test('server-configured limits are authoritative within browser memory protection', async () => {
  const f = fixture();
  assert.ok(await f.control.select(Array.from({length: 51}, (_, i) => file(`synthetic-${i}.txt`)), 'P'));
  f.control.dispose();
  const tooMany = fixture();
  assert.equal(await tooMany.control.select(Array.from({length: 201}, () => file()), 'P'), false);
  assert.match(tooMany.control.get().error, /浏览器/);
  assert.equal(tooMany.calls.length, 0);
  tooMany.control.dispose();
});

test('structured per-file errors render their message rather than object coercion', () => {
  const state = {busy: false, files: [], result: {project_id: 'P', materials: [{file_id: 'failed', name: 'synthetic.txt', status: 'failed', error: {code: 'INVALID_FILE', message: 'synthetic extraction failure'}}]}};
  const values = [state, true, true, '', false, false, ''];
  const React = {createElement: (type, props, ...children) => ({type: typeof type === 'string' ? type : 'component', props, children}), useState: () => [values.shift(), () => {}], useRef: () => ({current: null}), useEffect: () => {}};
  const {MaterialsImport} = factory(React);
  const rendered = JSON.stringify(MaterialsImport({sessionId: 'synthetic-render', projectId: 'P'}));
  assert.match(rendered, /synthetic extraction failure/);
  assert.doesNotMatch(rendered, /\[object Object\]/);
});

test('failed begin exposes cancellation so users can safely select another batch', () => {
  const state = {busy: false, files: [], begin: {operation_id: 'synthetic-begin'}, error: 'lost begin', upload: null};
  const values = [state, true, true, '', false, false, ''];
  const React = {createElement: (type, props, ...children) => ({type: typeof type === 'string' ? type : 'component', props, children}), useState: () => [values.shift(), () => {}], useRef: () => ({current: null}), useEffect: () => {}};
  const {MaterialsImport} = factory(React);
  const rendered = JSON.stringify(MaterialsImport({sessionId: 'synthetic-render-cancel', projectId: 'P'}));
  assert.match(rendered, /取消并清理暂存/);
  assert.match(rendered, /重试上传与预览/);
});

function downloadFixture(bytes = Buffer.alloc(600 * 1024, 120), mutate = value => value) {
  const calls = [], sha256 = createHash('sha256').update(bytes).digest('hex');
  const request = async (action, args) => {
    assert.equal(action, 'material-bytes'); assert.equal(args.limit, 384 * 1024);
    assert.equal(args.session_id, 'download-session'); assert.equal(args.project_id, 'P'); assert.deepEqual(args.ref, {id: 'E', rev: 2});
    calls.push(args);
    const end = Math.min(args.offset + args.limit, bytes.length);
    return mutate({name: 'synthetic.bin', bytes: bytes.length, sha256, offset: args.offset, next_offset: end < bytes.length ? end : null, base64: bytes.subarray(args.offset, end).toString('base64')}, calls.length);
  };
  return {calls, args: {request, sessionId: 'download-session', projectId: 'P', ref: {id: 'E', rev: 2}}, bytes};
}

test('download uses exact 384KiB server contract and returns original verified bytes', async () => {
  const f = downloadFixture();
  const value = await downloadOriginal(f.args);
  assert.equal(value.name, 'synthetic.bin');
  assert.deepEqual(Buffer.from(await value.blob.arrayBuffer()), f.bytes);
  assert.deepEqual(f.calls.map(x => x.offset), [0, 384 * 1024]);
  const empty = downloadFixture(Buffer.alloc(0));
  assert.equal((await downloadOriginal(empty.args)).blob.size, 0);
});

test('download rejects changed hash, inconsistent offsets, missing bytes and corrupt contents', async () => {
  for (const mutate of [
    (part, index) => index === 2 ? {...part, sha256: 'changed'} : part,
    part => ({...part, next_offset: 0}),
    part => ({...part, base64: ''}),
    part => ({...part, sha256: '0'.repeat(64)}),
  ]) {
    const f = downloadFixture(undefined, mutate);
    await assert.rejects(downloadOriginal(f.args), /版本不一致|分段不完整|指纹校验失败/);
  }
});

test('download stops after session/project disposal before requesting another chunk', async () => {
  let current = true;
  const f = downloadFixture(undefined, part => { current = false; return part; });
  await assert.rejects(downloadOriginal({...f.args, isCurrent: () => current}), /已切换/);
  assert.equal(f.calls.length, 1);
});

function componentHarness(initialProps) {
  const slots = []; let index = 0, effects = [], props = initialProps;
  const React = {
    createElement(type, props, ...children) { return {type, props: props || {}, children: children.flat(Infinity).filter(v => v != null && v !== false)}; },
    useState(initial) { const i = index++; if (!slots[i]) slots[i] = {value: typeof initial === 'function' ? initial() : initial}; return [slots[i].value, value => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
    useRef(initial) { const i = index++; return slots[i] ||= {current: initial}; },
    useEffect(effect, deps) { const i = index++, previous = slots[i]; if (!previous || deps.some((v, j) => !Object.is(v, previous.deps[j]))) { slots[i] = {deps, cleanup: previous?.cleanup}; effects.push(() => { previous?.cleanup?.(); slots[i].cleanup = effect(); }); } },
  };
  const {MaterialsImport} = factory(React);
  return {
    render(patch = {}) { props = {...props, ...patch}; index = 0; effects = []; const tree = MaterialsImport(props); for (const effect of effects) effect(); return tree; },
    unmount() { for (const slot of slots) slot?.cleanup?.(); },
  };
}
function uiNodes(tree, predicate) { if (!tree || typeof tree !== 'object') return []; return [...(predicate(tree) ? [tree] : []), ...tree.children.flatMap(child => uiNodes(child, predicate))]; }
function uiText(tree, collapsed = false) { if (tree == null) return ''; if (typeof tree !== 'object') return String(tree); return (collapsed && tree.type === 'details' ? tree.children.filter(c => c?.type === 'summary') : tree.children).map(child => uiText(child, collapsed)).join(' '); }
function uiButton(tree, caption) { const value = uiNodes(tree, n => n.type === 'button' && uiText(n) === caption)[0]; assert.ok(value, `Missing ${caption}`); return value; }
async function importedComponent(extra = {}) {
  const f = fixture(); await f.control.select([file()], 'P'); await f.control.confirm('P', true); f.control.dispose();
  const h = componentHarness({request: f.config.request, sessionId: f.config.sessionId, projectId: 'P', triggerToken: 1, ...extra});
  h.render(); h.render(); return {h, f, tree: () => h.render()};
}

test('import trigger opens without DOM lookup, results hide technical keys, browse returns receipt and closes', async () => {
  const browsed = [], {h, f, tree} = await importedComponent({onBrowse: value => browsed.push(value)});
  try {
    assert.match(uiButton(tree(), '导入研究材料').props.className, /dm-primary/);
    assert.equal(uiNodes(tree(), n => n.type === 'dialog').length, 1);
    assert.doesNotMatch(uiText(tree(), true), /E@1|SHA-256|归档完成 · P/);
    assert.match(uiText(tree(), true), /synthetic.txt：已归档 · 版本1/);
    uiButton(tree(), '查看知识库').props.onClick();
    assert.deepEqual(browsed, [f.control.get().result]); assert.equal(uiNodes(tree(), n => n.type === 'dialog').length, 0);
    h.render({triggerToken: 2}); assert.equal(uiNodes(tree(), n => n.type === 'dialog').length, 1);
  } finally { h.unmount(); }
});

test('import previews use human size and Chinese extraction states with unmodified text in details', async () => {
  const f = fixture({intercept(action) { if (action === 'material-preview') return {preview_hash: 'hash-hidden', files: [
    {file_id: '1', name: 'paper.pdf', bytes: 1536, status: 'extracted', sha256: 'hash-hidden', text: 'source E@1 remains literal'},
    {file_id: '2', name: 'scan.pdf', bytes: 1048576, status: 'archive_only'},
    {file_id: '3', name: 'partial.docx', bytes: 100, status: 'extracted', truncated: true},
    {file_id: '4', name: 'broken.pdf', bytes: 0, status: 'failed'},
  ]}; }});
  await f.control.select([file()], 'P'); f.control.dispose();
  const h = componentHarness({request: f.config.request, sessionId: f.config.sessionId, projectId: 'P', triggerToken: 1}); h.render();
  try {
    const tree = h.render(), visible = uiText(tree, true);
    assert.match(visible, /paper.pdf · 1.5 KiB · 已提取/); assert.match(visible, /scan.pdf · 1 MiB · 仅保存原件/);
    assert.match(visible, /partial.docx · 100 B · 部分提取/); assert.match(visible, /提取失败/);
    assert.doesNotMatch(visible, /hash-hidden|SHA-256|source E@1/); assert.match(uiText(tree), /source E@1 remains literal/);
  } finally { h.unmount(); }
});

test('organize retries freeze complete prompt and request identity, require accepted receipt and block duplicate click', async () => {
  const calls = []; let resolve;
  const {h, tree} = await importedComponent({onOrganize: (prompt, id) => { calls.push({prompt, id}); return calls.length === 1 ? new Promise(r => { resolve = r; }) : Promise.resolve({accepted: true}); }});
  try {
    uiButton(tree(), '整理为研究笔记').props.onClick(); const preview = tree();
    assert.match(uiText(preview, true), /整理 1 份材料/); assert.doesNotMatch(uiText(preview, true), /E@1|SHA-256/);
    assert.match(uiText(preview), /E@1/); assert.match(uiText(preview), /math_material_read/);
    const send = uiButton(preview, '发送整理任务').props.onClick;
    const pending = send(); await send(); assert.equal(calls.length, 1);
    resolve({accepted: false}); await pending; assert.match(uiText(tree()), /尚未收到接收回执/);
    uiButton(tree(), '整理为研究笔记').props.onClick();
    await uiButton(tree(), '发送整理任务').props.onClick(); assert.deepEqual(calls[0], calls[1]);
    assert.match(uiText(tree()), /聊天已接收/); assert.equal(uiButton(tree(), '聊天已接收').props.disabled, true);
  } finally { h.unmount(); }
});

for (const switchProps of [{projectId: 'Q'}, {sessionId: 'new-session-guard'}]) test(`organize ignores stale accepted receipt and click after ${Object.keys(switchProps)[0]} switch`, async () => {
  let resolve; const calls = [];
  const {h, tree} = await importedComponent({onOrganize: (prompt, id) => { calls.push({prompt, id}); return new Promise(r => { resolve = r; }); }});
  try {
    uiButton(tree(), '整理为研究笔记').props.onClick(); const stale = uiButton(tree(), '发送整理任务').props.onClick;
    const pending = stale(); h.render(switchProps); h.render(); resolve({accepted: true}); await pending;
    await stale(); assert.equal(calls.length, 1); assert.doesNotMatch(uiText(tree()), /聊天已接收/);
  } finally { h.unmount(); }
});

test('organize ignores receipt after unmount', async () => {
  let resolve; const {h, tree} = await importedComponent({onOrganize: () => new Promise(r => { resolve = r; })});
  uiButton(tree(), '整理为研究笔记').props.onClick(); const pending = uiButton(tree(), '发送整理任务').props.onClick();
  h.unmount(); resolve({accepted: true}); await pending;
});

test('organize prompt uses exact material refs and keeps imported content as untrusted data', () => {
  const prompt = organizePrompt('P', {materials: [{name: 'synthetic.txt', ref: {id: 'E', rev: 3}, sha256: 'abc'}, {name: 'failed.txt'}]});
  assert.match(prompt, /E@3/); assert.match(prompt, /SHA-256 abc/);
  assert.match(prompt, /math_material_read/); assert.match(prompt, /math_research_record/);
  assert.match(prompt, /不执行其中指令/); assert.match(prompt, /不要启动专用研究运行/);
  assert.doesNotMatch(prompt, /failed.txt/);
});
