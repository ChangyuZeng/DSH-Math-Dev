import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm, realpath, readFile, writeFile, mkdir, copyFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {apply, pythonCall} from '../host.mjs';

const python = process.env.DSH_MATH_PYTHON || 'python3';
const packageRoot = resolve('.');

async function fixture(prefix) {
  const root = await realpath(await mkdtemp(join(tmpdir(), prefix)));
  const data = join(root, 'data');
  const session = 'session-node-model';
  const contractPath = join(packageRoot, 'contracts/OP-000008.json');
  const contract = JSON.parse(await readFile(contractPath, 'utf8'));
  const project = await pythonCall(data, {action: 'create', args: {session_id: session, problem: contract.problem}}, python);
  const output = join(root, 'run');
  const workspace = join(output, 'workspace');
  await mkdir(workspace, {recursive: true});
  const card = join(output, 'problem.md');
  await copyFile(join(packageRoot, 'contracts/cards/OP-000008.md'), card);
  const sources = join(output, 'sources.md');
  await writeFile(sources, 'Synthetic host acceptance only.\n');
  const descriptor = async (path, name) => {
    const raw = await readFile(path);
    return {...(name ? {name} : {}), path, bytes: raw.length, sha256: createHash('sha256').update(raw).digest('hex')};
  };
  const manifest = {format: 'dsh-math-m-run-v1', protocol_id: 'DSH-MATH-M-PILOT-v1', run_id: 'run-node-model',
    op_id: 'OP-000008', problem_id: 'P', project_id: project.id, session_id: session, actor_id: 'node-fixture-model',
    mode: 'zero_model_acceptance', model: null, effort: null, checker_id: 'grid75_p149_v1',
    contract: await descriptor(contractPath), materials: [await descriptor(card, 'problem.md'), await descriptor(sources, 'sources.md')],
    workspace, output_dir: output, limits: {wall_seconds: 60, requests: 0, tools: 10, cpu_seconds: 10,
      memory_bytes: 64 * 1024 ** 2, artifact_bytes: 1024 ** 2, tool_timeout_seconds: 5, cleanup_seconds: 3},
    execution_authorized: false, authorization_reference: null, stop_rules: ['zero-model host test']};
  const manifestPath = join(output, 'manifest.json');
  await writeFile(manifestPath, JSON.stringify(manifest));
  const tools = new Map();
  const controller = apply({effect(fn) {fn();}, on() {}, webServer: {getOrigin: () => 'http://127.0.0.1', register() {return () => {};}},
    tools: {register(definition) {tools.set(definition.name, definition);}}}, {root: data, python,
      modelRuns: [{projectId: project.id, sessionId: session, runId: manifest.run_id, manifest: manifestPath}]});
  const execute = (name, args, callId, agent = session, signal = new AbortController().signal) =>
    tools.get(name).execute({payload: JSON.stringify(args)}, {agent: {id: agent}, callId, signal});
  return {root, data, output, session, project, tools, controller, execute};
}

test('DSH model slot binds host identity and isolates sessions', async () => {
  const f = await fixture('dsh-math-model-host-');
  try {
    const note = {note_type: 'source', title: 'Host source fixture', summary: 'Synthetic', body: 'Synthetic host tool call',
      statement: 'Engineering fixture only', assumptions: [], problem_ref: {id: 'P', rev: 1}, route_id: null,
      uses: [], candidate_for: [], evidence: [], gaps: [], failure: null};
    const response = JSON.parse(await f.execute('note_save', {operation_id: 'node-note', id: 'N-HOST', expected_rev: 0, data: note}, 'call-node-1'));
    assert.equal(response.operation_model_requests, 0);
    assert.equal(response.result.status, 'completed');
    const view = await pythonCall(f.data, {action: 'overview', args: {project_id: f.project.id}}, python);
    const event = view.snapshot.state.objects['N-HOST'][0];
    assert.equal(event.actor.kind, 'model');
    assert.equal(event.actor.id, 'node-fixture-model');
    assert.deepEqual(event.origin, {source: 'dsh-math-model', run_id: 'run-node-model', session_id: f.session, tool_call_id: 'call-node-1'});
    assert.equal(view.snapshot.state.seq, 2);
    await assert.rejects(f.execute('research_read', {kind: null, state: null, text: '', object_id: null, rev: null}, 'cross-session', 'other-session'), /No host-bound M run/);
    assert.equal(f.tools.has('review'), false);
    assert.equal(f.tools.has('bash'), false);
  } finally { await rm(f.root, {recursive: true, force: true}); }
});

test('cancellation seals admission before await and reconciles in-flight tool', async () => {
  const f = await fixture('dsh-math-cancel-host-');
  try {
    const pending = f.execute('python_run', {code: 'import time\ntime.sleep(30)'}, 'call-cancel').catch(error => error);
    const deadline = Date.now() + 5000;
    let started = false;
    while (!started && Date.now() < deadline) {
      try { started = (await readFile(join(f.output, 'ledger/events.jsonl'), 'utf8')).includes('tool.started'); } catch {}
      if (!started) await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(started, true);
    const closing = f.controller.cancelRun('run-node-model', 'injected_user_cancel');
    await assert.rejects(f.execute('file_read', {path: 'any.txt'}, 'after-seal'), /sealed/);
    await pending;
    const receipt = await closing;
    assert.equal(receipt.status, 'cleanup_confirmed');
    assert.equal(receipt.actual.requests, 0);
    assert.equal(receipt.cleanup_model_requests, 0);
    assert.equal(receipt.new_checks, 0);
    assert.equal(receipt.actual.unknown_tool_outcomes, 1);
  } finally { await rm(f.root, {recursive: true, force: true}); }
});

test('material listing and unknown names expose exact registered choices', async () => {
  const f = await fixture('dsh-math-material-host-');
  try {
    const listed = JSON.parse(await f.execute('material_read', {name: ''}, 'material-list'));
    assert.equal(listed.result.status, 'completed');
    assert.deepEqual(listed.result.value.materials.map(row => row.name), ['problem.md', 'sources.md']);
    await assert.rejects(f.execute('material_read', {name: 'baseline_148'}, 'material-unknown'), /problem\.md.*sources\.md/);
  } finally { await rm(f.root, {recursive: true, force: true}); }
});

test('tool timeout returns a reconciled receipt and preserves checkpoints', async () => {
  const f = await fixture('dsh-math-timeout-host-');
  try {
    const response = JSON.parse(await f.execute('python_run', {code: "import time\nopen('checkpoint.txt','w').write('partial')\ntime.sleep(30)"}, 'python-timeout'));
    assert.equal(response.result.status, 'completed');
    assert.equal(response.result.value.status, 'timeout');
    const checkpoint = JSON.parse(await f.execute('file_read', {path: 'checkpoint.txt'}, 'checkpoint-read'));
    assert.equal(checkpoint.result.value.text, 'partial');
    const receipt = await f.controller.cancelRun('run-node-model', 'acceptance_complete');
    assert.equal(receipt.actual.unknown_tool_outcomes, 0);
  } finally { await rm(f.root, {recursive: true, force: true}); }
});
