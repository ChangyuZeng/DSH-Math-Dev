import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import UI from '../library-ui.cjs';

let serial = 0;
const entry = (patch = {}) => ({entry: {kind: 'object', id: 'private-uuid'}, kind: 'Note', ref: {id: 'private-uuid', rev: 3}, content_rev: 3, metadata_rev: 0, title: '黎曼积分笔记', summary: '研究说明', removed: false, can_manage: true, can_edit: true, updated_at: '2026-11-03T06:00:00Z', bytes: 2048, ...patch});
const object = {id: 'private-uuid', rev: 3, kind: 'Note', data: {title: '黎曼积分笔记', body: '原始正文 🧮'}};
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
function server(item = entry(), content = object) {
  const calls = [];
  return {calls, item, content, async request(action, args) {
    calls.push({action, args: structuredClone(args)});
    if (action === 'library-list') return {items: [item], total: 1, offset: args.offset, next_offset: null};
    if (action === 'library-read') {
      const chars = Array.from(JSON.stringify({library: item, content})), end = Math.min(chars.length, args.offset + 80);
      return {item, text: chars.slice(args.offset, end).join(''), offset: args.offset, next_offset: end < chars.length ? end : null, total_chars: chars.length};
    }
    if (action === 'material-read') return {text: '原文段落', offset: args.offset, next_offset: null, total_chars: 4, warnings: [], truncated: false};
    if (action === 'scratchpad-save') return {scratchpad: {...args, rev: args.expected_rev + 1}};
    if (action === 'project-draft-save') return {draft: {id: args.draft_id, rev: args.expected_rev + 1, fields: args.patch}};
    return {entry: args.entry, metadata: {metadata_rev: args.expected_rev + 1, ...args.patch, removed: action === 'library-remove'}};
  }};
}
function control(t, s, options = {}) {
  const c = UI.createLibraryController({request: s.request, sessionId: `session-${++serial}`, projectId: 'project-a', writable: true, uuid: () => `stable-op-${serial}`, ...options});
  t.after(() => c.dispose()); return c;
}
test('standalone factory publishes browser global and CommonJS helpers', () => {
  const context = {}; vm.runInNewContext(readFileSync(new URL('../library-ui.cjs', import.meta.url), 'utf8'), context);
  assert.equal(typeof context.DshMathLibraryUI, 'function');
  assert.equal(UI.formatSize(2048), '2.0 KiB'); assert.equal(UI.formatTime('bad-date'), '');
});
test('durable listing sends all filters before pagination and requests removed-only trash', async t => {
  const s = server(), c = control(t, s); await c.load();
  assert.equal(s.calls[0].action, 'library-list'); assert.equal(s.calls[0].args.kind, null);
  await c.filter({query: '积分', kind: 'material', sort: 'created_desc', trash: true});
  const args = s.calls.at(-1).args;
  assert.equal(args.removed_only, true); assert.equal(args.include_removed, true); assert.equal(args.query, '积分'); assert.equal(args.kind, 'material'); assert.equal(args.sort, 'created_desc');
  assert.deepEqual(c.get().items, [], 'active entries never leak into trash');
  await c.load(40); assert.equal(s.calls.at(-1).args.offset, 40);
});
test('JSON reads concatenate every Unicode-aware page and retain exact object', async t => {
  const s = server(), c = control(t, s); const selected = await c.select(s.item);
  assert.deepEqual(selected, object); assert.ok(s.calls.filter(x => x.action === 'library-read').length > 1);
  assert.equal(c.get().content.data.body, '原始正文 🧮');
});
test('overlapping reads ignore old responses, including after disposal', async t => {
  const held = deferred(), s = server(); let first = true, notifications = 0;
  const c = control(t, s, {notify: () => notifications++, request: (action, args) => {
    if (action === 'library-list' && first) { first = false; return held.promise; } return s.request(action, args);
  }});
  const older = c.load(); await c.filter({query: 'new'}); assert.equal(c.get().query, 'new');
  const before = notifications; c.dispose(); held.resolve({items: [entry({title: 'wrong project'})], offset: 0, total: 99, next_offset: null}); await older;
  assert.equal(notifications, before); assert.equal(c.get().total, 1);
});
test('metadata writes use independent CAS and lost-response retry preserves exact operation', async t => {
  const s = server(); let lose = true;
  const c = control(t, s, {request: async (action, args) => { const value = await s.request(action, args); if (action === 'library-update' && lose) { lose = false; throw Error('lost reply'); } return value; }});
  await c.select(s.item); c.editMetadata('title', '用户改名');
  assert.equal(await c.saveMetadata(), false); const attempt = structuredClone(c.get().attempt);
  c.editMetadata('title', '不可变更未确认操作'); assert.equal(c.get().metadata.title, '用户改名');
  assert.ok(await c.retry()); const writes = s.calls.filter(x => x.action === 'library-update');
  assert.deepEqual(writes[0].args, writes[1].args); assert.deepEqual(writes[0].args, attempt.args);
  assert.equal(writes[0].args.expected_rev, 0); assert.equal(c.get().selected.metadata_rev, 1); assert.equal(c.get().attempt, null);
});
test('uncertain operation survives controller remount but is isolated by session and project', async t => {
  const s = server(); const sessionId = `retained-${++serial}`;
  const c = control(t, s, {sessionId, request: async (action, args) => { if (action === 'library-update') throw Error('network'); return s.request(action, args); }});
  await c.select(s.item); c.editMetadata('summary', '本地内容'); await c.saveMetadata(); const attempt = structuredClone(c.get().attempt); c.dispose();
  const other = control(t, s, {sessionId, projectId: 'project-b'}); assert.equal(other.get().attempt, null);
  const remount = control(t, s, {sessionId}); assert.deepEqual(remount.get().attempt, attempt); await remount.retry();
  const write = s.calls.find(x => x.action === 'library-update'); assert.equal(write.args.project_id, 'project-a'); assert.deepEqual(write.args, attempt.args);
});
test('delete requires a separate confirmation and keeps readable references for that exact item', async t => {
  const s = server(entry({referenced_by: [{title: '相关证明路线', kind: 'Note', ref: {id: 'another', rev: 1}}]})), c = control(t, s);
  c.manage(s.item); assert.equal(s.calls.length, 0); assert.equal(c.get().confirm.referenced_by[0].title, '相关证明路线');
  await c.confirmRemove(); assert.equal(s.calls[0].action, 'library-remove'); assert.equal(s.calls[0].args.expected_rev, 0); assert.match(c.get().notice, /历史/);
  await c.manage({...s.item, removed: true, metadata_rev: 1}, true); assert.equal(s.calls.at(-2).action, 'library-restore');
});
test('material selection reads extracted text through paginated material-read', async t => {
  const s = server(entry({kind: 'material', material: {name: 'notes.txt', bytes: 2048}})), c = control(t, s);
  await c.select(s.item); assert.equal(c.get().material.text, '原文段落');
  assert.deepEqual(s.calls.at(-1).args.ref, {id: 'private-uuid', rev: 3}); await c.materialPage(12000); assert.equal(s.calls.at(-1).args.offset, 12000);
});
test('scratchpad content and metadata edits save separately without losing unsaved text', async t => {
  const s = server(entry({entry: {kind: 'scratchpad', id: 'pad'}, kind: 'scratchpad', ref: null, content_rev: 5}), {title: '草稿标题', body: '完整草稿', rev: 5}), c = control(t, s);
  await c.select(s.item); c.editContent('body', '本地编辑'); c.editMetadata('summary', '元数据编辑'); await c.saveMetadata();
  assert.equal(c.get().dirty, true); assert.equal(c.get().fields.body, '本地编辑'); await c.saveContent();
  const write = s.calls.find(x => x.action === 'scratchpad-save'); assert.equal(write.args.expected_rev, 5); assert.equal(write.args.body, '本地编辑'); assert.equal(c.get().dirty, false);
});
test('session drafts save through existing endpoint with captured draft id/content revision', async t => {
  const s = server(entry({entry: {kind: 'draft', id: 'draft-one'}, kind: 'preparation', ref: null, content_rev: 2}), {fields: {title: '目标', statement: '原始条件'}}), c = control(t, s, {projectId: null});
  await c.select(s.item); c.editContent('statement', '新条件'); await c.saveContent();
  const write = s.calls.find(x => x.action === 'project-draft-save'); assert.equal(write.args.draft_id, 'draft-one'); assert.equal(write.args.expected_rev, 2); assert.equal(write.args.patch.statement, '新条件'); assert.equal('project_id' in write.args, false);
});
test('read-only capabilities block all writes and dirty edit cannot be silently replaced', async t => {
  const s = server(), c = control(t, s, {writable: false}); await c.select(s.item); c.editMetadata('title', 'bad'); assert.equal(await c.saveMetadata(), false); assert.equal(c.manage(s.item), false);
  assert.ok(s.calls.every(x => x.action === 'library-read'));
  const editable = control(t, s); await editable.select(s.item); editable.editMetadata('title', '保留'); assert.equal(await editable.select(entry({title: 'different'})), false); assert.equal(editable.get().metadata.title, '保留');
});

test('CAS conflict reread preserves local edits and requires a new explicit save', async t => {
  const s = server(); let conflict = true;
  const c = control(t, s, {request: async (action, args) => {
    if (action === 'library-update' && conflict) { conflict = false; s.item.metadata_rev = 4; throw Object.assign(Error('changed'), {code: 'VERSION_CONFLICT'}); }
    return s.request(action, args);
  }});
  await c.select(s.item); c.editMetadata('title', '保留本地标题'); assert.equal(await c.saveMetadata(), false);
  assert.equal(c.get().attempt, null); assert.equal(c.get().conflict, true); assert.equal(await c.saveMetadata(), false);
  assert.ok(await c.recoverConflict()); assert.equal(c.get().metadata.title, '保留本地标题'); assert.equal(c.get().dirty, true);
  assert.ok(await c.saveMetadata()); assert.equal(s.calls.find(x => x.action === 'library-update').args.expected_rev, 4);
});

// A small element/hook harness: these are behavior contracts, not DOM/visual acceptance.
function hooks() {
  let cells = [], cursor = 0;
  const effects = [];
  return {Fragment: Symbol('fragment'), createElement(type, props, ...children) { return {type, props: props || {}, children: children.flat(Infinity).filter(v => v != null && v !== false)}; },
    useState(initial) { const i = cursor++; if (!(i in cells)) cells[i] = typeof initial === 'function' ? initial() : initial; return [cells[i], v => { cells[i] = typeof v === 'function' ? v(cells[i]) : v; }]; },
    useRef(initial) { const i = cursor++; return cells[i] ||= {current: initial}; },
    useEffect(fn, deps) { const i = cursor++, old = cells[i]; if (!old || deps.some((v, n) => v !== old.deps[n])) { old?.cleanup?.(); cells[i] = {deps}; effects.push(() => { cells[i].cleanup = fn(); }); } },
    render(component, props) { cursor = 0; const tree = component(props); effects.splice(0).forEach(fn => fn()); return tree; },
    dispose() { for (const cell of cells) cell?.cleanup?.(); }};
}
const nodes = tree => typeof tree === 'object' && tree ? [tree, ...tree.children.flatMap(nodes)] : [];
const contentText = tree => typeof tree === 'object' && tree ? tree.children.map(contentText).join(' ') : String(tree);
test('browser rows show material import time and fall back to saved or created time for other items', async t => {
  const imported = '2026-10-28T03:04:05Z', updated = '2026-11-02T08:09:10Z', saved = '2026-10-30T04:05:06Z';
  for (const [patch, expected] of [
    [{kind: 'material', updated_at: null, created_at: imported, bytes: 37}, imported],
    [{kind: 'material', updated_at: updated, created_at: imported, bytes: 37}, imported],
    [{updated_at: null, saved_at: saved, created_at: imported}, saved],
    [{updated_at: null, saved_at: null, created_at: imported}, imported],
    [{updated_at: updated, saved_at: saved, created_at: imported}, updated],
  ]) {
    const r = hooks(), s = server(entry(patch)); t.after(() => r.dispose()); const {LibraryBrowser} = UI(r, null);
    const props = {request: s.request, sessionId: `time-${++serial}`, projectId: 'project-a', writable: true};
    r.render(LibraryBrowser, props); await new Promise(resolve => setImmediate(resolve));
    const tree = r.render(LibraryBrowser, props), meta = nodes(tree).find(n => n.props.className === 'dm-library-meta');
    assert.equal(contentText(meta), `${patch.kind === 'material' ? '导入材料' : '笔记'} · ${new Date(expected).toLocaleString()} · ${patch.kind === 'material' ? '37 B' : '2.0 KiB'}`);
  }
});

test('browser mounts human names and formal row delegates exact object to main inspector', async t => {
  const r = hooks(), s = server(); t.after(() => r.dispose()); const {LibraryBrowser, LibraryManager} = UI(r, null); let inspected;
  const props = {request: s.request, sessionId: `ui-${++serial}`, projectId: 'project-a', writable: true, view: {objects: []}, inspect: value => { inspected = value; }};
  r.render(LibraryBrowser, props); await new Promise(resolve => setImmediate(resolve)); let tree = r.render(LibraryBrowser, props);
  assert.match(contentText(tree), /黎曼积分笔记/); assert.doesNotMatch(contentText(tree), /private-uuid/);
  const rowButton = nodes(tree).find(n => n.type === 'button' && contentText(n) === '黎曼积分笔记'); await rowButton.props.onClick();
  assert.deepEqual(inspected, object); assert.equal(LibraryManager(props).props.initialTrash, true);
});
