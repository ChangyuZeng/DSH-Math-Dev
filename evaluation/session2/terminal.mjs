#!/usr/bin/env node
// Session2 entry: production math loop or the separately qualified native DSH adapter.
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {isAbsolute, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createWorkflowCall, startWorkflowRuntime, startOfflineWorkflowRuntime} from '../../workflow_runtime.mjs';
import {executeToolLoop} from '../../workflow_executor.mjs';
import {parseExact, fault} from '../../workflow_ports.mjs';

const source = fileURLToPath(new URL('../..', import.meta.url));
const prepareScript = fileURLToPath(new URL('./prepare.py', import.meta.url));
const plainScript = fileURLToPath(new URL('./plain/terminal.py', import.meta.url));
const errorRecord = error => ({name: error.name, code: error.code ?? null, message: error.message,
  stack: error.stack ?? null, accounting: error.accounting ?? null, outcomeUnknown: error.outcomeUnknown ?? null,
  details: error.details ?? null});
const save = (directory, name, value) => writeFile(join(directory, name), JSON.stringify(value, null, 2) + '\n', {mode: 0o600});

function python(script, argv, executable, {signal} = {}) {
  return new Promise((accept, reject) => {
    const child = spawn(executable, ['-B', script, ...argv], {cwd: source,
      env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '', stderr = '', bytes = 0;
    const abort = () => child.kill('SIGTERM');
    signal?.addEventListener('abort', abort, {once: true});
    if (signal?.aborted) abort();
    child.stdout.on('data', chunk => {
      bytes += chunk.length;
      if (bytes > 64 * 1024 * 1024) child.kill('SIGTERM');
      else stdout += chunk;
    });
    child.stderr.on('data', chunk => { if (stderr.length < 1024 * 1024) stderr += chunk; });
    child.once('error', reject);
    child.once('close', (code, childSignal) => {
      signal?.removeEventListener('abort', abort);
      accept({exit_code: code, signal: childSignal, stdout, stderr});
    });
  });
}

export async function runTerminal(args) {
  args = {python: 'python3', ...args};
  if (!['fixture', 'real'].includes(args.mode)) throw fault('CLI_MODE', '--mode fixture|real is required');
  if (!isAbsolute(args.output ?? '') || !isAbsolute(args.registration ?? '')) {
    throw fault('CLI_PATH', '--output and --registration must be absolute');
  }
  if (args.mode === 'real' && args.authorizeUpstream !== true) {
    throw fault('UPSTREAM_UNAUTHORIZED', 'Real mode requires explicit --authorize-upstream');
  }
  if (args.mode === 'real' && (args.responses || args.fixtureResponses)) {
    throw fault('FIXTURE_REAL', 'Real mode cannot accept fixture responses');
  }
  const frozen = parseExact(await readFile(args.registration, 'utf8'));
  const slot = frozen.slots?.find(item => item.id === args.slot);
  if (!slot || !['plain', 'math-current'].includes(slot.arm)) throw fault('CLI_SLOT', 'Unknown preregistered slot');
  if (slot.arm === 'plain') {
    if (args.fixtureResponses) throw fault('CLI_FIXTURE', 'Plain fixtures require --responses JSON file');
    const argv = ['--registration', args.registration, '--slot', args.slot, '--mode', args.mode, '--output', args.output,
      ...(args.authorizeUpstream ? ['--authorize-upstream'] : []), ...(args.responses ? ['--responses', args.responses] : [])];
    const controller = new AbortController();
    const interrupted = () => controller.abort();
    process.on('SIGINT', interrupted); process.on('SIGTERM', interrupted);
    let result;
    try { result = await python(plainScript, argv, args.python, {signal: controller.signal}); }
    finally { process.off('SIGINT', interrupted); process.off('SIGTERM', interrupted); }
    let final;
    try { final = parseExact(await readFile(join(args.output, 'final.json'), 'utf8')); }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      let startupEvidence = null;
      try { startupEvidence = parseExact(await readFile(join(args.output, 'process.json'), 'utf8')); }
      catch (readError) { if (readError.code !== 'ENOENT') throw readError; }
      throw fault('PLAIN_FAILED', 'Native DSH adapter produced no final record; inspect startup evidence',
        {details: {child: result, process: startupEvidence}});
    }
    if (final.arm !== 'plain' || !['completed', 'failed'].includes(final.status) || !final.usage || !final.identity) {
      throw fault('PLAIN_FINAL_SCHEMA', 'Native DSH adapter final record is invalid');
    }
    if (result.exit_code !== 0 || final.status === 'failed') final.failure ??= {
      code: 'PLAIN_FAILED', message: 'Native DSH adapter failed; inspect its final and process evidence'};
    return final;
  }
  let responses;
  if (args.mode === 'fixture') {
    responses = args.fixtureResponses ?? (args.responses ? parseExact(await readFile(args.responses, 'utf8')) : null);
    if (!Array.isArray(responses)) throw fault('FIXTURE_REQUIRED', 'Fixture mode requires an explicit JSON response array');
  }
  // Exclusive fresh directory: no replay, continuation, or replacement of a lineage.
  await mkdir(args.output, {mode: 0o700});
  const record = {format: 'dsh-math-session2-process-v1', mode: args.mode, arm: slot.arm, slot,
    output: args.output, registration: args.registration, started_at: new Date().toISOString(), pid: process.pid,
    upstream_authorized: args.mode === 'real' && args.authorizeUpstream === true,
    max_actions: null, max_physical_requests: 120, boundaries: [], exceptions: [], runtime: null, run: null};
  let runtime, call, packet;
  const controller = new AbortController();
  const interrupted = signal => { record.boundaries.push({type: 'signal', signal, at: new Date().toISOString()}); controller.abort(); };
  const onInt = () => interrupted('SIGINT'), onTerm = () => interrupted('SIGTERM');
  process.on('SIGINT', onInt); process.on('SIGTERM', onTerm);
  const checkpoint = () => save(args.output, 'process.json', record);
  try {
    await checkpoint();
    record.preparation = await python(prepareScript, ['--output', args.output, '--mode', args.mode,
      '--registration', args.registration, '--slot', args.slot, ...(args.authorizeUpstream ? ['--authorize-upstream'] : [])],
    args.python, {signal: controller.signal});
    if (record.preparation.exit_code !== 0) throw fault('PREPARE_FAILED', 'Preregistered preparation failed; see prepare-error.json');
    packet = parseExact(record.preparation.stdout);
    record.identity = {sessionId: packet.sessionId, projectId: packet.projectId, runId: packet.runId, root: packet.root};
    await checkpoint();
    const options = {...packet, python: args.python, signal: controller.signal};
    call = createWorkflowCall(options);
    runtime = args.mode === 'fixture' ? await startOfflineWorkflowRuntime({...options, responses}) : await startWorkflowRuntime(options);
    record.runtime = runtime.metadata;
    await checkpoint();
    let observedRequests = 0, observedMinutes = 0;
    const authorize = async () => {
      const current = await call('workflow-control', {project_id: packet.projectId, session_id: packet.sessionId,
        command: {action: 'read', run_id: packet.runId}});
      const count = current.physical_requests.length;
      const minutes = (Date.now() - Date.parse(record.started_at)) / 60000;
      const requestPoint = Math.floor(count / 20), timePoint = Math.floor(minutes / 30);
      if (requestPoint > observedRequests || timePoint > observedMinutes) {
        record.boundaries.push({type: 'observation', physical_requests: count, elapsed_minutes: minutes,
          request_points_crossed: requestPoint - observedRequests, time_points_crossed: timePoint - observedMinutes,
          effect: 'record-only; no interruption, budget change, or quota reset', at: new Date().toISOString()});
        observedRequests = requestPoint; observedMinutes = timePoint;
        await checkpoint();
      }
    };
    const initial = await call('workflow-state', {action: 'read', project_id: packet.projectId, run_id: packet.runId});
    record.run = await executeToolLoop({call, modelPort: runtime.modelPort, toolPort: runtime.toolPort,
      projectId: packet.projectId, runId: packet.runId, expectedRev: initial.rev, kind: 'derive',
      signal: controller.signal, authorize});
  } catch (error) {
    record.exceptions.push(errorRecord(error));
    record.failure = errorRecord(error);
    if (call && packet) {
      try {
        record.run = await call('workflow-state', {action: 'read', project_id: packet.projectId, run_id: packet.runId});
        // Pause only a clean running run; never reconcile unknown calls or replay effects.
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
      registration: {type: 'string'}, slot: {type: 'string'}, python: {type: 'string', default: 'python3'},
      responses: {type: 'string'}, 'authorize-upstream': {type: 'boolean', default: false}}});
    const record = await runTerminal({...values, authorizeUpstream: values['authorize-upstream']});
    console.log(JSON.stringify({output: record.output, identity: record.identity, arm: record.arm,
      status: record.run?.status ?? record.status, physical_requests: record.run?.physical_requests?.length,
      failure: record.failure ?? null, close: record.close}));
    if (record.failure) process.exitCode = 1;
  } catch (error) { console.error(JSON.stringify(errorRecord(error))); process.exitCode = 1; }
}
