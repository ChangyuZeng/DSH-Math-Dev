// Synthetic in-memory APIs, deterministic timers, and a mock hook renderer only.
// These assert asynchronous behavior, not browser layout, actual focus, or KaTeX output.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const F = require('../forms.cjs');
const descriptor = require('../project-fields.json');
const ProjectUI = require('../project-form.cjs');
const Workbench = require('../workbench-ui.cjs');
const copy = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
let sequence = 0;
const unique = () => `synthetic-${++sequence}`;
function clock() {
  let now = 0, id = 0; const jobs = new Map();
  return {setTimer(fn, ms) { const key = ++id; jobs.set(key, {fn, at: now + ms}); return key; }, clearTimer(key) { jobs.delete(key); },
    get size() { return jobs.size; }, async advance(ms) { const end = now + ms;
      while (true) { const next = [...jobs].filter(([, job]) => job.at <= end).sort((a, b) => a[1].at - b[1].at)[0]; if (!next) break;
        now = next[1].at; jobs.delete(next[0]); next[1].fn(); await settle(); }
      now = end; await settle();
    }};
}
function pad(rev, title = '合成标题', body = `合成正文 v${rev}`) {
  return {format: 'dsh-math-scratchpad-v1', id: 'main', project_id: 'synthetic-project', rev, title, body, saved_at: `fixture-${rev}`, actor: 'human', origin: {source: 'fixture'}};
}
function scratchServer(initial = null) {
  let current = initial; const versions = new Map(initial ? [[initial.rev, initial]] : []), operations = new Map(), calls = [];
  const value = rev => ({scratchpad: (rev == null ? current : versions.get(rev)) || null, history: [...versions.values()].map(({rev, title, saved_at, actor}) => ({rev, title, saved_at, actor}))});
  const request = async (action, args) => {
    calls.push({action, args: copy(args)});
    if (action === 'scratchpad-read') return copy(value(args.rev));
    const prior = operations.get(args.operation_id);
    if (prior) { assert.deepEqual({action, args}, prior.input); return copy(prior.output); }
    if (args.expected_rev !== (current?.rev || 0)) throw Error('REV_CONFLICT: fixture changed');
    if (action === 'scratchpad-save') current = {...pad((current?.rev || 0) + 1, args.title, args.body), project_id: args.project_id};
    else if (action === 'scratchpad-restore') { const source = versions.get(args.restore_rev); assert.ok(source); current = {...source, rev: current.rev + 1}; }
    else throw Error(`Unexpected action: ${action}`);
    versions.set(current.rev, copy(current)); const output = copy(value());
    operations.set(args.operation_id, {input: copy({action, args}), output}); return output;
  };
  return {request, calls, get: () => copy(value()), replace(next) { current = copy(next); versions.set(next.rev, copy(next)); }};
}
function scratch(t, server, extra = {}) {
  const timer = clock(); const control = Workbench.createScratchpadController({sessionId: unique(), projectId: 'synthetic-project', writable: true,
    request: server.request, uuid: unique, ...timer, ...extra});
  t.after(() => control.dispose()); return {control, timer};
}
const completeFields = () => ({...F.projectDefaults(descriptor), title: '合成目标', statement: 'synthetic statement\n  exact\n', quantifiers: 'forall synthetic n', domain: 'synthetic domain',
  completion_criteria: ['明确的合成检查条件'], sources: ['synthetic://fixture']});
function draftServer(initialFields = null) {
  let current = initialFields ? {id: unique(), session_id: 'synthetic-session', rev: 1, fields: copy(initialFields), status: 'draft', committed_project_id: null} : null;
  const operations = new Map(), calls = [];
  const value = () => ({draft: copy(current), validation: current ? F.projectValidation(descriptor, current.fields) : null});
  const request = async (action, args) => {
    calls.push({action, args: copy(args)});
    if (action === 'project-draft-read') return value();
    const prior = operations.get(args.operation_id);
    if (prior) { assert.deepEqual({action, args}, prior.input); return copy(prior.output); }
    if (args.draft_id && (args.draft_id !== current?.id || args.expected_rev !== current?.rev)) throw Error('REV_CONFLICT: draft changed');
    let output;
    if (action === 'project-draft-save') {
      current = {id: args.draft_id || unique(), session_id: args.session_id, rev: args.draft_id ? current.rev + 1 : 1,
        fields: {...(args.draft_id ? current.fields : {}), ...copy(args.patch)}, status: 'draft', committed_project_id: null}; output = value();
    } else if (action === 'finalize-draft') {
      assert.equal(F.projectValidation(descriptor, current.fields).complete, true);
      const project = {id: `project-${current.id}`, execution_authorized: false}; current = {...current, rev: current.rev + 1, status: 'committed', committed_project_id: project.id}; output = {project, draft: copy(current)};
    } else throw Error(`Unexpected action: ${action}`);
    operations.set(args.operation_id, {input: copy({action, args}), output: copy(output)}); return output;
  };
  return {request, calls, get: value, replace(fields) { current = {...current, rev: (current?.rev || 0) + 1, fields: copy(fields)}; }};
}
function project(t, server, extra = {}) {
  const control = ProjectUI.createController({request: server.request, sessionId: unique(), descriptor, F, uuid: unique, ...extra});
  t.after(() => control.dispose()); return control;
}
const writes = server => server.calls.filter(call => call.action !== 'scratchpad-read' && call.action !== 'project-draft-read');

test('scratchpad debounce saves exact text after 800ms and coalesces rapid edits', async t => {
  const server = scratchServer(), {control, timer} = scratch(t, server); await control.load();
  const body = '  $x\\le y$\r\n中文 😀\n\n';
  control.edit('body', 'first'); await timer.advance(799); assert.equal(writes(server).length, 0);
  control.edit('title', '标题\r\n'); control.edit('body', body); await timer.advance(799); assert.equal(writes(server).length, 0);
  await timer.advance(1); assert.equal(writes(server).length, 1); assert.equal(writes(server)[0].args.expected_rev, 0);
  assert.equal(server.get().scratchpad.body, body); assert.equal(server.get().scratchpad.title, '标题\r\n'); assert.equal(control.get().dirty, false);
  assert.equal(timer.size, 0);
});

test('typing during an in-flight autosave remains dirty and saves serially against the new revision', async t => {
  const server = scratchServer(pad(2)), gate = deferred(); let first = true;
  const {control, timer} = scratch(t, server, {request: async (action, args) => { if (action === 'scratchpad-save' && first) { first = false; await gate.promise; } return server.request(action, args); }});
  await control.load(); control.edit('body', 'first payload'); await timer.advance(800); assert.equal(control.get().busy, true);
  const same = control.save(); control.edit('body', 'newer local\n exact'); await timer.advance(5000); assert.equal(writes(server).length, 0);
  gate.resolve(); await same; assert.equal(control.get().body, 'newer local\n exact'); assert.equal(control.get().pad.body, 'first payload'); assert.equal(control.get().dirty, true);
  await timer.advance(799); assert.equal(writes(server).length, 1); await timer.advance(1);
  assert.deepEqual(writes(server).map(call => call.args.expected_rev), [2, 3]); assert.equal(server.get().scratchpad.body, 'newer local\n exact'); assert.equal(control.get().dirty, false);
});

test('flush waits for pending save and includes subsequent edits before navigation or promotion', async t => {
  const server = scratchServer(), gate = deferred(); let hold = true;
  const {control} = scratch(t, server, {request: async (action, args) => { if (action === 'scratchpad-save' && hold) { hold = false; await gate.promise; } return server.request(action, args); }});
  await control.load(); control.edit('body', 'v1'); const save = control.save(); control.edit('body', 'v2');
  let finished = false; const leave = control.canLeave().then(result => { finished = true; return result; }); await settle(); assert.equal(finished, false);
  gate.resolve(); await save; assert.equal(await leave, true); assert.equal(writes(server).length, 2); assert.equal(server.get().scratchpad.body, 'v2');
});

test('scratch conflict retains local bytes across project/session unmount and needs explicit rebase', async t => {
  const server = scratchServer(pad(1)), sessionId = unique(), projectId = 'conflict-fixture';
  const {control, timer} = scratch(t, server, {sessionId, projectId}); await control.load(); control.edit('body', '  retained local\r\n');
  server.replace(pad(2, 'remote', 'external change')); await timer.advance(800);
  assert.match(control.get().error, /REV_CONFLICT/); assert.equal(control.get().dirty, true); assert.equal(control.get().body, '  retained local\r\n');
  assert.equal(await control.canLeave(), false); await timer.advance(10000); assert.equal(writes(server).length, 2, 'No unbounded auto retry after conflict');
  await control.load(); assert.equal(control.get().incoming.scratchpad.rev, 2); assert.equal(control.get().body, '  retained local\r\n'); control.dispose();
  const other = scratch(t, scratchServer(), {sessionId: unique(), projectId}).control; await other.load(); assert.equal(other.get().body, '');
  const {control: reopened, timer: reopenedTimer} = scratch(t, server, {sessionId, projectId}); await reopened.load();
  assert.equal(reopened.get().body, '  retained local\r\n'); assert.ok(reopened.get().incoming); reopened.useIncoming(true);
  await reopenedTimer.advance(5000); assert.equal(writes(server).length, 2, 'Choosing a baseline alone is not a write');
  assert.equal(await reopened.flush(), true); assert.equal(writes(server).at(-1).args.expected_rev, 2); assert.equal(server.get().scratchpad.body, '  retained local\r\n');
});

test('ambiguous scratch save retries the identical operation before saving newer text', async t => {
  const server = scratchServer(); let lose = true; const attempted = [];
  const {control} = scratch(t, server, {request: async (action, args) => { if (action === 'scratchpad-save') attempted.push(copy(args)); const value = await server.request(action, args);
    if (action === 'scratchpad-save' && lose) { lose = false; throw Error('network disconnected after write'); } return value; }});
  await control.load(); control.edit('body', 'first'); assert.equal(await control.save(), false); control.edit('body', 'latest');
  assert.equal(await control.flush(), true); assert.deepEqual(attempted[0], attempted[1]); assert.notEqual(attempted[1].operation_id, attempted[2].operation_id);
  assert.equal(server.get().scratchpad.rev, 2); assert.equal(server.get().scratchpad.body, 'latest'); assert.equal(control.get().attempt, null);
});

test('history reads cannot replace current text and restore creates a new CAS revision', async t => {
  const server = scratchServer(pad(1, 'old title', 'old body')), {control} = scratch(t, server); await control.load();
  control.edit('body', 'new current'); await control.flush(); await control.history(1);
  assert.equal(control.get().preview.body, 'old body'); assert.equal(control.get().body, 'new current');
  assert.equal(await control.restore(1), true); assert.equal(control.get().pad.rev, 3); assert.equal(control.get().body, 'old body');
  assert.equal(control.get().history.length, 3); const request = writes(server).at(-1);
  assert.equal(request.action, 'scratchpad-restore'); assert.equal(request.args.expected_rev, 2); assert.equal(request.args.restore_rev, 1);
  control.edit('body', 'unsaved'); assert.equal(await control.restore(2), false); assert.equal(writes(server).length, 2);
});

test('retrying an ambiguous restore never overwrites text typed after its initial attempt', async t => {
  const server = scratchServer(pad(1, 'old', 'old text')); server.replace(pad(2, 'current', 'current text'));
  let lose = true; const {control} = scratch(t, server, {request: async (action, args) => { const value = await server.request(action, args);
    if (action === 'scratchpad-restore' && lose) { lose = false; throw Error('lost restore reply'); } return value; }});
  await control.load(); assert.equal(await control.restore(1), false); control.edit('body', 'typed after failed restore');
  assert.equal(await control.save(), true); assert.equal(control.get().body, 'typed after failed restore'); assert.equal(control.get().pad.body, 'old text'); assert.equal(control.get().dirty, true);
  await control.flush(); assert.equal(control.get().pad.rev, 4); assert.equal(control.get().body, 'typed after failed restore');
  const restores = writes(server).filter(call => call.action === 'scratchpad-restore'); assert.deepEqual(restores[0].args, restores[1].args);
});

test('older reads and history requests cannot regress a saved scratch revision', async t => {
  const server = scratchServer(pad(1)), late = deferred(), history = deferred(); let slowRead = false;
  const {control} = scratch(t, server, {request: async (action, args) => {
    if (action === 'scratchpad-read' && args.rev === 1) return history.promise;
    if (action === 'scratchpad-read' && slowRead) return late.promise;
    return server.request(action, args);
  }});
  await control.load(); slowRead = true; const read = control.load(), preview = control.history(1);
  control.edit('body', 'revision 2'); await control.save(); late.resolve({scratchpad: pad(1), history: []}); history.resolve({scratchpad: pad(1), history: []});
  assert.equal(await read, false); assert.equal(await preview, false); assert.equal(control.get().pad.rev, 2); assert.equal(control.get().body, 'revision 2'); assert.equal(control.get().preview, null);
});

test('scratch unmount cancels debounce and suppresses response notifications and follow-up writes', async t => {
  const server = scratchServer(), late = deferred(); let notices = 0;
  const {control, timer} = scratch(t, server, {notify: () => notices++, request: async (action, args) => action === 'scratchpad-save' ? late.promise : server.request(action, args)});
  await control.load(); control.edit('body', 'local'); const save = control.save(); control.edit('body', 'newer'); control.dispose(); const atUnmount = notices;
  late.resolve({scratchpad: pad(1), history: []}); assert.equal(await save, false); await timer.advance(5000);
  assert.equal(notices, atUnmount); assert.equal(writes(server).length, 0); assert.equal(timer.size, 0);
  const second = scratch(t, server); await second.control.load(); second.control.edit('body', 'pending timer'); second.control.dispose(); await second.timer.advance(800); assert.equal(writes(server).length, 0);
});

test('readonly legacy scratch permits reads/history but never save/restore/promotion flush', async t => {
  const server = scratchServer(pad(1)), {control, timer} = scratch(t, server, {writable: false}); await control.load();
  control.edit('body', 'illegal'); assert.equal(control.get().body, pad(1).body); assert.equal(await control.save(), false); assert.equal(await control.flush(), false);
  assert.equal(await control.restore(1), false); assert.equal(await control.history(1), true); await timer.advance(5000); assert.equal(writes(server).length, 0);
});

test('same-session project draft loads on open and dirty refresh cannot overwrite local fields', async t => {
  const server = draftServer(completeFields()), sessionId = unique(), control = project(t, server, {sessionId}); await control.load();
  assert.equal(server.calls[0].args.session_id, sessionId); control.edit('title', 'local\n exact'); server.replace({...completeFields(), title: 'chat update'}); await control.load();
  assert.equal(control.get().fields.title, 'local\n exact'); assert.equal(control.get().incoming.draft.fields.title, 'chat update'); control.dispose();
  const reopened = project(t, server, {sessionId}); await reopened.load(); assert.equal(reopened.get().fields.title, 'local\n exact'); reopened.useIncoming(true);
  assert.equal(reopened.get().draft.rev, 2); assert.equal(reopened.get().fields.title, 'local\n exact'); assert.equal(await reopened.save(), true);
  assert.equal(writes(server).at(-1).args.expected_rev, 2);
  const different = project(t, draftServer(), {sessionId: unique()}); await different.load(); assert.equal(different.get().fields.title, '');
});

test('manual new follows draft-save then user finalize and incomplete content creates no project', async t => {
  const server = draftServer(), control = project(t, server); await control.load(); control.edit('title', 'partial synthetic');
  assert.equal(await control.finalize(), false); assert.deepEqual(writes(server).map(call => call.action), ['project-draft-save']); assert.equal(control.get().validation.complete, false);
  for (const [key, value] of Object.entries(completeFields())) control.edit(key, value);
  assert.equal(await control.save(), true); assert.equal(writes(server).some(call => call.action === 'finalize-draft'), false);
  const result = await control.finalize(); assert.equal(result.execution_authorized, false);
  const calls = writes(server), lastSave = calls.at(-2), finalize = calls.at(-1);
  assert.equal(lastSave.action, 'project-draft-save'); assert.equal(finalize.action, 'finalize-draft'); assert.equal(finalize.args.draft_id, control.get().draft.id);
  assert.equal(finalize.args.expected_rev, control.get().draft.rev - 1); assert.equal('parent' in lastSave.args.patch, false);
});

test('double finalize is rejected synchronously and edits during save require another explicit confirmation', async t => {
  const server = draftServer(completeFields()), gate = deferred(); let hold = true;
  const control = project(t, server, {request: async (action, args) => { if (action === 'project-draft-save' && hold) { hold = false; await gate.promise; } return server.request(action, args); }});
  await control.load(); const one = control.finalize(); assert.equal(await control.finalize(), false); control.edit('title', 'edited while saving'); gate.resolve();
  assert.equal(await one, false); assert.equal(control.get().fields.title, 'edited while saving'); assert.equal(control.get().dirty, true);
  assert.equal(writes(server).filter(call => call.action === 'finalize-draft').length, 0); assert.match(control.get().error, /内容已修改/);
  assert.ok(await control.finalize()); assert.equal(writes(server).filter(call => call.action === 'finalize-draft').length, 1);
});

test('ambiguous finalize retries identical identity without saving again and locks draft edits', async t => {
  const server = draftServer(completeFields()); let lose = true;
  const control = project(t, server, {request: async (action, args) => { const value = await server.request(action, args);
    if (action === 'finalize-draft' && lose) { lose = false; throw Error('finalize response lost'); } return value; }});
  await control.load(); assert.equal(await control.finalize(), false); assert.ok(control.get().finalizeAttempt); control.edit('title', 'must not mutate finalized draft');
  assert.equal(control.get().fields.title, completeFields().title); const projectResult = await control.finalize(); assert.ok(projectResult.id);
  const finalizes = writes(server).filter(call => call.action === 'finalize-draft'); assert.deepEqual(finalizes[0].args, finalizes[1].args);
  assert.equal(writes(server).filter(call => call.action === 'project-draft-save').length, 1);
});

test('persistent pending finalize survives remount, locks edits, and only explicit confirmation retries', async t => {
  const fields = completeFields(), snapshot = {draft: {id: 'durable-draft', rev: 9, fields, status: 'draft'}, validation: {complete: true},
    pending_finalization: {operation_id: 'durable-original-operation', expected_rev: 9}};
  const calls = [], sessionId = unique(), request = async (action, args) => { calls.push({action, args: copy(args)});
    if (action === 'project-draft-read') return copy(snapshot);
    assert.equal(action, 'finalize-draft'); return {project: {id: 'durable-project'}, draft: {...snapshot.draft, rev: 10, status: 'committed'}};
  };
  const original = project(t, {request}, {sessionId}); await original.load(); original.dispose();
  const reopened = project(t, {request}, {sessionId}); await reopened.load(); reopened.edit('title', 'must not change'); reopened.fresh();
  assert.equal(reopened.get().fields.title, fields.title); assert.equal(calls.filter(call => call.action !== 'project-draft-read').length, 0);
  assert.equal(await reopened.save(), false); assert.equal(calls.filter(call => call.action !== 'project-draft-read').length, 0);
  assert.equal((await reopened.finalize()).id, 'durable-project');
  assert.deepEqual(calls.at(-1), {action: 'finalize-draft', args: {session_id: sessionId, draft_id: 'durable-draft', expected_rev: 9, operation_id: 'durable-original-operation'}});
  // Also simulate total window-state loss: a brand-new controller key learns only from the server read.
  const reloaded = project(t, {request}); await reloaded.load(); assert.equal(reloaded.get().finalizeAttempt.operation_id, 'durable-original-operation');
});

test('a pending freeze discovered while dirty preserves local edits as a separate new draft after recovery', async t => {
  const server = draftServer(completeFields()); let pending = false; const sessionId = unique();
  const control = project(t, server, {sessionId, request: async (action, args) => {
    if (action === 'project-draft-read') return {...server.get(), pending_finalization: pending ? {operation_id: 'remote-confirm', expected_rev: 1} : null};
    return server.request(action, args);
  }});
  await control.load(); control.edit('title', 'unsaved local should survive'); pending = true; await control.load();
  assert.equal(control.get().fields.title, 'unsaved local should survive'); assert.equal(control.get().finalizeAttempt.operation_id, 'remote-confirm');
  assert.ok(await control.finalize()); assert.equal(control.get().draft, null); assert.equal(control.get().dirty, true); assert.equal(control.get().fields.title, 'unsaved local should survive');
  control.dispose(); const reopened = project(t, server, {sessionId}); assert.equal(reopened.get().fields.title, 'unsaved local should survive');
});

test('project failed save retains bytes and retries original request before changed content', async t => {
  const server = draftServer(); let lose = true;
  const control = project(t, server, {request: async (action, args) => { const value = await server.request(action, args);
    if (action === 'project-draft-save' && lose) { lose = false; throw Error('lost save reply'); } return value; }});
  await control.load(); control.edit('statement', 'first\r\n'); assert.equal(await control.save(), false); control.edit('statement', 'latest\r\n exact');
  assert.equal(await control.save(), false); assert.equal(writes(server).length, 1); assert.equal(control.get().fields.statement, 'latest\r\n exact');
  assert.equal(await control.retrySave(), true); assert.deepEqual(writes(server)[0].args, writes(server)[1].args); assert.equal(control.get().dirty, true);
  assert.equal(await control.canLeave(), true); assert.equal(server.get().draft.fields.statement, 'latest\r\n exact'); assert.equal(control.get().dirty, false);
});

test('project stale read cannot overwrite saved revision and unmount prevents finalize follow-up', async t => {
  const server = draftServer(completeFields()), late = deferred(); let slow = false;
  const control = project(t, server, {request: async (action, args) => action === 'project-draft-read' && slow ? late.promise : server.request(action, args)});
  await control.load(); const original = server.get(); slow = true; const read = control.load(); control.edit('title', 'saved latest'); await control.save(); late.resolve(original);
  assert.equal(await read, false); assert.equal(control.get().fields.title, 'saved latest'); assert.equal(control.get().draft.rev, 2);
  const gate = deferred(); let updates = 0; const unmounted = project(t, draftServer(completeFields()), {notify: () => updates++, request: async action => action === 'project-draft-read' ? original : gate.promise});
  await unmounted.load(); const finalize = unmounted.finalize(); unmounted.dispose(); const count = updates; gate.resolve(original); assert.equal(await finalize, false); assert.equal(updates, count);
});

test('import is preview-first, binds exact fingerprint, and does not repeat writes after success', async t => {
  const calls = [], gate = deferred();
  const control = Workbench.createImportController({sessionId: unique(), uuid: unique, request: async (action, args) => {
    calls.push({action, args: copy(args)}); if (action === 'import-legacy-preview') return {preview: {fingerprint: `fingerprint-${args.campaign}`, schema_version: 3}}; return gate.promise;
  }}); t.after(() => control.dispose()); control.edit('/synthetic/legacy'); assert.equal(await control.confirm(), false); assert.equal(calls.length, 0);
  assert.equal(await control.preview(), true); assert.deepEqual(calls.map(call => call.action), ['import-legacy-preview']);
  const confirm = control.confirm(); assert.equal(await control.confirm(), false); assert.equal(calls.length, 2);
  assert.equal(calls[1].args.expected_fingerprint, 'fingerprint-/synthetic/legacy'); gate.resolve({id: 'imported-fixture', mode: 'legacy_readonly_copy'});
  assert.equal((await confirm).id, 'imported-fixture'); assert.equal((await control.confirm()).id, 'imported-fixture'); assert.equal(calls.length, 2);
  control.edit('/synthetic/other'); assert.equal(control.get().preview, null); assert.equal(await control.confirm(), false);
});

test('import failures retain fingerprint for idempotent retry and stale previews cannot publish', async t => {
  let fail = true; const calls = [], control = Workbench.createImportController({sessionId: unique(), uuid: unique, request: async (action, args) => {
    calls.push({action, args: copy(args)}); if (action === 'import-legacy-preview') return {preview: {fingerprint: 'fixed'}};
    if (fail) { fail = false; throw Error('network lost'); } return {id: 'imported'};
  }}); t.after(() => control.dispose()); control.edit('/synthetic'); await control.preview(); assert.equal(await control.confirm(), false);
  assert.match(control.get().error, /network lost/); assert.equal((await control.confirm()).id, 'imported'); assert.deepEqual(calls[1], calls[2]);
  const gate = deferred(); let notices = 0; const stale = Workbench.createImportController({sessionId: unique(), uuid: unique, request: async () => gate.promise, notify: () => notices++});
  stale.edit('/synthetic'); const preview = stale.preview(); stale.dispose(); const count = notices; gate.resolve({preview: {fingerprint: 'late'}}); assert.equal(await preview, false); assert.equal(notices, count);
});

test('uncertain import survives modal reopen without permitting a new operation or path', async t => {
  const sessionId = unique(), calls = []; let fail = true;
  const options = {sessionId, uuid: unique, request: async (action, args) => { calls.push({action, args: copy(args)});
    if (action === 'import-legacy-preview') return {preview: {fingerprint: 'durable-fingerprint'}};
    if (fail) { fail = false; throw Error('lost import receipt'); } return {id: 'one-copy'};
  }};
  const first = Workbench.createImportController(options); first.edit('/synthetic/legacy'); await first.preview(); assert.equal(await first.confirm(), false); first.dispose();
  const reopened = Workbench.createImportController(options); t.after(() => reopened.dispose());
  reopened.edit('/different'); assert.equal(reopened.get().path, '/synthetic/legacy'); assert.equal(await reopened.preview(), false); assert.equal(calls.length, 2);
  assert.equal((await reopened.confirm()).id, 'one-copy'); assert.deepEqual(calls[1], calls[2]);
});

test('explicit SOURCE_CHANGED rejection unlocks a fresh preview before another import confirmation', async t => {
  let fingerprint = 'first'; const calls = [], control = Workbench.createImportController({sessionId: unique(), uuid: unique, request: async (action, args) => {
    calls.push({action, args: copy(args)}); if (action === 'import-legacy-preview') return {preview: {fingerprint}};
    if (fingerprint === 'first') { fingerprint = 'second'; const error = Error('source changed'); error.code = 'SOURCE_CHANGED'; throw error; } return {id: 'new-copy'};
  }}); t.after(() => control.dispose()); control.edit('/synthetic'); await control.preview(); assert.equal(await control.confirm(), false);
  assert.equal(control.get().attempt, null); assert.equal(control.get().preview, null); assert.equal(await control.confirm(), false);
  await control.preview(); assert.equal((await control.confirm()).id, 'new-copy'); assert.equal(calls[3].args.expected_fingerprint, 'second'); assert.notEqual(calls[1].args.operation_id, calls[3].args.operation_id);
});

test('candidate requires preview and explicit append; normal CAS autosave preserves all original bytes', async t => {
  const original = '  original\r\n$math$\n', candidate = '  pasted 😀\r\n', server = scratchServer(pad(4, 'title', original));
  const {control, timer} = scratch(t, server); await control.load();
  control.editCandidate('candidateText', candidate); control.editCandidate('candidateSource', 'manual source');
  assert.equal(await control.canLeave(), false); assert.equal(control.adoptCandidate(), false); assert.equal(writes(server).length, 0);
  assert.equal(control.previewCandidate(), true); assert.equal(writes(server).length, 0); assert.equal(control.get().body, original);
  const expected = control.get().candidatePreview.result;
  assert.equal(control.adoptCandidate(), true); assert.equal(control.get().body, expected); assert.ok(expected.startsWith(original)); assert.ok(expected.endsWith(candidate));
  assert.match(expected, /未经审核/); assert.match(expected, /manual source/); assert.equal(control.get().candidateText, '');
  assert.equal(writes(server).length, 0); await timer.advance(800);
  assert.deepEqual(writes(server).map(call => call.action), ['scratchpad-save']); assert.equal(writes(server)[0].args.expected_rev, 4);
  assert.equal(server.get().scratchpad.body, expected); assert.equal(await control.canLeave(), true);
});

test('candidate is retained only for its session/project, guards departure and requires fresh preview after edits', async t => {
  const sessionId = unique(), projectId = unique(), server = scratchServer(pad(1));
  const {control} = scratch(t, server, {sessionId, projectId}); await control.load();
  control.editCandidate('candidateText', 'candidate exact\r\n'); control.editCandidate('candidateSource', 'source exact\n');
  control.previewCandidate(); control.edit('body', 'new local body'); assert.equal(control.adoptCandidate(), false);
  assert.equal(control.get().body, 'new local body'); control.dispose();
  const reopened = scratch(t, server, {sessionId, projectId}).control;
  assert.equal(reopened.get().candidateText, 'candidate exact\r\n'); assert.equal(reopened.get().candidateSource, 'source exact\n');
  assert.equal(await reopened.canLeave(), false); await reopened.load(); assert.equal(reopened.previewCandidate(), true);
  const otherSession = scratch(t, server, {sessionId: unique(), projectId}).control;
  const otherProject = scratch(t, server, {sessionId, projectId: unique()}).control;
  assert.equal(otherSession.get().candidateText, ''); assert.equal(otherProject.get().candidateText, '');
  reopened.cancelCandidate(); assert.equal(reopened.get().candidateText, ''); assert.equal(reopened.get().candidateSource, '');
  assert.equal(reopened.get().body, 'new local body'); assert.equal(await reopened.canLeave(), true);
});

test('candidate cannot be adopted after remote refresh, conflicts, unknown attempts, busy writes or on readonly projects', async t => {
  const server = scratchServer(pad(1)), {control} = scratch(t, server);
  control.editCandidate('candidateText', 'suggestion'); assert.equal(control.previewCandidate(), false);
  await control.load(); assert.equal(control.previewCandidate(), true); server.replace(pad(2)); await control.load();
  assert.equal(control.adoptCandidate(), false); assert.equal(control.get().body, pad(2).body);
  control.previewCandidate(); control.edit('body', 'local'); server.replace(pad(3)); await control.load();
  assert.ok(control.get().incoming); assert.equal(control.previewCandidate(), false); assert.equal(control.adoptCandidate(), false); assert.equal(writes(server).length, 0);
  const gate = deferred(), busy = scratch(t, scratchServer(pad(1)), {request: async action => action === 'scratchpad-read' ? {scratchpad: pad(1)} : gate.promise}).control;
  await busy.load(); busy.editCandidate('candidateText', 'candidate'); busy.previewCandidate(); busy.edit('title', 'changed'); const saving = busy.save();
  assert.equal(busy.adoptCandidate(), false); gate.reject(Error('unknown receipt')); await saving;
  assert.ok(busy.get().attempt); assert.equal(busy.adoptCandidate(), false); assert.equal(busy.previewCandidate(), false);
  const readonly = scratch(t, server, {writable: false}).control; await readonly.load(); readonly.editCandidate('candidateText', 'illegal');
  assert.equal(readonly.get().candidateText, ''); assert.equal(readonly.previewCandidate(), false); assert.equal(readonly.adoptCandidate(), false);
});

test('candidate source-only input protects beforeunload and survives a writable-to-readonly remount', async t => {
  const prior = globalThis.window, listeners = new Map(); globalThis.window = {addEventListener: (name, fn) => listeners.set(name, fn)};
  // A fresh module instance installs its own beforeunload listener against this synthetic window.
  const modulePath = require.resolve('../workbench-ui.cjs'); const cached = require.cache[modulePath]; delete require.cache[modulePath];
  const Fresh = require('../workbench-ui.cjs'); require.cache[modulePath] = cached;
  t.after(() => { if (prior === undefined) delete globalThis.window; else globalThis.window = prior; });
  const sessionId = unique(), projectId = unique(), server = scratchServer();
  const options = {request: server.request, sessionId, projectId, writable: true, uuid: unique};
  const control = Fresh.createScratchpadController(options); await control.load(); control.editCandidate('candidateSource', 'source only');
  let prevented = false; listeners.get('beforeunload')({preventDefault() { prevented = true; }}); assert.equal(prevented, true); control.dispose();
  const readonly = Fresh.createScratchpadController({...options, writable: false}); t.after(() => readonly.dispose());
  assert.equal(readonly.get().candidateSource, 'source only'); assert.equal(await readonly.canLeave(), false);
  readonly.cancelCandidate(); assert.equal(await readonly.canLeave(), true); assert.equal(writes(server).length, 0);
});

function hooks() {
  const slots = [], effects = [], scheduled = []; let cursor = 0;
  const React = {createElement(type, props, ...children) { return {type, props: props || {}, children: children.flat(Infinity).filter(value => value !== null && value !== undefined && value !== false)}; },
    useState(initial) { const index = cursor++; if (!slots[index]) slots[index] = {value: typeof initial === 'function' ? initial() : initial}; return [slots[index].value, value => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }]; },
    useRef(initial) { const index = cursor++; if (!slots[index]) slots[index] = {current: initial}; return slots[index]; },
    useEffect(effect, deps) { const index = cursor++, old = effects[index]; if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) scheduled.push(() => { old?.cleanup?.(); effects[index] = {deps, cleanup: effect()}; }); }};
  return {React, render(Component, props) { cursor = 0; return Component(props); }, flush() { while (scheduled.length) scheduled.shift()(); }, unmount() { effects.forEach(effect => effect?.cleanup?.()); }};
}
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...(tree.children || []).flatMap(nodes)];
const findButton = (tree, text) => { const button = nodes(tree).find(node => node.type === 'button' && node.children.includes(text)); assert.ok(button, `Missing button: ${text}`); return button; };

test('project form hides redundant help visually but retains accessible guidance, errors and explicit finalize', async t => {
  const mock = hooks(), server = draftServer(), created = [], guards = new Map();
  const {ProjectForm} = ProjectUI(mock.React, F, function Preview() {});
  const props = {request: server.request, sessionId: unique(), descriptor, onCreated: value => created.push(value), registerGuard: (key, guard) => { guards.set(key, guard); return () => guards.delete(key); }};
  const render = () => mock.render(ProjectForm, props); render(); mock.flush(); await settle(); let tree = render(); mock.flush();
  assert.equal(nodes(tree).filter(node => node.type === 'fieldset').length, 3);
  const primary = nodes(tree).find(node => node.props.className === 'dm-project-primary');
  assert.deepEqual(nodes(primary).filter(node => node.props.className === 'dm-project-field').map(node => node.props.key), ['title', 'statement', 'completion_criteria']);
  assert.equal(nodes(tree).find(node => node.props.className === 'dm-details dm-project-conditions').props.open, undefined);
  assert.equal(nodes(tree).filter(node => node.props.className === 'dm-field-help').length, 0);
  assert.equal(nodes(tree).filter(node => node.props.className === 'dm-sr-only').length, descriptor.fields.length);
  assert.ok(nodes(tree).some(node => node.props.role === 'radiogroup' && node.props['aria-describedby'] === 'dm-project-target_type-help'));
  assert.equal(nodes(tree).filter(node => node.props.className === 'dm-target-card').length, 3);
  assert.ok(nodes(tree).some(node => node.children.includes(descriptor.target_types[0].description))); assert.equal(writes(server).length, 0);
  await tree.props.onSubmit({preventDefault() {}}); tree = render(); assert.ok(nodes(tree).some(node => node.props.className === 'dm-field-error'));
  assert.equal(created.length, 0); assert.deepEqual(writes(server).map(call => call.action), ['project-draft-save']);
  for (const field of descriptor.fields) {
    if (field.key === 'target_type') continue;
    const control = nodes(tree).find(node => node.props['aria-describedby'] === `dm-project-${field.key}-help`);
    const value = completeFields()[field.key]; control.props.onChange({target: {value: Array.isArray(value) ? value.join('\n') : value}});
  }
  tree = render(); await tree.props.onSubmit({preventDefault() {}}); assert.equal(created.length, 1); assert.equal(created[0].execution_authorized, false);
  assert.ok(guards.has('project-form')); mock.unmount(); assert.equal(guards.size, 0); t.after(() => mock.unmount());
});

test('both conflict components show readable versions with and without comparison factory and selecting local never writes', async t => {
  const prior = globalThis.DshMathComparisonUI;
  t.after(() => { if (prior === undefined) delete globalThis.DshMathComparisonUI; else globalThis.DshMathComparisonUI = prior; });
  for (const withComparison of [false, true]) {
    if (withComparison) globalThis.DshMathComparisonUI = require('../comparison-ui.cjs'); else delete globalThis.DshMathComparisonUI;
    const mock = hooks(), server = draftServer(completeFields()), {ProjectForm} = ProjectUI(mock.React, F);
    const props = {request: server.request, sessionId: unique(), descriptor};
    const render = () => mock.render(ProjectForm, props); render(); mock.flush(); await settle(); let tree = render();
    nodes(tree).find(node => node.props['aria-describedby'] === 'dm-project-title-help').props.onChange({target: {value: 'my title'}});
    server.replace({...completeFields(), title: 'remote title'}); findButton(tree, '读取会话草稿').props.onClick(); await settle(); tree = render();
    if (withComparison) {
      const compared = nodes(tree).find(node => typeof node.type === 'function' && node.props.localVersion);
      assert.equal(compared.props.local.title, 'my title'); assert.equal(compared.props.remote.title, 'remote title'); assert.match(compared.props.remoteVersion, /版本2/);
    } else assert.ok(nodes(tree).some(node => node.type === 'pre' && node.children.includes('remote title')));
    findButton(tree, '保留我的修改，稍后重新保存').props.onClick(); assert.equal(writes(server).length, 0); mock.unmount();
    const smock = hooks(), sserver = scratchServer(pad(1)), {Scratchpad} = Workbench(smock.React, F);
    const sprops = {request: sserver.request, sessionId: unique(), projectId: unique(), writable: true};
    const srender = () => smock.render(Scratchpad, sprops); srender(); smock.flush(); await settle(); let stree = srender();
    nodes(stree).find(node => node.props.className === 'dm-input dm-scratch-body').props.onChange({target: {value: 'my body'}});
    sserver.replace(pad(2, 'remote title', 'remote body')); findButton(stree, '读取最新版本').props.onClick(); await settle(); stree = srender();
    if (withComparison) {
      const compared = nodes(stree).find(node => typeof node.type === 'function' && node.props.localVersion);
      assert.equal(compared.props.local.body, 'my body'); assert.equal(compared.props.remote.body, 'remote body'); assert.equal(compared.props.remoteVersion, 'v2');
    } else assert.ok(nodes(stree).some(node => node.type === 'pre' && node.children.includes('remote body')));
    findButton(stree, '保留我的修改，稍后重新保存').props.onClick(); assert.equal(writes(sserver).length, 0); smock.unmount();
  }
});

test('project onDraftState only notifies changed state and cannot mutate the live draft', async t => {
  const mock = hooks(), server = draftServer(), states = [], {ProjectForm} = ProjectUI(mock.React, F);
  const props = {request: server.request, sessionId: unique(), descriptor, onDraftState: state => { states.push(copy(state)); state.fields.title = 'callback mutation'; }};
  const render = () => mock.render(ProjectForm, props); render(); mock.flush(); await settle(); render(); mock.flush();
  const count = states.length; render(); mock.flush(); assert.equal(states.length, count); assert.ok(count > 0); assert.equal(writes(server).length, 0);
  let tree = render(); const title = nodes(tree).find(node => node.props['aria-describedby'] === 'dm-project-title-help'); assert.equal(title.props.value, '');
  title.props.onChange({target: {value: 'local title'}}); tree = render(); mock.flush(); assert.equal(states.at(-1).fields.title, 'local title'); assert.equal(writes(server).length, 0);
  mock.unmount(); t.after(() => mock.unmount());
});

test('scratch component exposes paste/preview/explicit append and guards unadopted input without Comparison global', async t => {
  const mock = hooks(), server = scratchServer(pad(1)), guards = new Map();
  const old = globalThis.DshMathComparisonUI; delete globalThis.DshMathComparisonUI;
  const {Scratchpad} = Workbench(mock.React, F);
  if (old) globalThis.DshMathComparisonUI = old;
  const props = {request: server.request, sessionId: unique(), projectId: unique(), writable: true,
    registerGuard: (key, fn) => { guards.set(key, fn); return () => guards.delete(key); }};
  const render = () => mock.render(Scratchpad, props); render(); mock.flush(); await settle(); let tree = render(); mock.flush();
  nodes(tree).find(node => node.props.className === 'dm-input dm-candidate-input').props.onChange({target: {value: 'pasted candidate'}});
  tree = render(); mock.flush(); assert.equal(await guards.get('scratchpad')(), false);
  findButton(tree, '预览原稿与追加结果').props.onClick(); tree = render(); assert.equal(writes(server).length, 0);
  assert.ok(nodes(tree).some(node => node.type === 'pre' && node.children.includes(pad(1).body)));
  findButton(tree, '追加到草稿（未经审核）').props.onClick(); tree = render();
  assert.equal(nodes(tree).find(node => node.props.className === 'dm-input dm-candidate-input').props.value, '');
  assert.ok(nodes(tree).find(node => node.props.className === 'dm-input dm-scratch-body').props.value.includes('pasted candidate'));
  assert.equal(writes(server).length, 0); mock.unmount(); t.after(() => mock.unmount());
});

test('scratch component promotion flushes saved exact revision and readonly exposes no mutation callbacks', async t => {
  const mock = hooks(), server = scratchServer(), promoted = [], gate = deferred(); let hold = true;
  const {Scratchpad} = Workbench(mock.React, F, function Preview() {});
  const props = {request: async (action, args) => { if (action === 'scratchpad-save' && hold) { hold = false; await gate.promise; } return server.request(action, args); },
    sessionId: unique(), projectId: 'synthetic-project', writable: true, onPromote: (...args) => promoted.push(args)};
  const render = () => mock.render(Scratchpad, props); render(); mock.flush(); await settle(); let tree = render();
  nodes(tree).find(node => node.type === 'textarea').props.onChange({target: {value: 'exact promotion\n'}}); tree = render();
  const promote = findButton(tree, '整理为笔记').props.onClick(); findButton(tree, '整理为待证事项').props.onClick(); await settle(); assert.equal(promoted.length, 0);
  gate.resolve(); await promote; assert.equal(promoted.length, 1); assert.equal(promoted[0][0], 'Note'); assert.equal(promoted[0][1].rev, 1); assert.equal(promoted[0][1].body, 'exact promotion\n');
  mock.unmount();
  const readonly = hooks(), ui = Workbench(readonly.React, F), readProps = {...props, sessionId: unique(), writable: false, request: server.request};
  readonly.render(ui.Scratchpad, readProps); readonly.flush(); await settle(); const readTree = readonly.render(ui.Scratchpad, readProps);
  for (const text of ['立即保存', '整理为笔记', '整理为待证事项', '恢复为新版本']) assert.equal(nodes(readTree).some(node => node.type === 'button' && node.children.includes(text)), false);
  assert.ok(nodes(readTree).filter(node => ['input', 'textarea'].includes(node.type)).every(node => node.props.disabled)); readonly.unmount(); t.after(() => { mock.unmount(); readonly.unmount(); });
});
