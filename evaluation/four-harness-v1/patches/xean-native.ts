// Transport/tool boundary for the frozen Xean copy. Pi still owns every turn,
// conversation, fork, role submission, notebook update and verification stage.
import { createHash } from "node:crypto";
import { defineTool } from "@earendil-works/pi-durable";
import { Assignment } from "./role-runtime.ts";
import { resolveCall } from "./policy/prompts.ts";
import declarations from "./four-harness-tools.json";

const digest = (value: unknown) => createHash("sha256").update(JSON.stringify(value ?? null)).digest("hex");
export const outputCap = Number(process.env.FOUR_HARNESS_MAX_OUTPUT);
if (!Number.isSafeInteger(outputCap) || outputCap <= 0 || outputCap > 16384)
  throw new Error("Missing frozen gateway output cap");
const routes = JSON.parse(process.env.FOUR_HARNESS_ROUTES ?? "null");
if (!routes || process.env.FOUR_HARNESS_OFFLINE_FIXTURE !== "1")
  throw new Error("Native X requires explicit isolated offline gateway routes");
const roles = ["explorer", "coordinator", "verifier"] as const;
for (const role of roles) {
  const route = routes[role], url = new URL(route?.url ?? "about:blank");
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port ||
      url.pathname !== `/X/${role}` || url.username || url.password || url.search || url.hash || !route.token)
    throw new Error("Invalid native X localhost role route");
}
async function roleRoute(api: any, context: any) {
  const assignment = await api.snapshot(Assignment, api.conversationId, context);
  if (!assignment?.descriptor) throw new Error("Missing native X role assignment");
  return routes[resolveCall(assignment.descriptor).role];
}
async function post(route: any, suffix: string, body: unknown) {
  const response = await fetch(route.url + suffix, {
    method: "POST", redirect: "error", signal: AbortSignal.timeout(65000),
    headers: { Authorization: `Bearer ${route.token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok || value.ok === false) throw new Error(`Gateway refused native operation: ${JSON.stringify(value)}`);
  return value;
}
export const meteredTool: typeof defineTool = (options) => {
  const execute = options.execute;
  return defineTool({ ...options, async execute(value, api, context) {
    const route = await roleRoute(api, context);
    const call_id = `${api.conversationId}:${api.callId}`;
    await post(route, "/intrinsic", { phase: "start", name: options.name, call_id, args_sha256: digest(value) });
    const cpu = process.cpuUsage(), wall = performance.now();
    let result: Awaited<ReturnType<typeof execute>> | undefined, status = "failed";
    try {
      result = await execute(value, api, context);
      status = "completed";
      return result;
    } finally {
      const spent = process.cpuUsage(cpu);
      await post(route, "/intrinsic", { phase: "finish", name: options.name, call_id,
        cpu_seconds: (spent.user + spent.system) / 1000000, wall_seconds: (performance.now() - wall) / 1000,
        result_sha256: digest(result), status });
    }
  } });
};
const canonical = (value: unknown) => JSON.stringify(value, (_key, item) =>
  item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]])) : item);
async function admittedDeclarations() {
  // Authenticate every role before native tools or model calls can be registered.
  // The frozen file is the schema authority; a manifest only admits a subset.
  const manifests = await Promise.all(roles.map((role) => post(routes[role], "/manifest", {})));
  const frozen = new Map(declarations.map((tool: any) => [tool.name, tool]));
  let admission: string | undefined;
  let names: string[] = [];
  for (const manifest of manifests) {
    if (manifest.offline !== true || !["offline", "smoke", "formal"].includes(manifest.scope) ||
        canonical(manifest.model_policy) !== canonical({ provider: "openai-codex", model: "gpt-6-astra", effort: "max" }) ||
        manifest.context_window !== 65536 || manifest.budget?.max_output_tokens !== outputCap ||
        !Array.isArray(manifest.tools) || manifest.tools.length === 0)
      throw new Error("Native X manifest policy differs from the frozen gateway");
    names = [];
    for (const tool of manifest.tools) {
      if (!tool || typeof tool.name !== "string" || names.includes(tool.name) || !frozen.has(tool.name) ||
          canonical(tool) !== canonical(frozen.get(tool.name)) ||
          (manifest.scope === "smoke" && ["check", "submit"].includes(tool.name)))
        throw new Error("Native X manifest contains an unknown, duplicate, or changed tool schema");
      names.push(tool.name);
    }
    const current = canonical({ scope: manifest.scope, names: [...names].sort() });
    if (admission !== undefined && admission !== current)
      throw new Error("Native X manifest tool admission differs between roles");
    admission = current;
  }
  return declarations.filter((tool: any) => names.includes(tool.name));
}
export const commonTools = (await admittedDeclarations()).map((tool: any) => defineTool({
  name: tool.name, description: tool.description, parameters: tool.parameters,
  callers: ["model"], replay: "unsafe", executionMode: "sequential",
  async execute(value: unknown, api: any, context: any) {
    const result = await post(await roleRoute(api, context), "/tools", { name: tool.name, arguments: value });
    return { output: [{ type: "text", text: JSON.stringify(result) }], isError: result.ok !== true };
  },
}));
export function pinnedPayload(payload: Record<string, any>) {
  if (payload.model !== "gpt-6-astra" || payload.reasoning?.effort !== "max")
    throw new Error("Native X model/effort drift; no effort fallback is allowed");
  if (payload.max_output_tokens !== undefined && payload.max_output_tokens !== outputCap)
    throw new Error("Native X output-cap drift");
  return { ...payload, max_output_tokens: outputCap };
}
