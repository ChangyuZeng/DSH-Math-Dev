import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const {build, serialize} = require('../process-graph.cjs');
const ZERO = '0'.repeat(64);
const H = value => createHash('sha256').update(String(value)).digest('hex');
const clone = value => structuredClone(value);
const actor = {kind: 'human', id: 'local-user'};
const origin = {source: 'dsh-math-human', run_id: null, session_id: 'session', tool_call_id: null};
const time = '2026-01-01T00:00:00Z';
const ref = (id, rev = 1) => ({id, rev});
const object = (kind, id, rev, data, schema = 4) => ({schema_version: schema, kind, id, rev, created_at: time, actor: clone(actor), origin: clone(origin), data});
const problem = (id = 'P', rev = 1, extra = {}, schema = 4) => object('Problem', id, rev, {
  title: '公开目标', statement: '公开数学命题', quantifiers: 'for all', domain: 'integers', conventions: [],
  target_type: schema === 3 ? 'engineering_fixture' : 'prove_or_disprove', parent: null, relation: '',
  completion_criteria: ['公开标准'], sources: ['public'], knowledge_status: schema === 3 ? 'synthetic_fixture' : 'unverified_open', ...extra,
}, schema);
const note = (id = 'N', rev = 1, extra = {}, schema = 4) => object('Note', id, rev, {
  note_type: 'argument', title: '公开笔记', summary: '公开研究摘要', body: '不复制全文', statement: '公开陈述',
  assumptions: [], problem_ref: ref('P'), route_id: null, uses: [], candidate_for: [], evidence: [], gaps: [], failure: null, ...extra,
}, schema);
const obligation = (id = 'O', extra = {}) => object('Obligation', id, 1, {
  statement: '公开义务', scope: 'scope', completion_criteria: ['criterion'], check_method: 'human', checker_id: null,
  problem_ref: ref('P'), uses: [], candidates: [], evidence: [], next_action: 'next', state: 'open', reason: 'reason', ...extra,
});
const evidence = (id = 'E', extra = {}) => object('Evidence', id, 1, {
  evidence_type: 'source_excerpt', problem_ref: ref('P'), attachments: [], source: {locator: 'public', version: null, accessed_at: null, conditions: []},
  source_status: 'unverified_source', support_scope: '公开支持范围', limits: ['limit'], production: {method: 'human', version: '1'}, citation: null, ...extra,
});

// Synthetic inputs shaped exactly like backend.overview and replay.aggregate.
// No existing project data, services, subprocesses, research or checker execution.
function fixture(batches = [[problem()]], schema = 4) {
  const events = [], objects = [], stateObjects = {};
  for (const [index, changes] of batches.entries()) {
    const event = {schema_version: schema, kind: 'commit', campaign_id: 'campaign', event_id: 'event-' + (index + 1),
      seq: index + 1, operation_id: 'op-' + (index + 1), request_hash: H('request-' + index), time, actor: clone(actor), origin: clone(origin),
      changes: clone(changes), result: {event_id: 'event-' + (index + 1), seq: index + 1, versions: changes.map(o => ref(o.id, o.rev))},
      previous_hash: index ? events[index - 1].hash : ZERO, event_hash: H('event-' + (index + 1)), recovery: null, control: {}};
    events.push({source: 'research/events.jsonl', seq: event.seq, hash: event.event_hash, time, kind: 'research.commit', data: event});
    for (const value of changes) { objects.push(clone(value)); (stateObjects[value.id] ||= []).push(clone(value)); }
  }
  const head = events.length ? events.at(-1).hash : ZERO;
  return {
    view: {project: {format: 'dsh-math-project-v1', id: 'project', campaign_id: 'campaign', session_id: 'session', title: '公开项目', schema_version: schema},
      snapshot: {usable: true, state: {campaign_id: 'campaign', seq: events.length, head_hash: head, objects: stateObjects, research: {claims: [], routes: []}}}, objects, links: [], groups: {}},
    replay: {format: 'dsh-math-replay-v1', sources: [], events, research_head: head, operation_model_requests: 0, session_model_requests: null,
      runs: [], requests: [], coverage: {native: 'not_selected', transport: 'only_explicitly_selected_files'}},
  };
}
function addSource(f, path, rows, options = {}) {
  const adapter = options.adapter || 'ledger-py-v1';
  const sha = H('file:' + path);
  const descriptor = {adapter, path, sha256: sha, bytes: 123, integrity: adapter === 'native-v4' ? 'byte_hash_only_no_native_chain' : 'verified_chain', ...options};
  delete descriptor.adapterOverride;
  f.replay.sources.push(descriptor);
  const events = rows.map((item, index) => {
    const data = {seq: index + 1, previous: index ? H(path + ':' + index) : ZERO, sha256: H(path + ':' + (index + 1)),
      project_id: 'project', session_id: 'session', run_id: 'run', ...item};
    return {source: path, seq: data.seq, hash: data.sha256, file_sha256: descriptor.sha256, kind: data.kind, data};
  });
  f.replay.events.push(...events);
  return {descriptor, events};
}
const toolStart = (call_id, extra = {}) => ({kind: 'tool.started', call_id, name: 'note_save', arguments: {secret: 'SECRET_ARGUMENT'}, ...extra});
const toolEnd = (call_id, extra = {}) => ({kind: 'tool.finished', call_id, result: {status: 'completed', value: {secret: 'SECRET_RESULT'}}, ...extra});
test('route projection preserves exact Problems, latest notes, failures and explicit obligation links', () => {
  const f = fixture([[problem(), obligation('O'), obligation('U'), note('N', 1, {route_id: 'old'})],
    [note('N', 2, {route_id: 'r1', candidate_for: [ref('O')], gaps: ['registered gap only']}), note('F', 1, {route_id: 'r1', note_type: 'failed_attempt', failure: {observed: 'failed exact', negated_scope: 'local only'}}),
      note('Z', 1), problem('P', 2), note('V', 1, {problem_ref: ref('P', 2), route_id: 'r1'}),
      note('X', 1, {problem_ref: ref('missing'), route_id: 'r1'})]]);
  const graph = build(f.view, f.replay), old = graph.routes.find(group => group.problem.rev === 1), current = graph.routes.find(group => group.problem.rev === 2);
  assert.deepEqual(old.routes.map(route => route.id), ['r1', null]);
  assert.deepEqual(old.routes[0].notes.map(note => note.ref), [ref('N', 2), ref('F')]);
  assert.equal(old.routes[0].notes[1].failure.negated_scope, 'local only');
  assert.deepEqual(old.routes[0].obligations.map(row => row.ref), [ref('O')]);
  assert.deepEqual(old.routes[0].notes[0].gaps, ['registered gap only']);
  assert.equal(old.routes[0].obligations[0].next_action, 'next');
  assert.deepEqual(old.obligations.map(row => row.ref), [ref('U')]);
  assert.deepEqual(current.routes[0].notes.map(row => row.ref), [ref('V')]);
  assert.deepEqual(graph.unresolved_notes.map(row => row.ref), [ref('X')]);
  assert.equal(graph.edges.some(edge => edge.relation === 'route' || edge.relation === 'causes'), false);
  assert.equal(JSON.stringify(graph.routes).includes('不复制全文'), false);
});

const eventNodes = graph => graph.nodes.filter(n => n.kind === 'event');
const recordNodes = graph => graph.nodes.filter(n => n.kind === 'record');
const ledgerNodes = graph => eventNodes(graph).filter(n => n.source.path !== 'research/events.jsonl');
function deepFreeze(value) {
  if (value && typeof value === 'object') { Object.freeze(value); for (const child of Object.values(value)) deepFreeze(child); }
  return value;
}

test('CJS and isolated browser UMD publish exactly build and serialize', () => {
  assert.deepEqual(Object.keys(require('../process-graph.cjs')).sort(), ['build', 'serialize']);
  const source = readFileSync(new URL('../process-graph.cjs', import.meta.url), 'utf8');
  const sandbox = vm.createContext({});
  vm.runInContext('const build = 42; const serialize = 43; const FORMAT = 44;', sandbox);
  vm.runInContext(source, sandbox); vm.runInContext(source, sandbox);
  assert.deepEqual(Object.keys(sandbox.DshMathProcessGraph).sort(), ['build', 'serialize']);
  const f = fixture();
  assert.equal(sandbox.DshMathProcessGraph.serialize(sandbox.DshMathProcessGraph.build(f.view, f.replay)), serialize(build(f.view, f.replay)));
});

test('same inputs serialize deterministically with indent and newline, without mutation', () => {
  const f = fixture([[problem(), note()]]);
  addSource(f, '/explicit/ledger.jsonl', [toolStart('call'), toolEnd('call')]);
  const before = clone(f); deepFreeze(f);
  const a = build(f.view, f.replay), b = build(f.view, f.replay);
  assert.equal(serialize(a), serialize(b)); assert.deepEqual(f, before);
  assert.ok(serialize(a).endsWith('\n')); assert.match(serialize(a), /\n  "counts": \{/);
  assert.deepEqual(JSON.parse(serialize(a)), a);
  const reversedKeys = Object.fromEntries(Object.entries(a).reverse());
  assert.equal(serialize(a), serialize(reversedKeys));
  assert.equal(a.format, 'dsh-math-process-graph-v1');
  assert.deepEqual(Object.keys(a).sort(), ['format', 'project_id', 'project_title', 'research_head', 'research_seq', 'nodes', 'edges', 'sources', 'warnings', 'counts', 'notice', 'routes', 'unresolved_notes'].sort());
});

test('one project root, source grouping, actual multiple changes and precise same-ID revisions', () => {
  const f = fixture([[problem(), note()], [note('N', 2, {summary: '修订二'})]]);
  const graph = build(f.view, f.replay);
  assert.equal(graph.nodes.filter(n => n.parentId === null).length, 1);
  assert.deepEqual(graph.counts, {events: 2, records: 3, omitted: 0});
  const events = eventNodes(graph), records = recordNodes(graph);
  assert.deepEqual(events.map(n => n.source.seq), [1, 2]);
  assert.equal(records[0].parentId, events[0].id); assert.equal(records[1].parentId, events[0].id); assert.equal(records[2].parentId, events[1].id);
  assert.deepEqual(records.map(n => n.ref), [ref('P'), ref('N'), ref('N', 2)]);
  assert.equal(new Set(graph.nodes.map(n => n.id)).size, graph.nodes.length);
  assert.equal(graph.edges.some(e => e.from === events[0].id && e.to === events[1].id && e.relation === 'contains'), false);
  for (const n of graph.nodes) {
    assert.ok(['project', 'source', 'event', 'record'].includes(n.kind));
    assert.ok(['recorded', 'unknown', 'error'].includes(n.status));
    assert.ok(n.facts.every(fact => typeof fact.label === 'string' && typeof fact.value === 'string'));
  }
});

test('research action captions use only verified public products and finite actor categories', () => {
  const f = fixture([[problem(), note()], [note('N', 2, {title: '精确修订标题', summary: '公开修订摘要'})]]);
  f.replay.events[1].data.actor.kind = 'model';
  f.replay.events[1].data.actor.private_reasoning = 'ACTOR_SECRET';
  const graph = build(f.view, f.replay), events = eventNodes(graph);
  assert.equal(events[0].title, '登记 2 个研究产物'); assert.match(events[0].summary, /人类登记/);
  assert.match(events[1].title, /^修订研究笔记 · 精确修订标题$/);
  assert.match(events[1].summary, /模型登记.*公开修订摘要/);
  assert.ok(events[1].facts.some(f => f.label === 'actor.kind' && f.value === 'model'));
  assert.doesNotMatch(serialize(graph), /ACTOR_SECRET/);
  const incompatible = fixture([[problem('P', 1, {}, 3)]], 3);
  incompatible.replay.events[0].data.changes[0].data.title = 'UNVERIFIED_TITLE';
  assert.doesNotMatch(serialize(build(incompatible.view, incompatible.replay)), /UNVERIFIED_TITLE/);
});

test('source-local seq orders events and never compares their timestamps', () => {
  const f = fixture([[problem()], [note()]]);
  addSource(f, '/ledger.jsonl', [toolStart('c'), toolEnd('c')]);
  f.replay.events.reverse(); f.replay.events[0].time = '1900'; f.replay.events.at(-1).time = '2100';
  const graph = build(f.view, f.replay);
  assert.deepEqual(eventNodes(graph).map(n => [n.source.path, n.source.seq]), [
    ['research/events.jsonl', 1], ['research/events.jsonl', 2], ['/ledger.jsonl', 1], ['/ledger.jsonl', 2],
  ]);
});

test('explicit public references are non-contains edges with precise revision targets', () => {
  const review = object('Review', 'R', 1, {target: ref('O'), candidate: ref('N', 2), evidence: [ref('E')], dependencies: [ref('N')], method: 'model', verdict: 'pass', execution: {private: 'NO_REVIEW_EXECUTION'}});
  const f = fixture([[problem(), note(), obligation()], [
    problem('P2', 1, {parent: ref('P'), relation: 'subproblem'}), note('N', 2, {uses: [ref('N')], candidate_for: [ref('O')], evidence: [ref('E')]}),
    evidence('E', {citation: {condition_map: [{obligation: ref('O'), explanation: 'NO_CITATION_BODY'}]}}), review,
  ]]);
  f.view.links = [{from: ref('N', 2), to: ref('E'), relation: 'evidence'}, {from: ref('E'), to: ref('O'), relation: 'condition_gap'}];
  const graph = build(f.view, f.replay), records = recordNodes(graph);
  const find = (name, rev = 1) => records.find(n => n.ref.id === name && n.ref.rev === rev).id;
  for (const [from, rev, to, toRev, relation] of [
    ['P2', 1, 'P', 1, 'parent_problem'], ['N', 2, 'P', 1, 'problem_ref'], ['N', 2, 'N', 1, 'uses'],
    ['N', 2, 'O', 1, 'candidate_for'], ['N', 2, 'E', 1, 'evidence'], ['E', 1, 'O', 1, 'condition_gap'],
    ['R', 1, 'O', 1, 'review_target'], ['R', 1, 'N', 2, 'review_candidate'], ['R', 1, 'N', 1, 'dependencies'],
  ]) assert.equal(graph.edges.filter(e => e.from === find(from, rev) && e.to === find(to, toRev) && e.relation === relation).length, 1);
  assert.equal(records.find(n => n.ref.id === 'R').status, 'recorded');
  assert.doesNotMatch(serialize(graph), /NO_REVIEW_EXECUTION|NO_CITATION_BODY/);
});

test('missing explicit reference warns without inventing a target or guessing latest revision', () => {
  const f = fixture([[problem(), note('N', 1, {uses: [ref('missing'), ref('N', 99)]})]]);
  const graph = build(f.view, f.replay);
  assert.equal(recordNodes(graph).length, 2);
  assert.equal(graph.edges.some(e => e.relation === 'uses'), false);
  assert.ok(graph.warnings.some(w => w.includes('精确 revision')));
});

test('stale replay head always rejects and asks to refresh', () => {
  const f = fixture(); f.replay.research_head = H('stale');
  assert.throws(() => build(f.view, f.replay), /head 不一致.*请刷新/);
});

test('project, campaign and schema identities fail closed', () => {
  for (const change of [
    f => { f.view.snapshot.state.campaign_id = 'other'; },
    f => { f.replay.events[0].data.campaign_id = 'other'; },
    f => { f.replay.project_id = 'other'; },
    f => { f.view.project.schema_version = 5; },
    f => { f.replay.events[0].data.schema_version = 3; },
  ]) { const f = fixture(); change(f); assert.throws(() => build(f.view, f.replay), /过程图/); }
});

test('missing/unusable envelope, incorrect sequence and broken declared chain reject', () => {
  for (const change of [
    f => { f.view.snapshot.usable = false; }, f => { delete f.replay.sources; },
    f => { delete f.replay.events; }, f => { delete f.view.snapshot.state.objects; },
    f => { f.replay.events.pop(); }, f => { f.replay.events[0].seq = 2; },
    f => { f.replay.events[0].hash = H('wrong'); }, f => { f.replay.events[0].data.previous_hash = H('wrong'); },
    f => { delete f.replay.events[0].data.changes; }, f => { f.replay.events[0].data.event_id = ''; },
  ]) { const f = fixture(); change(f); assert.throws(() => build(f.view, f.replay), /过程图/); }
});

test('overview revisions not produced by known changes are disclosed, never faked as events', () => {
  const f = fixture(); const orphan = note();
  f.view.objects.push(clone(orphan)); f.view.snapshot.state.objects.N = [clone(orphan)];
  const graph = build(f.view, f.replay);
  assert.deepEqual(graph.counts, {events: 1, records: 1, omitted: 1});
  assert.equal(graph.sources[0].unmapped_records, 1);
  assert.ok(graph.warnings.some(w => w.includes('未将概览对象伪装')));
});

test('mismatched/duplicate revision and stale overview objects cannot get a clickable ref', () => {
  for (const change of [
    f => { f.replay.events[0].data.changes[0].data.title = 'mismatch'; },
    f => { f.replay.events[0].data.changes.push(clone(f.replay.events[0].data.changes[0])); },
    f => { f.view.objects[0].data.title = 'different snapshot'; },
    f => { f.view.objects.push(clone(f.view.objects[0])); },
  ]) { const f = fixture(); change(f); assert.throws(() => build(f.view, f.replay), /过程图/); }
});

test('foreign project, absent session/run and run-session drift never attach ledger nodes', () => {
  const f = fixture();
  addSource(f, '/foreign.jsonl', [toolStart('x', {project_id: 'different'}), toolEnd('x', {project_id: 'different'})]);
  addSource(f, '/missing.jsonl', [toolStart('y', {session_id: null}), toolEnd('z', {run_id: ''})]);
  addSource(f, '/drift.jsonl', [toolStart('c', {session_id: 'one'}), toolEnd('c', {session_id: 'two'})]);
  const graph = build(f.view, f.replay);
  assert.equal(ledgerNodes(graph).length, 0); assert.equal(graph.counts.omitted, 6);
  assert.ok(graph.warnings.some(w => w.includes('project/session/run')));
});

test('same call ID in different sources, runs and number/string namespaces never mixes', () => {
  const f = fixture();
  addSource(f, '/a.jsonl', [toolStart(1), toolEnd('1'), toolStart('same', {run_id: 'a'}), toolEnd('same', {run_id: 'b'}), toolStart('cross-source')]);
  addSource(f, '/b.jsonl', [toolEnd('cross-source'), toolStart(1), toolEnd(1), toolStart('1'), toolEnd('1')]);
  const graph = build(f.view, f.replay), nodes = ledgerNodes(graph);
  assert.equal(new Set(nodes.map(n => n.id)).size, 10);
  assert.equal(nodes.filter(n => n.status === 'unknown').length, 6);
  assert.equal(graph.edges.filter(e => e.relation === 'same_tool').length, 2);
  for (const e of graph.edges.filter(e => e.relation === 'same_tool')) {
    const a = nodes.find(n => n.id === e.from), b = nodes.find(n => n.id === e.to);
    assert.equal(a.source.path, b.source.path);
    assert.equal(a.facts.find(f => f.label === 'call_id').value, b.facts.find(f => f.label === 'call_id').value);
  }
});

test('request identities are source/run/type isolated from each other and from tool IDs', () => {
  const f = fixture();
  addSource(f, '/requests.jsonl', [
    {kind: 'request.reserved', request_id: 1}, {kind: 'request.finished', request_id: '1'},
    toolStart(1), toolEnd(1), {kind: 'request.finished', request_id: 1, error: null},
    {kind: 'request.reserved', request_id: 'r', run_id: 'r1'}, {kind: 'request.finished', request_id: 'r', run_id: 'r2'},
  ]);
  const graph = build(f.view, f.replay);
  assert.equal(graph.edges.filter(e => e.relation === 'same_request').length, 1);
  assert.equal(graph.edges.filter(e => e.relation === 'same_tool').length, 1);
  assert.equal(ledgerNodes(graph).filter(n => n.status === 'unknown').length, 3);
});

test('unfinished, finish-without-start, duplicate and inverted lifecycles stay unknown', () => {
  const f = fixture();
  addSource(f, '/incomplete.jsonl', [toolStart('pending'), toolEnd('orphan'), toolEnd('inverted'), toolStart('inverted'), toolStart('duplicate'), toolStart('duplicate'), toolEnd('duplicate')]);
  f.replay.runs = [{run_id: 'run', usage_complete: true}]; f.replay.requests = [{request_id: 'fake', outcome: 'finished'}];
  const graph = build(f.view, f.replay);
  assert.ok(ledgerNodes(graph).every(n => n.status === 'unknown'));
  assert.ok(ledgerNodes(graph).every(n => !/成功|运行中|已通过/.test(n.statusLabel)));
  assert.equal(graph.edges.filter(e => e.relation === 'same_tool').length, 0);
  assert.doesNotMatch(serialize(graph), /usage_complete|"fake"|"outcome"/);
});

test('matched finishes only record lifecycle, finite error statuses do not expose error text', () => {
  const f = fixture();
  addSource(f, '/status.jsonl', [toolStart('ok'), toolEnd('ok'), toolStart('error'), toolEnd('error', {result: {status: 'error', message: 'SECRET_ERROR', error: 'SECRET_CODE'}}),
    toolStart('unknown'), toolEnd('unknown', {result: {status: 'SECRET_STATUS', nested: 'SECRET_NESTED'}}),
    {kind: 'request.reserved', request_id: 'r'}, {kind: 'request.finished', request_id: 'r', error: 'SECRET_REQUEST_ERROR'},
    {kind: 'request.reserved', request_id: 'partial'}, {kind: 'request.finished', request_id: 'partial', complete: false},
  ]);
  const graph = build(f.view, f.replay), nodes = ledgerNodes(graph);
  assert.deepEqual(nodes.map(n => n.status), ['recorded', 'recorded', 'error', 'error', 'unknown', 'unknown', 'error', 'error', 'unknown', 'unknown']);
  assert.doesNotMatch(serialize(graph), /SECRET_/);
  assert.ok(nodes[1].statusLabel.includes('非数学通过')); assert.ok(nodes[3].statusLabel.includes('非数学否定'));
});

test('native and audit sources retain only descriptors/counts, never messages or fake chains', () => {
  const f = fixture();
  addSource(f, '/native.jsonl', [{kind: 'session', message: 'NATIVE_SECRET'}, {kind: 'tool.finished', result: 'NATIVE_RESULT_SECRET', private_reasoning: 'PRIVATE_REASONING'}], {adapter: 'native-v4', integrity: 'verified_chain'});
  addSource(f, '/audit.jsonl', [{kind: 'service.frame', args: 'FRAME_SECRET'}, {kind: 'request.finished', message: 'AUDIT_SECRET'}], {adapter: 'audit-js-v1'});
  const graph = build(f.view, f.replay);
  assert.equal(ledgerNodes(graph).length, 0); assert.equal(graph.counts.omitted, 4);
  const native = graph.sources.find(s => s.path === '/native.jsonl');
  assert.equal(native.integrity, 'byte_hash_only_no_native_chain'); assert.equal(native.file_sha256, H('file:/native.jsonl'));
  assert.equal(native.omitted, 2); assert.equal(native.events, 0); assert.equal(own(native, 'hash'), false);
  assert.doesNotMatch(serialize(graph), /NATIVE_SECRET|NATIVE_RESULT_SECRET|PRIVATE_REASONING|FRAME_SECRET|AUDIT_SECRET/);
  assert.ok(graph.warnings.some(w => w.includes('不是事件链')));
});
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

test('tool arguments/result payload, nested allowed-looking fields and credentials never leak', () => {
  const f = fixture([[problem(), note('N', 1, {body: 'PRIVATE_BODY', private_reasoning: 'RESEARCH_PRIVATE_REASONING', credentials: 'RESEARCH_CREDENTIAL'})]]);
  const nested = {name: 'NESTED_NAME_SECRET', status: 'completed', title: 'NESTED_TITLE_SECRET', summary: 'NESTED_SUMMARY_SECRET', id: 'NESTED_ID_SECRET', rev: 1,
    message: 'NESTED_MESSAGE_SECRET', private_reasoning: 'NESTED_REASONING_SECRET', credentials: 'NESTED_CREDENTIAL_SECRET'};
  addSource(f, '/safe.jsonl', [toolStart('call', {arguments: nested, credentials: 'TOP_CREDENTIAL_SECRET'}), toolEnd('call', {result: {status: 'completed', value: nested, message: 'TOP_MESSAGE_SECRET'}}),
    {kind: 'service.frame', frame: nested}, {kind: 'future.event', ...nested},
  ]);
  const graph = build(f.view, f.replay), saved = serialize(graph);
  assert.doesNotMatch(saved, /SECRET|PRIVATE_BODY|RESEARCH_PRIVATE_REASONING|RESEARCH_CREDENTIAL/);
  assert.match(saved, /公开研究摘要/); assert.equal(graph.counts.omitted, 2);
  assert.ok(ledgerNodes(graph).at(-1).facts.some(f => f.label === '结果类型（无原文）' && f.value === 'object'));
});

test('external result bodies and unsupported native data are not even recursively visited', () => {
  const f = fixture();
  const body = {}; Object.defineProperty(body, 'status', {enumerable: true, get() { throw new Error('private body inspected'); }});
  addSource(f, '/safe.jsonl', [toolStart('call'), toolEnd('call', {result: {status: 'completed', value: body}})]);
  const source = addSource(f, '/native.jsonl', [{kind: 'message'}], {adapter: 'native-v4'});
  Object.defineProperty(source.events[0], 'data', {get() { throw new Error('native data inspected'); }});
  const graph = build(f.view, f.replay);
  assert.equal(ledgerNodes(graph).length, 2); assert.equal(graph.counts.omitted, 1);
});

test('unselected, unknown and unsupported sources/events disclose omissions without fabricated data', () => {
  const f = fixture([[problem()], [note()]]);
  f.replay.events[1].kind = 'research.future'; f.replay.events[1].data.kind = 'future';
  addSource(f, '/future.jsonl', [{kind: 'tool.started', call_id: 'c', arguments: 'SECRET'}], {adapter: 'future-v9'});
  addSource(f, '/unverified.jsonl', [toolStart('c')], {integrity: 'unknown'});
  addSource(f, '/known.jsonl', [{kind: 'future.kind', secret: 'SECRET'}, toolStart('ok')]);
  f.replay.events.push({source: '/not-selected.jsonl', kind: 'tool.started', data: {secret: 'SECRET'}});
  const graph = build(f.view, f.replay);
  assert.deepEqual(graph.counts, {events: 2, records: 1, omitted: 6});
  assert.ok(graph.warnings.some(w => w.includes('未知研究事件')));
  assert.ok(graph.warnings.some(w => w.includes('不受支持')));
  assert.ok(graph.warnings.some(w => w.includes('无显式来源描述')));
  assert.doesNotMatch(serialize(graph), /"SECRET"|future-v9/);
});

test('external source paths, row hashes and whole-file SHA remain separate locators', () => {
  const f = fixture(); const {events} = addSource(f, '/explicit/a.jsonl', [toolStart('c'), toolEnd('c')]);
  const graph = build(f.view, f.replay), n = ledgerNodes(graph)[0];
  assert.equal(n.source.path, '/explicit/a.jsonl'); assert.equal(n.source.seq, 1);
  assert.equal(n.source.hash, events[0].hash); assert.equal(n.source.file_sha256, H('file:/explicit/a.jsonl'));
  assert.notEqual(n.source.hash, n.source.file_sha256);
  assert.equal(graph.sources[0].file_sha256, undefined);
  assert.equal(recordNodes(graph)[0].source.event_id, 'event-1');
});

test('missing/mismatched ledger locator fields and invalid identities are explicitly omitted', () => {
  const f = fixture();
  const {events} = addSource(f, '/broken.jsonl', [toolStart('a'), toolStart('b'), toolStart('c'), toolStart(null), toolStart({value: 'd'})]);
  delete events[0].file_sha256; events[1].hash = H('wrong'); events[2].seq = 20;
  const graph = build(f.view, f.replay);
  assert.equal(ledgerNodes(graph).length, 0); assert.equal(graph.counts.omitted, 5);
  assert.ok(graph.warnings.some(w => w.includes('seq/hash/文件 SHA')));
  assert.ok(graph.warnings.some(w => w.includes('identity')));
});

test('duplicate source paths reject, duplicate ledger seq is omitted, conflicting tool names stay unknown', () => {
  const duplicate = fixture(); addSource(duplicate, '/same.jsonl', []); addSource(duplicate, '/same.jsonl', []);
  assert.throws(() => build(duplicate.view, duplicate.replay), /来源路径重复/);
  const f = fixture();
  addSource(f, '/seq.jsonl', [toolStart('same', {seq: 1}), toolEnd('same', {seq: 1}), toolStart('conflict'), toolEnd('conflict', {name: 'file_write'})]);
  const graph = build(f.view, f.replay);
  assert.equal(graph.counts.omitted, 2); assert.equal(ledgerNodes(graph).length, 2);
  assert.ok(ledgerNodes(graph).every(n => n.status === 'unknown'));
  assert.ok(graph.warnings.some(w => w.includes('名称冲突')));
});

test('control/recovery are real recorded events without fabricated revisions or raw control text', () => {
  const f = fixture([[problem()], [], []]);
  for (const [index, kind] of [[1, 'control'], [2, 'recovery']]) {
    f.replay.events[index].kind = 'research.' + kind;
    f.replay.events[index].data.kind = kind;
    f.replay.events[index].data.control = {raw: 'SECRET_CONTROL'};
    f.replay.events[index].data.recovery = {message: 'SECRET_RECOVERY'};
  }
  const graph = build(f.view, f.replay);
  assert.deepEqual(graph.counts, {events: 3, records: 1, omitted: 0});
  assert.deepEqual(eventNodes(graph).slice(1).map(n => n.title), ['研究控制记录', '日志恢复记录']);
  assert.doesNotMatch(serialize(graph), /SECRET_CONTROL|SECRET_RECOVERY/);
});

test('public text is kind-specific, review payload and offline running status do not become live output', () => {
  const review = object('Review', 'R', 1, {method: 'program', verdict: 'pass', target: ref('N'), candidate: null, evidence: [], dependencies: [], title: 'REVIEW_TITLE_SECRET', summary: 'REVIEW_SUMMARY_SECRET', statement: 'REVIEW_STATEMENT_SECRET'});
  const run = object('Run', 'RUN', 1, {mode: 'offline_fixture', status: 'running', title: 'RUN_TITLE_SECRET', summary: 'RUN_SUMMARY_SECRET', usage: {private: 'RUN_USAGE_SECRET'}});
  const f = fixture([[problem(), note(), review, run]]);
  const graph = build(f.view, f.replay), saved = serialize(graph);
  assert.doesNotMatch(saved, /SECRET|"running"/);
  assert.equal(recordNodes(graph).find(n => n.ref.id === 'RUN').status, 'recorded');
  assert.equal(recordNodes(graph).find(n => n.ref.id === 'RUN').title, '离线运行记录');
  assert.equal(recordNodes(graph).find(n => n.ref.id === 'R').summary, '');
});

test('unknown object types and missing tool names are disclosed without arbitrary text leakage', () => {
  const unknown = object('FutureRecord', 'F', 1, {summary: 'FUTURE_SECRET'});
  const f = fixture([[problem(), unknown]]);
  addSource(f, '/unnamed.jsonl', [toolStart('x', {name: null}), toolEnd('x')]);
  const graph = build(f.view, f.replay);
  assert.equal(recordNodes(graph).length, 1); assert.equal(graph.counts.omitted, 1);
  assert.ok(graph.warnings.some(w => w.includes('未知研究对象类型')));
  assert.ok(graph.warnings.some(w => w.includes('缺少有效名称')));
  assert.equal(eventNodes(graph)[0].status, 'unknown');
  assert.doesNotMatch(serialize(graph), /FUTURE_SECRET/);
});

test('no external selections means uncollected, and operation zero is not session zero', () => {
  const f = fixture(); f.replay.operation_model_requests = 0; f.replay.session_model_requests = 123;
  const graph = build(f.view, f.replay);
  assert.ok(graph.warnings.some(w => w.includes('未采集') && w.includes('请求总数未知')));
  assert.match(graph.notice, /非实时/); assert.match(graph.notice, /非内部思维链/); assert.match(graph.notice, /非审核证据/);
  assert.match(graph.notice, /本次读取不调用模型/); assert.match(graph.notice, /整个会话模型请求总数仍未知/);
  assert.equal(own(graph, 'session_model_requests'), false); assert.equal(own(graph, 'operation_model_requests'), false);
});

test('explicit research origin correlates only unique matching run/session/typed call, never by time', () => {
  const f = fixture();
  f.replay.events[0].data.origin = {source: 'dsh-math-model', run_id: 'run', session_id: 'session', tool_call_id: 'call'};
  addSource(f, '/one.jsonl', [toolStart('call'), toolEnd('call')]);
  let graph = build(f.view, f.replay);
  assert.equal(graph.edges.filter(e => e.relation === 'origin_tool').length, 1);
  addSource(f, '/two.jsonl', [toolStart('call'), toolEnd('call')]);
  graph = build(f.view, f.replay);
  assert.equal(graph.edges.filter(e => e.relation === 'origin_tool').length, 0);
  assert.ok(graph.warnings.some(w => w.includes('未猜测来源关联')));
  f.replay.events[0].data.origin.session_id = 'other-session';
  assert.equal(build(f.view, f.replay).edges.filter(e => e.relation === 'origin_tool').length, 0);
});

test('large projections keep all nodes and serialize all records, only excerpts are shortened', () => {
  const notes = Array.from({length: 1200}, (_, i) => note('N' + i, 1, {summary: '公开内容'.repeat(150)}));
  const f = fixture([[problem(), ...notes]]);
  const graph = build(f.view, f.replay);
  assert.deepEqual(graph.counts, {events: 1, records: 1201, omitted: 0});
  assert.equal(recordNodes(graph).length, 1201);
  assert.equal(JSON.parse(serialize(graph)).nodes.filter(n => n.kind === 'record').length, 1201);
  assert.equal(recordNodes(graph).at(-1).ref.id, 'N1199');
  assert.ok(recordNodes(graph).at(-1).summary.endsWith('…（节选）'));
});

test('schema 3 readonly maps exact changes and clearly labels compatibility', () => {
  const f = fixture([[problem('P', 1, {}, 3), note('N', 1, {}, 3)]], 3);
  const graph = build(f.view, f.replay);
  assert.deepEqual(recordNodes(graph).map(n => n.ref), [ref('P'), ref('N')]);
  assert.ok(graph.warnings.some(w => w.includes('schema 3 只读兼容')));
  assert.equal(graph.counts.omitted, 0);
});

test('schema 3 unmappable changes are omitted with a compatibility warning, never invented', () => {
  const f = fixture([[problem('P', 1, {}, 3)]], 3);
  f.replay.events[0].data.changes[0].data.title = 'not the exact overview';
  const graph = build(f.view, f.replay);
  assert.equal(recordNodes(graph).length, 0); assert.equal(graph.counts.omitted, 1);
  assert.ok(graph.warnings.some(w => w.includes('schema 3 兼容受限')));
});

test('empty exact snapshot is stable and invalid serialized format is rejected', () => {
  const f = fixture([]); const graph = build(f.view, f.replay);
  assert.deepEqual(graph.counts, {events: 0, records: 0, omitted: 0});
  assert.equal(graph.nodes.length, 2); assert.equal(graph.research_head, ZERO);
  assert.throws(() => serialize({format: 'audit-bundle'}), /不是受支持的过程图/);
});
