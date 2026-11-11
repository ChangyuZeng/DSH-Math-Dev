#!/usr/bin/env node
// Unified terminal entry. Does not alter production runtime, quota, or scheduling.
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {mkdir, writeFile} from 'node:fs/promises';
import {isAbsolute, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createWorkflowCall, startWorkflowRuntime, startOfflineWorkflowRuntime} from '../../workflow_runtime.mjs';
import {executeToolStep} from '../../workflow_executor.mjs';
import {parseExact, fault} from '../../workflow_ports.mjs';

const source = fileURLToPath(new URL('../..', import.meta.url));
const prepareScript = fileURLToPath(new URL('./prepare.py', import.meta.url));
const errorRecord = error => ({name: error.name, code: error.code ?? null, message: error.message,
  stack: error.stack ?? null, accounting: error.accounting ?? null, outcomeUnknown: error.outcomeUnknown ?? null});
const save = (directory, name, value) => writeFile(join(directory, name), JSON.stringify(value, null, 2) + '\n', {mode: 0o600});

function fixtureResponses(spec) {
  if (spec.execution.checker.id !== 'workflow_certificate_v2_S1') throw fault('FIXTURE_TASK', 'This fixture is defined only for S1');
  // Fixture-only data. This function is never called in real mode and these values
  // are never added to the frozen spec, problem, or production prompt.
  const residues = new Set(Array.from({length: 16}, (_, i) => ((i + 1) ** 2) % 17));
  const values = [];
  for (let i = 0; i < 17; i++) for (let j = i + 1; j < 17; j++) values.push(residues.has((j - i) % 17) ? 1 : 0);
  const note = {note_type: 'argument', title: 'Paley construction plan',
    summary: 'Plan to generate and independently check a finite coloring.',
    body: 'Generate quadratic residues modulo 17 and color edges by residue differences. In the next action compute it, inspect an invalid candidate check, correct the candidate, check and submit.',
    statement: 'The proposed construction remains unchecked at this stage.', assumptions: [],
    problem_ref: spec.problem_ref, route_id: null, uses: [], candidate_for: [], evidence: [],
    gaps: ['No certificate check has yet been performed.'], failure: null};
  const proposal = {summary: 'Persisted the construction plan for the next action.',
    changes: [{kind: 'Note', id: 'session1-plan', expected_rev: 0, data: note}], next_action: 'derive',
    next_target: {id: 'session1-plan', rev: 1}, progress: 'new_argument', limitations: note.gaps};
  const response = output => ({id: 'session1-fixture-' + randomUUID(), model: spec.model_policy.model,
    reasoning: {effort: spec.model_policy.effort}, status: 'completed', output,
    usage: {input_tokens: 20, output_tokens: 10, total_tokens: 30}});
  const invoke = (name, args) => response([{type: 'function_call', name,
    call_id: 'fixture-' + name + '-' + randomUUID(), arguments: JSON.stringify(args)}]);
  return [response([{type: 'message', role: 'assistant', content: [{type: 'output_text', text: JSON.stringify(proposal)}]}]),
    invoke('compute', {code: "import json\nr={i*i%17 for i in range(1,17)}\nv=[int((j-i)%17 in r) for i in range(17) for j in range(i+1,17)]\nprint(json.dumps({'values':v}))"}),
    invoke('check', {candidate: {values: Array(136).fill(0)}}),
    invoke('check', {candidate: {values}}), invoke('submit', {candidate: {values}})];
}

async function preparation(args, record) {
  return new Promise((accept, reject) => {
    const child = spawn(args.python, ['-B', prepareScript, '--output', args.output, '--mode', args.mode,
      '--task', args.task, ...(args.authorizeUpstream ? ['--authorize-upstream'] : []),
      ...(args.stageGoal ? ['--stage-goal', args.stageGoal] : [])],
    {cwd: source, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.once('error', reject);
    child.once('close', code => {
      record.preparation = {exit_code: code, stdout, stderr};
      if (code !== 0) reject(fault('PREPARE_FAILED', 'Official project preparation failed; see process.json and prepare-error.json'));
      else { try { accept(parseExact(stdout)); } catch (error) { reject(error); } }
    });
  });
}

export async function runTerminal(args) {
  if (!['fixture', 'real'].includes(args.mode)) throw fault('CLI_MODE', '--mode fixture|real is required');
  if (!isAbsolute(args.output ?? '')) throw fault('CLI_OUTPUT', '--output must be an absolute new directory');
  if (args.mode === 'real' && args.authorizeUpstream !== true) throw fault('UPSTREAM_UNAUTHORIZED', 'Real mode requires --authorize-upstream');
  // Nonrecursive exclusive mkdir rejects existing output and never reuses lineage.
  await mkdir(args.output, {mode: 0o700});
  const record = {format: 'dsh-math-session1-process-v1', mode: args.mode, task: args.task,
    output: args.output, started_at: new Date().toISOString(), pid: process.pid,
    upstream_authorized: args.mode === 'real' && args.authorizeUpstream === true,
    max_actions: 3, max_physical_requests: 8, boundaries: [], exceptions: [], runtime: null, run: null};
  let runtime, call, packet;
  const controller = new AbortController();
  const interrupted = signal => { record.boundaries.push({type: 'signal', signal, at: new Date().toISOString()}); controller.abort(); };
  const onInt = () => interrupted('SIGINT'), onTerm = () => interrupted('SIGTERM');
  process.on('SIGINT', onInt); process.on('SIGTERM', onTerm);
  const checkpoint = () => save(args.output, 'process.json', record);
  try {
    await checkpoint();
    packet = await preparation(args, record);
    record.identity = {sessionId: packet.sessionId, projectId: packet.projectId, runId: packet.runId, root: packet.root};
    await checkpoint();
    const options = {...packet, python: args.python, signal: controller.signal};
    call = createWorkflowCall(options);
    runtime = args.mode === 'fixture'
      ? await startOfflineWorkflowRuntime({...options, responses: fixtureResponses(packet.spec)})
      : await startWorkflowRuntime(options);
    record.runtime = runtime.metadata;
    await checkpoint();
    const read = () => call('workflow-control', {project_id: packet.projectId, session_id: packet.sessionId,
      command: {action: 'read', run_id: packet.runId}});
    const authorize = async () => { await read(); };
    const boundary = async (reason, details = {}) => {
      record.boundaries.push({type: 'external_stop', reason, ...details, at: new Date().toISOString()});
      await checkpoint();
      throw fault('SESSION1_STOP', reason, {knownUnsent: true});
    };
    const modelPort = Object.freeze({prepare: (...values) => runtime.modelPort.prepare(...values),
      complete: async options => {
        const current = await read();
        const count = current.physical_requests.length;
        // One production complete may retry twice internally. Reserve its maximum
        // before dispatch; never shrink the qualified 120-request ledger budget.
        // executeToolStep journals this call's model_started before complete.
        // Exactly that one pending logical call is expected at this boundary.
        if (current.accounting_unreconciled || current.usage.unreconciled_calls !== 1) {
          return boundary('unreconciled_physical_accounting', {physical_requests: count});
        }
        if (count + 3 > 8) return boundary('physical_retry_headroom', {physical_requests: count, required_headroom: 3});
        return runtime.modelPort.complete(options);
      }});
    let run = await read(), kind = 'derive', targetRef = null;
    for (let index = 0; index < 3 && run.status === 'running'; index++) {
      record.boundaries.push({type: 'action_started', index: index + 1, kind, target_ref: targetRef});
      await checkpoint();
      run = await executeToolStep({call, modelPort, toolPort: runtime.toolPort, projectId: packet.projectId,
        runId: packet.runId, expectedRev: run.rev, kind, targetRef, dependencyRefs: targetRef ? [targetRef] : [], signal: controller.signal, authorize});
      record.run = run;
      await checkpoint();
      const action = run.actions.filter(item => item.status === 'committed').at(-1);
      const noteChange = action?.proposal?.changes?.find(item => item.kind === 'Note');
      if (index === 0 && !noteChange) await boundary('first_action_missing_durable_note');
      if (run.status !== 'running') break;
      targetRef = index === 0 ? {id: noteChange.id, rev: noteChange.expected_rev + 1} : action?.proposal?.next_target ?? null;
      kind = action?.proposal?.next_action ?? 'stop';
      if (kind === 'stop') break;
      if (index === 2) await boundary('action_limit', {actions: 3});
    }
    record.run = await read();
    if (record.run.status === 'running') {
      record.run = await call('workflow-state', {action: 'pause', project_id: packet.projectId, run_id: packet.runId,
        expected_rev: record.run.rev, operation_id: randomUUID()});
    }
  } catch (error) {
    record.exceptions.push(errorRecord(error));
    record.failure = errorRecord(error);
    if (call && packet) {
      try {
        record.run = await call('workflow-state', {action: 'read', project_id: packet.projectId, run_id: packet.runId});
        if (record.run.status === 'running') record.run = await call('workflow-state', {action: 'pause',
          project_id: packet.projectId, run_id: packet.runId, expected_rev: record.run.rev, operation_id: randomUUID()});
      } catch (readError) { record.exceptions.push(errorRecord(readError)); }
    }
  } finally {
    if (runtime) {
      try { await runtime.close(); record.close = {drained: true}; }
      catch (error) { record.exceptions.push(errorRecord(error)); record.failure ??= errorRecord(error); record.close = {drained: false}; }
    }
    process.off('SIGINT', onInt); process.off('SIGTERM', onTerm);
    record.finished_at = new Date().toISOString();
    await checkpoint();
  }
  return record;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    const {values} = parseArgs({options: {mode: {type: 'string'}, output: {type: 'string'},
      task: {type: 'string', default: 'S1'}, python: {type: 'string', default: 'python3'},
      'authorize-upstream': {type: 'boolean', default: false}, 'stage-goal': {type: 'string'}}});
    const record = await runTerminal({mode: values.mode, output: values.output, task: values.task,
      python: values.python, authorizeUpstream: values['authorize-upstream'], stageGoal: values['stage-goal']});
    console.log(JSON.stringify({output: record.output, identity: record.identity, status: record.run?.status,
      physical_requests: record.run?.physical_requests?.length, failure: record.failure ?? null, close: record.close}));
    if (record.failure && record.failure.code !== 'SESSION1_STOP') process.exitCode = 1;
  } catch (error) { console.error(JSON.stringify(errorRecord(error))); process.exitCode = 1; }
}
