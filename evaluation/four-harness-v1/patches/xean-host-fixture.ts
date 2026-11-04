// Offline task fixture; provider output is supplied only by the external fake gateway.
import { join } from "node:path";
import { runNativeHost, settings } from "./four-harness-host-runtime.ts";
if (process.env.FOUR_HARNESS_OFFLINE_FIXTURE !== "1") throw new Error("Offline fixture only");
const turns = process.argv.includes("--two-explorer-turns") ? 2 : 1;
const result = await runNativeHost({ path: join(process.cwd(), "synthetic-campaign.sqlite"),
  evidenceFile: join(process.cwd(), "synthetic-host-evidence.json"), definition: {
    task: { problem: "Offline fixture: derive 2 + 3 = 5 from 2 + 2 = 4.",
      completionCriteria: "The second generated note proves the requested arithmetic using the first note." },
    settings: settings(turns),
  } });
console.log(JSON.stringify({ kind: result.kind, status: result.report.status,
  physical_request_count: result.physical_requests.filter(record => record.request_id).length,
  evidence_file: "synthetic-host-evidence.json", live_ready: false }));
if (result.report.status.outcome !== "accepted") process.exitCode = 1;
