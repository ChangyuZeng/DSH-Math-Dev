# Isolated Astra A/B optimization

This is a thin serial driver around the existing `four-harness-v1` tools, ledger, transport, sandbox and native M workflow. It does not replace the native reasoning loop or introduce an evaluation service.

## P0 baseline

The original source is `ed3eee82cc427302cea4e9d60f51ee37d4830e80`. The local production partial-stream fix is `32c68ac2c4f98f0e499bfa99fb6fbf49ec246651`. The initial P0 anchor is `82e400b`. Before any target solve, an additional red regression exposed missing SSE delta evidence in the experiment transport; the common addendum preserves those uncommitted fragments without replay. The definitive A is the complete source commit recorded in the frozen protocol with `context_policy=full`. B shares every reliability change.

| Observation | Classification | Evidence and disposition |
|---|---|---|
| Workflow stream emitted partial text without usage, then failed; text disappeared | Production defect | Red regression reproduced loss. Preserve invalid partial text with nullable usage; never commit/replay it. |
| Native total/context token policies rejected `null` | Production capability gap for this authorized protocol | Red schema regression; add nullable cumulative policy while retaining request/output limits and unknown-usage halt. Finite defaults unchanged. |
| Experiment compute rejected nullable CPU/time caps | Experiment adapter defect | Red optional-limit tests; nullable guards throughout parent and child, without weakening OS containment or artifact/RSS limits. |
| HTTP error path referenced deleted credential closure | Experiment transport defect | Existing diagnostic tests failed with wrong `TRANSPORT_READ`. Retain safe code/parameter/digest diagnostics and numeric usage; no upstream secret text. |
| Transient failed receipts had no bounded physical-request retry path | Experiment adapter gap | Optimization-only explicit receipt allowlist, Retry-After/backoff/cancellation, identical payload and no side-effect output; each retry reserves another of 120 physical requests. |
| UTF-8 bytes rejected valid long model context | Experiment adapter policy defect | Existing history supplies diagnostic trigger. Optimization retains estimate as evidence and delegates actual token context authority to provider. No claim that bytes are model tokens. |
| Native output captured after cleanup could disappear on cleanup failure | Experiment runner defect | Red cleanup regression; capture to fresh log files before owned-process cleanup. No replay. |
| Truncated SSE candidate deltas disappeared before terminal receipt | Experiment transport defect | Red parser regression plus Future propagation regression. Preserve bounded uncommitted output fragments in host audit; keep unknown usage unknown, never execute or replay partial tool calls. |
| SSE test fixture assumed unknown events fail immediately / cancellation used readline | Test adaptation drift | Current parser drains future extensions and actual I/O uses read1; align tests. Missing terminal remains an interruption, no replay. |
| Candidate file durability, independent checks, operation receipts | Existing capabilities | Submit persists content-addressed bytes and an immutable fresh-process checker receipt; native durable operations reconcile without redispatch. Receipt IDs intentionally differ per actual submission. No second candidate loop added. |
| Historical overload run recovered after null compute repair | Not evidence of actual overload recovery | Recovery mechanism needs its own offline failure injection; the historical event did not trigger it. |
| Historical R posthoc valid candidate after truncation | Other harness observation | Does not establish M candidate loss. Old Ring15, cyclic10303 and Code48 are diagnostic only and excluded from experiment tasks. |

Initial adapter regression: 214 tests, 4 failures, 8 errors, 28 skipped. After P0: build passed; full Python suite 408 tests, 30 skips (explicit native opt-ins and environment-specific checks); Node suite passed. Native M compute/commit and unknown-usage/no replay opt-ins passed. The new optimization null-budget native compute/commit opt-in passed. Exact logs are outside the source tree in the campaign directory.

System Node 22 aborted on this host during npm/build and Python material extraction. All reported passing builds use bundled Node 24.21.0 with `DSH_MATH_NODE` and PATH pinned. No daily runtime/configuration was modified.

## One B factor

`context_policy=omit-host-metrics` removes only the root `metrics` field from the serialization of common-tool replies sent to the solver. A uses `full`, which is also the default. Both preserve every mathematical output, error, candidate, independent check, file path, and hash, as well as the entire tool reply in the host log. Nested fields named metrics are retained. Only optimization scope can activate B.

The host currently appends telemetry to every tool reply. The frozen 80-step fixture demonstrates >16000 repeated context bytes without mathematical-field changes. The experiment measures whether this matters in actual solves. No improved success or cost claim follows from that fixture alone.

## Frozen execution

Run `experiment.py freeze` after committing the source. It freezes source/dependency hashes, 12 task cards, balanced serial adjacent A/B blocks, three repeats, one B, promotion rules, and unknown accounting. Run one separate connectivity request, then `experiment.py campaign`. Every solve gets a new native environment, common workspace, gateway capability and ledger. Source/private checker/audit logs are outside solver-readable roots. No target answers or historical runs are supplied.

Development: 6 tasks (two constructions, two iterative-search objects, two multistep derivations with persistent intermediate rows/chains), 36 solves. Holdout: three disjoint certificate families, 18 solves only if promotion passes. Three reserve cards are not automatically consumed. Holdout target positive certificates are never generated/read during implementation. Checker positive tests for those families use tiny unrelated toy instances.

Explicit independent valid submit is the only formal success. `check`, final text, and posthoc workspace validity are separately recorded. Native termination, infrastructure failure, request exhaustion and mathematical failure remain distinguishable. Unknown usage is null with known subtotals, never a zero-cost request. No extra repeats or configurations are scheduled.

`protocol.json` in the campaign directory is authoritative. Once frozen, modifying tracked code invalidates further dispatch. Serial runner startup failures preserve their slot and evidence; it never silently retries/reset a solve. A differential adapter/isolation defect halts pending runs for audit.
