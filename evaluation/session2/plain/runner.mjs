// Ordinary installed DSH Agent driver. No math/workflow/research plugins.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { AsyncLocalStorage } from 'node:async_hooks';
const options = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const {projectToolResult} = await import(options.toolViewModule ?? new URL('../../../workflow_tool_view.mjs', import.meta.url).href);
const modules = options.modules;
const load = (name) => import(pathToFileURL(path.join(modules, '@deepseek-ai', name, 'lib/index.js')).href);
const { Context } = await load('cordis');
const { createUserMessage, isAgentLoopRequest } = await load('dsh-llm');
const ctx = new Context();
const plugins = ['dsh-llm', 'dsh-session', 'dsh-agent', 'dsh-system-prompt',
  'dsh-tools', 'dsh-session-projection', 'dsh-agent-loop', 'dsh-token-meter', 'dsh-compaction-basic'];
for (const name of plugins) await ctx.plugin((await load(name)).default,
  name === 'dsh-compaction-basic' ? {maxTokens: 128000} : {});
const originalFetch = globalThis.fetch;
const active = new AsyncLocalStorage();
let lastRequest;
const calls = new Map();
const journal = (value) => fs.appendFileSync(options.events, JSON.stringify(value) + '\n');
// Every adapter HTTP dispatch, including compaction/auxiliary/retries, is forced
// through the host Gateway. No provider credential is present in this process.
globalThis.fetch = async (input, init) => {
  const request = new Request(input, init);
  const url = new URL(request.url);
  const expected = new URL(options.gateway);
  if (url.origin !== expected.origin || url.pathname !== expected.pathname + '/v1/responses')
    throw new Error('UNMETERED_NETWORK_ROUTE');
  const headers = new Headers(request.headers);
  const scope = active.getStore();
  const auxiliary = scope?.kind !== 'agent';
  const target = auxiliary ? request.url.replace('/M/worker/', '/M/compaction/') : request.url;
  headers.set('authorization', 'Bearer ' + (auxiliary ? process.env.FOUR_HARNESS_COMPACTION_TOKEN : process.env.FOUR_HARNESS_TOKEN));
  if (lastRequest) headers.set('X-Four-Parent-Request-Id', lastRequest);
  headers.set('X-Four-Thread-Id', options.sessionId);
  journal({type: 'physical_dispatch', kind: scope?.kind ?? 'auxiliary', parent: lastRequest ?? null});
  journal({type: 'native_wire_request', payload: JSON.parse(await request.clone().text())});
  const response = await originalFetch(new Request(target, {method: request.method, headers, body: await request.text()}));
  const id = response.headers.get('X-Four-Request-Id');
  if (id) lastRequest = id;
  journal({type: 'physical_receipt', request_id: id, status: response.status, kind: scope?.kind ?? 'auxiliary'});
  return response;
};
// Keep the native pi-ai message conversion and native streamed response parser.
await ctx.plugin(await load('dsh-llm-pi-ai'), {providers: {'openai-codex': {
  displayName: 'Session2 host-only Codex', apiKeyEnv: 'FOUR_HARNESS_TOKEN',
  api: 'openai-responses', baseURL: options.gateway + '/v1',
  retryPolicy: {mode: 'normal', maxRetries: 0},
  models: [{id: 'gpt-6-astra', name: 'GPT-6 Astra', contextWindow: 272000,
    maxTokens: 128000, input: ['text'], reasoningEfforts: {max: 'max'}}]
}}});
ctx.on('llm/stream', async function* (request, next) {
  // Native compaction omits effort; freeze the same effort for that call too.
  if (request.reasoningEffort === undefined) request.reasoningEffort = 'max';
  if (request.provider !== 'openai-codex' || request.model !== 'gpt-6-astra' || request.reasoningEffort !== 'max' || request.maxTokens !== 128000)
    throw new Error('MODEL_POLICY_DRIFT');
  const kind = isAgentLoopRequest(request) ? 'agent' : 'compaction_or_auxiliary';
  journal({type: 'native_model_call', kind, request});
  const stream = active.run({kind}, () => next());
  const iterator = stream[Symbol.asyncIterator]();
  while (true) {
    const part = await active.run({kind}, () => iterator.next());
    if (part.done) return;
    if (part.value.type === 'block-end' && part.value.block?.type === 'tool-call')
      calls.set(part.value.block.id, lastRequest);
    yield part.value;
  }
});
ctx.on('session/event', (session, event) => journal({type: 'native_session_event', session_id: session.id, event}));
ctx.on('agent/error', ({error}) => journal({type: 'native_error', message: String(error), stack: error?.stack}));
const catalog = options.tools;
const allowed = new Set(catalog.map(tool => tool.name));
for (const tool of catalog) ctx.tools.register({...tool,
      // execute returns the complete receipt; only native model rendering projects it.
      output: {schema: {}, render: (_args, value) => [{type: 'text', text: JSON.stringify(
        projectToolResult(tool.name, value, {request_id: value?.metrics?.request_id ?? null}))}]},
      async execute(args, exec) {
        const parent = calls.get(exec.callId) ?? lastRequest;
        if (!parent) throw new Error('MISSING_TOOL_PARENT');
        const response = await originalFetch(options.gateway + '/tools', {method: 'POST',
          headers: {'content-type': 'application/json', authorization: 'Bearer ' + process.env.FOUR_HARNESS_TOKEN,
            'X-Four-Parent-Request-Id': parent, 'X-Four-Native-Call-Id': exec.callId},
          body: JSON.stringify({name: tool.name, arguments: args}), signal: exec.signal});
        const value = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(value));
        journal({type: 'native_common_tool', name: tool.name, call_id: exec.callId, parent_request_id: parent});
        const receipt = value.result;
        if (tool.name === 'submit' && value.ok && receipt?.status === 'completed' &&
            receipt?.verification_basis === 'fresh_process_exact_audit_rerun' &&
            receipt?.independent_check?.legal === true && receipt?.independent_check?.target_met === true)
          exec.concludeTurn();
        return value;
      }
    });
const {agent, dispose} = await ctx.agents.create({sessionId: options.sessionId,
  meta: {cwd: process.cwd()},
  agentOptions: {provider: 'openai-codex', model: 'gpt-6-astra', reasoningEffort: 'max', maxTokens: 128000},
  setup(agentCtx) {
    agentCtx.tools.restrict({allow: [...allowed]});
  }
});
journal({type: 'engine_identity', constructor: agent.constructor.name, plugins,
  math_plugin: false, research_scheduler: false, tools: agent.ctx.tools.schemas().map(x => x.name)});
agent.followup(createUserMessage({content: [{type: 'text', text: options.prompt}], source: {kind: 'user'}}));
await agent.whenIdle();
if (options.qualificationCompact) {
  const compacted = await ctx.compaction.compactNow(agent, new AbortController().signal);
  journal({type: 'native_qualification_compaction', compacted});
}
const events = Array.from({length: agent.session.seq}, (_, index) => agent.session.eventAt(index));
const terminal = events.filter(event => event.type === 'turn/end').at(-1);
journal({type: 'native_terminal', terminal});
await dispose();
process.exitCode = terminal?.data.reason.kind === 'completed' ? 0 : 1;
