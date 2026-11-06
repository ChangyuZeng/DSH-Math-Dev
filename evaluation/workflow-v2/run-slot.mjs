import {readFileSync, mkdirSync, openSync, writeFileSync, fsyncSync, closeSync, renameSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {executeToolLoop} from '../../workflow_executor.mjs';
import {createWorkflowCall, startWorkflowRuntime, startOfflineWorkflowRuntime} from '../../workflow_runtime.mjs';
import {parseExact, fault} from '../../workflow_ports.mjs';

function save(path, value) {
  mkdirSync(dirname(path), {recursive: true, mode: 0o700});
  const pending = path + '.pending-' + randomUUID();
  const fd = openSync(pending, 'wx', 0o600);
  try { writeFileSync(fd, JSON.stringify(value) + '\n'); fsyncSync(fd); } finally { closeSync(fd); }
  renameSync(pending, path);
  const directory = openSync(dirname(path), 'r');
  try { fsyncSync(directory); } finally { closeSync(directory); }
}

async function execute(options, responses) {
  const {root, projectId, runId, output, python = 'python3', signal, ownerFd} = options;
  const call = createWorkflowCall({root, python});
  let runtime, run, failure = null, metadata = null;
  const start = process.hrtime.bigint();
  try {
    runtime = responses === null ? await startWorkflowRuntime({root, projectId, runId, python, signal, ownerFd}) :
      await startOfflineWorkflowRuntime({root, projectId, runId, python, signal, ownerFd, responses});
    metadata = runtime.metadata;
    run = await call('workflow-state', {action: 'read', project_id: projectId, run_id: runId});
    run = await executeToolLoop({call, modelPort: runtime.modelPort, toolPort: runtime.toolPort,
      projectId, runId, expectedRev: run.rev, kind: 'derive', signal});
  } catch (error) {
    failure = {code: error.code ?? 'SLOT_PROCESS', outcome_unknown: error.outcomeUnknown === true};
  } finally {
    try { run = await call('workflow-state', {action: 'read', project_id: projectId, run_id: runId}); }
    catch { failure ??= {code: 'STATE_UNAVAILABLE', outcome_unknown: true}; }
    try { await runtime?.close(); }
    catch { failure ??= {code: 'RUNTIME_CLEANUP', outcome_unknown: true}; }
  }
  const record = {format: 'dsh-math-workflow-slot-v2', run: run ?? null, runtime: metadata,
    failure, wall_seconds: Number(process.hrtime.bigint() - start) / 1e9};
  if (output) save(resolve(output), record);
  return record;
}

export function runSlot(options) {
  if (Object.hasOwn(options, 'responses') || Object.hasOwn(options, 'fixture')) throw fault('RUNTIME_OPTIONS', 'Product slot refuses fixture transport');
  return execute(options, null);
}
export function runOfflineSlot({responses, ...options}) {
  if (!Array.isArray(responses)) throw fault('FIXTURE_SCHEMA', 'Explicit offline responses required');
  return execute(options, responses);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = new AbortController();
  for (const name of ['SIGINT', 'SIGTERM']) process.once(name, () => controller.abort());
  try {
    const request = parseExact(readFileSync(0, 'utf8'));
    const result = await runSlot({...request, signal: controller.signal});
    process.stdout.write(JSON.stringify({ok: result.failure === null, code: result.failure?.code ?? null}) + '\n');
    if (result.failure) process.exitCode = 1;
  } catch (error) {
    process.stdout.write(JSON.stringify({ok: false, code: error.code ?? 'SLOT_PROCESS'}) + '\n');
    process.exitCode = 1;
  }
}
