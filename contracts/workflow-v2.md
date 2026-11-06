# Tool workflow v2

`dsh-math-workflow-v2` is an explicit, opt-in product execution contract. Version 1 remains readable and keeps its original tool-free runner. Version 2 is disabled unless the profile enables `workflowV2.enabled`; `verification_policy` defaults to `model-directed-v1`. The alternate `verify-revise-v1` policy is experimental and is never selected by reading old results.

## Shared execution and authority

`workflow_executor.mjs` is the execution source for the product UI, explicitly granted ordinary-session tools, and the new experiment driver. ModelPort, ToolPort and the workflow journal provide the boundaries. The new path does not rewrite prompts in source or monkeypatch a legacy store.

The qualified runtime supports the nine registered finite-certificate tasks D1–D6/H1–H3, using their exact public Problem fields, task hashes and checker hashes. Arbitrary checker paths are not accepted. The public checker source contains no stored successful solutions. A prose proposal can be archived and reviewed by a fresh model context, but model review is advisory.

The product UI binds its actual ordinary session through `ctx.agents.get`. Both UI execution and ordinary tools check the selected preset's PLAN state, session identity, current filesystem policy, and project access before any runtime or model request. The current subprocess tree is qualified only for `danger-full-access` sessions; other modes fail closed without escalation. `math_workflow_read` reads authorized run identities/revisions. `math_workflow_execute` requires an additional per-run `grant_chat` capability bound to the same session. The tool cannot prepare a run, grant itself access, alter the frozen policy, or inherit project `auto_record` authority. M agents and delegated sessions cannot use these ordinary-session tools.

## Frozen resources and physical accounting

RunSpec records the actual tool names, effects and schema hashes; isolated workspace; registered checker/task hashes; `openai-codex/gpt-6-astra/max`; and 120 physical attempts for the entire lineage. Worker, verifier, reviser and summary calls share this allowance. There is no cumulative token or solve-time limit. The per-response output cap is 128000 tokens.

The metered gateway alone reserves attempts and retries. Its complete attempt projection is persisted after each call, including failures and retry attempts. Logical actions do not increment physical counters. Missing usage remains null, with known subtotals and unknown-request counts separately recorded. Cached-input and reasoning tokens are subsets, not extra charges; optional details do not determine completeness of input/output/total usage.

Default deadlines are connection 30 s, send 60 s, response headers 300 s and byte-reset stream idle 300 s. Stage diagnostics use allowlisted classes/errno and send state, never raw credential-bearing exceptions. At most two retries follow the first attempt. Only known-unsent transient failures or qualified failed, output-free retryable HTTP responses may retry. Unknown send outcomes, partial output, schema/auth/model errors and truncated streams do not retry automatically.

## Effects, candidates and completion

A tool call ID and exact raw-argument hash are durably claimed before effects. A completed identical receipt may be returned; an uncertain claim requires reconciliation and never replays its effect. Raw gateway tool request/response evidence is written around the effect, independently of the JS receipt. Unsafe integers are forwarded losslessly, duplicate JSON keys are rejected, and immutable receipts bind exact bytes.

A candidate is archived before checking or verification: raw arguments, canonical candidate, Evidence, provenance, Note and candidate Obligation have exact revision references. Tool writes do not waive freshness checks on the action's declared premises. Research commits precede workflow receipts; only the exact committed receipt may recover an interrupted commit.

`check` and `submit` are distinct. A formal success requires an explicit submit and its independent fresh-process terminal check. Extra registered program Reviews do not manufacture submit success. Program checks, model advice, human acceptance and global mathematical claims remain separate. The first independently valid submit stops the workflow. Post-hoc valid artifacts are reported separately.

For B, a new candidate with a qualified failed program check triggers a fresh reviser using public failed constraints. A prose candidate gets a separate verifier context containing only the frozen problem, candidate and declared premises; generation history and previous review traces are absent. Each revision chain has at most two revisions, shared across program and prose paths. Inconclusive checks remain inconclusive. Repeated unchanged candidates and the revision limit stop that chain. All candidate archival and tool facilities are common to A and B.

## Interrupted runs and exports

A live runtime may serve successive manual steps while its process remains open. Its durable lineage claim is never removed to obtain a fresh budget. A used or interrupted runtime cannot cold-start a pristine gateway under the same or continued lineage; retained logs support reconciliation, not automatic effect replay. The UI consequently does not offer v2 continuation as a working resume feature. Preparation of a separate run requires a new explicit user action and is not an experiment retry.

Exports include exact candidate/checker evidence, the fixed native checker and its pinned public dependency. `verify_bundle.py` must succeed both in the original export and after relocation. A passing technical verifier is not human mathematical acceptance.
