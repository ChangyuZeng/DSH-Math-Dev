// Offline transport fixture for the pinned public host. Native Pi owns every
// task, role transition, verification call, notebook commit, and acceptance.
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { createModels } from "@earendil-works/pi-ai";
import { fauxProvider } from "@earendil-works/pi-ai/providers/faux";
import { streamSimple as responsesStream } from "@earendil-works/pi-ai/api/openai-responses";
import { normalizeContext } from "@earendil-works/pi-ai/utils/transcript";
import { ToolResultEntry } from "@earendil-works/pi-durable";
import { open, inspect, exportAccepted } from "./src/host.ts";
import { readReport, readStatus } from "./src/report.ts";
import { Notebook, readNotebook } from "./src/state.ts";
import { Assignment } from "./src/role-runtime.ts";
import { resolveCall } from "./src/policy/prompts.ts";
import { outputCap } from "./src/four-harness-native.ts";
import { join } from "node:path";
import { writeFile } from "node:fs/promises";

if (process.env.FOUR_HARNESS_OFFLINE_FIXTURE !== "1") throw new Error("Offline fixture only");
const routes = JSON.parse(process.env.FOUR_HARNESS_ROUTES ?? "null");
const roles = ["explorer", "coordinator", "verifier"] as const;
const models = createModels();
const physical: any[] = [];
let resolveOwner: (value: any) => void;
const ownerReady = new Promise<any>((resolve) => { resolveOwner = resolve; });
const detached = (value: any) => JSON.parse(JSON.stringify(value));
const equal = (left: any, right: any) => JSON.stringify(left) === JSON.stringify(right);

async function pages(read: (cursor?: any) => Promise<any>) {
  const values: any[] = [];
  let cursor: any;
  do {
    const page = await read(cursor);
    values.push(...page.items);
    cursor = page.next;
    if (values.length > 4096) throw new Error("Bounded fixture evidence exceeded");
  } while (cursor);
  return values;
}

function stage(descriptor: any) {
  return descriptor.kind === "verifier" ? descriptor.name : descriptor.kind;
}

async function nativeEvidence(role: string, thread: string | null) {
  const owner = await ownerReady;
  return owner.root.commit(async (tx: any) => {
    const conversations = await pages((cursor) => tx.scanConversations({}, 128, cursor));
    const candidates: any[] = [];
    for (const conversation of conversations) {
      if (!conversation.owner || (thread && String(conversation.id) !== thread)) continue;
      const assignment = await tx.doc(Assignment, conversation.id);
      if (assignment.profile?.provider !== `four-harness-${role}` || !assignment.descriptor) continue;
      const running = await tx.scanTasks({ conversationId: conversation.id, status: "running" }, 128);
      if (!running.items.length) continue;
      const worker = await tx.task(conversation.owner.taskId);
      const campaign = worker?.owner === undefined ? undefined : await tx.task(worker.owner);
      candidates.push({ conversation, assignment, worker, campaign,
        active_tasks: running.items.map((task: any) => ({ id: task.id, kind: task.kind, owner: task.owner })) });
    }
    if (candidates.length !== 1) throw new Error("Physical request has ambiguous native ownership");
    const current = candidates[0];
    const previous = physical.at(-1);
    let predecessor: any = null;
    if (previous?.native) {
      const conversationId = previous.native.conversation.id;
      const entries = await pages((cursor) => tx.scanEntries({ conversationId, order: "ascending" }, 128, cursor));
      const receipts = entries.flatMap((entry: any) => ToolResultEntry.is(entry)
        ? (entry.model ?? []).filter((message: any) => message.role === "toolResult" && !message.isError)
          .map((message: any) => ({ entry_id: entry.id, tool_name: message.toolName,
            call_id: message.toolCallId, details: message.details ?? null })) : []);
      predecessor = { worker: await tx.task(previous.native.worker.id), receipts };
    }
    const notebook = readNotebook(await tx.doc(Notebook, owner.root.id));
    return detached({ ...current, stage: stage(current.assignment.descriptor), predecessor,
      recorded_checks: notebook.checks, native_status: await readStatus(tx, owner.root.id) });
  }, context);
}

// These are observed native causality edges, never merely the preceding time.
function cause(previous: any, current: any, payload: any): string | null {
  if (!previous?.request_id || !previous.ok) return null;
  const receipts = current.predecessor?.receipts ?? [];
  if (previous.native.conversation.id === current.conversation.id) {
    const outputs = (payload.input ?? []).filter((item: any) => item.type === "function_call_output");
    return outputs.some((item: any) => receipts.some((receipt: any) =>
      receipt.call_id?.split("|")[0] === item.call_id)) ? "native_tool_result_continuation" : null;
  }
  const previousDescriptor = previous.native.assignment.descriptor;
  const previousTool = resolveCall(previousDescriptor).tool;
  const receipt = receipts.findLast((value: any) => value.tool_name === previousTool && value.details !== null);
  if (!receipt || current.campaign?.id !== previous.native.campaign?.id) return null;
  const before = previous.native.stage, after = current.stage;
  const descriptor = current.assignment.descriptor;
  if (before === "explorer" && after === "coordinator" &&
      current.predecessor.worker?.state.outcome?.status === "completed" &&
      receipt.details.notes.every((note: any) => descriptor.input.notes.some((next: any) => next.text === note.text)))
    return "native_explorer_submission_to_coordinator";
  if (before === "coordinator" && after === "source" &&
      current.predecessor.worker?.state.outcome?.status === "completed" &&
      current.worker.input.kind === "verify" &&
      equal(receipt.details.verify, current.worker.input.input.verify))
    return "native_coordination_to_verification_worker";
  if (current.worker.id !== previous.native.worker.id) return null;
  if ((before === "source" && after === "correctness") ||
      (before === "correctness" && after === "requirements") ||
      (before === "requirements" && after === "statement")) {
    return current.recorded_checks.some((check: any) => check.call === receipt.entry_id &&
      check.values.some((verdict: any) => verdict.verifier === before))
      ? "native_recorded_check_to_next_verifier" : null;
  }
  if (before === "statement" && after === "proof" && equal(receipt.details, descriptor.statement))
    return "native_statement_to_blind_proof";
  if (before === "proof" && after === "reconstruction" && equal(receipt.details.proof, descriptor.proof))
    return "native_proof_to_reconstruction_comparison";
  return null;
}

for (const role of roles) {
  // fauxProvider contributes metadata only; all streams use the native Responses
  // implementation below, with a mandatory fake gateway and frozen policy.
  const definition = fauxProvider({ provider: `four-harness-${role}`, api: "openai-responses",
    models: [{ id: "gpt-6-astra", reasoning: true }] });
  const nativeModels = definition.models.map((model) => ({ ...model, api: "openai-responses",
    thinkingLevelMap: { max: "max" }, contextWindow: 65536, maxTokens: outputCap }));
  models.setProvider({ ...definition.provider, getModels: () => nativeModels, getAllModels: () => nativeModels });
}
models.streamSimple = (model: any, transcript: any, options: any) => {
  const role = model.provider.replace("four-harness-", ""), route = routes[role];
  if (!roles.includes(role) || !route) throw new Error("Unknown native role");
  const linkedFetch = async (input: any, init?: any) => {
    const url = typeof input === "string" ? input : input.url ?? String(input);
    if (url !== route.url + "/v1/responses") throw new Error("Unqualified provider route");
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    const payload = JSON.parse(init?.body ?? await input.clone().text());
    const native = await nativeEvidence(role, headers.get("X-Four-Thread-Id"));
    headers.set("X-Four-Thread-Id", String(native.conversation.id));
    const previous = physical.at(-1), reason = cause(previous, native, payload);
    if (reason) headers.set("X-Four-Parent-Request-Id", previous.request_id);
    const record: any = { role, native, sequence_previous_request: previous?.request_id ?? null,
      parent_request_id: reason ? previous.request_id : null, parent_reason: reason,
      ancestry_gap: !!previous && !reason, request_id: null, ok: false };
    physical.push(record);
    const response = await fetch(input, { ...init, headers, redirect: "error" });
    record.request_id = response.headers.get("X-Four-Request-Id");
    record.http_status = response.status;
    record.ok = response.ok;
    return response;
  };
  return responsesStream({ ...model, api: "openai-responses", baseUrl: route.url + "/v1",
    contextWindow: 65536, maxTokens: outputCap }, normalizeContext(transcript),
    { ...options, apiKey: route.token, maxTokens: outputCap, maxRetries: 0,
      reasoning: "max", transport: "sse", fetch: linkedFetch });
};

const profile = (role: string) => ({ provider: `four-harness-${role}`, model: "gpt-6-astra", reasoning: "max" });
const path = join(process.cwd(), "synthetic-campaign.sqlite");
const owner = await open(path, { models, create: {
  task: { problem: "Offline fixture: derive 2 + 3 = 5 from 2 + 2 = 4.",
    completionCriteria: "The second generated note proves the requested arithmetic using the first note." },
  settings: { explorer: profile("explorer"), coordinator: profile("coordinator"),
    source: profile("verifier"), correctness: profile("verifier"), requirements: profile("verifier"),
    reconstruction: profile("verifier"), maxExplorerTurns: 1, window: 100000 },
} });
resolveOwner!(owner);
try {
  await owner.root.waitForIdle(context);
} finally {
  await owner.close();
}
const evidence = await inspect(path, async (tx: any, root: any) => {
  const conversations = await pages((cursor) => tx.scanConversations({}, 128, cursor));
  const tasks = await pages((cursor) => tx.scanTasks({}, 128, cursor));
  const assignments: any[] = [];
  const submissions: any[] = [];
  for (const conversation of conversations) {
    if (conversation.owner) assignments.push({ conversation_id: conversation.id,
      assignment: detached(await tx.doc(Assignment, conversation.id)) });
    const entries = await pages((cursor) => tx.scanEntries({ conversationId: conversation.id, order: "ascending" }, 128, cursor));
    for (const entry of entries) if (ToolResultEntry.is(entry))
      for (const message of entry.model ?? []) if (message.role === "toolResult")
        submissions.push({ conversation_id: conversation.id, entry_id: entry.id,
          tool_name: message.toolName, call_id: message.toolCallId, is_error: message.isError,
          details: message.details ?? null });
  }
  return detached({ report: await readReport(tx, root), conversations, tasks, assignments, submissions });
});
const accepted = evidence.report.status.outcome === "accepted";
const result = { kind: "actual_native_x_host", ...evidence, physical_requests: physical,
  accepted_argument: accepted ? await exportAccepted(path) : null,
  mechanism: "public open + native createWorkflow + Pi SQLite tasks + original verify + readReport",
  live_ready: false };
// Bun can terminate before a large console pipe drains. Await the complete
// fixture artifact; stdout stays a small pointer and is never the receipt store.
await writeFile(join(process.cwd(), "synthetic-host-evidence.json"), JSON.stringify(result));
console.log(JSON.stringify({ kind: result.kind, status: evidence.report.status,
  physical_request_count: physical.length, evidence_file: "synthetic-host-evidence.json", live_ready: false }));
if (!accepted) process.exitCode = 1;
