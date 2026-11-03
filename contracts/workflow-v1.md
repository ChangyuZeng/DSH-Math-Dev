# dsh-math-workflow-v1

This additive protocol stores research scheduling separately from the existing schema-4 research event log. Historical M manifests, run ledgers, human Review contracts and export verification retain their original semantics.

## Authority and execution

A human prepares a RunSpec and explicitly starts it from the project panel. Research execution is disabled unless the plugin configuration has `workflowExecution: {enabled: true}` and the host exposes the LLM service. Ordinary conversation tools cannot start research. The first executor implements short, serial, tool-free model actions; it does not run Python, retrieve papers, or invoke historical checker runs. Calling an action consumes a reserved request. The resolved provider, model, reasoning effort and maximum output must match the frozen RunSpec. There is no application-level retry or model fallback. Provider-internal accounting/transport behavior remains a dependency of the adapter.

Workers return readable candidate arguments or obligations, not human Reviews. The shared service validates their complete schemas, frozen Problem reference, dependencies and compare-and-set revisions before writing with model authority. A model statement of progress or correctness is advisory. Program passes, mathematical scope, model critique and human acceptance are separate facts.

## Durable records

- RunSpec: protocol format, project and exact Problem, fixed model policy, finite request/token/output/context limits, stage goal, allowed tools (currently empty), and up to three frozen routes.
- ActionAttempt: identity, kind, context snapshot, reservation, proposal, usage, exact output references and lifecycle state.
- ContextSnapshot: exact records and reference closure, research head, public output contract, content hash and conservative UTF-8 byte estimate plus framing allowance. Essential assumptions are never silently truncated. Oversized inputs fail before model execution.
- Continuation: new run identity and explicitly frozen specification, with prior run revision/event hash and research head. Unknown request outcomes cannot be replayed as if they had not occurred.

The selected action is derive, check, revise, decompose or reconstruct. Stop pauses the run for human assessment; it never certifies the mathematical goal. The human can launch one action or explicitly choose budgeted serial execution. Serial execution follows validated next-action recommendations with fresh contexts, stopping on a stop recommendation, two consecutive committed no-progress results, exhausted budget, an error, or human pause. Route selection remains visible to the human; preparation and start alone do not launch a model request.

## Persistence and recovery

The store uses compare-and-set revisions, idempotent operation identities, append-only hashed workflow events and process locks. A service transaction lock coordinates research commits and workflow controls. Research events remain the single authority for mathematical content.

Research content is committed before the workflow output receipt. If acknowledgement is lost, retrying archival checks the exact existing research operation and fills or returns the same receipt; it does not call the model. A partially received stream preserves known usage but marks accounting incomplete. Missing usage is unknown, never zero, and blocks further execution of that run. Reconciliation does not rerun uncertain actions.

Human pause aborts the local active request and waits for its accounting/reconciliation outcome. Cross-process operations are subject to durable revision and transaction checks. Reloading the page does not create a new model call.

## Verification and limits

Exact premise revision changes produce `needs_recheck`, not a counterexample. Reconstruction excludes the target argument and unrelated directory titles; it uses a fresh request. Independent context reduces anchoring but does not remove a fixed model's shared blind spots. Failure to reconstruct is inconclusive.

The verification key identifies exact target, premises, action, model and checker version. It is provenance for potential reuse; the initial release does not skip an independent check solely because its key matches. Automatic route switching, skipping calls through verification reuse and executable checker integration require further validated strategy adapters.

Engineering tests use synthetic temporary projects and fake streams. They establish state/permission/recovery properties, not improved mathematical performance. The fixed-model evaluation protocol is [workflow-evaluation-v1.md](workflow-evaluation-v1.md); paid mathematical trials require a separately frozen dataset and budget authorization.
