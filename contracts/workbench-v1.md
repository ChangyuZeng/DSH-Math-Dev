# Workbench v1 integration contract

Implementation contract for dsh-math 0.6.0. This is a preparation/write-up layer, not a research runner or a change to research schema 4.

## Shared data

`project-fields.json` is the field descriptor authority. Build exposes it as `globalThis.DshMathProjectFields` before client modules. Partial `fields` contain only descriptor keys (strings, string arrays, enum values); parent=null/relation='' are injected only during finalization. Missing required content remains missing; defaults target_type=prove_or_disprove, knowledge_status=unverified_open, conventions=[] are transparent editable defaults. Final validate_data('Problem') remains required.

Draft shape: `{format:'dsh-math-preparation-v1', id, session_id, rev, fields, status:'draft'|'committed', committed_project_id:null|string, actor, origin, saved_at}`. Validation shape: `{complete:boolean, missing_fields:[string], errors:[{field,code,message}], questions:[{id,question,options?:[{label,description?}]}]}`. A save/read returns `{draft:null|Draft,validation:null|Validation,pending_finalization:null|{operation_id,expected_rev}}`. An unfinished human finalization is discoverable after browser/host restart: a null-id read prioritizes the session's pending intent, otherwise the latest active draft. The UI freezes that draft and offers an explicit human retry with the persisted exact operation; reading never finalizes it. Saving incomplete drafts is allowed. Unknown keys/invalid primitive types/oversize data are rejected; valid-shape incomplete or invalid enum content must be represented as validation failures where safe.

Scratchpad shape: `{format:'dsh-math-scratchpad-v1', id:'main', project_id, rev, title, body, actor, origin, saved_at}`. Return `{scratchpad:null|Scratchpad,history:[{rev,title,saved_at,actor}]}`. One pad per project; preserve UTF-8 exactly; title max2000 chars/body max256KiB. Save is a new immutable revision; restoring old content creates a new revision. All writes use expected_rev + operation_id, including duplicate request replay and differing-content conflict detection. State must survive host restart. No content enters research/ or default bundles.

## Human API actions

The existing POST /dsh-math/api envelope remains `{action,args}`. Backend success is returned by Host as `{ok:true,value}`; typed failures remain errors with stable codes in messages. Human identity is supplied by the authenticated API path, not args.

- `project-draft-read` args `{session_id,draft_id?}`: omitted/null id prioritizes this session's unfinished finalization; otherwise it reads the newest active draft, or null. A specified id must belong to the requested session.
- `project-draft-save` args `{session_id,draft_id?:null|string,expected_rev,operation_id,patch}`: null id creates a draft with expected_rev=0, return Draft/Validation. Null-id identical operation retry must return same draft. Patch merges fields; no hidden completion.
- `finalize-draft` args `{session_id,draft_id,expected_rev,operation_id}`: validate exact draft and create once; return `{project,draft}`. Committed draft is immutable. Retries return same project, interrupted create recoverable, unfinished projects invisible. Project remains execution_authorized=false.
- `scratchpad-read` args `{session_id,project_id,rev?:null|integer}`: selected revision plus history, or null.
- `scratchpad-save` args `{session_id,project_id,expected_rev,operation_id,title,body}`.
- `scratchpad-restore` args `{session_id,project_id,expected_rev,operation_id,restore_rev}`.
- `scratchpad-promote-link` args `{session_id,project_id,scratchpad_rev,target:{id,rev},operation_id}`: after human saves a Note/Obligation via the existing editor, validate exact existing source/target and record sidecar provenance only; return `{linked:true,target}`. Does not create formal content. Original pad retained.
- `import-legacy-preview` args `{campaign}` returns `{preview:{title,campaign_id,schema_version:3,seq,head_hash,fingerprint,object_count,attachment_count}}`. No mkdir/export/copy; validate usable exact schema3 and one root Problem.
- `import-legacy-confirm` args `{session_id,campaign,expected_fingerprint,operation_id}` returns project metadata. Revalidate fingerprint, safe temporary staging outside projects, atomic publish only when complete. Source untouched. Existing import-legacy action may retain backward compatibility through the same safe internals.

The human UI may explicitly select existing projects across sessions as before; model access is narrower. All scratchpad write/restore/promote and formal mutations share a project gate requiring metadata schema4 + daily_research and actual research schema4. Imported readonly projects are not made writable by sidecars. Read failures/unsupported schemas fail closed.

## Chat tools and dedicated Python entry

Lead registers math_project_prepare, math_project_draft_read, math_scratchpad_read, math_scratchpad_save in a distinct ordinary-chat group. Payload fields match human action args EXCEPT session_id is forbidden. Tools use direct object args; nullable draft_id/rev can be required by model schema. Identity/root/tool-call may not be supplied by model. Scratchpad save only on explicit user request; multiple projects require deliberate selection. Model project ownership requires metadata.session_id == bound host session. No parent/child-session inference.

`preparation_tools.py ROOT` reads one JSON request from stdin: `{session_id,tool_call_id,name,arguments}`. ROOT and these identities are supplied by Host. It validates exact trusted envelope/argument keys, constructs actor model and origin `{source:'dsh-math-chat',session_id,run_id:null,tool_call_id}`, and calls the sidecar implementation directly; NEVER routes through backend human dispatch/mutate. Emits backend-style `{ok:true,value}` or `{ok:false,error,message}` and nonzero exit for failure. Allowed names only the four chat tools above. No create-Problem, review, export, checker, run, files, arbitrary paths. Model saves return scope choices/errors if project mismatch; do not auto-pick first project.

Host excludes these tools from headless/M agents and preserves MODEL_TOOLS allowlist. Write calls must obey verified DSH plan/read-only/approval policy or fail closed; details owned by Lead. No bypass via browser API.

## Client composition

Lead supplies globalThis.DshMathPreview(React, MarkdownText) returning a safe `{Preview}` React component accepting `{text}`. Native MarkdownText may be unavailable: fallback still supports safe Markdown/text and local KaTeX. No raw HTML/remote resources/code execution. Lead owns renderer/build/package files.

UI teammate owns client-main/client-theme/forms/form-ui and new project-form/workbench-ui modules, registering global helper factories consistent with existing modules. Cross-module names should be reported to Lead. Draft load on open/focus; never overwrite dirty edits. Call project-draft-save then finalize-draft with returned id/rev; commit only user click. Scratchpad 800ms save, conflict retains local edits, switching/closing handles unsaved state honestly. Promote opens existing full editor; successful formal save records promote-link. Inline tests may use explicit fixtures; do not simulate success when APIs fail.

## Integration discipline

Backend teammate owns backend.py, workbench_store.py, preparation_tools.py, tests/test_workbench.py. UI teammate owns frontend modules/tests specified on task board. Lead owns project-fields.json, this contract, host/chat-tools, renderer, build/package/docs and integration tests. Report contract changes before implementing incompatibilities. No edits to historical research, old exports, third-party plugins, or production Desktop configuration. Tests use new temporary fixtures. No real open-problem runs.
