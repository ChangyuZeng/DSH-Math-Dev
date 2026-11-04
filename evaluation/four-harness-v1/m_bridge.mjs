import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {writeFile} from 'node:fs/promises';
import {createMAdapter, createPythonWorkflowService, COMMON_TOOL_NAMES} from './m_adapter.mjs';

const API_NAMES = Object.fromEntries(Object.entries(COMMON_TOOL_NAMES).map(([api, common]) => [common, api]));
const error = (code, message, extra = {}) => Object.assign(Error(message), {code, ...extra});
const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
const SMOKE_POLICY = {provider: 'openai-codex', model: 'gpt-6-astra', effort: 'max'};
const SMOKE_BUDGET = {model_requests: 2, total_tokens: 131072, max_output_tokens: 2048, context_tokens: 65536,
  wall_seconds: 180, tool_calls: 8, tool_timeout_seconds: 10, artifact_bytes: 16777216};
const SMOKE_TASK = 'Non-target arithmetic task: compute 2 + 3 using a common tool,\nthen report the result in the native harness. This is an engineering check.\n';
const SMOKE_SUBMISSION = 'Report the arithmetic result through the native harness; no target construction is requested.\n';

function requireSmoke(manifest, execution, offline) {
  const expected = {mode: 'authorized_smoke', arm: 'M', run_id: execution?.run_id,
    campaign_id: execution?.campaign_id, model_requests: 2, campaign_max_requests: 8};
  const names = manifest.tools?.map(tool => tool.name).sort();
  const five = Object.keys(COMMON_TOOL_NAMES).filter(name => !['check', 'submit'].includes(name)).sort();
  if ((!offline && (!execution || typeof execution.run_id !== 'string' || !execution.run_id ||
        typeof execution.campaign_id !== 'string' || !execution.campaign_id ||
        canonical(execution) !== canonical(expected) || canonical(manifest.execution) !== canonical(execution))) ||
      manifest.offline !== offline || manifest.scope !== 'smoke' || manifest.canonical_cold_input_checked !== true ||
      canonical(manifest.model_policy) !== canonical(SMOKE_POLICY) || manifest.context_window !== 65536 ||
      canonical(manifest.budget) !== canonical(SMOKE_BUDGET) || canonical(names) !== canonical(five)) {
    throw error('SMOKE_AUTHORIZATION', 'Exact cold smoke execution, model, budget, and five tools must be bound by the gateway');
  }
}

/** Localhost transport binding. Live execution requires the host gateway's exact
 * authorized-smoke manifest and caller-bound run/campaign identity. No upstream
 * discovery, credentials, retries, or alternative model clients are present.
 */
export function createGatewayBindings({gateway, token, fetchImpl = globalThis.fetch}) {
  const base = new URL(gateway);
  if (base.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(base.hostname) || base.pathname !== '/' ||
      base.search || base.hash || base.username || base.password || !base.port) {
    throw error('GATEWAY_ENDPOINT', 'Use an explicit loopback HTTP gateway origin');
  }
  if (typeof token !== 'string' || !token || /[\r\n]/.test(token)) throw error('CAPABILITY', 'Gateway capability token required');
  let manifest;
  const post = async (path, body, {signal, parentRequestId} = {}) => {
    const headers = {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'};
    if (parentRequestId) headers['X-Four-Parent-Request-Id'] = parentRequestId;
    let received;
    try {
      received = await fetchImpl(new URL(`/M/worker/${path}`, base), {method: 'POST', headers,
        body: JSON.stringify(body), signal, redirect: 'error'});
    } catch {
      // A broken connection is unknown even if the peer might not have received
      // the body. The ledger owns the durable reservation; never replay here.
      throw error('GATEWAY_CONNECTION', 'Gateway connection ended without a receipt; no retry', {dispatched: true});
    }
    let value;
    try { value = await received.json(); } catch {
      throw error('GATEWAY_RECEIPT', 'Gateway returned no JSON receipt; no retry', {dispatched: true,
        request_id: received.headers.get('X-Four-Request-Id')});
    }
    if (!received.ok) {
      const receipt = value?.error;
      throw error(receipt?.code ?? 'GATEWAY_REJECTED', receipt?.message ?? 'Gateway rejected request',
        {dispatched: receipt?.dispatched !== false, request_id: receipt?.request_id ?? received.headers.get('X-Four-Request-Id'),
          usage: receipt?.usage});
    }
    return {value, requestId: received.headers.get('X-Four-Request-Id')};
  };
  return Object.freeze({
    async preflight({requireOffline = true, execution, smoke = false, signal} = {}) {
      manifest = undefined; // A failed new preflight revokes the earlier admission.
      const {value} = await post('manifest', {}, {signal});
      if (requireOffline && value.offline !== true) throw error('LIVE_DISABLED', 'A verified offline fixture gateway is required');
      if (!requireOffline || smoke) requireSmoke(value, execution, requireOffline);
      if (!value.model_policy || !Array.isArray(value.tools) || !Number.isSafeInteger(value.context_window)
          || !['offline', 'formal', 'smoke'].includes(value.scope)) {
        throw error('GATEWAY_MANIFEST', 'Gateway must declare exact model policy, tool scope, common tools, and context window');
      }
      manifest = structuredClone(value);
      return structuredClone(manifest);
    },
    async transport({request, parentRequestId, signal}) {
      if (!manifest) throw error('PREFLIGHT_REQUIRED', 'Gateway manifest must be checked before dispatch', {dispatched: false});
      const {value, requestId} = await post('v1/responses', request, {signal, parentRequestId});
      if (!requestId) throw error('REQUEST_ID_MISSING', 'Gateway physical request identity is missing', {dispatched: true, usage: value.usage});
      return {response: value, requestId};
    },
    async tools({name, arguments: args, requestId, signal}) {
      if (!manifest) throw error('PREFLIGHT_REQUIRED', 'Gateway manifest must be checked before tools');
      if (!Object.hasOwn(API_NAMES, name) || !manifest.tools.some(schema => schema.name === API_NAMES[name])) {
        throw error('TOOL_NOT_ALLOWED', 'Only tools admitted by the current scope are available');
      }
      return (await post('tools', {name: API_NAMES[name], arguments: args}, {signal, parentRequestId: requestId})).value;
    },
  });
}

export function syntheticProblem() {
  return {title: 'Synthetic addition fixture', statement: 'Compute 2 + 3 using only synthetic inputs.',
    quantifiers: 'one pair', domain: 'integers', conventions: [], target_type: 'engineering_fixture',
    parent: null, relation: '', completion_criteria: ['The synthetic result is five'],
    sources: ['new temporary fixture'], knowledge_status: 'synthetic_fixture'};
}

export function syntheticProposal() {
  return {summary: 'Synthetic addition candidate and review obligation recorded', changes: [
    {kind: 'Note', id: 'N', expected_rev: 0, data: {note_type: 'argument', title: 'Addition candidate',
      summary: 'Two and three total five.', body: '2 + 3 = 5.', statement: 'The result is five.', assumptions: [],
      problem_ref: {id: 'P', rev: 1}, route_id: null, uses: [], candidate_for: [{id: 'O', rev: 1}],
      evidence: [], gaps: ['Needs review'], failure: null}},
    {kind: 'Obligation', id: 'O', expected_rev: 0, data: {statement: 'Check the addition candidate.',
      scope: 'Only this synthetic input', completion_criteria: ['Verify the displayed arithmetic'],
      check_method: 'human', checker_id: null, problem_ref: {id: 'P', rev: 1}, uses: [{id: 'N', rev: 1}],
      candidates: [{id: 'N', rev: 1}], evidence: [], next_action: 'Check arithmetic', state: 'candidate',
      reason: 'Unreviewed synthetic candidate'}},
  ], next_action: 'stop', next_target: null, progress: 'new_obligation', limitations: ['Synthetic candidate only; no human Review']};
}

export function smokeBootstrap() {
  return {problem: {title: 'Canonical non-target arithmetic smoke', statement: SMOKE_TASK,
    quantifiers: 'one specified input', domain: 'integers', conventions: [], target_type: 'engineering_fixture',
    parent: null, relation: '', completion_criteria: [SMOKE_SUBMISSION],
    sources: ['canonical smoke task.txt and submission.txt'], knowledge_status: 'synthetic_fixture'},
    stageGoal: SMOKE_TASK + SMOKE_SUBMISSION};
}

function parseArguments(argv) {
  const required = ['gateway', 'token-env', 'root', 'run-id', 'python'];
  const accepted = new Set([...required, 'execution-env', 'evidence']);
  const result = {};
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (['--offline', '--smoke'].includes(arg)) {
      if (result[arg.slice(2)]) throw error('ARGUMENTS', 'Duplicate execution mode flag');
      result[arg.slice(2)] = true; continue;
    }
    if (!arg.startsWith('--') || !accepted.has(arg.slice(2)) || !argv[index + 1] || Object.hasOwn(result, arg.slice(2))) {
      throw error('ARGUMENTS', 'Expected --offline and/or --smoke with gateway, token-env, root, run-id, python');
    }
    result[arg.slice(2)] = argv[++index];
  }
  if (!result.offline && !result.smoke) throw error('LIVE_DISABLED', 'Explicit offline fixture or authorized smoke execution required');
  if (required.some(name => !result[name])) throw error('ARGUMENTS', 'All bridge arguments are required');
  for (const name of ['token-env', 'execution-env'])
    if (result[name] && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(result[name])) throw error('ARGUMENTS', 'Expected an environment variable name');
  if (!result.offline && !result['execution-env']) throw error('SMOKE_AUTHORIZATION', 'Live smoke requires an execution binding environment variable');
  return result;
}

export async function runBridge(argv, {env = process.env, stdin = process.stdin, signal} = {}) {
  const args = parseArguments(argv);
  const token = env[args['token-env']];
  let execution;
  if (!args.offline) {
    try { execution = JSON.parse(env[args['execution-env']] ?? 'null'); } catch { /* rejected below */ }
    if (!execution || execution.run_id !== args['run-id']) throw error('SMOKE_AUTHORIZATION', 'CLI run must match its exact smoke execution binding');
  }
  const bindings = createGatewayBindings({gateway: args.gateway, token});
  const manifest = await bindings.preflight({requireOffline: args.offline === true, execution, smoke: args.smoke === true, signal});
  let text = '';
  for await (const chunk of stdin) {
    text += chunk;
    if (Buffer.byteLength(text) > 1024 * 1024) throw error('BOOTSTRAP_LIMIT', 'Bootstrap JSON exceeds limit');
  }
  if (args.smoke && text.trim()) throw error('BOOTSTRAP_SCOPE', 'Smoke uses only the frozen canonical task; custom bootstrap is unavailable');
  const bootstrap = args.smoke ? smokeBootstrap() : text.trim() ? JSON.parse(text) : {};
  const service = await createPythonWorkflowService({root: args.root, python: args.python});
  const physicalRequests = [];
  const transport = async request => {
    const record = {request: structuredClone(request.request), request_id: null,
      parent_request_id: request.parentRequestId ?? null};
    physicalRequests.push(record);
    try { const result = await bindings.transport(request); record.request_id = result.requestId; return result; }
    catch (failure) { record.request_id = failure.request_id ?? null; record.error = {code: failure.code, dispatched: failure.dispatched}; throw failure; }
  };
  const adapter = createMAdapter({service, transport, tools: bindings.tools,
    toolSchemas: manifest.tools, toolScope: manifest.scope,
    resolvedModel: manifest.model_policy, contextWindow: manifest.context_window});
  await adapter.initialize({runId: args['run-id'], problem: bootstrap.problem ?? syntheticProblem(),
    modelPolicy: manifest.model_policy, budget: bootstrap.budget ?? {
      requests: manifest.budget?.model_requests, total_tokens: manifest.budget?.total_tokens,
      max_output_tokens: manifest.budget?.max_output_tokens},
    contextBudget: bootstrap.contextBudget ?? {max_tokens: manifest.context_window - manifest.budget?.max_output_tokens},
    stageGoal: bootstrap.stageGoal ?? 'Record a candidate for 2 + 3 and its unreviewed obligation using only the synthetic fixture.'});
  let result, failure;
  try { result = await adapter.run({signal}); }
  catch (caught) {
    failure = {code: caught.code ?? 'WORKFLOW_FAILED', message: caught.message};
    result = {run: await adapter.read()};
  }
  const evidence = {...result, ok: !failure, error: failure ?? null, research: await service.inspect(),
    physical_requests: physicalRequests, workflow_root: service.root, offline: manifest.offline, execution: execution ?? null,
    mechanisms: {loop: 'current_executeLoop', resume_dispatch: false, parallel_dispatch: false, compaction: false,
      durable_reconciliation_without_replay: true}};
  if (args.evidence) await writeFile(args.evidence, JSON.stringify(evidence), {flag: 'wx', mode: 0o600});
  return evidence;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  try {
    const result = await runBridge(process.argv.slice(2), {signal: controller.signal});
    process.stdout.write(JSON.stringify(result) + '\n');
    if (!result.ok) process.exitCode = 1;
  } catch (failure) {
    process.stdout.write(JSON.stringify({ok: false, error: {code: failure.code ?? 'BRIDGE_FAILED', message: failure.message}}) + '\n');
    process.exitCode = 1;
  } finally {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
}
