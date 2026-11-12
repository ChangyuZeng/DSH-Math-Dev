# Session 2 formal baseline entry

This directory implements offline preparation and qualification for the first six development baseline slots. It does not authorize real calls. Freeze source only after all shared edits and both adapters' offline qualification finish. Formal tasks are D7, D8, D9, one replicate each for `plain` and `math-current`; held-out tasks cannot be added to this formal batch.

`math-current` calls the unmodified production `createWorkflowCall`, `startWorkflowRuntime`, and `executeToolLoop` with the production `model-directed-v1` RunSpec and stage goal. No Session1 import, forced Note, deliberate bad check, Paley hint, three-action cap, eight-request cap, or three-retry headroom stop is used. The production loop retains its model-directed next action, two-consecutive-no-progress stop, explicit independently checked submit stop, exact references, permissions, and failure handling.

For fair public input, each fresh math evaluation project receives a deep copy of the official Problem whose `sources` contains only `Frozen public task statement supplied for this evaluation.` The official schema requires a nonempty provenance list, so this neutral placeholder replaces bibliographic method clues without changing that schema. Bibliographic sources, method names in citations, DOI, solvability and other calibration metadata stay in host preregistration/preflight evidence and do not enter its model snapshot. The frozen contract, task cards, production context builder and executor are unchanged. This is shared-input sanitization for baseline fairness, not a Session3 scheduling or strategy change. The D8 regression checks the actual audited model request as well as the retained host sources.

`plain` routes to `plain/terminal.py`; that adapter must run ordinary native DSH with equivalent offline CommonTools and the same model/effort and physical accounting. The unified entry never substitutes the math executor for plain. Consult the adapter's own qualification evidence and restrictions before treating it as an eligible formal arm.

The native budget is exactly 120 physical attempts, including retries, with no total-token or solve-time cap, max output 128000, and no experiment-added context cap. Production/provider context and tool isolation limits still apply. Every 20 physical requests or 30 elapsed minutes, math entry records an observation at the next authorization checkpoint without stopping or changing resources. The same observation opportunity applies to plain. No budget extension is authorized in this batch. Unknown service snapshot remains null; unknown usage remains unknown in native records.

## Offline tests

Run from source root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B evaluation/session2/test_session2.py
```

This creates only fresh temporary fixture artifacts under `evaluation/session2/.offline/`. Tests cover nine requests and nine research actions (exceeding Session1 caps), no forced durable note, production compute/check/submit and independent success, native no-progress stop, fixture/real separation, missing authorization, exclusive slot claim, source-drift rejection, and incomplete usage retained as null/unknown with dispatch stopped. Fixtures are not formal outcomes or evidence of model mathematical capability. They use the separate offline production runtime constructor and never load upstream credentials.

## Preregister, then dispatch

After all implementation and qualification are complete, choose a fresh evidence directory **outside the source checkout** and write one immutable registration. Do not use this example to start a model run without separate human authorization.

```sh
python3 -B evaluation/session2/protocol.py \
  --output /absolute/new-batch/registration.json \
  --tasks D7,D8,D9 \
  --plain-qualification /absolute/passed-plain-qualification.json
```

Registration records the exact source file inventory/hash, public tasks and checker hashes, model and effort, tool contracts, budgets, observation/stop rules, six slots, and limitations. Formal registration additionally requires passed offline plain qualification: the complete artifact, its SHA-256, installed native module pins, and fixture evidence pins are frozen and rechecked before every dispatch for both arms. Missing qualification or a changed native/evidence file rejects dispatch. Existing registration files are rejected. If any source changes afterward, dispatch rejects it as SOURCE_DRIFT and requires a newly preregistered batch. Do not overwrite a previous registration or erase a slot claim to selectively rerun a failed attempt.

The unified entry has these parameters:

```text
node evaluation/session2/terminal.mjs
  --registration /absolute/new-batch/registration.json
  --slot D7--math-current--r1
  --mode fixture|real
  --output /absolute/new-batch/new-slot-output
  [--python /absolute/python]
  [--responses /absolute/offline-response-array.json]   # fixture only
  [--authorize-upstream]                              # real only, explicit authorization
```

Use `D7--plain--r1` for the native DSH adapter. Output must be a new directory. Real mode rejects fixture responses and fixture-only preregistration; fixture mode requires explicit response data. No real call was made while implementing this entry. A real command/flag is an admission mechanism, not permission for the current agent to spend requests.

Before any runtime dispatch, the preparation path checks the frozen source/task/model/tool identity, claims the slot exclusively beside the registration, creates a fresh isolated project/session/run for math, uses official prepare/start interfaces, and saves preflight evidence. Failed claims, startup, calls, and unknown outcomes are retained and must be reported; there is no cold continuation or quota reset.

Plain output uses `final.json` for the unified entry result and retains raw child-process evidence in `process.json`; the unified entry reads raw process evidence only if startup produced no final record. Math output includes `process.json`, `preflight.json`, and the official project/run/runtime store. Native physical request receipts, full request/response audit records, candidate archives, immutable submissions, checker results, and tool receipts remain in the production runtime directory named by `process.json`. Errors retain code, accounting if supplied, and unknown outcome status. Shutdown drains the production runtime and records cleanup failure. Unknown outcomes are not converted to unsent, complete usage, mathematical failure, human Review, or global proof.

## Remaining qualification boundary

Offline fixture passage verifies implementation wiring. It does not prove provider availability, a real native DSH end-to-end six-slot run, the model's server snapshot, successful resolution of D7/D8/D9, or overall workflow improvement. Recompute actual submitted certificates independently and report finite-task `local_claim` only; preserve R1/R2 and human review boundaries. This Session2 implementation changes no production scheduler or strategy, no Session1 evidence, and no task/reference answers.
