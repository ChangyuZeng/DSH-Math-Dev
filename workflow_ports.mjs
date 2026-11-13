import {createHash} from 'node:crypto';

export function fault(code, message, details = {}) {
  return Object.assign(new Error(message), {code, ...details});
}

/** Preserve numeric tokens and reject ambiguous duplicate keys. */
export function parseExact(text) {
  if (typeof JSON.rawJSON !== 'function') throw fault('INTEGER_RUNTIME', 'A qualified lossless JSON runtime is required');
  const value = JSON.parse(text, (_key, item, context) => {
    if (typeof item === 'number' && (!Number.isSafeInteger(item) || !/^-?\d+$/.test(context?.source ?? ''))) {
      return JSON.rawJSON(context.source);
    }
    return item;
  });
  // JSON.parse has already checked grammar; this second pass checks only key uniqueness.
  let position = 0;
  const whitespace = () => { while (/\s/.test(text[position] ?? '') && position < text.length) position++; };
  const quoted = () => {
    const start = position++;
    while (position < text.length) {
      const char = text[position++];
      if (char === '\\') position++;
      else if (char === '"') return JSON.parse(text.slice(start, position));
    }
    throw fault('INVALID_JSON', 'Unterminated JSON string');
  };
  const scan = () => {
    whitespace();
    if (text[position] === '"') { quoted(); return; }
    if (text[position] === '{') {
      position++; whitespace(); const keys = new Set();
      if (text[position] === '}') { position++; return; }
      while (position < text.length) {
        whitespace(); const key = quoted();
        if (keys.has(key)) throw fault('INVALID_JSON', 'Duplicate JSON key');
        keys.add(key); whitespace(); position++; scan(); whitespace();
        if (text[position++] === '}') return;
      }
    } else if (text[position] === '[') {
      position++; whitespace();
      if (text[position] === ']') { position++; return; }
      while (position < text.length) { scan(); whitespace(); if (text[position++] === ']') return; }
    } else { while (position < text.length && !/[\s,}\]]/.test(text[position])) position++; }
  };
  scan();
  return value;
}

export function canonical(value) {
  if (JSON.isRawJSON?.(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key =>
    JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
export function hash(value) { return createHash('sha256').update(typeof value === 'string' ? value : canonical(value)).digest('hex'); }
const usageKeys = ['input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens'];

export function physicalProjection(source, role) {
  if (source?.format !== 'four-harness-accounting-v2' || !Array.isArray(source.requests) ||
      !Array.isArray(source.request_ids) || source.requests.length !== source.request_ids.length ||
      source.requests.some((row, i) => row.request_id !== source.request_ids[i])) {
    throw fault('ACCOUNTING', 'Complete physical attempt projection is missing');
  }
  return {format: 'dsh-math-physical-accounting-v2', requests: source.requests.map(row => ({
    request_id: row.request_id, logical_request_id: row.logical_request_id ?? source.logical_request_id,
    parent_request_id: row.parent_request_id ?? null, role, status: row.status,
    usage: row.usage ? Object.fromEntries(usageKeys.map(key => [key, row.usage[key] ?? null])) : null,
    usage_complete: row.usage?.complete === true,
    raw_usage: row.raw_usage ?? null,
  }))};
}

/** Thin loopback bindings. The host gateway alone owns upstream retries/quota. */
export function createMeteredPorts({gateway, bindings, fetchImpl = globalThis.fetch}) {
  const base = new URL(gateway);
  if (base.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(base.hostname) || !base.port ||
      base.pathname !== '/' || base.search || base.hash || base.username || base.password) {
    throw fault('GATEWAY_ENDPOINT', 'An explicit loopback gateway origin is required');
  }
  let manifest;
  const post = async (role, path, payload, {signal, parentRequestId, callId} = {}) => {
    const binding = bindings?.[role];
    if (!binding || typeof binding.token !== 'string' || !binding.token || /[\r\n]/.test(binding.token) ||
        !/^\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+\/$/.test(binding.path)) {
      throw fault('CAPABILITY', 'This role has no host-bound gateway capability');
    }
    const headers = {'Authorization': `Bearer ${binding.token}`, 'Content-Type': 'application/json'};
    if (parentRequestId) headers['X-Four-Parent-Request-Id'] = parentRequestId;
    if (callId) headers['X-Four-Native-Call-Id'] = callId;
    let received;
    try {
      received = await fetchImpl(new URL(binding.path + path, base), {method: 'POST', headers,
        body: JSON.stringify(payload), signal, redirect: 'error'});
    } catch {
      throw fault('GATEWAY_CONNECTION', 'Gateway connection ended without a receipt; reconcile without replay', {outcomeUnknown: true});
    }
    let value;
    try { value = parseExact(await received.text()); }
    catch { throw fault('GATEWAY_RECEIPT', 'Gateway returned no valid receipt; reconcile without replay', {outcomeUnknown: true}); }
    const accounting = value.four_harness_accounting ? physicalProjection(value.four_harness_accounting, role) : null;
    if (!received.ok) throw fault(value.error?.code ?? 'GATEWAY_REJECTED', 'Gateway rejected or interrupted the request',
      {accounting, gatewayAccounting: value.four_harness_accounting ?? null, outcomeUnknown: value.error?.dispatched !== false});
    return {value, accounting, requestId: received.headers.get('X-Four-Request-Id')};
  };
  const modelPort = {
    async prepare(spec, {signal} = {}) {
      manifest = null;
      const {value} = await post('worker', 'manifest', {}, {signal});
      if (spec.format !== 'dsh-math-workflow-v2' || canonical(value.model_policy) !== canonical(spec.model_policy) ||
          value.budget?.model_requests !== spec.budget.requests || value.budget?.max_output_tokens !== spec.budget.max_output_tokens ||
          value.budget?.total_tokens !== spec.budget.total_tokens || !Array.isArray(value.tools)) {
        throw fault('GATEWAY_MANIFEST', 'Gateway model, physical quota, or tools differ from RunSpec');
      }
      if (spec.tools.length !== value.tools.length || spec.tools.some(tool => {
        const actual = value.tools.find(item => item.name === tool.name);
        return !actual || hash(actual.parameters) !== tool.schema_sha256;
      })) throw fault('TOOL_CAPABILITY', 'Actual tool schemas differ from the frozen capabilities');
      if (spec.execution.transport !== 'metered-codex' || spec.execution.accounting_authority !== 'gateway') {
        throw fault('ACCOUNTING', 'Workflow requires its physical reservation authority');
      }
      if (spec.execution.verification_policy === 'verify-revise-v1' && (!bindings.verifier || !bindings.reviser)) {
        throw fault('CAPABILITY', 'Verifier and reviser must share the frozen gateway');
      }
      manifest = value;
      return {tools: value.tools, contextWindow: value.context_window};
    },
    async complete({request, parentRequestId, role = 'worker', signal}) {
      if (!manifest) throw fault('PREFLIGHT_REQUIRED', 'Prepare the frozen gateway before execution');
      const result = await post(role, 'v1/responses', request, {signal, parentRequestId});
      if (!result.accounting || !result.requestId || result.accounting.requests.at(-1)?.request_id !== result.requestId) {
        throw fault('ACCOUNTING', 'Terminal response lacks a complete physical attempt chain', {accounting: result.accounting});
      }
      return {response: result.value, accounting: result.accounting, requestId: result.requestId,
        gatewayAccounting: result.value.four_harness_accounting};
    },
  };
  const toolPort = {
    async prepareCandidate(name, args, {callId, parentRequestId, role = 'worker', signal}) {
      if (!['check', 'submit'].includes(name) || !manifest?.tools.some(tool => tool.name === name) ||
          typeof callId !== 'string' || !callId || !parentRequestId) {
        throw fault('TOOL_IDENTITY', 'Candidate preparation requires a declared tool, native call and physical parent');
      }
      const {value} = await post(role, 'prepare-candidate', {name, arguments: args}, {signal, parentRequestId, callId});
      if (value.ok === false && value.error && value.metrics?.phase === 'candidate_preparation') return value;
      if (typeof value.prepare_id !== 'string' || !value.prepare_id || value.candidate_sha256 !== hash(value.candidate) ||
          canonical(value.candidate_file) !== canonical(args.candidate_file)) {
        throw fault('CANDIDATE_BINDING', 'Prepared candidate receipt differs from the requested snapshot');
      }
      return value;
    },
    async dispatch(name, args, {callId, parentRequestId, role = 'worker', signal, preparedCandidate}) {
      if (!manifest?.tools.some(tool => tool.name === name)) throw fault('TOOL_CAPABILITY', 'Tool is outside the frozen capability');
      if (typeof callId !== 'string' || !callId || !parentRequestId) throw fault('TOOL_IDENTITY', 'A native call and physical parent are required');
      return (await post(role, 'tools', {name, arguments: args,
        ...(preparedCandidate === undefined ? {} : {prepared_candidate: preparedCandidate})}, {signal, parentRequestId, callId})).value;
    },
  };
  return Object.freeze({modelPort: Object.freeze(modelPort), toolPort: Object.freeze(toolPort)});
}
