import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {createMeteredPorts, fault, parseExact} from './workflow_ports.mjs';

const service = fileURLToPath(new URL('./workflow_service.py', import.meta.url));
const runtimeScript = fileURLToPath(new URL('./workflow_runtime.py', import.meta.url));
const source = fileURLToPath(new URL('.', import.meta.url));
const MAX_REPLY = 64 * 1024 * 1024;

/** Real host service boundary; lossless JSON in both directions, no patching. */
export function createWorkflowCall({root, python = 'python3'}) {
  if (typeof root !== 'string' || !root) throw fault('RUNTIME_ROOT', 'Research root is required');
  return (action, args) => new Promise((accept, reject) => {
    const child = spawn(python, ['-B', service, resolve(root)], {
      cwd: source, stdio: ['pipe', 'pipe', 'pipe'], env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'},
    });
    const chunks = [];
    let size = 0, failure;
    child.stdout.on('data', chunk => {
      size += chunk.length;
      if (size > MAX_REPLY) { failure = fault('SERVICE_SIZE', 'Host reply exceeded its bound'); child.kill('SIGTERM'); }
      else chunks.push(chunk);
    });
    // Errors may contain research text; neither stderr nor arbitrary process
    // exceptions are echoed into UI logs. Structured service errors are enough.
    child.stderr.resume();
    child.stdin.on('error', () => {});
    child.on('error', () => reject(fault('SERVICE_PROCESS', 'Could not start the workflow service')));
    child.on('close', code => {
      if (failure) return reject(failure);
      let reply;
      try { reply = parseExact(Buffer.concat(chunks).toString('utf8')); }
      catch { return reject(fault('SERVICE_RECEIPT', 'Host service exited without a valid receipt')); }
      if (code !== 0 || reply.ok !== true) return reject(fault(reply.error ?? 'SERVICE_FAILED', reply.message ?? 'Host service failed'));
      accept(reply.value);
    });
    try { child.stdin.end(JSON.stringify({action, args})); }
    catch (error) { failure = error; child.kill('SIGTERM'); }
  });
}

/** Product entry: test transport cannot be selected by config or environment. */
export function startWorkflowRuntime(options) {
  if (['fixture', 'responses', 'offline', 'transport', 'fetchImpl'].some(key => Object.hasOwn(options, key))) {
    throw fault('RUNTIME_OPTIONS', 'Product runtime does not accept test transport options');
  }
  return start(options, null);
}

/** Deliberately separate offline entry, never used by the product entry. */
export function startOfflineWorkflowRuntime({responses, ...options}) {
  if (!Array.isArray(responses)) throw fault('FIXTURE_SCHEMA', 'Explicit offline responses are required');
  return start(options, {format: 'dsh-math-offline-responses-v1', responses});
}

async function start({root, projectId, runId, python = 'python3', signal, startupTimeoutMs = 30000, ownerFd}, fixture) {
  if (signal?.aborted) throw fault('CANCELLED', 'Runtime startup cancelled');
  if (typeof root !== 'string' || !root || typeof projectId !== 'string' || typeof runId !== 'string') {
    throw fault('RUNTIME_OPTIONS', 'Root, projectId and runId are required');
  }
  if (!Number.isSafeInteger(startupTimeoutMs) || startupTimeoutMs < 1 || startupTimeoutMs > 60000) {
    throw fault('RUNTIME_OPTIONS', 'Startup deadline must be between 1 and 60000 ms');
  }
  if (ownerFd !== undefined && (!Number.isSafeInteger(ownerFd) || ownerFd < 3)) {
    throw fault('RUNTIME_OPTIONS', 'Inherited owner lock descriptor is invalid');
  }
  const child = spawn(python, ['-B', runtimeScript, ...(fixture ? ['--offline-fixture'] : [])], {
    cwd: source, stdio: ['pipe', 'pipe', 'pipe', 'pipe', ...(ownerFd === undefined ? [] : [ownerFd])],
    env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'},
  });
  // A campaign's inherited flock stays open in the runtime until it has drained,
  // even when the Python driver or this Node executor unexpectedly exits.
  // The dedicated fourth pipe is consumed here only. Never expose the child,
  // token-bearing readiness object, or its output stream to a UI logger.
  child.stdout.resume(); child.stderr.resume(); child.stdin.on('error', () => {});
  let exited = false, exitCode, exitSignal, startupError;
  const exit = new Promise(accept => {
    child.on('error', () => { startupError = fault('RUNTIME_PROCESS', 'Could not start the qualified runtime'); accept(); });
    child.on('close', (code, sig) => { exited = true; exitCode = code; exitSignal = sig; accept(); });
  });
  let closePromise;
  const close = () => closePromise ??= (async () => {
    signal?.removeEventListener('abort', aborted);
    if (!exited && !startupError) {
      child.stdin.end(JSON.stringify({op: 'close'}) + '\n');
      // Graceful drain first. A killed child leaves its durable claim/evidence;
      // next startup rejects it rather than creating an empty ledger.
      const terminate = setTimeout(() => child.kill('SIGTERM'), 35000);
      const kill = setTimeout(() => child.kill('SIGKILL'), 40000);
      try { await exit; } finally { clearTimeout(terminate); clearTimeout(kill); }
    }
    if (exitCode !== 0 && exitCode !== undefined || exitSignal) {
      throw fault('RUNTIME_CLEANUP', 'Runtime stopped abnormally; retained evidence requires reconciliation');
    }
  })();
  const aborted = () => { void close().catch(() => {}); };
  signal?.addEventListener('abort', aborted, {once: true});
  const lines = createInterface({input: child.stdio[3], crlfDelay: Infinity});
  try {
    const readiness = new Promise((accept, reject) => {
      const timer = setTimeout(() => reject(fault('RUNTIME_START_TIMEOUT', 'Qualified runtime startup timed out')), startupTimeoutMs);
      const finish = fn => value => { clearTimeout(timer); fn(value); };
      lines.once('line', finish(line => {
        try {
          if (Buffer.byteLength(line) > MAX_REPLY) throw fault('RUNTIME_RECEIPT', 'Oversized readiness receipt');
          const ready = parseExact(line);
          if (ready.ok !== true) throw fault(ready.code ?? 'RUNTIME_FAILED', 'Qualified runtime startup failed');
          accept(ready);
        } catch (error) { reject(error); }
      }));
      exit.then(finish(() => reject(startupError ?? fault('RUNTIME_EXITED', 'Qualified runtime exited before readiness'))));
    });
    child.stdin.write(JSON.stringify({root: resolve(root), projectId, runId, ...(fixture ? {fixture} : {})}) + '\n');
    const ready = await readiness;
    if (signal?.aborted || exited) throw fault('CANCELLED', 'Runtime startup was interrupted');
    const ports = createMeteredPorts({gateway: ready.gateway, bindings: ready.bindings});
    const alive = () => {
      if (exited || closePromise || signal?.aborted) throw fault('RUNTIME_CLOSED', 'Runtime is closed; reconcile without replay');
    };
    // Preserve the exact existing ModelPort/ToolPort path for both UI and campaign.
    const modelPort = Object.freeze({
      prepare: (...args) => { alive(); return ports.modelPort.prepare(...args); },
      complete: (...args) => { alive(); return ports.modelPort.complete(...args); },
    });
    const toolPort = Object.freeze({dispatch: (...args) => { alive(); return ports.toolPort.dispatch(...args); }});
    return Object.freeze({modelPort, toolPort, close, metadata: Object.freeze(ready.metadata)});
  } catch (error) {
    await close().catch(() => {});
    throw error;
  } finally {
    lines.close();
  }
}
