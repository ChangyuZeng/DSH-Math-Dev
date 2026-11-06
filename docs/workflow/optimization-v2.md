# Workflow v2 reliability and verification experiment

This records the plan approved on 2026-11-06 (Asia/Shanghai). The implementation starts from `c12e384988567daa402c65fe50db241becba3759`. The previous optimization protocol and results remain historical and unchanged. The new protocol is frozen only after implementation and offline qualification.

## Execution contract

One product executor serves UI, authorized ordinary chat, and the experiment adapter. It uses explicit ModelPort, ToolPort and Journal interfaces and the existing research/workflow stores. No generated-source prompt replacement or store monkeypatch is used by v2. v1 remains readable with its original semantics.

The only model is `openai-codex/gpt-6-astra`, reasoning effort `max`, including verification and summaries. The gateway owns physical reservations and retries; the workflow stores a projection of every attempt, including retries and failures. Every solve permits 120 attempts, without a cumulative token or elapsed-time cap. Unknown usage remains null, with known subtotals separate. Cache and reasoning are subsets, not added costs.

The v2 RunSpec freezes actual tools and schema hashes, effects, workspace, checker binding, model, transport policy, budget and verification policy. UI and chat share the same project/session and filesystem admission; chat cannot inherit another session's research authority. Exact research revisions, CAS, program/model/human review distinctions and export boundaries remain enforced.

Candidate bytes and hashes are archived before checking; checks bind exact target/witness versions and fixed checker code. Check does not submit. Explicit submit receives an independent fresh check; only that receipt counts as formal success. Unknown model/tool effects are reconciled without replay.

## Reliability

Diagnostics record credential, DNS, TCP, TLS, send, response headers, stream, decode and terminal stages, with allowlisted error type/errno and send state. Synchronous failures also emit evidence. Secrets, arbitrary exception strings and upstream error bodies are excluded.

Default independent limits: connection total 30 seconds, send 60 seconds, response headers 300 seconds, stream idle 300 seconds. Stream idle resets on bytes; no whole-response deadline is imposed. Legacy explicit timeout arguments remain available to historical tooling.

Only provably not-sent transient failures or qualified explicit no-output 429/5xx responses may retry, at most twice per logical request. Retry-After and cancellation are honored. Uncertain sends, partial output, stream truncation, invalid schema, authentication failure and model drift do not replay. All attempts count against quota.

Two consecutive infrastructure-ended solves pause the queue; authentication/model/protocol drift pauses immediately. Queue state preserves the next slot and incomplete pair. At most four connectivity/recovery requests are authorized across this round: initial qualification uses at most two; one recovery requires two consecutive successful non-target probes and offline preflight. A second pause or insufficient probes ends automatic execution. Started failed solves are retained, never replaced.

## One mathematical factor

A uses model-directed next actions on the shared reliable tool-capable executor. B changes only `verification_policy` to `verify-revise-v1` (default off).

For B, a new candidate is checked against its hash, exact target and checker version. A qualified program checker is used when available. Otherwise an independent model context sees the problem, candidate and declared dependencies, without the generation trace or old reviews. Failed public constraints or explicit gaps guide revision. Each candidate chain permits at most two automatic revisions. Inconclusive verification is neither success nor mathematical refutation. Infrastructure failures do not count as mathematical no progress.

No metrics omission, extra context compression, parallel search or reused-check skipping is added. Shared archival/check infrastructure is available equally to A and B. Every B mechanism event is persisted immediately.

## Frozen experiment

Reuse six development cards and the three untouched holdout cards. Development is explicitly an already-seen set, but solver sessions cannot access past solutions/logs. New D5/D6 persistence/recovery checks supplement rather than rewrite historical mathematical verdicts.

Default: 6 development tasks × A/B × 3 repetitions = 36 solves. Only a preregistered positive development signal permits 3 holdout tasks × A/B × 3 = 18. Runs are serial, clean, isolated, and use a frozen balanced pair order. No added configurations/repetitions or post-result tuning.

Primary outcome: independent formal submit success. Posthoc valid construction, infrastructure interruption and mathematical non-solution remain separate. Secondary paired-success measures are tokens and elapsed time, aggregated by task. Waiting, checking, retry and role costs are separate. Unknown usage is never imputed.

Promotion requires comparable source/checks/isolation, observed B triggers, no additional B infrastructure failures, and either more B formal successes, or no fewer successes with a positive preregistered paired token/time change. There is no 15% threshold. Small effects and uncertainty are reported without broad claims. Holdout direction must also be favorable before default enablement; otherwise keep the experimental switch off or withdraw it. No holdout is consumed for a tie, absent trigger, or incomparable evidence.

## Qualification and delivery

Offline regressions cover stage errors/redaction, deadlines, retry boundaries, physical quota, optional usage details, queue persistence, exact tool identity, crash boundaries, big integers, stale references, candidate schema, verifier isolation, permission rejection and transferable exports. UI/chat/experiment entrypoints must agree on a synthetic transcript. Required build/full tests and isolated headless UI/export qualification run before release.

The local evidence package includes exact source/dependencies, frozen protocol, each started run, candidate/check receipts, nullable accounting, stage and role costs, hashes, an independent audit and conclusions. Only source, tests, documentation and sanitized summaries are published. No Desktop deployment or daily configuration changes. Push to `origin/main` only by normal fast-forward, after checking remote state; never force-push.

## Method sources

- [Aletheia](https://arxiv.org/html/2602.10177v1): separate generation, verification and revision; natural-language verification remains advisory.
- [AlphaEvolve](https://arxiv.org/html/2506.13131v1): executable evaluators and durable candidates for checkable tasks; no copied distributed population machinery.
- [Xean at 26669ebb](https://github.com/chaoxu/xean/tree/26669ebb071f8fc13e8a51f66a474d7ce9af387a): exact dependencies, failure records and fresh verification contexts. Its accounting limitations are not adopted.

These sources motivate hypotheses, not measured advantages for dsh-math.
