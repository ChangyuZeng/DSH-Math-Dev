import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {createMAdapter, createPythonWorkflowService, COMMON_TOOL_NAMES} from './m_adapter.mjs';

const API_NAMES = Object.fromEntries(Object.entries(COMMON_TOOL_NAMES).map(([api, common]) => [common, api]));
const error = (code, message, extra = {}) => Object.assign(Error(message), {code, ...extra});

/** Low-level transport binding, not authorization to run a live benchmark.
 * The CLI below only accepts an explicitly offline gateway fixture. The common
 * schemas/policy come from its authenticated manifest; no credentials/upstream
 * discovery, retries, native tools, or alternative model clients are present.
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
    async preflight({requireOffline = true, signal} = {}) {
      const {value} = await post('manifest', {}, {signal});
      if (requireOffline && value.offline !== true) throw error('LIVE_DISABLED', 'This bridge CLI requires a verified offline fixture gateway');
      if (!value.model_policy || !Array.isArray(value.tools) || !Number.isSafeInteger(value.context_window)) {
        throw error('GATEWAY_MANIFEST', 'Gateway must declare exact model policy, common tools, and context window');
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
    async tools({name, arguments: args, signal}) {
      if (!manifest) throw error('PREFLIGHT_REQUIRED', 'Gateway manifest must be checked before tools');
      if (!Object.hasOwn(API_NAMES, name)) throw error('TOOL_NOT_ALLOWED', 'Only the seven common tools are available');
      return (await post('tools', {name: API_NAMES[name], arguments: args}, {signal})).value;
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

function parseArguments(argv) {
  const accepted = new Set(['gateway', 'token-env', 'root', 'run-id', 'python']);
  const result = {};
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === '--offline') { result.offline = true; continue; }
    if (!arg.startsWith('--') || !accepted.has(arg.slice(2)) || !argv[index + 1] || Object.hasOwn(result, arg.slice(2))) {
      throw error('ARGUMENTS', 'Expected --offline --gateway URL --token-env NAME --root TEMP --run-id ID --python PATH');
    }
    result[arg.slice(2)] = argv[++index];
  }
  if (!result.offline) throw error('LIVE_DISABLED', 'Only explicit --offline fixture execution is available');
  if ([...accepted].some(name => !result[name])) throw error('ARGUMENTS', 'All bridge arguments are required');
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(result['token-env'])) throw error('ARGUMENTS', 'Expected a capability environment variable name');
  return result;
}

export async function runBridge(argv, {env = process.env, stdin = process.stdin, signal} = {}) {
  const args = parseArguments(argv);
  const token = env[args['token-env']];
  const bindings = createGatewayBindings({gateway: args.gateway, token});
  const manifest = await bindings.preflight({requireOffline: true, signal});
  let text = '';
  for await (const chunk of stdin) {
    text += chunk;
    if (Buffer.byteLength(text) > 1024 * 1024) throw error('BOOTSTRAP_LIMIT', 'Bootstrap JSON exceeds limit');
  }
  const bootstrap = text.trim() ? JSON.parse(text) : {};
  const service = await createPythonWorkflowService({root: args.root, python: args.python});
  const adapter = createMAdapter({service, transport: bindings.transport, tools: bindings.tools,
    toolSchemas: manifest.tools, resolvedModel: manifest.model_policy, contextWindow: manifest.context_window});
  await adapter.initialize({runId: args['run-id'], problem: bootstrap.problem ?? syntheticProblem(),
    modelPolicy: manifest.model_policy, budget: bootstrap.budget ?? {
      requests: manifest.budget?.model_requests, total_tokens: manifest.budget?.total_tokens,
      max_output_tokens: manifest.budget?.max_output_tokens},
    contextBudget: bootstrap.contextBudget ?? {max_tokens: manifest.context_window - manifest.budget?.max_output_tokens},
    stageGoal: bootstrap.stageGoal ?? 'Record a candidate for 2 + 3 and its unreviewed obligation using only the synthetic fixture.'});
  const result = await adapter.run({signal});
  return {...result, workflow_root: service.root, offline: true};
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  try {
    const result = await runBridge(process.argv.slice(2), {signal: controller.signal});
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch (failure) {
    process.stdout.write(JSON.stringify({ok: false, error: {code: failure.code ?? 'BRIDGE_FAILED', message: failure.message}}) + '\n');
    process.exitCode = 1;
  } finally {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
}
