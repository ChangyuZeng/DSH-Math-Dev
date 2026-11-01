// Synthetic project data and hook renderer: not browser layout or native clipboard acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import vm from 'node:vm';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const require = createRequire(import.meta.url);
const UI = require('../record-ui.cjs');
const F = require('../forms.cjs');
const clone = value => JSON.parse(JSON.stringify(value));
const ref = o => ({id: o.id, rev: o.rev});
const problem = (rev = 1) => ({schema_version: 4, kind: 'Problem', id: 'P', rev, data: {title: '合成研究目标', statement: 'forall synthetic n', quantifiers: 'forall n', domain: 'synthetic', conventions: [], completion_criteria: ['人工审阅'], sources: ['/private/sensitive-source'], parent: null, relation: '', target_type: 'exploratory', knowledge_status: 'unverified_open'}});
const note = (id = 'N', rev = 1, patch = {}) => ({schema_version: 4, kind: 'Note', id, rev, data: {...F.draft('Note', {id: 'P', rev: 1}, () => 'fixture').data, title: '合成标题', body: `exact body ${id}@${rev}\r\n  保留空白\n`, statement: 'synthetic claim', summary: 'synthetic summary', ...patch}});
const evidence = () => ({schema_version: 4, kind: 'Evidence', id: 'E', rev: 1, data: {...F.draft('Evidence', {id: 'P', rev: 1}, () => 'fixture').data, source: {locator: '/Users/private/secret', version: 'edition 2', accessed_at: null, conditions: []}, support_scope: 'local_claim', limits: ['unverified source'], attachments: [{sha256: 'a'.repeat(64), bytes: 20, logical_name: 'PRIVATE-FILENAME.txt', relative_path: 'attachments/sha256/' + 'a'.repeat(64), text: 'ATTACHMENT-SECRET'}]}});
const viewOf = (records, groups = {}) => ({project: {id: 'project-fixture', title: '合成项目'}, objects: records, groups});
function harness() {
  const slots = []; let position = 0, effects = [];
  const React = {
    createElement(type, props, ...children) { return {type, props: props || {}, children: children.flat(Infinity).filter(v => v !== null && v !== undefined && v !== false)}; },
    useState(initial) { const index = position++; if (!slots[index]) slots[index] = {value: typeof initial === 'function' ? initial() : initial}; return [slots[index].value, value => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }]; },
    useRef(initial) { const index = position++; if (!slots[index]) slots[index] = {current: initial}; return slots[index]; },
    useEffect(effect, deps) { const index = position++, previous = slots[index]; if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) { slots[index] = {deps, cleanup: previous?.cleanup}; effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); }); } },
  };
  const components = UI(React, F, ({text}) => React.createElement('safe-preview', {text}));
  return {React, components, render(name, props, beforeEffects) { position = 0; effects = []; const tree = (typeof name === 'function' ? name : components[name])(props); beforeEffects?.(tree); for (const effect of effects) effect(); return tree; }, slots};
}
function nodes(tree, predicate) { const out = []; const visit = value => { if (!value || typeof value !== 'object') return; if (predicate(value)) out.push(value); for (const child of value.children || []) visit(child); }; visit(tree); return out; }
function strings(tree) { if (tree == null) return ''; if (typeof tree !== 'object') return String(tree); return (tree.children || []).map(strings).join(' '); }
function button(tree, caption) { const found = nodes(tree, n => n.type === 'button' && strings(n) === caption)[0]; assert.ok(found, `Missing button ${caption}`); return found; }

test('CommonJS factory and global UMD expose identical read-only component contract', () => {
  assert.equal(typeof UI, 'function');
  assert.deepEqual(Object.keys(harness().components), ['RecordReader', 'RecordBrowser', 'ReviewSummary', 'ExportReceipt', 'ContextPack']);
  const sandbox = {}; vm.runInNewContext(fs.readFileSync(new URL('../record-ui.cjs', import.meta.url), 'utf8'), sandbox);
  assert.equal(typeof sandbox.DshMathRecordUI, 'function');
  assert.equal(sandbox.DshMathRecordUI.PAGE_SIZE, 60);
});

test('500 history records default to newest per ID, incremental 60, searchable by ID/title/body', () => {
  const records = Array.from({length: 100}, (_, id) => Array.from({length: 5}, (_, rev) => note(`N-${id}`, rev + 1, {title: `中文标题 ${id}`, body: `only-body-token-${id} revision-${rev + 1}`}))).flat().reverse();
  const view = viewOf(records);
  assert.equal(records.length, 500);
  const first = UI.filterRecords(view); assert.equal(first.total, 100); assert.equal(first.rows.length, 60); assert.ok(first.hasMore); assert.ok(first.rows.every(r => r.object.rev === 5));
  assert.equal(UI.filterRecords(view, {limit: 120}).rows.length, 100);
  assert.equal(UI.filterRecords(view, {history: 'all', limit: 600}).rows.length, 500);
  assert.equal(UI.filterRecords(view, {history: 'historical', limit: 600}).rows.length, 400);
  assert.equal(UI.filterRecords(view, {query: 'N-99@3', history: 'all'}).rows[0].object.rev, 3);
  assert.equal(UI.filterRecords(view, {query: '中文标题 99'}).rows[0].object.id, 'N-99');
  assert.equal(UI.filterRecords(view, {query: 'only-body-token-99'}).rows[0].object.rev, 5);
});

test('registered group/assessment stays exact; historical revisions never inherit current acceptance', () => {
  const old = note('N', 1), latest = note('N', 2), foreign = note('Other', 1);
  const view = viewOf([old, latest, foreign], {accepted: [{object: latest, assessment: {ref: ref(latest), state: 'accepted', reasons: ['scope-specific']}}], notes: [{object: foreign, assessment: {ref: ref(latest), state: 'accepted'}}]});
  const rows = UI.recordRows(view);
  assert.equal(rows.find(r => r.object === old).assessment, null);
  assert.equal(rows.find(r => r.object === foreign).assessment, null);
  assert.equal(UI.filterRecords(view, {status: 'accepted', history: 'all'}).total, 1);
  assert.deepEqual(UI.filterRecords(view, {group: 'accepted', history: 'all'}).rows.map(r => r.object.rev), [2]);
});

test('references and backlinks never replace missing or historical revisions and reject malicious IDs', () => {
  const old = note('A', 1), newer = note('A', 2), b = note('B', 1, {uses: [{id: 'A', rev: 1}, {id: 'A', rev: 99}, {id: 'javascript:alert(1)', rev: 1}]});
  const view = viewOf([old, newer, b]);
  assert.equal(UI.exactObject(view, {id: 'A', rev: 1}), old);
  assert.equal(UI.exactObject(view, {id: 'A', rev: 99}), null);
  assert.equal(UI.exactObject(view, {id: 'A', rev: '1'}), null);
  assert.equal(UI.validRef({id: '<img src=x>', rev: 1}), false);
  assert.equal(UI.references(b).filter(r => !r.valid).length, 1);
  assert.equal(UI.backlinks(view, old)[0].object, b);
  assert.equal(UI.backlinks(view, newer).length, 0);
  const inspectCalls = [], h = harness(), tree = h.render('RecordReader', {object: b, view, inspect: o => inspectCalls.push(o)});
  const refs = nodes(tree, n => typeof n.type === 'function' && n.type.name === 'Ref').map(n => n.type(n.props));
  const first = refs.find(n => nodes(n, m => m.type === 'button').length);
  nodes(first, n => n.type === 'button')[0].props.onClick(); assert.equal(inspectCalls[0], old);
  assert.equal(refs.filter(n => strings(n).includes('A@99')).length, 1);
  assert.equal(refs.filter(n => strings(n).includes('无效引用')).length, 1);
});

test('RecordBrowser retains selected old object after history filter and snapshot update', () => {
  const old = note('N', 1), latest = note('N', 2), calls = [], h = harness(); let view = viewOf([old, latest]);
  const props = () => ({view, inspect: object => calls.push(object)});
  let tree = h.render('RecordBrowser', props());
  nodes(tree, n => n.type === 'select').at(-1).props.onChange({target: {value: 'all'}});
  tree = h.render('RecordBrowser', props()); nodes(tree, n => n.type === 'button' && strings(n) === '合成标题')[0].props.onClick();
  assert.equal(calls[0], old);
  nodes(tree, n => n.type === 'select').at(-1).props.onChange({target: {value: 'latest'}});
  view = viewOf([old, latest, note('N', 3)]); tree = h.render('RecordBrowser', props());
  assert.match(strings(tree), /保留选中：N@1/); button(tree, '重新打开选中版本').props.onClick(); assert.equal(calls.at(-1), old);
  view = viewOf([latest]); tree = h.render('RecordBrowser', props()); assert.match(strings(tree), /未替换为新版/);
});

test('Reader sends text through injected safe Preview, preserves full raw, and only caller supplies write controls', () => {
  const malicious = '<img src="https://tracker.invalid/x" onerror="alert(1)">\n[bad](javascript:alert(1)) $x$';
  const object = note('N', 1, {body: malicious}); const h = harness();
  const child = {type: 'caller-action', props: {}, children: ['lead controls']};
  const tree = h.render('RecordReader', {object, view: viewOf([object]), children: child});
  const field = nodes(tree, n => n.props.label === '正文')[0], body = field.type(field.props), bodyNode = nodes(body, n => typeof n.type === 'function' && n.type.name === 'Body')[0];
  assert.equal(bodyNode.type(bodyNode.props).props.text, malicious);
  assert.ok(nodes(tree, n => n.type === 'caller-action').length);
  assert.equal(nodes(tree, n => n.props.dangerouslySetInnerHTML).length, 0);
  assert.equal(nodes(tree, n => n.type === 'a' || n.type === 'img').length, 0);
  assert.match(strings(tree), /N@1/);
  assert.equal(nodes(tree, n => n.type === 'details')[0].props.open, undefined);
  assert.match(strings(nodes(tree, n => n.type === 'details')[0]), /https:\/\/tracker.invalid/);
  assert.equal(nodes(tree, n => n.type === 'button').length, 0, 'Reader has no built-in edit, review, or pin without callbacks');
  const heading = nodes(tree, n => n.type === 'h3')[0]; assert.equal(heading.props.tabIndex, -1); assert.ok(heading.props.ref);
});

test('actual safe Preview keeps reader hostile HTML/images inert and unsafe hrefs noninteractive', () => {
  const h = harness(), {Preview} = require('../math-preview.cjs')(h.React), components = UI(h.React, F, Preview);
  const object = note('N', 1, {body: '<script>unsafe()</script>\n\n![tracking](https://tracker.invalid)\n\n[x](javascript:alert) [file](file:///private) [ok](https://example.org)'});
  const tree = h.render(components.RecordReader, {object, view: viewOf([object])});
  const field = nodes(tree, n => n.props.label === '正文')[0], fieldTree = field.type(field.props), body = nodes(fieldTree, n => n.type?.name === 'Body')[0];
  const previewNode = body.type(body.props), rendered = previewNode.type(previewNode.props);
  assert.equal(nodes(rendered, n => ['img', 'script', 'iframe', 'object'].includes(n.type)).length, 0);
  assert.deepEqual(nodes(rendered, n => n.type === 'a').map(n => n.props.href), ['https://example.org']);
  assert.match(strings(rendered), /unsafe\(\)/);
});

test('pinned Reader can disable autofocus and primary Reader ref focuses on exact revision changes', () => {
  const h = harness(), one = note('N', 1), two = note('N', 2); let focused = 0;
  const tree = h.render('RecordReader', {object: one, view: viewOf([one, two]), autoFocus: false});
  nodes(tree, n => n.type === 'h3')[0].props.ref.current = {focus: () => focused++};
  h.render('RecordReader', {object: two, view: viewOf([one, two]), autoFocus: false}); assert.equal(focused, 0);
  h.render('RecordReader', {object: two, view: viewOf([one, two]), autoFocus: true}); assert.equal(focused, 1);
  h.render('RecordReader', {object: one, view: viewOf([one, two]), autoFocus: true}); assert.equal(focused, 2);
});

test('unknown records keep raw folded and long bodies remain complete rather than preview-truncated', () => {
  const h = harness(), object = {id: 'U', rev: 1, kind: 'FutureKind', data: {unknown: '<script>bad</script>'}};
  const tree = h.render('RecordReader', {object, view: viewOf([object])});
  assert.match(strings(tree), /未知记录类型/); assert.match(strings(tree), /<script>bad<\/script>/); assert.equal(nodes(tree, n => n.type === 'script').length, 0);
  const large = '界'.repeat(100000), n = note('L', 1, {body: large});
  const detail = h.render('RecordReader', {object: n, view: viewOf([n])});
  const f = nodes(detail, n => n.props.label === '正文')[0], field = f.type(f.props), b = nodes(field, n => n.type?.name === 'Body')[0], rendered = b.type(b.props);
  assert.equal(nodes(rendered, n => n.type === 'pre')[0].children[0], large);
});

test('ReviewSummary presents only real returned fields, false eligibility and exact basis, never inferred pass', () => {
  const h = harness(), preview = {eligible_for_pass: false, required_scope: 'local_claim', blockers: ['unresolved_gaps'], basis: {target: {id: 'N', rev: 3}}, extension: {kept: true}};
  const tree = h.render('ReviewSummary', {preview});
  const fields = nodes(tree, n => n.props.label).map(n => [n.props.label, n.props.value]);
  assert.ok(fields.some(([label, value]) => /技术提交资格/.test(label) && value.startsWith('false')));
  assert.ok(!fields.some(([label]) => label === '依据哈希'));
  assert.match(strings(tree), /不是数学验证/); assert.match(strings(tree), /extension/);
});

test('receipt preserves zero operation requests and unknown session usage without inventing success', () => {
  const h = harness(), receipt = {directory: '/tmp/export with space', captured_seq: 0, operation_model_requests: 0, session_model_requests: null};
  const tree = h.render('ExportReceipt', {receipt}), fields = nodes(tree, n => n.props.label);
  assert.equal(fields.find(n => n.props.label === '本次操作模型请求数').props.value, 0);
  assert.match(fields.find(n => n.props.label === '会话模型请求数').props.value, /未知/);
  assert.equal(nodes(tree, n => n.props.label === '复制导出路径')[0].props.value, receipt.directory);
  assert.match(strings(tree), /不是独立复验结果/);
  assert.ok(!fields.some(n => n.props.label === '返回字段 status'));
  const failed = h.render('ExportReceipt', {receipt: {status: 'unknown', error: 'connection interrupted'}});
  assert.equal(nodes(failed, n => n.props.label === '返回字段 status')[0].props.value, 'unknown');
  assert.equal(nodes(failed, n => n.props.label === '复制导出路径').length, 0);
});

test('context defaults to only selected exact record and explicit exact Problem, never all references/attachments', () => {
  const p1 = problem(1), p2 = problem(2), e = evidence(), n = note('N', 1, {evidence: [ref(e)]});
  const view = viewOf([p1, p2, e, n, note('N', 2), ...Array.from({length: 100}, (_, i) => note(`Extra-${i}`))]);
  const keys = UI.initialContextRefs(view, n); assert.deepEqual(keys, ['N@1', 'P@1']);
  const pack = UI.buildContextPack(view, n, keys);
  assert.equal(pack.valid, true); assert.deepEqual(pack.records.map(o => UI.refKey(o.ref)).sort(), ['N@1', 'P@1']);
  assert.equal(pack.records.find(o => o.kind === 'Note').data.body, n.data.body);
  assert.deepEqual(pack.records.find(o => o.kind === 'Note').data.evidence, [ref(e)]);
  assert.equal(pack.text.includes('ATTACHMENT-SECRET'), false); assert.equal(pack.text.includes('/private/sensitive-source'), false);
  assert.equal(pack.text.includes('Extra-'), false); assert.ok(pack.warnings.some(w => /历史版本/.test(w)));
  assert.equal(UI.currentProblem(view, n), p1);
});

test('context only allows project member versions and explicit locator opt-in while attachments never include raw bytes', () => {
  const p = problem(), e = evidence(), view = viewOf([p, e]), keys = UI.initialContextRefs(view, e);
  const before = clone(view);
  let pack = UI.buildContextPack(view, e, [...keys, 'FOREIGN@1']);
  assert.equal(pack.records.length, 2); assert.ok(pack.warnings.some(w => /FOREIGN@1/.test(w)));
  for (const secret of ['/Users/private/secret', '/private/sensitive-source', 'PRIVATE-FILENAME', 'ATTACHMENT-SECRET', 'relative_path']) assert.equal(pack.text.includes(secret), false);
  pack = UI.buildContextPack(view, e, keys, {includeLocators: true});
  assert.ok(pack.text.includes('/Users/private/secret')); assert.ok(pack.text.includes('/private/sensitive-source')); assert.equal(pack.text.includes('ATTACHMENT-SECRET'), false);
  assert.deepEqual(view, before);
  const foreign = {...e, data: {...e.data, support_scope: 'different project collision'}};
  assert.deepEqual(UI.initialContextRefs(view, foreign), []); assert.equal(UI.buildContextPack(view, foreign, keys).valid, false);
  assert.deepEqual(UI.initialContextRefs(view, {...e, rev: 999}), []);
});

test('review context resolves explicit target Problem but never guesses unrelated root; long pack not truncated', () => {
  const p = problem(), n = note('N', 1, {body: '长'.repeat(80000)}), review = {kind: 'Review', id: 'R', rev: 1, data: {target: ref(n), scope: 'local_claim'}};
  const view = viewOf([p, n, review]);
  assert.deepEqual(UI.initialContextRefs(view, review), ['R@1', 'P@1']);
  assert.equal(UI.currentProblem(view, {kind: 'Unknown', data: {}}), null);
  const pack = UI.buildContextPack(view, n, UI.initialContextRefs(view, n)); assert.ok(pack.long); assert.ok(pack.text.includes(n.data.body));
  assert.match(pack.text, /not research authorization/);
  const h = harness(); let tree = h.render('ContextPack', {view, selected: n});
  assert.equal(nodes(tree, x => x.props.label === '明确复制完整上下文包')[0].props.disabled, true);
  const confirm = nodes(tree, x => x.type === 'label' && strings(x).includes('内容较长'))[0]; nodes(confirm, n => n.type === 'input')[0].props.onChange({target: {checked: true}});
  tree = h.render('ContextPack', {view, selected: n}); assert.equal(nodes(tree, x => x.props.label === '明确复制完整上下文包')[0].props.disabled, false);
  tree = h.render('ContextPack', {view: {...view, project: {id: 'other'}}, selected: n}); assert.equal(nodes(tree, x => x.props.label === '明确复制完整上下文包')[0].props.disabled, true);
});

test('ContextPack initial full object never upgrades when selected prop or view newest version changes', () => {
  const p = problem(), old = note('N', 1), newer = note('N', 2), h = harness(); let view = viewOf([p, old, newer]);
  let tree = h.render('ContextPack', {view, selected: old}); assert.match(strings(tree), /初始记录：N@1/);
  view = viewOf([p, old, newer, note('N', 3)]); tree = h.render('ContextPack', {view, selected: newer});
  assert.match(strings(tree), /初始记录：N@1/);
  const preview = nodes(tree, n => n.props['aria-label'] === '完整上下文包预览')[0]; assert.match(strings(preview), /exact body N@1/); assert.doesNotMatch(strings(preview), /exact body N@2/);
});

test('clipboard failure is reported, fallback must return true, and unavailable paths cannot pretend success', async () => {
  let value = '', removed = 0, restored = 0;
  assert.equal((await UI.copyText('payload', {navigator: {clipboard: {writeText: async text => { value = text; }}}})).ok, true); assert.equal(value, 'payload');
  const environment = result => ({navigator: {clipboard: {writeText: async () => { throw Error('denied'); }}}, document: {activeElement: {focus: () => restored++}, body: {appendChild() {}}, createElement: () => ({style: {}, setAttribute() {}, focus() {}, select() {}, remove() { removed++; }}), execCommand: () => result}});
  const fallback = await UI.copyText('payload', environment(true)); assert.equal(fallback.ok, true); assert.equal(fallback.method, 'fallback'); assert.equal(fallback.warning, 'denied');
  const failed = await UI.copyText('payload', environment(false)); assert.equal(failed.ok, false); assert.match(failed.error, /denied/); assert.match(failed.error, /拒绝备用复制/);
  assert.equal((await UI.copyText('payload', {})).ok, false); assert.equal(removed, 2); assert.equal(restored, 2);
});

test('title fallback is a 160-codepoint visual summary without changing original statements or search/context', () => {
  const statement = '😀'.repeat(200) + '\r\n  exact tail searchable-tail', object = {id: 'O', rev: 1, kind: 'Obligation', data: {...F.draft('Obligation', {id: 'P', rev: 1}, () => 'fixture').data, statement}};
  const view = viewOf([problem(), object]), original = clone(object), summary = UI.title(object);
  assert.equal(Array.from(summary).length, 160); assert.ok(summary.endsWith('…')); assert.ok(!summary.includes('\uFFFD'));
  const h = harness(), tree = h.render('RecordReader', {object, view});
  assert.equal(strings(nodes(tree, n => n.type === 'h3')[0]), summary);
  assert.equal(nodes(tree, n => n.props.label === '待证陈述')[0].props.value, statement);
  assert.equal(JSON.parse(nodes(nodes(tree, n => n.type === 'details')[0], n => n.type === 'pre')[0].children[0]).data.statement, statement);
  assert.equal(UI.filterRecords(view, {query: 'searchable-tail'}).rows[0].object, object);
  assert.equal(UI.buildContextPack(view, object, UI.initialContextRefs(view, object)).records.find(o => o.kind === 'Obligation').data.statement, statement);
  assert.equal(UI.title(note('N', 1, {title: '长标题'.repeat(100), statement})), '长标题'.repeat(100), 'Real title is not replaced by a statement summary');
  assert.deepEqual(object, original);
});

test('Chinese kind/state labels preserve original enums and unknowns without inferred assessments', () => {
  assert.deepEqual(['Problem', 'Note', 'Obligation', 'Evidence', 'Review'].map(UI.kindLabel), ['研究问题', '笔记', '待证义务', '证据', '审核记录']);
  assert.equal(UI.kindLabel('FutureKind'), 'FutureKind'); assert.equal(UI.kindLabel('__proto__'), '__proto__');
  assert.equal(UI.stateLabel('accepted'), '已登记接受（accepted）'); assert.equal(UI.stateLabel('future_state'), 'future_state');
  const object = note(), assessment = {ref: ref(object), state: 'accepted', reasons: [], review_id: 'R'}, view = viewOf([object, evidence()], {accepted: [{object, assessment}]});
  const h = harness(), tree = h.render('RecordBrowser', {view});
  const options = nodes(tree, n => n.type === 'option');
  assert.equal(strings(options.find(n => n.props.value === 'Note')), '笔记');
  const stateOptions = nodes(nodes(tree, n => n.type === 'select')[2], n => n.type === 'option');
  assert.equal(strings(stateOptions.find(n => n.props.value === 'accepted')), '已登记接受（accepted）');
  const metas = nodes(tree, n => n.props.className === 'dm-record-meta').map(strings);
  assert.ok(metas.some(s => s.includes('笔记 · N@1') && s.includes('accepted')));
  assert.ok(metas.some(s => s.includes('证据 · E@1') && s.includes('无此版本审核评估')));
  const reader = harness().render('RecordReader', {object, view});
  const registered = nodes(reader, n => n.type?.name === 'Registered')[0]; assert.match(strings(registered.type(registered.props)), /已登记接受（accepted）/);
});

test('Reader focuses then scrolls nearest while pinned reader performs neither action', () => {
  const object = note(), calls = [], mount = tree => { nodes(tree, n => n.type === 'h3')[0].props.ref.current = {focus: options => calls.push(['focus', options]), scrollIntoView: options => calls.push(['scroll', options])}; };
  const h = harness(); h.render('RecordReader', {object, view: viewOf([object])}, mount);
  assert.deepEqual(calls, [['focus', {preventScroll: true}], ['scroll', {block: 'nearest'}]]);
  const pinned = harness(); pinned.render('RecordReader', {object, view: viewOf([object]), autoFocus: false}, mount);
  assert.equal(calls.length, 2);
});

test('ContextPack heading focuses and scrolls on open only, not selection/filter rerenders', () => {
  const object = note(), view = viewOf([problem(), object]), calls = [], h = harness();
  let tree = h.render('ContextPack', {selected: object, view}, tree => {
    const heading = nodes(tree, n => n.type === 'h3')[0]; assert.equal(heading.props.tabIndex, -1);
    heading.props.ref.current = {focus: options => calls.push(['focus', options]), scrollIntoView: options => calls.push(['scroll', options])};
  });
  assert.deepEqual(calls, [['focus', {preventScroll: true}], ['scroll', {block: 'nearest'}]]);
  nodes(tree, n => n.type === 'input' && n.props.type === 'search')[0].props.onChange({target: {value: 'N'}});
  tree = h.render('ContextPack', {selected: object, view}); assert.equal(calls.length, 2);
});

test('real backend ResearchView.assess returns exact ref consumed by Registered, using only in-memory synthetic records', () => {
  const object = note(), problemObject = problem();
  const script = `import json, sys\nsys.path.insert(0, sys.argv[1])\nfrom domain import ResearchView\nrecords = json.load(sys.stdin)\nclass MemoryStore:\n    events = []\n    def __init__(self):\n        self.records = {o['id']: [o] for o in records}\n    def get(self, object_id, rev=None):\n        versions = self.records[object_id]\n        return versions[-1] if rev is None else next(o for o in versions if o['rev'] == rev)\nstore = MemoryStore()\nprint(json.dumps(ResearchView(store, check_files=False).assess({'id':'N','rev':1})))\n`;
  const output = spawnSync('python3', ['-B', '-c', script, fileURLToPath(new URL('../runtime/p2c/', import.meta.url))], {input: JSON.stringify([problemObject, object]), encoding: 'utf8'});
  assert.equal(output.status, 0, output.stderr || output.error?.message);
  const assessment = JSON.parse(output.stdout); assert.deepEqual(assessment.ref, ref(object)); assert.equal(assessment.state, 'unreviewed');
  const view = viewOf([problemObject, object], {notes: [{object, assessment}]}), row = UI.recordRows(view).find(r => r.object === object);
  assert.equal(row.assessment, assessment); assert.equal(row.status, 'unreviewed');
  const tree = harness().render('RecordReader', {object, view}), registered = nodes(tree, n => n.type?.name === 'Registered')[0];
  assert.match(strings(registered.type(registered.props)), /未审核（unreviewed）/);
  assert.doesNotMatch(strings(registered.type(registered.props)), /无可用的已登记审核评估/);
});

test('component copy failure exposes complete selectable fallback, not success text', async () => {
  const h = harness(); const receiptTree = h.render('ExportReceipt', {receipt: {directory: '/full/path'}}), copyNode = nodes(receiptTree, n => n.props.label === '复制导出路径')[0];
  let tree = h.render(copyNode.type, copyNode.props); await button(tree, '复制导出路径').props.onClick(); tree = h.render(copyNode.type, copyNode.props);
  assert.match(strings(tree), /复制失败/); assert.doesNotMatch(strings(tree), /已复制/);
  assert.equal(nodes(tree, n => n.type === 'textarea')[0].props.value, '/full/path');
});
