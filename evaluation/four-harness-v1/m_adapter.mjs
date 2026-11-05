import {createHash, randomUUID} from 'node:crypto';
import {mkdtemp, readFile, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';

const REPO = fileURLToPath(new URL('../../', import.meta.url));
const SERVICE = fileURLToPath(new URL('./m_service.py', import.meta.url));
export const WORKFLOW_SOURCE_PIN = Object.freeze({
  commit: '32c68ac2c4f98f0e499bfa99fb6fbf49ec246651',
  sha256: 'af7beeac207c59eae4c0b1fa8a2712ade6a4d8afccb30b234e6b73a4e4feb061',
});
const OLD_SENTENCE = 'No tool or network calls are available.';
const NEW_SENTENCE = 'Only the supplied benchmark tools are available. Use their common JSON contracts; network access is unavailable. After tool use return the required workflow proposal JSON.';
export const COMMON_TOOL_NAMES = Object.freeze({materials_list: 'materials.list', materials_read: 'materials.read',
  workspace_read: 'workspace.read', workspace_write: 'workspace.write', compute: 'compute', check: 'check', submit: 'submit'});
const hash = value => createHash('sha256').update(value).digest('hex');
const canonical = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
const fail = (code, message) => Object.assign(Error(message), {code});
const checkAbort = signal => { if (signal?.aborted) throw fail('CANCELLED', 'Benchmark workflow cancelled'); };

/** The only generated-source change is one exact ACTION_SYSTEM sentence.
 * executeLoop and executeStep, including claim/error/commit behavior, are unchanged.
 */
export async function loadBenchmarkWorkflow({sourcePath = join(REPO, 'workflow_runner.mjs')} = {}) {
  const original = await readFile(sourcePath, 'utf8');
  if (hash(original) !== WORKFLOW_SOURCE_PIN.sha256) throw fail('SOURCE_DRIFT', 'Frozen workflow_runner.mjs source changed');
  if (original.split(OLD_SENTENCE).length !== 2) throw fail('SOURCE_DRIFT', 'Expected one tool-free ACTION_SYSTEM sentence');
  const generated = original.replace(OLD_SENTENCE, NEW_SENTENCE);
  const module = await import(`data:text/javascript;base64,${Buffer.from(generated).toString('base64')}`);
  return {module, evidence: {source_pin: WORKFLOW_SOURCE_PIN, generated_sha256: hash(generated),
    changes: [{before: OLD_SENTENCE, after: NEW_SENTENCE}], execute_loop_unchanged: true, execute_step_unchanged: true}};
}

/** A short-lived Python process per durable transaction, never a model process.
 * root must be a new empty OS-temporary directory; omitted root creates one.
 */
export async function createPythonWorkflowService({root, python = process.env.DSH_MATH_PYTHON ?? 'python3'} = {}) {
  root = await realpath(root ?? await mkdtemp(join(await realpath(tmpdir()), 'four-harness-m-')));
  const call = (action, args) => new Promise((resolve, reject) => {
    const child = spawn(python, ['-I', '-S', '-B', SERVICE, root], {cwd: REPO, stdio: ['pipe', 'pipe', 'pipe']});
    let stdout = '', stderr = '', overflow = false;
    const timer = setTimeout(() => child.kill('SIGTERM'), 30_000);
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { stdout += chunk; if (Buffer.byteLength(stdout) > 16 * 1024 * 1024) { overflow = true; child.kill('SIGTERM'); } });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-16_384); });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', code => {
      clearTimeout(timer);
      if (overflow) return reject(fail('SERVICE_OUTPUT_LIMIT', 'Benchmark service output exceeds limit'));
      let result;
      try { result = JSON.parse(stdout); } catch { return reject(fail('SERVICE_FAILURE', `Benchmark service exited ${code}: ${stderr || stdout}`)); }
      if (code !== 0 || result.ok !== true) return reject(fail(result.error ?? 'SERVICE_FAILURE', result.message ?? stderr));
      resolve(result.value);
    });
    child.stdin.on('error', () => {}); // close/error reports a failed transaction.
    child.stdin.end(JSON.stringify({action, args}));
  });
  return Object.freeze({root, call, initialize: args => call('initialize', args),
    inspect: () => call('inspect', {}), identity: () => call('identity', {})});
}

export function normalizeResponsesUsage(raw) {
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const usage = {input_tokens: integer(raw?.input_tokens), cached_input_tokens: integer(raw?.input_tokens_details?.cached_tokens),
    output_tokens: integer(raw?.output_tokens), reasoning_tokens: integer(raw?.output_tokens_details?.reasoning_tokens),
    total_tokens: integer(raw?.total_tokens), source: 'responses.usage', complete: false};
  usage.complete = Object.entries(usage).filter(([key]) => !['source', 'complete'].includes(key)).every(([, value]) => value !== null)
    && usage.total_tokens === usage.input_tokens + usage.output_tokens
    && usage.cached_input_tokens <= usage.input_tokens && usage.reasoning_tokens <= usage.output_tokens;
  return usage;
}

function validateTools(schemas, scope) {
  if (!['offline', 'formal', 'smoke', 'optimization'].includes(scope)) throw fail('TOOLS_SCOPE', 'A known benchmark tool scope is required');
  const admitted = new Set(Object.keys(COMMON_TOOL_NAMES).filter(name => scope !== 'smoke' || !['check', 'submit'].includes(name)));
  if (!Array.isArray(schemas) || schemas.length !== admitted.size) {
    throw fail('TOOLS', scope === 'smoke' ? 'All five non-target smoke tool schemas are required' : 'All seven common benchmark tool schemas are required');
  }
  const names = new Set();
  for (const schema of schemas) {
    if (schema?.type !== 'function' || !admitted.has(schema.name) || names.has(schema.name) || !schema.parameters) {
      throw fail('TOOLS', 'Use unique Responses function schemas admitted by the current scope');
    }
    names.add(schema.name);
  }
  return JSON.parse(JSON.stringify(schemas));
}

/** Injected transport is called once per physical Responses request. It must
 * reserve/meter through the central ledger, must never retry, and returns either
 * the raw Responses object or {response, requestId} (gateway header identity).
 * Errors with dispatched:false are rejected before dispatch; all others are
 * conservatively unknown. tools({name,arguments,callId,signal}) invokes the shared
 * tool implementation with dotted names and returns its JSON value unchanged.
 */
export function createMAdapter({service, transport, tools, toolSchemas, resolvedModel, contextWindow, toolScope = 'offline'}) {
  if (!service?.call || typeof transport !== 'function' || typeof tools !== 'function') {
    throw fail('CONFIG', 'A Python workflow service, metered transport, and common tools callback are required');
  }
  const schemas = validateTools(toolSchemas, toolScope), toolsHash = hash(canonical(schemas));
  const admittedTools = new Set(schemas.map(schema => schema.name));
  let identity, runner, busy = false, activeAccounting, frozenSpec;
  const evidence = async () => (runner ??= await loadBenchmarkWorkflow()).evidence;
  const getIdentity = async () => {
    identity ??= await service.identity();
    if (identity.tools_sha256 !== toolsHash) throw fail('TOOLS_DRIFT', 'Common tool schemas differ from the frozen benchmark');
    return identity;
  };
  const readRun = async () => {
    const id = await getIdentity();
    return service.call('workflow-state', {action: 'read', project_id: id.project_id, run_id: id.run_id});
  };
  const call = async (action, args) => {
    if (action === 'workflow-state' && ['propose', 'fail'].includes(args.action)) {
      if (!activeAccounting) throw fail('ACCOUNTING', 'Missing benchmark action accounting');
      args = {...args, benchmark_accounting: structuredClone(activeAccounting)};
    }
    return service.call(action, args);
  };
  const resolved = async signal => {
    checkAbort(signal);
    const value = typeof resolvedModel === 'function' ? await resolvedModel({signal}) : resolvedModel;
    if (!value?.provider || !value?.model || !value?.effort || !Number.isSafeInteger(contextWindow) || contextWindow < 1) {
      throw fail('MODEL_IDENTITY', 'Explicit resolved provider/model/effort and contextWindow are required');
    }
    return value;
  };
  const aggregate = () => {
    const usage = activeAccounting.requests.filter(row => row.status !== 'not_dispatched').map(row => row.usage).filter(Boolean);
    const total = usage.reduce((n, row) => n + (row.total_tokens ?? 0), 0);
    const output = usage.reduce((n, row) => n + (row.output_tokens ?? 0), 0);
    // The unchanged runner accepts only total/output. Full nullable usage remains
    // on every request record; these two values are its legacy subtotal projection.
    return usage.some(row => row.total_tokens !== null) && total >= output
      ? {inputTokens: total - output, outputTokens: output, totalTokens: total} : null;
  };

  const makeLLM = emit => ({prepareCall: async (config, signal) => {
    const actual = await resolved(signal);
    activeAccounting = {format: 'dsh-math-m-action-accounting-v1', requests: []};
    return {config: {...config, provider: actual.provider, model: actual.model, reasoningEffort: actual.effort},
      context: {contextWindow}, stream: async function* (options) {
        let input = options.messages.map(message => ({role: message.role,
          content: message.content.map(part => ({type: 'input_text', text: part.text}))}));
        let parentRequestId = null;
        try {
          for (;;) {
            checkAbort(signal);
            const request = {model: config.model, reasoning: {effort: config.reasoningEffort}, max_output_tokens: config.maxTokens,
              instructions: options.system, input, tools: schemas, parallel_tool_calls: false, stream: false, store: false,
              truncation: 'disabled'};
            const inputTokenBound = Buffer.byteLength(JSON.stringify(request)) + 2048;
            if (toolScope !== 'optimization' && (inputTokenBound > frozenSpec.context_budget.max_tokens || inputTokenBound + config.maxTokens > contextWindow)) {
              throw fail('CONTEXT_BUDGET', 'Full benchmark tool context exceeds the frozen context budget');
            }
            const row = {request_id: randomUUID(), parent_request_id: parentRequestId, status: 'unknown', usage: null, raw_usage: null};
            activeAccounting.requests.push(row);
            let received;
            try {
              received = await transport({request: structuredClone(request), requestId: row.request_id, parentRequestId,
                role: 'worker', inputTokenBound, signal});
            } catch (error) {
              if (error.dispatched === false || error.error?.dispatched === false) row.status = 'not_dispatched';
              if (error.request_id ?? error.error?.request_id) row.request_id = error.request_id ?? error.error.request_id;
              if (error.usage) { row.raw_usage = structuredClone(error.usage); row.usage = normalizeResponsesUsage(error.usage); }
              throw error;
            }
            const response = received?.response ?? received;
            row.request_id = received?.requestId ?? received?.request_id ?? row.request_id;
            parentRequestId = row.request_id;
            row.raw_usage = response?.usage ?? null;
            row.usage = normalizeResponsesUsage(response?.usage);
            if (!row.usage.complete) throw fail('USAGE_MISSING', 'Responses usage is missing, incomplete, or inconsistent; reconcile without replay');
            row.status = 'finished';
            if (response.model !== actual.model || response.reasoning?.effort !== actual.effort ||
                (response.provider !== undefined && response.provider !== actual.provider)) {
              throw fail('MODEL_DRIFT', 'Responses model controls differ from frozen policy');
            }
            checkAbort(signal);
            if (response.status !== 'completed' || !Array.isArray(response.output)) {
              throw fail('RESPONSE_INCOMPLETE', 'Responses result did not complete');
            }
            if (row.usage.output_tokens > config.maxTokens) throw fail('OUTPUT_BUDGET', 'Physical request exceeded the frozen output cap');
            const toolCalls = response.output.filter(item => item.type === 'function_call');
            if (!toolCalls.length) {
              const text = response.output.filter(item => item.type === 'message').flatMap(item => item.content ?? [])
                .filter(item => item.type === 'output_text').map(item => item.text).join('');
              yield {type: 'text-delta', text};
              yield {type: 'usage', usage: aggregate()};
              yield {type: 'finish', reason: {kind: 'stop'}};
              return;
            }
            const toolResults = [], callIds = new Set();
            for (const toolCall of toolCalls) {
              checkAbort(signal);
              if (!admittedTools.has(toolCall.name) || typeof toolCall.call_id !== 'string' || callIds.has(toolCall.call_id)) {
                throw fail('UNAUTHORIZED_TOOL', 'Responses requested an unknown tool or duplicate call id');
              }
              callIds.add(toolCall.call_id);
              const args = JSON.parse(toolCall.arguments);
              if (!args || typeof args !== 'object' || Array.isArray(args)) throw fail('TOOL_ARGUMENTS', 'Tool arguments must be a JSON object');
              const name = COMMON_TOOL_NAMES[toolCall.name], started = performance.now();
              await emit({type: 'tool_started', request_id: row.request_id, call_id: toolCall.call_id, name,
                arguments_sha256: hash(canonical(args))});
              const result = await tools({name, arguments: args, callId: toolCall.call_id, requestId: row.request_id, signal});
              const output = JSON.stringify(result);
              if (output === undefined) throw fail('TOOL_RESULT', 'Common tools must return a JSON value');
              await emit({type: 'tool_finished', request_id: row.request_id, call_id: toolCall.call_id, name,
                result_sha256: hash(canonical(result)), wall_seconds: (performance.now() - started) / 1000});
              toolResults.push({type: 'function_call_output', call_id: toolCall.call_id, output});
            }
            input = [...input, ...response.output, ...toolResults];
          }
        } catch (error) {
          const usage = aggregate();
          if (usage) yield {type: 'usage', usage};
          throw error;
        }
      }};
  }});

  return Object.freeze({
    async preflight() {
      const source = await evidence(), actual = await resolved();
      return {ready: true, mode: 'injected_transport_only', source, resolved_model: actual,
        tools_sha256: toolsHash, tool_scope: toolScope, capabilities: schemas.map(schema => COMMON_TOOL_NAMES[schema.name]),
        accounting_coverage: 'every tool iteration invokes the required central metered transport'};
    },
    async initialize({runId, problem, modelPolicy, budget, contextBudget, stageGoal}) {
      if (busy || identity) throw fail('INVALID_STATE', 'Benchmark adapter is already initialized or running');
      await evidence();
      const initialized = await service.initialize({run_id: runId, problem, model_policy: modelPolicy,
        budget, context_budget: contextBudget, stage_goal: stageGoal, tools_sha256: toolsHash});
      identity = initialized.isolation;
      return initialized;
    },
    async run({kind = 'derive', targetRef = null, dependencyRefs = [], emit = async () => {}, signal} = {}) {
      if (busy) throw fail('INVALID_STATE', 'One benchmark loop at a time');
      busy = true;
      try {
        await evidence();
        let run = await readRun();
        frozenSpec = run.spec;
        checkAbort(signal);
        if (run.status !== 'prepared' || run.actions.length) throw fail('NO_REPLAY', 'Existing workflow cannot be dispatched again; reconcile only');
        // Resolve before start/reservation, and again in the unchanged executeStep.
        const actual = await resolved(signal);
        if (canonical(actual) !== canonical(run.spec.model_policy)) throw fail('MODEL_DRIFT', 'Resolved model controls differ from frozen RunSpec');
        run = await service.call('workflow-state', {action: 'start', project_id: identity.project_id, run_id: identity.run_id,
          expected_rev: run.rev, operation_id: randomUUID()});
        const result = await runner.module.executeLoop({call, llm: makeLLM(emit), projectId: identity.project_id,
          runId: identity.run_id, expectedRev: run.rev, kind, targetRef, dependencyRefs, signal});
        return {run: result, source: runner.evidence, tools_sha256: toolsHash, accounting_authority: 'central_request_ledger'};
      } finally { busy = false; }
    },
    async reconcile() {
      if (busy) throw fail('INVALID_STATE', 'Wait for the active loop before reconciliation');
      const run = await readRun();
      const active = run.actions.find(action => ['running', 'proposed'].includes(action.status));
      if (!active) return {run, dispatched: false};
      const result = await service.call('workflow-control', {project_id: identity.project_id, session_id: identity.session_id,
        command: {action: 'reconcile', run_id: identity.run_id, expected_rev: run.rev, operation_id: randomUUID()}});
      return {run: result, dispatched: false};
    },
    read: readRun,
  });
}
