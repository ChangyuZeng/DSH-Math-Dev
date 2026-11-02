import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const F = require('../forms.cjs');
const descriptor = require('../project-fields.json');

test('optional title derives a display label without filling mathematical fields', () => {
  const fields = {...F.projectDefaults(descriptor), statement: '  第一行明确问题😀\n其余问题条件'};
  const patch = F.projectPatch(descriptor, fields);
  assert.equal(patch.title, '第一行明确问题😀'); assert.equal(patch.statement, fields.statement);
  assert.equal(patch.quantifiers, ''); assert.equal(patch.domain, ''); assert.deepEqual(patch.sources, []);
  assert.equal(F.projectValidation(descriptor, patch).complete, false);
});

test('reference choices preserve revisions and isolate target and kind', () => {
  const problem = {id: 'P', rev: 1};
  const objects = [{id: 'N', rev: 1, kind: 'Note', data: {problem_ref: problem}}, {id: 'N', rev: 2, kind: 'Note', data: {problem_ref: problem}},
    {id: 'X', rev: 1, kind: 'Note', data: {problem_ref: {id: 'P', rev: 2}}}, {id: 'E', rev: 1, kind: 'Evidence', data: {problem_ref: problem}}];
  assert.deepEqual(F.choices(objects, ['Note'], problem).map(F.refKey), ['N@1', 'N@2']);
  assert.deepEqual(F.choices(objects, ['Note'], problem, 'N'), []);
});

test('draft saves exact text, immutable revision and stable operation identity', () => {
  let n = 0;
  const draft = F.draft('Note', {id: 'P', rev: 1}, () => `id-${++n}`);
  draft.data.statement = 'det >= 17776121037665193653653203126'; draft.data.evidence = [{id: 'E', rev: 1}];
  const a = F.request(draft), b = F.request(draft);
  assert.deepEqual(a, b); assert.equal(a.changes[0].data.statement, draft.data.statement); assert.equal(a.changes[0].data.evidence[0].rev, 1);
  const revised = F.draft('Note', {id: 'P', rev: 1}, () => `id-${++n}`, {id: 'N', rev: 3, data: draft.data});
  assert.equal(F.request(revised).changes[0].expected_rev, 3);
});

test('evidence request includes explicit file bytes and excerpt without hidden authority', () => {
  const draft = F.draft('Evidence', {id: 'P', rev: 1}, () => 'fixture');
  draft.files = [{logical_name: 'test.bin', base64: 'AP8='}]; draft.excerpt = '原文';
  const request = F.request(draft);
  assert.equal(request.files[0].base64, 'AP8='); assert.equal(request.excerpt, '原文');
  assert.equal(request.data.source_status, 'unverified_source'); assert.ok(!('actor' in request));
});

test('project fields use the shared descriptor without silent semantic completion', () => {
  const fields = F.projectDefaults(descriptor);
  assert.deepEqual(Object.keys(fields), descriptor.fields.map(field => field.key));
  assert.equal(fields.target_type, 'prove_or_disprove'); assert.equal(fields.knowledge_status, 'unverified_open');
  assert.deepEqual(fields.sources, []); assert.deepEqual(fields.conventions, []);
  assert.equal(F.projectValidation(descriptor, fields).complete, false);
  assert.ok(F.projectValidation(descriptor, fields).missing_fields.includes('completion_criteria'));
  fields.title = '  合成标题\r\n不裁切  '; fields.sources = ['  source exact  ', '', ' \t'];
  const patch = F.projectPatch(descriptor, fields);
  assert.equal(patch.title, fields.title); assert.deepEqual(patch.sources, ['  source exact  ']);
  assert.equal('parent' in patch, false); assert.equal('relation' in patch, false);
  fields.target_type = 'guessed';
  assert.ok(F.projectValidation(descriptor, fields).errors.some(error => error.field === 'target_type' && error.code === 'INVALID_ENUM'));
});

test('writability fails closed for legacy, incomplete, and metadata-only upgrades', () => {
  const view = {project: {schema_version: 4, mode: 'daily_research'}, snapshot: {usable: true}, objects: [{kind: 'Problem', schema_version: 4}]};
  assert.equal(F.writable(view), true);
  for (const bad of [undefined, {...view, project: {...view.project, mode: 'legacy_readonly_copy'}}, {...view, project: {...view.project, schema_version: 3}},
    {...view, snapshot: {}}, {...view, snapshot: {usable: false}}, {...view, objects: []}, {...view, objects: [{schema_version: 3}]}]) assert.equal(F.writable(bad), false);
});

test('scratch promotion prefills only user text and requires a full human record', () => {
  let n = 0;
  const pad = {project_id: 'synthetic', rev: 7, title: '标题\r\n', body: 'body\n  exact\n'};
  const note = F.promoteDraft('Note', {id: 'P', rev: 2}, () => `uuid-${++n}`, pad);
  assert.equal(note.data.title, pad.title); assert.equal(note.data.body, pad.body); assert.equal(note.data.summary, ''); assert.equal(note.data.statement, '');
  assert.deepEqual(note.data.evidence, []); assert.ok(F.recordErrors(note).includes('请填写主张'));
  assert.equal(note.promotion.scratchpad_rev, 7); assert.equal(note.promotion.project_id, 'synthetic');
  assert.notEqual(note.promotion.operation_id, note.operation_id);
  const obligation = F.promoteDraft('Obligation', {id: 'P', rev: 2}, () => `uuid-${++n}`, pad);
  assert.equal(obligation.data.statement, pad.body); assert.equal(obligation.data.scope, ''); assert.deepEqual(obligation.data.completion_criteria, []);
  assert.ok(F.recordErrors(obligation).length >= 4);
  assert.throws(() => F.promoteDraft('Evidence', {}, () => 'unused', pad), /保存草稿本/);
  assert.throws(() => F.promoteDraft('Note', {}, () => 'unused', {...pad, rev: 0}), /保存草稿本/);
});

test('next action digest is bounded, preserves exact targets and ignores empty actions', () => {
  const view = {groups: {obligations: [' ', 'first\nexact', 'second', 'third', 'fourth'].map((next_action, i) => ({object: {id: `O-${i}`, rev: i + 1, data: {statement: `s-${i}`, next_action}}}))}};
  const digest = F.nextActions(view);
  assert.equal(digest.length, 3); assert.deepEqual(digest[0], {ref: {id: 'O-1', rev: 2}, text: 'first\nexact', statement: 's-1'});
  assert.equal(F.nextActions(null).length, 0);
});
