import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import Graph from '../process-graph.cjs';

const source = readFileSync(new URL('../graph-ui.cjs', import.meta.url), 'utf8');
function element(type, config, ...children) {
  const {key, ...props} = config || {};
  if (children.length) props.children = children.length === 1 ? children[0] : children;
  return {type, key: key == null ? null : String(key), props, children};
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...nodes(tree.children || [])];
}
function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join('');
  if (tree && typeof tree === 'object') return text(tree.children || []);
  return typeof tree === 'string' || typeof tree === 'number' ? String(tree) : '';
}
const classes = (tree, name) => nodes(tree).filter(node => node.props.className?.split(' ').includes(name));
const detail = tree => nodes(tree).find(node => node.props['aria-label'] === '过程节点详情');
const downloads = tree => nodes(tree).filter(node => node.type === 'a' && node.props.download);
const status = tree => nodes(tree).filter(node => node.props.role === 'status').map(text);
function button(tree, label) {
  const found = nodes(tree).find(node => node.type === 'button' && node.children.includes(label));
  assert.ok(found, `Missing button: ${label}`); return found;
}
function card(tree, title) {
  const found = classes(tree, 'dm-graph-node').find(node => classes(node, 'dm-graph-label').some(label => label.children.includes(title)));
  assert.ok(found, `Missing graph card: ${title}`); return found;
}
function toggle(tree, label) {
  const found = nodes(tree).find(node => node.props['aria-label'] === label && node.type === 'button');
  assert.ok(found, `Missing graph toggle: ${label}`); return found;
}
function filter(tree, label, value) {
  const field = nodes(tree).find(node => ['input', 'select'].includes(node.type) && node.props['aria-label'] === label);
  assert.ok(field, `Missing filter: ${label}`);
  field.props.onChange({currentTarget: {value}});
}
const rowTitles = tree => classes(tree, 'dm-graph-row').map(row => text(classes(row, 'dm-graph-label')[0]));
function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

// An element/hook contract harness, NOT React DOM, browser download, focus, or
// visual acceptance evidence. Ref targets are explicit spies; no browser is used.
function graphTest(initial, {failDownload = false} = {}) {
  const cells = [], created = [], revoked = [], inspections = [], focused = [], scrolled = [];
  let cursor = 0, mounted = true, graph = initial;
  const controls = {failDownload};
  const slot = (kind, create) => {
    const index = cursor++;
    if (!(index in cells)) cells[index] = {kind, ...create()};
    assert.equal(cells[index].kind, kind, 'Hook order must remain stable');
    return cells[index];
  };
  const React = {
    createElement: element,
    useState(initialValue) {
      const cell = slot('state', () => ({value: typeof initialValue === 'function' ? initialValue() : initialValue}));
      return [cell.value, next => {
        assert.equal(mounted, true, 'No state update after unmount');
        cell.value = typeof next === 'function' ? next(cell.value) : next;
      }];
    },
    useRef: initialValue => slot('ref', () => ({value: {current: initialValue}})).value,
    useEffect(callback, deps) {
      const cell = slot('effect', () => ({}));
      const changed = !cell.deps || !deps || deps.length !== cell.deps.length || deps.some((value, index) => !Object.is(value, cell.deps[index]));
      cell.deps = deps; cell.callback = callback;
      if (changed) cell.pending = true;
    },
  };
  const context = {
    Blob,
    URL: {
      createObjectURL(blob) {
        if (controls.failDownload) throw Error('Synthetic unsupported Blob URL');
        assert.ok(blob instanceof Blob);
        const url = `blob:synthetic-${created.length + 1}`;
        created.push({url, blob}); return url;
      },
      revokeObjectURL(url) {
        assert.ok(created.some(entry => entry.url === url), 'Revoke only a URL owned by this component');
        assert.equal(revoked.includes(url), false, 'Revoke each owned URL exactly once');
        revoked.push(url);
      },
    },
    fetch: () => assert.fail('Graph UI must not make network, model, or mutation requests'),
    document: undefined,
  };
  vm.runInNewContext(source, context);
  const Component = context.DshMathGraphUI(React, Graph);
  const render = (next = graph) => {
    assert.equal(mounted, true); graph = next; cursor = 0;
    const tree = Component({graph, inspect: ref => inspections.push(ref)});
    for (const node of nodes(tree)) if (node.props.ref) node.props.ref.current = {
      focus: () => focused.push(text(node)), scrollIntoView: options => scrolled.push(options),
    };
    for (const cell of cells) if (cell.kind === 'effect' && cell.pending) {
      cell.pending = false; cell.cleanup?.(); cell.cleanup = cell.callback();
    }
    return tree;
  };
  const unmount = () => {
    if (!mounted) return;
    for (const cell of cells) if (cell.kind === 'effect') { cell.cleanup?.(); cell.cleanup = undefined; }
    mounted = false;
  };
  return {render, unmount, controls, created, revoked, inspections, focused, scrolled, context};
}

// Explicit synthetic projection descriptors. No backend data or research is
// created; Graph.serialize is the production implementation, not a JSON mock.
function graphFixture(count = 2) {
  const root = {id: 'project', parentId: null, kind: 'project', title: 'Synthetic project', status: 'recorded', statusLabel: '只读快照', facts: []};
  const source = {id: 'source', parentId: root.id, kind: 'source', title: 'Synthetic ledger', subtitle: '/synthetic/source.jsonl', status: 'recorded', statusLabel: '来源记录', facts: []};
  const nodes = [root, source], edges = [{from: root.id, to: source.id, relation: 'contains'}];
  for (let index = 0; index < count; index++) {
    const event = {id: `event-${index}`, parentId: source.id, kind: 'event', title: `Recorded action ${index + 1}`, status: 'recorded', statusLabel: '事件已记录', facts: [{label: '用量', value: 'unknown'}]};
    const record = {id: `record-${index}`, parentId: event.id, kind: 'record', title: `Synthetic note ${index + 1}`, summary: '公开记录摘要', status: 'recorded', statusLabel: '产物已登记', facts: [{label: 'revision', value: String(index + 1)}], ref: {id: 'N-synthetic', rev: index + 1}, source: {path: '/synthetic/source.jsonl', seq: index + 1, hash: 'a'.repeat(64)}};
    nodes.push(event, record);
    edges.push({from: source.id, to: event.id, relation: 'contains'}, {from: event.id, to: record.id, relation: 'produces'});
  }
  if (count > 1) edges.push(
    {from: 'record-0', to: 'record-1', relation: 'uses', label: '显式使用'},
    {from: 'record-1', to: 'record-0', relation: 'dependencies', label: '交叉引用'},
    {from: 'record-0', to: 'not-collected', relation: 'uses', label: '未采集引用'},
  );
  return {format: 'dsh-math-process-graph-v1', project_id: 'synthetic-project', project_title: root.title,
    research_head: 'a'.repeat(64), research_seq: count, sources: [{adapter: 'ledger-py-v1', path: '/synthetic/source.jsonl'}],
    nodes, edges, warnings: ['未选择的来源未采集，会话请求总数未知。'], counts: {events: count, records: count, omitted: 0},
    notice: '这是公开研究产物的只读快照，不是内部思维链。'};
}

test('download owns complete JSON bytes and reports dispatch, never confirmed save', async () => {
  const graph = deepFreeze(graphFixture()), t = graphTest(graph);
  assert.equal(t.created.length, 0, 'Factory evaluation must not allocate a URL');
  assert.equal(downloads(t.render()).length, 0, 'Effect result appears only in the next render');
  let tree = t.render(), [link] = downloads(tree);
  assert.equal(t.created.length, 1);
  assert.equal(link.props.href, t.created[0].url);
  assert.equal(link.props.download, 'dsh-math-process-synthetic-project-seq-2.json');
  assert.equal(t.created[0].blob.type, 'application/json;charset=utf-8');
  assert.equal(await t.created[0].blob.text(), Graph.serialize(graph));
  assert.deepEqual(JSON.parse(await t.created[0].blob.text()), graph);
  link.props.onClick(); tree = t.render();
  assert.deepEqual(status(tree), ['已请求浏览器下载 JSON；请在下载列表确认保存结果。']);
  assert.doesNotMatch(status(tree).join(''), /已保存|保存成功|已完成下载/);
  t.render(); assert.equal(t.created.length, 1, 'Rerenders reuse the owned snapshot URL');
  const next = deepFreeze({...graphFixture(1), research_head: 'b'.repeat(64)});
  t.render(next); tree = t.render();
  assert.deepEqual(t.revoked, ['blob:synthetic-1']);
  assert.equal(downloads(tree)[0].props.href, 'blob:synthetic-2');
  assert.deepEqual(status(tree), [], 'A new snapshot has not yet requested a download');
  assert.equal(await t.created[1].blob.text(), Graph.serialize(next));
  t.unmount(); t.unmount();
  assert.deepEqual(t.revoked, ['blob:synthetic-1', 'blob:synthetic-2']);
  assert.equal(t.inspections.length, 0);
});

test('unsupported downloads expose a complete explicit JSON fallback without claiming success', () => {
  const graph = deepFreeze(graphFixture()), t = graphTest(graph, {failDownload: true});
  t.render(); let tree = t.render();
  assert.equal(downloads(tree).length, 0);
  assert.deepEqual(status(tree), ['当前客户端不能创建下载；可展开下方 JSON 快照手动保存。']);
  const json = nodes(tree).find(node => node.type === 'details' && typeof node.props.onToggle === 'function');
  assert.ok(json); assert.equal(nodes(json).some(node => node.type === 'pre'), false);
  json.props.onToggle({currentTarget: {open: true}}); tree = t.render();
  assert.deepEqual(JSON.parse(nodes(tree).find(node => node.type === 'pre').children[0]), graph);
  nodes(tree).find(node => node.type === 'details' && node.props.onToggle).props.onToggle({currentTarget: {open: false}});
  assert.equal(nodes(t.render()).some(node => node.type === 'pre'), false);
  t.unmount(); assert.deepEqual(t.created, []); assert.deepEqual(t.revoked, []);
});

test('graph replacement cannot leave a revoked link or stale download status', async testContext => {
  await testContext.test('successful URL then unsupported replacement', () => {
    const graph = deepFreeze(graphFixture()), t = graphTest(graph);
    t.render(); downloads(t.render())[0].props.onClick();
    t.controls.failDownload = true;
    t.render({...graph, warnings: ['new snapshot']});
    const tree = t.render();
    assert.deepEqual(t.revoked, ['blob:synthetic-1']);
    assert.equal(downloads(tree).length, 0, 'A revoked old URL must not remain clickable after the replacement failed');
    assert.deepEqual(status(tree), ['当前客户端不能创建下载；可展开下方 JSON 快照手动保存。']);
    t.unmount();
  });
  await testContext.test('unsupported URL then successful replacement', () => {
    const graph = deepFreeze(graphFixture()), t = graphTest(graph, {failDownload: true});
    t.render(); assert.equal(status(t.render()).length, 1);
    t.controls.failDownload = false;
    t.render({...graph, warnings: ['new snapshot']});
    const tree = t.render();
    assert.equal(downloads(tree).length, 1);
    assert.deepEqual(status(tree), [], 'The new snapshot must not retain an obsolete unsupported-download claim');
    t.unmount();
  });
});

test('40-item pagination and independent folding never truncate exported nodes or edges', async () => {
  const graph = deepFreeze(graphFixture(85)), t = graphTest(graph), bytes = Graph.serialize(graph);
  t.render(); let tree = t.render();
  assert.equal(classes(tree, 'dm-graph-event').length, 40);
  assert.equal(classes(tree, 'dm-graph-record').length, 0, 'Event children start folded');
  const exported = JSON.parse(await t.created[0].blob.text());
  assert.equal(exported.nodes.length, 172); assert.deepEqual(exported, graph);
  button(tree, '显示更多（40 / 85）').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-event').length, 80);
  button(tree, '显示更多（80 / 85）').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-event').length, 85);
  assert.equal(classes(tree, 'dm-graph-more').length, 0);
  let control = toggle(tree, '展开 Recorded action 1 的子节点');
  assert.equal(control.props['aria-expanded'], false); control.props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-record').length, 1);
  assert.equal(toggle(tree, '收起 Recorded action 1 的子节点').props['aria-expanded'], true);
  assert.equal(toggle(tree, '展开 Recorded action 2 的子节点').props['aria-expanded'], false);
  toggle(tree, '收起 Synthetic ledger 的子节点').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-event').length, 0);
  toggle(tree, '展开 Synthetic ledger 的子节点').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-event').length, 85, 'Source folding preserves its page limit');
  assert.equal(classes(tree, 'dm-graph-record').length, 1, 'Folding a source preserves each event state');
  assert.equal(Graph.serialize(graph), bytes);
  assert.equal(await t.created[0].blob.text(), bytes);
  assert.equal(t.created.length, 1, 'Presentation changes must not regenerate or silently narrow the snapshot');
  t.unmount();
});

test('node details retain exact refs, cross-links, and Escape focus return contracts', () => {
  const graph = deepFreeze(graphFixture()), t = graphTest(graph);
  t.render(); toggle(t.render(), '展开 Recorded action 1 的子节点').props.onClick();
  let returnedFocus = 0, stopped = 0;
  card(t.render(), 'Synthetic note 1').props.onClick({currentTarget: {focus: () => returnedFocus++}});
  let tree = t.render(), pane = detail(tree);
  assert.ok(pane);
  assert.equal(card(tree, 'Synthetic note 1').props['aria-pressed'], true);
  assert.deepEqual(t.focused, ['Synthetic note 1']);
  assert.deepEqual(Array.from(t.scrolled, value => value.block), ['nearest']);
  assert.deepEqual(JSON.parse(nodes(pane).find(node => node.type === 'pre').children[0]), graph.nodes.find(node => node.id === 'record-0').source);
  button(pane, '打开记录 N-synthetic@1').props.onClick();
  assert.deepEqual(t.inspections, [{id: 'N-synthetic', rev: 1}]);
  const relations = classes(pane, 'dm-graph-relations')[0];
  assert.match(text(relations), /显式引用（可能有交叉或环，不强行树化）/);
  assert.match(text(relations), /未收录的精确引用/);
  assert.doesNotMatch(text(relations), /Recorded action 1/);
  button(relations, 'Synthetic note 2').props.onClick(); tree = t.render(); pane = detail(tree);
  assert.equal(nodes(pane).find(node => node.type === 'h3').children[0], 'Synthetic note 2');
  button(pane, '打开记录 N-synthetic@2').props.onClick();
  assert.deepEqual(t.inspections, [{id: 'N-synthetic', rev: 1}, {id: 'N-synthetic', rev: 2}]);
  pane.props.onKeyDown({key: 'Enter', stopPropagation: () => stopped++});
  assert.ok(detail(t.render())); assert.equal(stopped, 0);
  pane.props.onKeyDown({key: 'Escape', stopPropagation: () => stopped++});
  tree = t.render(); assert.equal(detail(tree), undefined);
  assert.equal(stopped, 1); assert.equal(returnedFocus, 1, 'Cross-link navigation keeps the original card ref');
  assert.equal(card(tree, 'Synthetic note 1').props['aria-pressed'], false);
  card(tree, 'Recorded action 1').props.onClick({currentTarget: {focus: () => returnedFocus++}});
  assert.match(text(detail(t.render())), /unknown/);
  button(detail(t.render()), '关闭节点详情').props.onClick();
  assert.equal(detail(t.render()), undefined); assert.equal(returnedFocus, 2);
  assert.equal(t.created.length, 1); t.unmount();
});

test('untrusted labels, source locators and unknown facts remain literal text, never innerHTML', () => {
  const literal = '<img src=x onerror="globalThis.__xss=1">&</pre><script>globalThis.__xss=2</script>';
  const graph = graphFixture(1), root = graph.nodes[0];
  Object.assign(root, {title: literal, summary: literal, status: 'unknown', statusLabel: 'unknown',
    facts: [{label: literal, value: 'unknown'}, {label: 'literal', value: literal}], source: {path: literal}});
  graph.warnings.push(literal); deepFreeze(graph);
  const t = graphTest(graph); t.render();
  card(t.render(), literal).props.onClick({currentTarget: {focus() {}}});
  let tree = t.render(), pane = detail(tree);
  assert.equal(card(tree, literal).props['data-status'], 'unknown');
  assert.ok(nodes(pane).some(node => node.type === 'dd' && node.children[0] === 'unknown'));
  assert.ok(nodes(pane).some(node => node.type === 'dd' && node.children[0] === literal));
  assert.deepEqual(JSON.parse(nodes(pane).find(node => node.type === 'pre').children[0]), {path: literal});
  assert.ok(nodes(tree).some(node => node.type === 'li' && node.children[0] === literal));
  nodes(tree).find(node => node.type === 'details' && node.props.onToggle).props.onToggle({currentTarget: {open: true}});
  tree = t.render();
  assert.ok(nodes(tree).some(node => node.type === 'pre' && node.children[0] === Graph.serialize(graph)));
  for (const node of nodes(tree)) {
    assert.equal(node.props.dangerouslySetInnerHTML, undefined);
    assert.equal(node.props.innerHTML, undefined);
    assert.ok(!['script', 'img', 'iframe'].includes(node.type));
    if (node.props.href) assert.match(node.props.href, /^blob:synthetic-/);
  }
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|\.innerHTML\s*=/);
  assert.equal(t.context.__xss, undefined);
  assert.match(text(tree), /不代表推理因果/);
  assert.match(text(tree), /不展示工具输入\/输出原文或模型推理流/);
  assert.equal(t.inspections.length, 0); t.unmount();
});

test('summary remains default and list filters preserve tree folding, pagination, and original graph bytes', async () => {
  const graph = graphFixture(85);
  graph.nodes.find(node => node.id === 'event-1').status = 'error';
  graph.nodes.find(node => node.id === 'event-2').status = 'unknown';
  deepFreeze(graph);
  const bytes = Graph.serialize(graph), t = graphTest(graph);
  t.render(); let tree = t.render();
  assert.equal(button(tree, '概要').props['aria-pressed'], true);
  assert.equal(button(tree, '逐条').props['aria-pressed'], false);
  assert.equal(classes(tree, 'dm-graph-list').length, 0);
  button(tree, '显示更多（40 / 85）').props.onClick(); tree = t.render();
  toggle(tree, '展开 Recorded action 1 的子节点').props.onClick();
  button(tree, '逐条').props.onClick(); tree = t.render();
  assert.equal(button(tree, '逐条').props['aria-pressed'], true);
  assert.equal(classes(tree, 'dm-graph-board').length, 0);
  assert.equal(classes(tree, 'dm-graph-row').length, 40);
  filter(tree, '节点类型', 'event'); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-row').length, 40);
  assert.equal(rowTitles(tree)[0], 'Recorded action 1');
  button(tree, '显示更多（40 / 85）').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-row').length, 80);
  button(tree, '显示更多（80 / 85）').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-row').length, 85);
  filter(tree, '记录状态', 'error'); tree = t.render();
  assert.deepEqual(rowTitles(tree), ['Recorded action 2']);
  filter(tree, '记录状态', 'unknown'); tree = t.render();
  assert.deepEqual(rowTitles(tree), ['Recorded action 3']);
  filter(tree, '搜索已收录记录', 'does not exist'); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-empty').length, 1);
  assert.equal(classes(tree, 'dm-graph-row').length, 0);
  button(tree, '清除筛选').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-row').length, 40, 'Filter edits reset incremental list limits');
  filter(tree, '节点类型', 'record'); tree = t.render();
  filter(tree, '搜索已收录记录', '  N-SYNTHETIC@85  '); tree = t.render();
  assert.deepEqual(rowTitles(tree), ['Synthetic note 85'], 'Exact references are searchable, case insensitive');
  const json = nodes(tree).find(node => node.type === 'details' && node.props.onToggle);
  json.props.onToggle({currentTarget: {open: true}}); tree = t.render();
  assert.ok(nodes(tree).some(node => node.type === 'pre' && node.children[0] === bytes));
  assert.equal(t.created.length, 1);
  assert.equal(await t.created[0].blob.text(), bytes);
  assert.deepEqual(JSON.parse(await t.created[0].blob.text()), graph, 'Filtered download retains all edges, sources and nodes');
  assert.equal(Graph.serialize(graph), bytes, 'All presentation state remains outside the frozen graph');
  button(tree, '概要').props.onClick(); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-event').length, 80, 'Summary page size survives mode changes');
  assert.equal(classes(tree, 'dm-graph-record').length, 1, 'Summary folds survive mode changes');
  button(tree, '逐条').props.onClick(); tree = t.render();
  assert.deepEqual(rowTitles(tree), ['Synthetic note 85'], 'List filters survive mode changes');
  assert.equal(t.inspections.length, 0);
  t.unmount();
});

test('list keeps source grouping and stable local seq order without a cross-source chronology', () => {
  const graph = graphFixture(2), first = graph.nodes.find(node => node.kind === 'source');
  for (let index = 0; index < 2; index++) {
    for (const kind of ['event', 'record']) graph.nodes.find(node => node.id === `${kind}-${index}`).source = {path: first.subtitle, seq: index ? 10 : 20};
  }
  const second = {...first, id: 'second', title: 'Second ledger', subtitle: '/synthetic/second.jsonl'};
  const unknownSource = {...first, id: 'unsupported', title: 'Unsupported source', subtitle: '/synthetic/native.jsonl', status: 'unknown', statusLabel: '消息未纳入'};
  const example = graph.nodes.find(node => node.id === 'event-0');
  graph.nodes.push(second,
    {...example, id: 'second-2', parentId: second.id, title: 'Second seq 2', source: {path: second.subtitle, seq: 2}, facts: [{label: '时间', value: '1900-01-01'}]},
    {...example, id: 'second-1', parentId: second.id, title: 'Second seq 1', source: {path: second.subtitle, seq: 1}, facts: [{label: '时间', value: '2100-01-01'}]},
    {...example, id: 'second-unknown', parentId: second.id, title: 'No known seq', source: {path: second.subtitle}}, unknownSource);
  graph.sources.push({adapter: 'ledger-py-v1', path: second.subtitle}, {adapter: 'native-v4', path: unknownSource.subtitle});
  graph.counts.events += 3;
  deepFreeze(graph);
  const before = Graph.serialize(graph), t = graphTest(graph);
  t.render(); button(t.render(), '逐条').props.onClick(); let tree = t.render();
  assert.deepEqual(classes(tree, 'dm-graph-source-group').map(group => group.props['aria-label']), ['Synthetic ledger', 'Second ledger', 'Unsupported source']);
  assert.deepEqual(rowTitles(tree), ['Synthetic ledger', 'Recorded action 2', 'Synthetic note 2', 'Recorded action 1', 'Synthetic note 1',
    'Second ledger', 'Second seq 1', 'Second seq 2', 'No known seq', 'Unsupported source']);
  assert.match(text(tree), /没有跨来源统一时间或推断因果/);
  assert.match(text(tree), /来源内 seq 未知/);
  filter(tree, '记录状态', 'unknown'); tree = t.render();
  assert.deepEqual(rowTitles(tree), ['Unsupported source'], 'Unknown source descriptors remain discoverable even without event rows');
  assert.equal(Graph.serialize(graph), before);
  t.unmount();
});

test('list pagination is independent per source and resets for a replacement snapshot', async () => {
  const graph = graphFixture(45), first = graph.nodes.find(node => node.kind === 'source');
  const second = {...first, id: 'second', title: 'Second ledger', subtitle: '/synthetic/second.jsonl'};
  const events = graph.nodes.filter(node => node.kind === 'event').map((node, index) => ({...node,
    id: `second-event-${index}`, parentId: second.id, title: `Second action ${index + 1}`, source: {path: second.subtitle, seq: index + 1}}));
  graph.nodes.push(second, ...events); graph.sources.push({adapter: 'ledger-py-v1', path: second.subtitle}); graph.counts.events += 45;
  deepFreeze(graph);
  const t = graphTest(graph); t.render(); button(t.render(), '逐条').props.onClick(); let tree = t.render();
  filter(tree, '节点类型', 'event'); tree = t.render();
  const sizes = tree => classes(tree, 'dm-graph-source-group').map(group => rowTitles(group).length);
  assert.deepEqual(sizes(tree), [40, 40]);
  toggle(tree, '显示更多 Synthetic ledger 的逐条记录').props.onClick(); tree = t.render();
  assert.deepEqual(sizes(tree), [45, 40]);
  assert.equal(classes(tree, 'dm-graph-list-more').length, 1);
  toggle(tree, '显示更多 Second ledger 的逐条记录').props.onClick(); tree = t.render();
  assert.deepEqual(sizes(tree), [45, 45]);
  const next = deepFreeze({...graph, research_head: 'c'.repeat(64)});
  t.render(next); tree = t.render();
  assert.deepEqual(sizes(tree), [40, 40]);
  assert.equal(nodes(tree).find(node => node.type === 'select' && node.props['aria-label'] === '节点类型').props.value, 'event');
  assert.equal(t.created.length, 2);
  assert.equal(await t.created[1].blob.text(), Graph.serialize(next));
  assert.deepEqual(t.revoked, ['blob:synthetic-1']);
  t.unmount();
});

test('list details and event products inspect only the original exact revision despite filtering', () => {
  const graph = deepFreeze(graphFixture(2)), t = graphTest(graph);
  t.render(); button(t.render(), '逐条').props.onClick(); let tree = t.render();
  filter(tree, '节点类型', 'event'); tree = t.render();
  card(tree, 'Recorded action 1').props.onClick({currentTarget: {focus() {}}}); tree = t.render();
  const products = classes(detail(tree), 'dm-graph-products')[0];
  assert.ok(products);
  button(products, '打开记录 N-synthetic@1').props.onClick();
  assert.equal(t.inspections[0], graph.nodes.find(node => node.id === 'record-0').ref);
  button(detail(tree), '关闭节点详情').props.onClick();
  filter(tree, '节点类型', 'record'); tree = t.render();
  let returns = 0;
  card(tree, 'Synthetic note 1').props.onClick({currentTarget: {focus() { returns++; }}}); tree = t.render();
  filter(tree, '搜索已收录记录', 'missing'); tree = t.render();
  assert.deepEqual(rowTitles(tree), []);
  button(detail(tree), '打开记录 N-synthetic@1').props.onClick();
  assert.equal(t.inspections[1], graph.nodes.find(node => node.id === 'record-0').ref, 'Filtering never upgrades or clears the selected historical ref');
  button(classes(detail(tree), 'dm-graph-relations')[0], 'Synthetic note 2').props.onClick(); tree = t.render();
  button(detail(tree), '打开记录 N-synthetic@2').props.onClick();
  assert.equal(t.inspections[2], graph.nodes.find(node => node.id === 'record-1').ref);
  detail(tree).props.onKeyDown({key: 'Escape', stopPropagation() {}});
  assert.equal(detail(t.render()), undefined); assert.equal(returns, 1);
  t.unmount();
});

test('list search treats malicious strings as literal text and never traverses extra payloads', () => {
  const literal = '<svg onload="globalThis.__xss=1"><script>alert(1)</script>&';
  const graph = graphFixture(1), event = graph.nodes.find(node => node.kind === 'event');
  Object.assign(event, {title: literal, summary: literal, subtitle: literal, statusLabel: literal,
    facts: [{label: literal, value: literal}], source: {path: literal, seq: 1}});
  // A projection extension must not become searchable or visible automatically.
  event.extraPayload = 'PRIVATE-PAYLOAD-NOT-A-SEARCH-FIELD';
  deepFreeze(graph);
  const t = graphTest(graph); t.render(); button(t.render(), '逐条').props.onClick(); let tree = t.render();
  filter(tree, '搜索已收录记录', literal); tree = t.render();
  assert.deepEqual(rowTitles(tree), [literal]);
  card(tree, literal).props.onClick({currentTarget: {focus() {}}}); tree = t.render();
  assert.ok(nodes(detail(tree)).some(node => node.type === 'dd' && node.children[0] === literal));
  for (const node of nodes(tree)) {
    assert.equal(node.props.dangerouslySetInnerHTML, undefined);
    assert.equal(node.props.innerHTML, undefined);
    assert.ok(!['script', 'img', 'iframe', 'svg'].includes(node.type));
    if (node.props.href) assert.match(node.props.href, /^blob:synthetic-/);
  }
  filter(tree, '搜索已收录记录', event.extraPayload); tree = t.render();
  assert.deepEqual(rowTitles(tree), []);
  assert.equal(t.context.__xss, undefined); assert.deepEqual(t.inspections, []);
  t.unmount();
});

test('unsupported download fallback stays complete even when the list has no matches', () => {
  const graph = deepFreeze(graphFixture(3)), t = graphTest(graph, {failDownload: true});
  t.render(); button(t.render(), '逐条').props.onClick(); let tree = t.render();
  filter(tree, '搜索已收录记录', 'no matches'); tree = t.render();
  assert.equal(classes(tree, 'dm-graph-empty').length, 1);
  nodes(tree).find(node => node.type === 'details' && node.props.onToggle).props.onToggle({currentTarget: {open: true}});
  tree = t.render();
  assert.equal(nodes(tree).find(node => node.type === 'pre').children[0], Graph.serialize(graph));
  assert.deepEqual(JSON.parse(nodes(tree).find(node => node.type === 'pre').children[0]), graph);
  assert.equal(downloads(tree).length, 0); assert.deepEqual(t.created, []);
  t.unmount();
});
