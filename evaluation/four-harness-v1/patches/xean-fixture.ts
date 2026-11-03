// Actual pinned Xean createRoleRuntime + native Pi GenerationTask. This fixture
// supplies only transport and frozen tool boundaries, never an agent loop.
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { createModels } from "@earendil-works/pi-ai";
import { fauxProvider } from "@earendil-works/pi-ai/providers/faux";
import { streamSimple as responsesStream } from "@earendil-works/pi-ai/api/openai-responses";
import { normalizeContext } from "@earendil-works/pi-ai/utils/transcript";
import { createRegistry, defineExtension, defineTask, Harness, MemoryStorage } from "@earendil-works/pi-durable";
import { createRoleRuntime, roleGenerationPolicy } from "./src/role-runtime.ts";
import { initializeNotebook } from "./src/state.ts";
import { outputCap } from "./src/four-harness-native.ts";

const routes = JSON.parse(process.env.FOUR_HARNESS_ROUTES ?? "null");
const models = createModels();
for (const role of ["explorer", "coordinator", "verifier"]) {
  // Registry model metadata only. The faux provider's stream is never called.
  const definition = fauxProvider({ provider: `four-harness-${role}`, api: "openai-responses",
    models: [{ id: "gpt-6-astra", reasoning: true }] });
  const nativeModels = definition.models.map(model => ({ ...model, api: "openai-responses",
    thinkingLevelMap: { max: "max" }, contextWindow: 65536, maxTokens: outputCap }));
  models.setProvider({ ...definition.provider, getModels: () => nativeModels, getAllModels: () => nativeModels });
}
models.streamSimple = (model: any, transcript: any, options: any) => {
  const role = model.provider.replace("four-harness-", ""), route = routes[role];
  if (!route) throw new Error("Unknown native role");
  return responsesStream({ ...model, api: "openai-responses", baseUrl: route.url + "/v1",
    contextWindow: 65536, maxTokens: outputCap }, normalizeContext(transcript),
    { ...options, apiKey: route.token, maxTokens: outputCap, maxRetries: 0, reasoning: "max", transport: "sse" });
};
const role = createRoleRuntime();
const Invoke = defineTask({ name: "four-harness.offline-role", version: 1, initial: () => ({ phase: "run" }),
  phases: { async run(task: any, runtime: any, ctx: any) {
    const result = await role.run(runtime, "offline", task.input.descriptor, task.input.profile, ctx);
    await runtime.commit(() => ({ status: "terminal", outcome: { status: "completed", result } }), ctx);
  } },
  async abort(_task: any, runtime: any, ctx: any) {
    await runtime.commit(() => ({ status: "terminal", outcome: { status: "aborted" } }), ctx);
  },
});
const registry = createRegistry();
registry.install(role.extension);
registry.install(defineExtension({ name: "four-harness.fixture", tasks: [Invoke] }));
const harness = await Harness.open(new MemoryStorage(), { registry, models,
  settings: { compaction: { enabled: false }, retry: { enabled: false, maxRetries: 0 }, restartLimit: 0,
    generation: roleGenerationPolicy, toolExecution: "sequential" } }, context);
try {
  const root = await harness.root(context, { init: (tx: any, id: any) => initializeNotebook(tx, id, "fixture-notes.sqlite") });
  const task = await root.commit((tx: any) => tx.createTask(Invoke, {
    descriptor: { kind: "explorer", input: { task: { problem: "Offline fixture: record 2 + 3 = 5.", completionCriteria: "One fixture note." }, notes: [], explorerGuidance: "Read common materials, then submit a fixture note." } },
    profile: { provider: "four-harness-explorer", model: "gpt-6-astra", reasoning: "max" },
  }, { ownership: { kind: "conversation" } }), context);
  const outcome = (await harness.waitForTask(task, context)).state.outcome;
  console.log(JSON.stringify({ kind: "actual_native_x_role", outcome, live_ready: false }));
  if (outcome.status !== "completed") process.exitCode = 1;
} finally {
  await harness.close(context);
}
