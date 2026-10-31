import {spawnSync} from 'node:child_process';
import {createHash, randomUUID} from 'node:crypto';
import {readFileSync, writeFileSync, existsSync, appendFileSync, watchFile, unwatchFile} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {apply as applyTools, MODEL_TOOLS} from './host.mjs';

export const name = 'dsh-math-live';
export const inject = ['tools', 'llm', 'agents'];
const here = dirname(fileURLToPath(import.meta.url));

export function apply(ctx, config) {
  const raw = readFileSync(config.manifest);
  const manifest = JSON.parse(raw);
  manifest.provider ??= 'openai-codex';
  const anchor = createHash('sha256').update(raw).digest('hex');
  const root = manifest.output_dir;
  const lifetime = new AbortController();
  let sealed = false, seq = 0, previous = '0'.repeat(64);
  const log = (kind, data = {}) => {
    const row = {seq: ++seq, previous, time: new Date().toISOString(), kind, run_id: manifest.run_id, session_id: manifest.session_id, ...data};
    const sha256 = createHash('sha256').update(JSON.stringify(row)).digest('hex');
    appendFileSync(join(root, 'runtime-audit.jsonl'), JSON.stringify({...row, sha256}) + '\n');
    previous = sha256;
  };
  function control(action, args = {}) {
    const result = spawnSync(config.python, ['-B', join(here, 'run_control.py')], {
      input: JSON.stringify({manifest: config.manifest, manifest_sha256: anchor, action, args}), encoding: 'utf8',
      env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, timeout: 30000, maxBuffer: 4 * 1024 * 1024,
    });
    let value;
    try { value = JSON.parse(result.stdout); } catch { throw Error('Run ledger control unavailable'); }
    if (result.status !== 0 || !value.ok) throw Error(value.error + ': ' + value.message);
    return value.value;
  }
  function seal(reason, body = null) {
    if (sealed) return;
    sealed = true;
    lifetime.abort(Error(reason));
    const agent = ctx.agents.get(manifest.session_id);
    agent?.cancel({kind: 'user'});
    log('run.sealed', {reason});
    writeFileSync(join(root, 'runtime-stop.json'), JSON.stringify({reason, body, run_id: manifest.run_id}), {flag: 'wx'});
  }
  const tools = applyTools(ctx, {...config, headless: true, deferCloseout: true, modelRuns: [{projectId: manifest.project_id, sessionId: manifest.session_id,
    runId: manifest.run_id, manifest: config.manifest}], onResearchStop: seal});
  control('open');
  ctx.on('agent/created', async ({agent}) => {
    if (agent.id !== manifest.session_id) throw Error('Unexpected agent/session; subagents disabled');
    agent.ctx.tools.restrict({allow: MODEL_TOOLS});
    const info = await ctx.llm.resolveModelInfo(manifest.provider, manifest.model);
    if (info.provider !== manifest.provider || info.id !== manifest.model || !info.reasoning?.efforts?.some(e => e.id === manifest.effort)) throw Error('Requested model/effort unavailable');
    writeFileSync(join(root, 'resolved-model.json'), JSON.stringify(info, null, 2) + '\n', {flag: 'wx'});
    log('model.resolved', {info});
  });
  ctx.on('llm/stream', async function* (options, next) {
    if (sealed || options.sessionId !== manifest.session_id || options.provider !== manifest.provider || options.model !== manifest.model || options.reasoningEffort !== manifest.effort || options.purpose) {
      seal('model_identity_or_auxiliary_call_mismatch');
      throw Error('Run model identity mismatch or auxiliary call denied');
    }
    if (JSON.stringify((options.tools ?? []).map(t => t.name).sort()) !== JSON.stringify([...MODEL_TOOLS].sort())) {
      seal('tool_allowlist_mismatch'); throw Error('Unexpected tool set');
    }
    const requestId = randomUUID();
    control('reserve', {request_id: requestId, model: options.model, effort: options.reasoningEffort});
    log('request.reserved', {id: requestId, model: options.model, effort: options.reasoningEffort, maxTokens: options.maxTokens ?? null,
      request_sha256: createHash('sha256').update(JSON.stringify({...options, signal: undefined})).digest('hex')});
    let usage = null, failure = null, terminal = false;
    try {
      const iterator = next();
      for await (const chunk of iterator) {
        if (lifetime.signal.aborted) throw lifetime.signal.reason;
        if (chunk.type === 'usage') usage = chunk.usage;
        if (chunk.type === 'finish') {
          terminal = true;
          if (['error', 'aborted'].includes(chunk.reason.kind)) failure = chunk.reason.failure?.code ?? chunk.reason.kind;
        }
        log('service.frame', {id: requestId, chunk});
        yield chunk;
      }
    } catch (error) { failure = String(error.message ?? error); throw error; }
    finally {
      if (!terminal && !failure) failure = 'incomplete_stream';
      try { control('settle', {request_id: requestId, usage, error: failure}); }
      catch (error) { log('settle.error', {id: requestId, error: error.message}); }
      log('request.settled', {id: requestId, usage, complete: terminal && !failure, error: failure});
      if (failure) seal('technical_failed:' + failure);
    }
  });
  const cancelFile = join(root, 'cancel-request.json');
  const cancel = () => { if (existsSync(cancelFile)) { seal('user_cancelled'); tools.cancelRun(manifest.run_id, 'user_cancelled').catch(error => log('cleanup.error', {error: error.message})); } };
  watchFile(cancelFile, {interval: 200}, cancel);
  ctx.effect(() => async () => {
    unwatchFile(cancelFile, cancel);
    sealed = true;
    lifetime.abort(Error('runtime disposed'));
    try {
      const receipt = await tools.cancelRun(manifest.run_id, 'runtime_shutdown');
      writeFileSync(join(root, 'tools-cleanup.json'), JSON.stringify(receipt, null, 2));
    } catch (error) { log('cleanup.error', {error: error.message}); }
  });
  log('runtime.ready', {protocol: manifest.protocol_id, manifest_sha256: anchor});
}
