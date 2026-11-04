// Provider I/O and evidence around the unchanged public Xean/Pi host.
// This module does not schedule roles, choose proofs, or reconstruct a model loop.
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { createModels } from "@earendil-works/pi-ai";
import { fauxProvider } from "@earendil-works/pi-ai/providers/faux";
import { streamSimple as responsesStream } from "@earendil-works/pi-ai/api/openai-responses";
import { normalizeContext } from "@earendil-works/pi-ai/utils/transcript";
import { ToolResultEntry } from "@earendil-works/pi-durable";
import { open, inspect, exportAccepted } from "./src/host.ts";
import { readReport, readStatus } from "./src/report.ts";
import { Notebook, readNotebook } from "./src/state.ts";
import { Assignment, roleGenerationPolicy } from "./src/role-runtime.ts";
import { resolveCall } from "./src/policy/prompts.ts";
import { outputCap, rememberPhysicalRequest, executionMode, executionBinding } from "./src/four-harness-native.ts";
import { lstat, writeFile } from "node:fs/promises";

const detached = (value: any) => JSON.parse(JSON.stringify(value));
const equal = (left: any, right: any) => JSON.stringify(left) === JSON.stringify(right);
const roles = ["explorer", "coordinator", "verifier"] as const;
async function pages(read: (cursor?: any) => Promise<any>) {
  const values: any[] = [];
  let cursor: any;
  do {
    const page = await read(cursor); values.push(...page.items); cursor = page.next;
    if (values.length > 4096) throw new Error("Bounded native evidence exceeded");
  } while (cursor);
  return values;
}
const stage = (descriptor: any) => descriptor.kind === "verifier" ? descriptor.name : descriptor.kind;

function cause(previous: any, current: any, payload: any): string | null {
  if (!previous?.request_id || !previous.ok) return null;
  const prior = current.predecessors.find((item: any) => item.conversation_id === previous.native.conversation.id);
  const receipts = prior?.receipts ?? [];
  if (previous.native.conversation.id === current.conversation.id) {
    const outputs = (payload.input ?? []).filter((item: any) => item.type === "function_call_output");
    if (outputs.some((item: any) => previous.tool_calls?.some((call: any) => call.id.split("|")[0] === item.call_id) &&
        receipts.some((receipt: any) => receipt.call_id?.split("|")[0] === item.call_id)))
      return "native_tool_result_continuation";
    if (previous.stop_reason === "length" && JSON.stringify(payload.input).includes(roleGenerationPolicy.continuationPrompt))
      return "native_length_continuation";
    return null;
  }
  if (current.conversation.parent?.conversationId === previous.native.conversation.id &&
      current.assignment.descriptor.kind === "coordinator") return "native_coordinator_prefix_fork";
  if (current.campaign?.id !== previous.native.campaign?.id) return null;
  const before = previous.native.stage, after = current.stage, descriptor = current.assignment.descriptor;
  const receipt = receipts.findLast((value: any) => value.tool_name === resolveCall(previous.native.assignment.descriptor).tool && value.details !== null &&
    previous.tool_calls?.some((call: any) => call.id.split("|")[0] === value.call_id?.split("|")[0]));
  if (!receipt) return null;
  if (before === "explorer" && after === "coordinator" && prior.worker?.state.outcome?.status === "completed" &&
      receipt.details.notes.every((note: any) => descriptor.input.notes.some((next: any) => next.text === note.text)))
    return "native_explorer_submission_to_coordinator";
  if (before === "coordinator" && after === "explorer" && prior.worker?.state.outcome?.status === "completed" &&
      receipt.details.filings.every((filing: any) => descriptor.input.notes.some((note: any) => note.id === filing.note && note.summary === filing.summary)))
    return "native_coordination_to_next_explorer";
  if (before === "coordinator" && ["source", "correctness", "requirements", "statement"].includes(after) &&
      prior.worker?.state.outcome?.status === "completed" && current.worker.input.kind === "verify" &&
      equal(receipt.details.verify, current.worker.input.input.verify)) return "native_coordination_to_verification_worker";
  if (current.worker.id !== previous.native.worker.id) return null;
  if ((before === "source" && after === "correctness") || (before === "correctness" && after === "requirements") ||
      (before === "requirements" && after === "statement")) {
    return current.recorded_checks.some((check: any) => check.call === receipt.entry_id &&
      check.values.some((verdict: any) => verdict.verifier === before)) ? "native_recorded_check_to_next_verifier" : null;
  }
  if (before === "statement" && after === "proof" && equal(receipt.details, descriptor.statement)) return "native_statement_to_blind_proof";
  if (before === "proof" && after === "reconstruction" && equal(receipt.details.proof, descriptor.proof)) return "native_proof_to_reconstruction_comparison";
  if (before === "reconstruction" && after === "proof" && equal(receipt.details.statements, descriptor.statement.statements))
    return "native_statement_correction_to_blind_proof";
  return null;
}

export async function runNativeHost({ path, definition, evidenceFile }: { path: string, definition: any, evidenceFile: string }) {
  // A cold launch never reopens an old SQLite journal, notebook, WAL, or export.
  for (const name of [path, path + "-wal", path + "-shm", path.replace(/\.sqlite$/, ".notes.sqlite"), evidenceFile]) {
    try { await lstat(name); throw new Error("Native X refuses pre-existing state; resume dispatch is disabled"); }
    catch (error: any) { if (error.code !== "ENOENT") throw error; }
  }
  const routes = JSON.parse(process.env.FOUR_HARNESS_ROUTES ?? "null"), models = createModels();
  const physical: any[] = [];
  let resolveOwner: (value: any) => void;
  const ownerReady = new Promise<any>((resolve) => { resolveOwner = resolve; });
  async function nativeEvidence(role: string, thread: string | null) {
    const owner = await ownerReady;
    return owner.root.commit(async (tx: any) => {
      const conversations = await pages((cursor) => tx.scanConversations({}, 128, cursor)), candidates: any[] = [];
      for (const conversation of conversations) {
        if (!conversation.owner || (thread && String(conversation.id) !== thread)) continue;
        const assignment = await tx.doc(Assignment, conversation.id);
        if (assignment.profile?.provider !== `four-harness-${role}` || !assignment.descriptor) continue;
        const running = await tx.scanTasks({ conversationId: conversation.id, status: "running" }, 128);
        if (!running.items.length) continue;
        const worker = await tx.task(conversation.owner.taskId), campaign = worker?.owner === undefined ? undefined : await tx.task(worker.owner);
        candidates.push({ conversation, assignment, worker, campaign,
          active_tasks: running.items.map((task: any) => ({ id: task.id, kind: task.kind, owner: task.owner })) });
      }
      if (candidates.length !== 1) throw new Error("Physical request has ambiguous native ownership");
      const predecessors: any[] = [], seen = new Set();
      for (const previous of physical.filter(record => record.request_id).toReversed()) {
        const conversationId = previous.native.conversation.id;
        if (seen.has(conversationId)) continue; seen.add(conversationId);
        const entries = await pages((cursor) => tx.scanEntries({ conversationId, order: "ascending" }, 128, cursor));
        const receipts = entries.flatMap((entry: any) => ToolResultEntry.is(entry)
          ? (entry.model ?? []).filter((message: any) => message.role === "toolResult")
            .map((message: any) => ({ entry_id: entry.id, tool_name: message.toolName, call_id: message.toolCallId, details: message.details ?? null })) : []);
        predecessors.push({ conversation_id: conversationId, worker: await tx.task(previous.native.worker.id), receipts });
      }
      const current = candidates[0], notebook = readNotebook(await tx.doc(Notebook, owner.root.id));
      return detached({ ...current, stage: stage(current.assignment.descriptor), predecessors,
        recorded_checks: notebook.checks, native_status: await readStatus(tx, owner.root.id) });
    }, context);
  }
  for (const role of roles) {
    // Registry metadata only. The faux stream is never invoked.
    const descriptor = fauxProvider({ provider: `four-harness-${role}`, api: "openai-responses", models: [{ id: "gpt-6-astra", reasoning: true }] });
    const metadata = descriptor.models.map(model => ({ ...model, api: "openai-responses", thinkingLevelMap: { max: "max" }, contextWindow: 65536, maxTokens: outputCap }));
    models.setProvider({ ...descriptor.provider, getModels: () => metadata, getAllModels: () => metadata });
  }
  models.streamSimple = (model: any, transcript: any, options: any) => {
    const role = model.provider.replace("four-harness-", ""), route = routes[role];
    if (!roles.includes(role) || !route) throw new Error("Unknown native role or unadopted compaction path");
    let record: any;
    const linkedFetch = async (input: any, init?: any) => {
      const url = typeof input === "string" ? input : input.url ?? String(input);
      if (url !== route.url + "/v1/responses") throw new Error("Unqualified provider route");
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      const payload = JSON.parse(init?.body ?? await input.clone().text());
      const native = await nativeEvidence(role, headers.get("X-Four-Thread-Id"));
      headers.set("X-Four-Thread-Id", String(native.conversation.id));
      const eligible = physical.filter(prior => prior.request_id && prior.ok).toReversed();
      // A fork's parent is its persisted native prefix, even if a different role ran later.
      eligible.sort((left, right) => Number(right.native.conversation.id === native.conversation.parent?.conversationId) -
        Number(left.native.conversation.id === native.conversation.parent?.conversationId));
      const edges = eligible.map(prior => ({ prior, reason: cause(prior, native, payload) })).filter(edge => edge.reason);
      const parent = edges[0];
      if (physical.length && !parent) throw new Error("Native descendant has no proven causal parent; dispatch denied");
      if (parent) headers.set("X-Four-Parent-Request-Id", parent.prior.request_id);
      record = { role, native, request: payload, sequence_previous_request: physical.at(-1)?.request_id ?? null,
        parent_request_id: parent?.prior.request_id ?? null, parent_reason: parent?.reason ?? null,
        causal_inputs: edges.map(edge => ({ request_id: edge.prior.request_id, reason: edge.reason })),
        ancestry_gap: false, request_id: null, ok: false };
      physical.push(record);
      const response = await fetch(input, { ...init, headers, redirect: "error" });
      record.request_id = response.headers.get("X-Four-Request-Id"); record.http_status = response.status; record.ok = response.ok;
      if (response.ok) rememberPhysicalRequest(native.conversation.id, record.request_id);
      else { try { record.gateway_error = (await response.clone().json()).error; } catch { record.gateway_error = null; } }
      return response;
    };
    const stream = responsesStream({ ...model, api: "openai-responses", baseUrl: route.url + "/v1", contextWindow: 65536, maxTokens: outputCap },
      normalizeContext(transcript), { ...options, apiKey: route.token, maxTokens: outputCap, maxRetries: 0, reasoning: "max", transport: "sse", fetch: linkedFetch });
    void stream.result().then(message => {
      if (record) { record.stop_reason = message.stopReason;
        record.tool_calls = detached(message.content.filter(item => item.type === "toolCall")); }
    }, () => { if (record) record.stop_reason = "unknown"; });
    return stream;
  };
  const owner = await open(path, { models, create: definition,
    source: async () => { throw new Error("External Codex source branch is disabled for this native profile"); } });
  resolveOwner!(owner);
  try { await owner.root.waitForIdle(context); } finally { await owner.close(); }
  const evidence = await inspect(path, async (tx: any, root: any) => {
    const conversations = await pages((cursor) => tx.scanConversations({}, 128, cursor)), tasks = await pages((cursor) => tx.scanTasks({}, 128, cursor));
    const assignments: any[] = [], submissions: any[] = [];
    for (const conversation of conversations) {
      if (conversation.owner) assignments.push({ conversation_id: conversation.id, assignment: detached(await tx.doc(Assignment, conversation.id)) });
      const entries = await pages((cursor) => tx.scanEntries({ conversationId: conversation.id, order: "ascending" }, 128, cursor));
      for (const entry of entries) if (ToolResultEntry.is(entry)) for (const message of entry.model ?? []) if (message.role === "toolResult")
        submissions.push({ conversation_id: conversation.id, entry_id: entry.id, tool_name: message.toolName,
          call_id: message.toolCallId, is_error: message.isError, details: message.details ?? null });
    }
    return detached({ report: await readReport(tx, root), conversations, tasks, assignments, submissions });
  });
  const accepted = evidence.report.status.outcome === "accepted";
  const budgetStop = physical.some(record => record.gateway_error?.code === "REQUEST_BUDGET" && record.gateway_error?.dispatched === false);
  const result = { kind: "actual_native_x_host", ...evidence, physical_requests: physical,
    accepted_argument: accepted ? await exportAccepted(path) : null, execution_mode: executionMode, execution: executionBinding,
    normal_request_budget_stop: budgetStop,
    mechanisms: { fork: "native_coordinator_prefix", length_continuation: "native_generation_policy",
      resume_dispatch: false, parallel_dispatch: false, compaction: false, external_codex_source: false },
    mechanism: "public open + native createWorkflow + Pi SQLite tasks + original verify + readReport", live_ready: false };
  await writeFile(evidenceFile, JSON.stringify(result), { flag: "wx" });
  return result;
}

export const profile = (role: string) => ({ provider: `four-harness-${role}`, model: "gpt-6-astra", reasoning: "max" });
export function settings(maxExplorerTurns = 10) {
  return { explorer: profile("explorer"), coordinator: profile("coordinator"), source: profile("verifier"),
    correctness: profile("verifier"), requirements: profile("verifier"), reconstruction: profile("verifier"), maxExplorerTurns, window: 100000 };
}
