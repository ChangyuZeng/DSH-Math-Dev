import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {humanApiRejection} from './human-api.mjs';
import {registerChatTools, CHAT_TOOLS} from './chat-tools.mjs';
import {executeStep, executeLoop, workflowCapability} from './workflow_runner.mjs';

const here = dirname(fileURLToPath(import.meta.url));
export const name = 'dsh-math';
export const inject = ['webServer', 'tools', 'connection'];

export const MODEL_TOOLS = ['research_read', 'note_save', 'obligation_save', 'evidence_register', 'check_witness', 'material_read', 'file_read', 'file_write', 'python_run', 'research_finish'];
const DESCRIPTIONS = {
  research_read: 'Read versioned objects. Required payload fields: {kind,state,text,object_id,rev}; text:"" and null for unused filters. Output is capped at 65536 JSON UTF-8 bytes. For a directory add page:{offset:0,limit:20} with object_id/rev:null; use page.next_offset and health.seq to detect changes. For exact large text add window:{field:"body",offset:0,limit:8000} with object_id and positive rev; window.next_offset counts Unicode characters. page/window are exclusive. Optional max_output_bytes:1024..65536. READ_OUTPUT_LIMIT means retry with a smaller page/window; content is never silently truncated.',
  note_save: 'Save a Note. payload JSON: {operation_id,id,expected_rev,data}. data requires note_type,title,summary,body,statement,assumptions,problem_ref,route_id,uses,candidate_for,evidence,gaps,failure. Use expected_rev 0 for new objects or the current revision when revising.',
  obligation_save: 'Save an open/candidate Obligation with {operation_id,id,expected_rev,data}; preserve scope and link exact evidence revisions. Use expected_rev 0 for new objects or the current revision when revising. Cannot change Problem or write human Review.',
  evidence_register: 'Register Evidence. payload JSON: {operation_id,id,expected_rev,data,attachment_text}. data must include attachments:[] plus evidence_type,problem_ref,source,source_status,support_scope,limits,production,citation; the host archives attachment_text.',
  check_witness: 'Run a fixed checker. payload JSON: {operation_id,checker_id,target:{id,rev},witness:{id,rev}}; the target Obligation revision must link that Evidence revision. Program pass remains pending scientific review.',
  material_read: 'List or read frozen materials. payload JSON: {name}; use name:"" to list exact registered names, then a name from that list to read.',
  file_read: 'Read UTF-8 text from this run workspace only: {path}.',
  file_write: 'Write UTF-8 text in this run workspace only: {path,text}.',
  python_run: 'Run Python in the no-network isolated workspace: {code}. Each call has a fixed timeout; write progress and checkpoints incrementally so a later call can resume. Timeout/output status is not a completed search. No shell, packages, host paths, or subagents.',
  research_finish: 'End research without further model requests. payload JSON: {classification:"candidate"|"no_progress"|"local_claim"|"known_reproduction"|"valid_below_target",summary:string,references:[{id,rev}]}. All claims remain pending scientific review.',
};

function spawnCommand(argv, request, {signal, onChild, timeoutMs = 60000} = {}) {
  return new Promise((resolveCall, reject) => {
    if (signal?.aborted) return reject(Error('Run cancelled before dispatch'));
    const child = spawn(argv[0], argv.slice(1), {
      stdio: ['pipe', 'pipe', 'pipe'], env: {...process.env, PYTHONDONTWRITEBYTECODE: '1', DSH_MATH_NODE: process.execPath, ELECTRON_RUN_AS_NODE: '1'}, detached: Boolean(signal),
    });
    let output = '', error = '', settled = false;
    const done = new Promise(resolveDone => child.once('close', resolveDone));
    onChild?.(child, done);
    const stop = () => {
      if (child.exitCode !== null || child.signalCode !== null) return;
      try { if (signal) process.kill(-child.pid, 'SIGKILL'); else child.kill('SIGTERM'); } catch (failure) { if (failure.code !== 'ESRCH') error += failure.message; }
    };
    const timer = setTimeout(stop, timeoutMs);
    signal?.addEventListener('abort', stop, {once: true});
    child.stdout.on('data', data => { output += data; if (output.length > 32 * 1024 * 1024) stop(); });
    child.stderr.on('data', data => { error += data; });
    child.on('error', failure => { if (!settled) { settled = true; clearTimeout(timer); reject(failure); } });
    child.on('close', code => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', stop);
      try {
        const result = JSON.parse(output);
        if (!result.ok || code) reject(Error(`${result.error}: ${result.message}`));
        else resolveCall(result.value);
      } catch (failure) { reject(Error(error || failure.message)); }
    });
    child.stdin.on('error', failure => { error ||= failure.message; });
    child.stdin.end(JSON.stringify(request));
  });
}

function spawnPython(script, root, request, python = 'python3', options) {
  return spawnCommand([python, '-B', join(here, script), root], request, options);
}

export function pythonCall(root, request, python = 'python3') {
  return spawnPython('backend.py', root, request, python);
}

function definition(tool, execute) {
  return {name: tool, description: DESCRIPTIONS[tool],
    parameters: {type: 'object', properties: {payload: {type: 'string'}}, required: ['payload'], additionalProperties: false},
    output: {schema: {type: 'string'}, render: (_, value) => [{type: 'text', text: value}]}, execute};
}

export function apply(ctx, config = {}) {
  const root = resolve(config.root ?? join(process.cwd(), '.dsh-math'));
  const bindings = config.modelRuns ?? [];
  if (!Array.isArray(bindings)) throw Error('modelRuns must be an array of host bindings');
  const gates = new Map();
  for (const binding of bindings) {
    if (!binding || typeof binding.projectId !== 'string' || typeof binding.sessionId !== 'string' || typeof binding.runId !== 'string' || typeof binding.manifest !== 'string' || gates.has(binding.runId)) throw Error('Invalid model run binding');
    const raw = readFileSync(binding.manifest);
    const manifest = JSON.parse(raw);
    if (manifest.project_id !== binding.projectId || manifest.session_id !== binding.sessionId || manifest.run_id !== binding.runId ||
        !Number.isInteger(manifest.limits?.cleanup_seconds) || manifest.limits.cleanup_seconds <= 0 || manifest.limits.cleanup_seconds > 120 ||
        !Number.isInteger(manifest.limits?.tool_timeout_seconds) || manifest.limits.tool_timeout_seconds <= 0 || manifest.limits.tool_timeout_seconds > 60) throw Error('Model run binding differs from its manifest');
    gates.set(binding.runId, {binding, manifestSha: createHash('sha256').update(raw).digest('hex'), sealed: false, active: new Map(), pending: Promise.resolve(), done: null, cleanupMs: manifest.limits.cleanup_seconds * 1000, toolTimeoutMs: manifest.limits.tool_timeout_seconds * 1000});
  }
  let pending = Promise.resolve();
  const workflowActive = new Map();
  const workflowCall = (action, args) => spawnPython('workflow_service.py', root, {action, args}, config.python);
  const capability = () => workflowCapability(config.workflowExecution, ctx.get?.('llm'));
  async function workflowRequest(request) {
    const args = request.args;
    if (!args || typeof args.project_id !== 'string' || typeof args.session_id !== 'string') throw Error('Workflow project/session required');
    const cap = capability();
    if (request.action === 'workflow') {
      if (args.command?.action === 'start' && !cap.enabled) throw Error(cap.reason);
      if (['pause', 'cancel', 'reconcile'].includes(args.command?.action)) {
        const current = await workflowCall('workflow-control', {project_id: args.project_id, session_id: args.session_id,
          command: {action: 'read', run_id: args.command.run_id}});
        if (current.rev !== args.command.expected_rev) throw Error('Workflow revision changed; refresh before control');
        const active = workflowActive.get(`${args.project_id}/${args.command.run_id}`);
        if (active) {
          active.controller.abort();
          await active.done;
          const latest = await workflowCall('workflow-control', {project_id: args.project_id, session_id: args.session_id,
            command: {action: 'read', run_id: args.command.run_id}});
          // An interrupted request is already paused pending reconciliation.
          if (args.command.action === 'pause' && ['paused', 'needs_reconcile'].includes(latest.status)) return {run: latest, capability: cap};
          args.command.expected_rev = latest.rev;
        }
      }
      const value = await workflowCall('workflow-control', args);
      return args.command?.action === 'list' ? {runs: value, capability: cap} : {run: value, capability: cap};
    }
    if (!cap.enabled) throw Error(cap.reason);
    const activeKey = `${args.project_id}/${args.run_id}`;
    if (workflowActive.has(activeKey)) throw Error('Workflow action already active');
    // Establish session authority through the same shared human-control gate.
    await workflowCall('workflow-control', {project_id: args.project_id, session_id: args.session_id,
      command: {action: 'read', run_id: args.run_id}});
    if (workflowActive.has(activeKey)) throw Error('Workflow action already active');
    const controller = new AbortController();
    let settled;
    const active = {controller, done: new Promise(resolve => { settled = resolve; })};
    workflowActive.set(activeKey, active);
    try {
      const run = await (request.action === 'workflow-run' ? executeLoop : executeStep)({call: workflowCall, llm: ctx.get('llm'), projectId: args.project_id,
        runId: args.run_id, expectedRev: args.expected_rev, kind: args.kind, targetRef: args.target_ref,
        dependencyRefs: args.dependency_refs, routeId: args.route_id, signal: controller.signal});
      return {run, capability: cap};
    } finally { if (workflowActive.get(activeKey) === active) workflowActive.delete(activeKey); settled(); }
  }
  ctx.effect(() => () => { for (const active of workflowActive.values()) active.controller.abort(); }, 'dsh-math: stop workflow calls');
  if (!config.headless) ctx.effect(() => ctx.webServer.register({kind: 'exact', path: '/dsh-math/api', async handler(req, res) {
    const reply = (status, value) => {
      res.writeHead(status, {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff'});
      res.end(JSON.stringify(value));
    };
    if (req.method !== 'POST') return reply(405, {error: 'POST required'});
    const rejection = humanApiRejection(ctx, req);
    if (rejection) return reply(rejection.status, {error: rejection.error});
    try {
      let body = '';
      for await (const chunk of req) {
        body += chunk;
        if (Buffer.byteLength(body) > 1024 * 1024) return reply(413, {error: 'Request exceeds 1 MiB'});
      }
      const request = JSON.parse(body);
      if (['workflow', 'workflow-step', 'workflow-run'].includes(request.action)) {
        return reply(200, {ok: true, value: await workflowRequest(request)});
      }
      const task = pending.then(() => pythonCall(root, request, config.python));
      pending = task.catch(() => {});
      reply(200, {ok: true, value: await task});
    } catch (error) { reply(400, {ok: false, error: error.message}); }
  }}), 'dsh-math: local human API');

  function modelCall(gate, request, signal) {
    if (gate.sealed) return Promise.reject(Error('Run is sealed'));
    const task = gate.pending.then(() => {
      if (gate.sealed) throw Error('Run is sealed before dispatch');
      return spawnPython('model_tools.py', root, request, config.python, {signal, timeoutMs: gate.toolTimeoutMs + 30000, onChild(child, done) {
        gate.active.set(child.pid, {child, done});
        done.finally(() => gate.active.delete(child.pid));
      }});
    });
    gate.pending = task.catch(() => {});
    return task;
  }

  if (bindings.length) {
    for (const tool of MODEL_TOOLS) ctx.tools.register(definition(tool, async (args, exec) => {
      if (!exec.agent?.id || !exec.callId) throw Error('Missing host execution identity');
      const gate = [...gates.values()].find(value => value.binding.sessionId === exec.agent.id);
      if (!gate) throw Error('No host-bound M run for this session');
      if (gate.sealed) throw Error('Run is sealed');
      if (!args || Object.keys(args).length !== 1 || typeof args.payload !== 'string') throw Error('Expected payload only');
      const arguments_ = JSON.parse(args.payload);
      const value = await modelCall(gate, {project_id: gate.binding.projectId, session_id: exec.agent.id, run_id: gate.binding.runId,
        tool_call_id: exec.callId, name: tool, arguments: arguments_, manifest: gate.binding.manifest, manifest_sha256: gate.manifestSha}, exec.signal);
      const body = value.result?.value;
      const checked = body?.review?.data?.execution?.result;
      if (body?.request_stop || checked?.verdict === 'pass' && checked?.values?.target_met === true) {
        gate.sealed = true;
        config.onResearchStop?.(body?.request_stop ? 'model_finished' : 'target_candidate_pending_review', body);
      }
      return JSON.stringify(value);
    }));
    ctx.on?.('agent/created', ({agent}) => {
      if (bindings.some(binding => binding.sessionId === agent.id)) agent.ctx.tools.restrict({allow: MODEL_TOOLS});
    });
  }

  if (!config.headless && config.chatPreparation !== false) registerChatTools(ctx, {
    root, python: config.python, bindings,
    run: (argv, request, signal) => spawnCommand(argv, request, {signal}),
  });

  function cancelRun(runId, reason = 'user_cancelled') {
    const gate = gates.get(runId);
    if (!gate) throw Error('Unknown run');
    if (gate.done) return gate.done;
    gate.sealed = true;
    for (const {child} of gate.active.values()) {
      try { process.kill(-child.pid, 'SIGKILL'); } catch (failure) { if (failure.code !== 'ESRCH') gate.killError = failure.message; }
    }
    const deadline = Date.now() + gate.cleanupMs;
    gate.done = (async () => {
      let timedOut = false;
      if (gate.active.size) {
        let timer;
        await Promise.race([Promise.allSettled([...gate.active.values()].map(value => value.done)),
          new Promise(resolve => { timer = setTimeout(() => { timedOut = true; resolve(); }, Math.max(1, deadline - Date.now())); })]);
        clearTimeout(timer);
      }
      const components = {model_tools: {confirmed: !timedOut && gate.active.size === 0 && !gate.killError, active: gate.active.size, error: gate.killError ?? null}};
      if (config.deferCloseout) return {status: components.model_tools.confirmed ? 'cleanup_confirmed' : 'cleanup_unverified', reason, components, timedOut};
      return spawnPython('run_closeout.py', root, {manifest: gate.binding.manifest, reason, components, timed_out: timedOut}, config.python);
    })();
    return gate.done;
  }
  return {cancelRun, modelToolNames: [...MODEL_TOOLS], chatToolNames: !config.headless && config.chatPreparation !== false ? [...CHAT_TOOLS] : []};
}
