// Cold smoke entry: only canonical task text, never an expected answer or role output.
import { runNativeHost, settings } from "./four-harness-host-runtime.ts";
const problem = "Non-target arithmetic task: compute 2 + 3 using a common tool,\nthen report the result in the native harness. This is an engineering check.\n";
const completionCriteria = "Report the arithmetic result through the native harness; no target construction is requested.\n";
const path = process.env.FOUR_HARNESS_STATE, evidenceFile = process.env.FOUR_HARNESS_EVIDENCE;
if (!path || !evidenceFile) throw new Error("Explicit fresh native state and evidence paths required");
const result = await runNativeHost({ path, evidenceFile, definition: { task: { problem, completionCriteria }, settings: settings() } });
console.log(JSON.stringify({ kind: result.kind, status: result.report.status,
  physical_request_count: result.physical_requests.filter(record => record.request_id).length,
  normal_request_budget_stop: result.normal_request_budget_stop, evidence_file: evidenceFile, live_ready: false }));
if (result.report.status.outcome !== "accepted" && !result.normal_request_budget_stop) process.exitCode = 1;
