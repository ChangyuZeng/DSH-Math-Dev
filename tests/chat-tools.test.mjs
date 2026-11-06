import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, realpathSync, existsSync, symlinkSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {CHAT_TOOLS, CHAT_SCHEMAS, CHAT_GUIDANCE, ordinaryChatAgent, validateChatArguments, prepareChatInvocation, registerChatTools, canonicalRoot} from '../chat-tools.mjs';
import {apply} from '../host.mjs';

function fixture() {
  const workspace = realpathSync(mkdtempSync(join(tmpdir(), 'dsh-math-chat-policy-')));
  const agent = {id: 'chat-a', session: {id: 'chat-a', header: {id: 'chat-a', cwd: workspace, isSeeded: false}}};
  const state = {active: false}, policy = {mode: 'danger-full-access', workspaceRoot: workspace, sessionId: agent.id};
  const calls = [], services = {planMode: {get(actual) {assert.equal(actual, agent); return state;}},
    sandboxPolicy: {resolve(request) {assert.deepEqual(Object.keys(request), ['session']); assert.equal(request.session, agent.session); return {...policy};}},
    sandbox: {async confine(argv, actual, signal) {calls.push({argv, actual, signal}); return {argv: ['/guard', ...argv], enforcement: 'full'};}}};
  agent.ctx = {get: name => services[name]};
  const ctx = {get: name => services[name]};
  const controller = new AbortController(), exec = {agent, callId: 'call-host-1', signal: controller.signal};
  const options = {name: 'math_project_prepare', root: join(workspace, 'data'), python: 'fixture-python', args: {draft_id: null, expected_rev: 0, operation_id: 'op-1', patch: {statement: '对所有 n ∈ ℕ，n ≥ 18446744073709551617'}}};
  return {workspace, agent, state, policy, calls, services, ctx, exec, options, controller};
}

test('chat schemas preserve shared fields and never offer authority-bearing arguments', () => {
  assert.deepEqual(CHAT_TOOLS, ['math_project_context', 'math_project_prepare', 'math_project_draft_read', 'math_research_read', 'math_research_record', 'math_material_read', 'math_library_list', 'math_library_read', 'math_library_manage']);
  for (const [name, schema] of Object.entries(CHAT_SCHEMAS)) {
    assert.equal(schema.additionalProperties, false);
    for (const forbidden of ['actor', 'origin', 'session_id', 'root', 'path', 'manifest', 'allowed', 'budget', 'verification_policy']) assert.equal(schema.properties[forbidden], undefined);
    // Opt-in workflow tools address an existing run; they cannot grant authority or prepare one.
    if (name.startsWith('math_workflow_')) {
      assert.ok(schema.properties.run_id);
      assert.equal(CHAT_TOOLS.includes(name), false);
    } else assert.equal(schema.properties.run_id, undefined);
  }
  assert.deepEqual(CHAT_SCHEMAS.math_project_prepare.properties.patch.properties.target_type.enum, ['construct', 'prove_or_disprove', 'exploratory']);
  assert.match(CHAT_GUIDANCE, /ask_user_question/);
  assert.match(CHAT_GUIDANCE, /不可通过浏览器或人类 API 代点确认/);
  assert.match(CHAT_GUIDANCE, /不要改权限/);
});

test('full-access invocation carries only host-bound identity and leaves state absent', async () => {
  const f = fixture();
  const result = await prepareChatInvocation(f.ctx, f.exec, f.options);
  assert.equal(result.argv[0], 'fixture-python');
  assert.match(result.argv[2], /preparation_tools\.py$/);
  assert.deepEqual(result.request, {session_id: f.agent.id, tool_call_id: f.exec.callId, name: f.options.name, arguments: f.options.args});
  assert.equal(f.calls.length, 0);
  assert.equal(existsSync(f.options.root), false);
  assert.match(result.request.arguments.patch.statement, /18446744073709551617/);
});

test('PLAN active or entering PLAN refuses writes, missing policy fails closed', async () => {
  for (const mode of ['active', 'pending']) {
    const f = fixture(); f.state[mode] = true;
    await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /PLAN_MODE/);
    assert.equal(existsSync(f.options.root), false);
  }
  const f = fixture(); delete f.services.planMode;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  const read = {...f.options, name: 'math_project_draft_read', args: {draft_id: null}};
  assert.equal((await prepareChatInvocation(f.ctx, f.exec, read)).request.name, read.name);
  delete f.services.sandboxPolicy;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, read), /POLICY_UNAVAILABLE/);
});

test('isolated preset plan policy wins over a permissive global controller and is resolved per call', async () => {
  const f = fixture(), selected = {active: false}, calls = [];
  f.services.agentPresets = {
    composedPreset(ctx) {assert.equal(ctx, f.agent.ctx); return 'standard';},
    serviceFor(agent, name) {assert.equal(agent, f.agent); assert.equal(name, 'planMode'); calls.push(name); return {get: () => selected};},
  };
  await prepareChatInvocation(f.ctx, f.exec, f.options);
  selected.active = true;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /PLAN_MODE/);
  selected.active = false; selected.pending = true;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /PLAN_MODE/);
  assert.equal(calls.length, 3);
  assert.equal(f.state.active, false);
  f.state.active = true; selected.pending = false;
  await prepareChatInvocation(f.ctx, f.exec, f.options);
  assert.equal(calls.length, 4);
  assert.equal(existsSync(f.options.root), false);
});

test('selected preset missing controller or unsupported registry fails closed without global fallback', async () => {
  const f = fixture();
  f.services.agentPresets = {composedPreset: () => 'ptc', serviceFor: () => undefined};
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  f.services.agentPresets.serviceFor = () => ({get: () => ({active: 'unknown'})});
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  f.services.agentPresets.composedPreset = () => null;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  delete f.services.agentPresets.composedPreset;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  assert.equal(existsSync(f.options.root), false);
});

test('unbound or unavailable preset registry cannot silently fall back to global policy', async () => {
  const f = fixture();
  f.services.agentPresets = {composedPreset: () => undefined, serviceFor() {assert.fail('No selected service should be queried');}};
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  delete f.services.agentPresets;
  await prepareChatInvocation(f.ctx, f.exec, f.options);
  f.agent.session.header.agentPreset = 'standard';
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
});

test('read-only denies mutation and confines reads; workspace writes confine exact argv', async () => {
  const f = fixture(); f.policy.mode = 'read-only';
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /READ_ONLY/);
  assert.equal(f.calls.length, 0);
  const read = {...f.options, name: 'math_project_draft_read', args: {draft_id: null}};
  assert.equal((await prepareChatInvocation(f.ctx, f.exec, read)).argv[0], '/guard');
  f.policy.mode = 'workspace-write';
  const result = await prepareChatInvocation(f.ctx, f.exec, f.options);
  assert.deepEqual(result.argv, ['/guard', 'fixture-python', '-B', result.argv[3], f.options.root]);
  assert.equal(f.calls.at(-1).actual.mode, 'workspace-write');
  assert.equal(f.calls.at(-1).signal, f.exec.signal);
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, {...f.options, root: join(f.workspace, '..', 'outside')}), /OUTSIDE_WORKSPACE/);
  assert.equal(existsSync(f.options.root), false);
});

test('partial confinement, invalid policy identity and symlink escapes never dispatch', async () => {
  const f = fixture(); f.policy.mode = 'workspace-write';
  f.services.sandbox.confine = async argv => ({argv, enforcement: 'partial'});
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  f.policy.sessionId = 'another-session';
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /POLICY_UNAVAILABLE/);
  f.policy.sessionId = f.agent.id;
  const external = realpathSync(mkdtempSync(join(tmpdir(), 'dsh-math-chat-outside-')));
  symlinkSync(external, join(f.workspace, 'linked'), 'dir');
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, {...f.options, root: join(f.workspace, 'linked', 'data')}), /OUTSIDE_WORKSPACE|UNSAFE_PATH/);
  mkdirSync(join(f.workspace, 'existing'));
  assert.equal(canonicalRoot(join(f.workspace, 'existing', 'missing', 'data')), join(f.workspace, 'existing', 'missing', 'data'));
});

test('subagents and M agents cannot inherit ordinary preparation; fork uses only its own identity', async () => {
  const f = fixture();
  assert.equal(ordinaryChatAgent(f.agent), true);
  f.agent.session.header.origin = 'subagent';
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /CHAT_SCOPE_REQUIRED/);
  delete f.agent.session.header.origin;
  f.agent.session.header.delegationDepth = 1;
  assert.equal(ordinaryChatAgent(f.agent), false);
  delete f.agent.session.header.delegationDepth;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, {...f.options, boundSessions: new Set([f.agent.id])}), /CHAT_SCOPE_REQUIRED/);
  f.agent.session.header.parentSession = 'parent-never-inherited'; f.agent.session.header.isSeeded = true;
  const value = await prepareChatInvocation(f.ctx, f.exec, f.options);
  assert.equal(value.request.session_id, f.agent.id);
  assert.equal(JSON.stringify(value).includes('parent-never-inherited'), false);
  delete f.agent.session.header.isSeeded;
  assert.equal(ordinaryChatAgent(f.agent), false);
});

test('exact argument validation rejects spoofing and bounds revisions and research windows', () => {
  const f = fixture();
  for (const field of ['actor', 'session_id', 'root', 'path']) assert.throws(() => validateChatArguments(f.options.name, {...f.options.args, [field]: 'spoof'}), /INVALID_ARGUMENTS/);
  assert.throws(() => validateChatArguments(f.options.name, {...f.options.args, patch: {unknown: 'x'}}), /INVALID_ARGUMENTS/);
  assert.throws(() => validateChatArguments(f.options.name, {...f.options.args, expected_rev: Number.MAX_SAFE_INTEGER + 1}), /INVALID_ARGUMENTS/);
  for (const name of ['math_scratchpad_read', 'math_scratchpad_save']) assert.throws(() => validateChatArguments(name, {}), /INVALID_ARGUMENTS/);
  const read = {project_id: 'chosen-project', ref: null, offset: 0, limit: 12000};
  assert.equal(validateChatArguments('math_research_read', read), read);
  for (const change of [{offset: -1}, {limit: 12001}, {ref: {id: 'N', rev: 0}}, {ref: {id: 'N', rev: 1, actor: 'spoof'}}]) assert.throws(() => validateChatArguments('math_research_read', {...read, ...change}), /INVALID_ARGUMENTS/);
  assert.throws(() => validateChatArguments('math_material_read', read), /INVALID_ARGUMENTS/);
  const context = {query: '', offset: 0, limit: 20};
  assert.equal(validateChatArguments('math_project_context', context), context);
  for (const change of [{query: 'x'.repeat(201)}, {offset: -1}, {limit: 51}, {session_id: 'spoof'}]) assert.throws(() => validateChatArguments('math_project_context', {...context, ...change}), /INVALID_ARGUMENTS/);
});

test('research record obeys PLAN and readonly policies and validates full Note data', async () => {
  const f = fixture();
  const data = {note_type: 'observation', title: '阶段记录', summary: '摘要', body: 'α + β', statement: '待核对', assumptions: [], problem_ref: {id: 'P', rev: 1}, route_id: null, uses: [], candidate_for: [], evidence: [], gaps: [], failure: null};
  const options = {...f.options, name: 'math_research_record', args: {project_id: 'chosen', id: 'N', expected_rev: 0, operation_id: 'save', data}};
  await prepareChatInvocation(f.ctx, f.exec, options);
  f.state.active = true;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, options), /PLAN_MODE/);
  f.state.active = false; f.policy.mode = 'read-only';
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, options), /READ_ONLY/);
  assert.throws(() => validateChatArguments(options.name, {...options.args, data: {...data, actor: 'human'}}), /INVALID_ARGUMENTS/);
  assert.throws(() => validateChatArguments(options.name, {...options.args, data: {...data, body: 'a'.repeat(256 * 1024)}}), /TOO_LARGE/);
  assert.match(CHAT_GUIDANCE, /不保存隐藏思维/);
  assert.match(CHAT_GUIDANCE, /阶段性主动/);
});

test('library arguments are discoverable, bounded and distinguish metadata CAS from content', () => {
  const list = {project_id: null, include_removed: false, removed_only: true, kind: null, query: '', sort: 'updated_desc', offset: 0, limit: 50};
  assert.equal(validateChatArguments('math_library_list', list), list);
  assert.throws(() => validateChatArguments('math_library_list', {...list, include_removed: 'yes'}), /INVALID_ARGUMENTS/);
  assert.throws(() => validateChatArguments('math_library_list', {...list, limit: 101}), /INVALID_ARGUMENTS/);
  const manage = {action: 'update', project_id: 'authorized-project', entry: {kind: 'object', id: 'N'}, expected_rev: 0, operation_id: 'display-1', patch: {title: 'Display', pinned: true, tags: ['tag']}};
  assert.equal(validateChatArguments('math_library_manage', manage), manage);
  assert.throws(() => validateChatArguments('math_library_manage', {...manage, patch: {body: 'change immutable content'}}), /INVALID_ARGUMENTS/);
  assert.throws(() => validateChatArguments('math_library_manage', {...manage, entry: {...manage.entry, actor: 'human'}}), /INVALID_ARGUMENTS/);
  assert.match(CHAT_GUIDANCE, /metadata_rev/);
  assert.match(CHAT_GUIDANCE, /不因自动记录偏好而自行移除/);
});

test('library manage uses existing PLAN and readonly policies; reads remain permitted', async () => {
  const f = fixture();
  const write = {...f.options, name: 'math_library_manage', args: {action: 'remove', project_id: 'project', entry: {kind: 'object', id: 'N'}, expected_rev: 0, operation_id: 'remove-1', patch: {}}};
  f.state.active = true;
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, write), /PLAN_MODE/);
  f.state.active = false; f.policy.mode = 'read-only';
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, write), /READ_ONLY/);
  const read = {...f.options, name: 'math_library_read', args: {project_id: 'project', entry: {kind: 'object', id: 'N'}, rev: 1, offset: 0, limit: 100}};
  assert.equal((await prepareChatInvocation(f.ctx, f.exec, read)).request.name, read.name);
});

test('abort during confinement is checked before subprocess admission', async () => {
  const f = fixture(); f.policy.mode = 'workspace-write';
  f.services.sandbox.confine = async argv => {f.controller.abort(); return {argv, enforcement: 'full'};};
  await assert.rejects(prepareChatInvocation(f.ctx, f.exec, f.options), /ABORTED/);
});

test('tools use normal registered bodies and separate prompt with host scope restrictions', async () => {
  const f = fixture(), definitions = new Map(), events = [], sections = [], calls = [];
  f.services.systemPrompt = {section(value) {sections.push(value);}};
  f.ctx.tools = {register(value) {definitions.set(value.name, value);}};
  f.ctx.on = (name, fn) => events.push({name, fn});
  registerChatTools(f.ctx, {root: f.options.root, python: 'fixture-python', run: async (...args) => {calls.push(args); return {draft: {rev: 1}, validation: {complete: false}};}});
  assert.equal(definitions.size, 9);
  assert.equal(sections[0].interpolate, false);
  assert.equal(sections[0].text({agent: f.agent}), CHAT_GUIDANCE);
  const result = JSON.parse(await definitions.get(f.options.name).execute(f.options.args, f.exec));
  assert.equal(result.validation.complete, false);
  assert.equal(calls[0][1].tool_call_id, f.exec.callId);
  const denied = [];
  events[0].fn({agent: {id: 'headless', ctx: {tools: {restrict: value => denied.push(value)}}}});
  assert.deepEqual(denied, [{deny: CHAT_TOOLS}]);
});

test('headless and explicit opt-out install no ordinary tools; modelRuns empty installs chat tools, no M tools', () => {
  for (const config of [{headless: true}, {headless: true, chatPreparation: true}, {chatPreparation: false}, {}]) {
    const names = [];
    const ctx = {on() {}, effect() {}, tools: {register: tool => names.push(tool.name)}};
    const controller = apply(ctx, {...config, root: '/never-written-chat-install', modelRuns: []});
    assert.deepEqual(names, config.headless || config.chatPreparation === false ? [] : CHAT_TOOLS);
    assert.deepEqual(controller.chatToolNames, names);
    assert.equal(names.some(name => ['note_save', 'python_run', 'checker_run'].includes(name)), false);
  }
});
