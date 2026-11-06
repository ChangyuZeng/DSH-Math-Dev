/* Existing MathView child; this module does not register a slot or start a runner. */
globalThis.DshMathWorkflowUI = function (React) {
  const {createElement: h, useState, useEffect, useRef} = React;
  const empty = () => ({provider: '', model: '', effort: '', stage_goal: '', requests: '', total_tokens: '', max_output_tokens: '', max_tokens: ''});
  const disabledCapability = {enabled: false, reason: '尚未确认模型执行授权。可以准备方案，不能启动。'};
  const json = value => JSON.stringify(value, null, 2);
  const uuid = () => { if (!globalThis.crypto?.randomUUID) throw Error('当前环境无法生成安全的操作标识。'); return globalThis.crypto.randomUUID(); };
  const kinds = [['derive', '推导'], ['check', '检查'], ['revise', '修订'], ['decompose', '拆分待证事项'], ['reconstruct', '盲重建'], ['stop', '结束流程（不调用模型）']];
  function WorkflowPanel(props) {
    return h(Panel, {...props, key: JSON.stringify([props.projectId, props.sessionId, props.problemRef])});
  }
  function Panel({request, projectId, sessionId, problemRef, writable = false, onChanged, onInspect}) {
    const [draft, setDraft] = useState(empty), [run, setRun] = useState(null), [runs, setRuns] = useState([]);
    const [mode, setMode] = useState('legacy'), [template, setTemplate] = useState(null), [policy, setPolicy] = useState('model-directed-v1');
    const [capability, setCapability] = useState(disabledCapability), [busy, setBusy] = useState(false);
    const [error, setError] = useState(''), [notice, setNotice] = useState(''), [kind, setKind] = useState('derive');
    const [uncertain, setUncertain] = useState(false), [continuousBusy, setContinuousBusy] = useState(false), [pausing, setPausing] = useState(false);
    const pauseLock = useRef(false);
    const life = useRef({active: true, busy: false, seq: 0, attemptedRun: null});
    const callbacks = useRef({}); callbacks.current = {writable, onChanged};
    const scope = {project_id: projectId, session_id: sessionId};
    const command = cmd => request('workflow', {...scope, command: cmd});
    function apply(value) {
      setCapability(value?.capability || disabledCapability);
      if (value?.run) setRun(previous => previous?.run_id === value.run.run_id && previous.rev > value.run.rev ? previous : value.run);
      if (Array.isArray(value?.runs)) setRuns(value.runs);
    }
    async function work(operation) {
      const state = life.current;
      if (!state.active || state.busy) return;
      state.busy = true; const seq = ++state.seq;
      setBusy(true); setError(''); setNotice('');
      const current = () => state.active && seq === state.seq;
      try { await operation(current); }
      catch (failure) { if (current()) setError(failure?.message || String(failure)); }
      finally { if (current()) { state.busy = false; setBusy(false); } }
    }
    async function refresh(selected = life.current.attemptedRun || run?.run_id) {
      return work(async current => {
        const listing = await command({action: 'list'});
        if (!current()) return;
        apply(listing);
        const id = selected || listing.runs?.[0]?.run_id;
        if (id) {
          const value = await command({action: 'read', run_id: id});
          if (!current()) return;
          apply(value);
        } else setRun(null);
        if (current()) { setUncertain(false); life.current.attemptedRun = null; }
      });
    }
    useEffect(() => {
      const state = life.current; state.active = true;
      if (projectId) refresh();
      return () => { state.active = false; state.seq++; state.busy = false; };
    }, [projectId, sessionId]);
    const v2 = run?.format === 'dsh-math-workflow-v2';
    const canExecute = (v2 ? capability.tool_workflow : capability)?.enabled === true;
    async function loadTemplate() {
      return work(async current => {
        const value = await request('workflow-template', {...scope, problem_ref: problemRef, verification_policy: policy});
        if (current()) { apply(value); setTemplate(value.spec); setNotice('工具能力与检查器已读取，请核对后冻结。'); }
      });
    }
    function spec() {
      if (mode === 'tools') {
        if (!template || template.problem_ref?.id !== problemRef?.id || template.problem_ref?.rev !== problemRef?.rev) throw Error('请先读取当前问题版本的工具能力。');
        return template;
      }
      for (const key of ['provider', 'model', 'effort', 'stage_goal']) if (!draft[key].trim()) throw Error('请明确填写 provider、model、effort 和阶段目标。');
      for (const key of ['requests', 'total_tokens', 'max_output_tokens', 'max_tokens']) if (!/^\d+$/.test(draft[key]) || !Number.isSafeInteger(Number(draft[key])) || Number(draft[key]) <= 0) throw Error('所有预算必须是有限的正整数。');
      if (Number(draft.max_output_tokens) > Number(draft.total_tokens)) throw Error('单次输出上限不能超过总 token 预算。');
      if (!problemRef?.id || !Number.isSafeInteger(problemRef.rev) || problemRef.rev < 1) throw Error('请先选择研究问题的精确版本。');
      return {format: 'dsh-math-workflow-v1', project_id: projectId, problem_ref: problemRef,
        model_policy: {provider: draft.provider.trim(), model: draft.model.trim(), effort: draft.effort.trim()},
        budget: {requests: Number(draft.requests), total_tokens: Number(draft.total_tokens), max_output_tokens: Number(draft.max_output_tokens)},
        tools: [], context_budget: {max_tokens: Number(draft.max_tokens)}, stage_goal: draft.stage_goal.trim()};
    }
    function failed(failure) {
      const rejected = ['INVALID_SCHEMA', 'INVALID_STATE', 'INVALID_SPEC', 'VERSION_CONFLICT', 'REVISION_CONFLICT', 'CAPABILITY_DISABLED', 'PERMISSION_DENIED', 'BUDGET_EXHAUSTED', 'PLAN_MODE', 'READ_ONLY', 'POLICY_UNAVAILABLE', 'RUNTIME_CONFINEMENT', 'RUNTIME_CAPABILITY', 'WORKFLOW_GRANT_REQUIRED', 'SESSION_UNAVAILABLE'].includes(failure?.code);
      setUncertain(!rejected);
      if (rejected) life.current.attemptedRun = null;
    }
    async function mutate(action, actionId) {
      if (!callbacks.current.writable || uncertain || pauseLock.current || !projectId || !sessionId) return;
      if (action === 'start' && (!canExecute || run?.status !== 'prepared')) return;
      if (action === 'commit' && !run?.actions?.some(item => item.action_id === actionId && item.status === 'proposed')) return;
      return work(async current => {
        const cmd = action === 'prepare'
          ? {action, spec: spec(), run_id: uuid(), operation_id: uuid(), expected_rev: 0}
          : {action, run_id: run.run_id, operation_id: uuid(), expected_rev: run.rev};
        if (action === 'continue') Object.assign(cmd, {new_run_id: uuid(), spec: run.spec});
        if (action === 'grant_chat') cmd.allowed = !(run.chat_execution?.allowed && run.chat_execution.session_id === sessionId);
        if (action === 'commit') { cmd.action_id = actionId; delete cmd.operation_id; }
        life.current.attemptedRun = cmd.new_run_id || cmd.run_id;
        let value;
        try { value = await command(cmd); }
        catch (failure) { if (current()) failed(failure); throw failure; }
        if (!current()) return;
        apply(value); setUncertain(false); life.current.attemptedRun = null;
        setNotice('操作已保存。');
        // A refresh/parent-render failure must never cause a mutation to be retried.
        try {
          const listing = await command({action: 'list'});
          if (!current()) return;
          apply(listing);
          const refreshed = await command({action: 'read', run_id: value.run.run_id});
          if (!current()) return;
          apply(refreshed);
          await callbacks.current.onChanged?.(refreshed.run);
        } catch (failure) { if (current()) setError(`操作已保存，但刷新失败：${failure.message}`); }
      });
    }
    async function pauseContinuous() {
      if (!life.current.active || !callbacks.current.writable || !continuousBusy || pauseLock.current) return;
      pauseLock.current = true; setPausing(true);
      const state = life.current, seq = state.seq;
      const current = () => state.active && seq === state.seq;
      try {
        const latest = await command({action: 'read', run_id: run.run_id});
        if (!current()) return;
        const value = await command({action: 'pause', run_id: run.run_id, expected_rev: latest.run.rev, operation_id: uuid()});
        if (!current()) return;
        apply(value); setNotice('暂停请求已返回，请核对最终状态。');
        await callbacks.current.onChanged?.(value.run);
      } catch (failure) { if (current()) setError(`暂停未确认：${failure.message}。请核对运行状态。`); }
      finally { pauseLock.current = false; if (current()) setPausing(false); }
    }
    async function step(continuous = false) {
      if (!callbacks.current.writable || uncertain || pauseLock.current || run?.status !== 'running' || (kind !== 'stop' && !canExecute)) return;
      return work(async current => {
        life.current.attemptedRun = run.run_id;
        let value;
        if (continuous) setContinuousBusy(true);
        try { value = await request(continuous ? 'workflow-run' : 'workflow-step', {...scope, run_id: run.run_id, expected_rev: run.rev, kind, target_ref: null, route_id: null, dependency_refs: []}); }
        catch (failure) { if (current()) failed(failure); throw failure; }
        finally { if (current() && continuous) setContinuousBusy(false); }
        if (!current()) return;
        apply(value); life.current.attemptedRun = null; setNotice('本步已返回，请核对结果。');
        try {
          const listing = await command({action: 'list'});
          if (!current()) return;
          apply(listing);
          const refreshed = await command({action: 'read', run_id: value.run.run_id});
          if (!current()) return;
          apply(refreshed); await callbacks.current.onChanged?.(refreshed.run);
        } catch (failure) { if (current()) setError(`本步已返回，但刷新失败：${failure.message}`); }
      });
    }
    const locked = busy || pausing || !writable || uncertain || !projectId || !sessionId;
    const button = (label, onClick, disabled = false, primary = false) => h('button', {type: 'button', className: `dm-button${primary ? ' dm-primary' : ''}`, onClick, disabled}, label);
    const field = (key, label, numeric = false) => h('label', {key, className: 'dm-field'}, label,
      h(key === 'stage_goal' ? 'textarea' : 'input', {className: 'dm-input', name: key, value: draft[key], required: true,
        type: numeric ? 'number' : undefined, min: numeric ? 1 : undefined, step: numeric ? 1 : undefined, rows: key === 'stage_goal' ? 3 : undefined,
        onChange: event => setDraft({...draft, [key]: event.target.value})}));
    const actions = run?.actions || [], unknown = actions.filter(action => action.status === 'unknown');
    const routeStates = {active: 'active（进行中）', needs_recheck: '需重查', needs_reassessment: '需重评'};
    const describeSuggestion = suggestion => suggestion ? `${kinds.find(([value]) => value === suggestion.kind)?.[1] || suggestion.kind} · 目标 ${suggestion.target_ref ? `${suggestion.target_ref.id}@${suggestion.target_ref.rev}` : '未指定'} · ${suggestion.reason || '未提供理由'}` : '未提供';
    return h('section', {className: 'dm-panel dm-workflow', 'aria-label': '固定模型研究流程', 'aria-busy': busy},
      h('div', {className: 'dm-panel-header'}, h('h3', {className: 'dm-panel-title'}, '固定模型研究流程'), button('刷新流程', () => refresh(), busy || pausing || !projectId)),
      h('p', {className: 'dm-record-text'}, '先冻结模型、目标、工具和请求额度，再启用运行。工具流程按物理请求计数，失败与重试也占额度；累计时间和 token 可不设上限。未知请求不会自动重发。'),
      !writable && h('p', {className: 'dm-panel-meta'}, '当前项目只读。'),
      !canExecute && h('p', {className: 'dm-local-hint', role: 'status'}, (v2 ? capability.tool_workflow?.reason : capability.reason) || disabledCapability.reason),
      error && h('p', {className: 'dm-error', role: 'alert'}, error),
      notice && h('p', {role: 'status'}, notice),
      uncertain && h('p', {className: 'dm-local-hint'}, '请求结果尚未确认，输入已保留。请先刷新核对已有运行；不会重发上次操作。'),
      h('details', {className: 'dm-details', open: !run}, h('summary', null, '准备新方案'),
        h('label', {className: 'dm-field'}, '执行能力', h('select', {className: 'dm-input', value: mode, disabled: locked,
          onChange: event => { setMode(event.target.value); setTemplate(null); }},
          h('option', {value: 'legacy'}, '文本研究流程'), h('option', {value: 'tools', disabled: capability.tool_workflow?.enabled !== true}, '带工具的证书工作流'))),
        mode === 'legacy' ? h('fieldset', {className: 'dm-editor-fields', disabled: locked},
          field('provider', 'Provider'), field('model', 'Model'), field('effort', 'Effort'), field('stage_goal', '阶段目标'),
          field('requests', '最多模型请求数', true), field('total_tokens', '总 token 预算', true), field('max_output_tokens', '单次最大输出 tokens', true), field('max_tokens', '上下文最大 tokens', true))
          : h(React.Fragment, null,
            h('label', {className: 'dm-field'}, '核验策略', h('select', {className: 'dm-input', value: policy, disabled: locked,
              onChange: event => { setPolicy(event.target.value); setTemplate(null); }}, h('option', {value: 'model-directed-v1'}, 'A · 模型原生决定'), h('option', {value: 'verify-revise-v1'}, 'B · 核验后定向修订（实验开关）'))),
            h('p', {className: 'dm-local-hint'}, '当前支持已注册的固定证书问题。gpt-6-astra/max，最多120个物理请求，不设累计时间或 token 上限；检查与正式提交分别记录。'),
            button('读取当前问题的工具能力', loadTemplate, locked || !problemRef),
            template && h('pre', {className: 'dm-code'}, json(template))),
        h('p', {className: 'dm-panel-meta'}, `问题版本：${problemRef ? `${problemRef.id}@${problemRef.rev}` : '尚未选择'} · 工具：${mode === 'legacy' ? '无' : template ? template.tools.map(tool => tool.name).join('、') : '等待读取'}`),
        button('冻结并准备方案', () => mutate('prepare'), locked || mode === 'tools' && !template, true)),
      runs.length > 0 && h('label', {className: 'dm-field'}, '已有运行', h('select', {className: 'dm-input', value: run?.run_id || '', disabled: busy || pausing || uncertain,
        onChange: event => refresh(event.target.value)}, h('option', {value: '', disabled: true}, '选择运行'), ...runs.map(item => h('option', {key: item.run_id, value: item.run_id}, `${item.run_id} · ${item.status}`)))),
      run && h(React.Fragment, null,
        h('p', {className: 'dm-record-text'}, `状态：${run.status} · 版本 ${run.rev} · ${run.run_id}`),
        h('p', {className: 'dm-record-text'}, `下一步：${run.next_action ? (typeof run.next_action === 'string' ? run.next_action : json(run.next_action)) : run.status === 'prepared' ? '等待明确启用运行' : run.status === 'running' ? '选择动作并执行一步' : run.status === 'needs_reconcile' ? '核对 unknown 后创建续接运行' : '查看结果或准备续接'}`),
        run.reason && h('p', {className: 'dm-local-hint'}, typeof run.reason === 'string' ? run.reason : json(run.reason)),
        h('p', {className: 'dm-record-text'}, `消耗：${run.usage?.requests_complete === false ? '已对账至少 ' : ''}${run.usage?.requests ?? '未知'} 次请求 / ${run.spec?.budget?.requests ?? '未知'}；${run.usage?.total_tokens ?? '未知'} tokens / ${run.spec?.budget?.total_tokens === null ? '不设累计上限' : run.spec?.budget?.total_tokens ?? '未知'}；usage ${run.usage?.complete === true ? '完整' : '不完整，不能按零消耗处理'}`),
        h('p', {className: 'dm-record-text'}, `Unknown 动作：${unknown.length}。独立人工接受状态：${run.scientific_status || '未提供'}。流程完成不代表数学接受。`),
        run.strategy && h('section', {className: 'dm-editor-section', 'aria-label': '路线策略建议'},
          h('h4', null, '路线策略建议'),
          h('p', {className: 'dm-local-hint'}, '模型建议仅供查看；不会自动选中动作或启动执行，技术错误与数学进展分别计数。'),
          h('p', {className: 'dm-record-text'}, `建议下一步：${describeSuggestion(run.strategy.next_action)}`),
          ...(run.strategy.routes || []).map(route => h('section', {key: route.route_id ?? 'default', className: 'dm-editor-section'},
            h('h4', null, `${route.route_id ?? '默认路线'} · ${routeStates[route.state] || route.state}`),
            h('p', {className: 'dm-record-text'}, `连续无进展：${route.consecutive_no_progress ?? '未知'} 次；技术错误：${route.technical_errors ?? '未知'} 次；已归档动作：${route.committed_actions ?? '未知'} 次。`),
            h('p', {className: 'dm-record-text'}, `建议：${describeSuggestion(route.next_action)}`),
            route.next_action?.rebuild_context_required && h('p', {className: 'dm-local-hint'}, '依赖版本已变化，需要重建上下文后重查。'),
            route.stale_refs?.length > 0 && h('details', {className: 'dm-details'}, h('summary', null, '需重查的精确引用'), h('pre', {className: 'dm-code'}, json(route.stale_refs)))))),
        Array.isArray(run.completion_projection) && run.completion_projection.length > 0 && h('details', {className: 'dm-details'},
          h('summary', null, '记录完成度投影（只读）'),
          h('p', {className: 'dm-local-hint'}, '记录状态与程序检查、模型评议、人工接受分别保留；不据此宣称全局问题已证明。'),
          h('pre', {className: 'dm-code'}, json(run.completion_projection))),
        h('details', {className: 'dm-details'}, h('summary', null, '已冻结方案'), h('pre', {className: 'dm-code'}, json(run.spec))),
        h('div', {className: 'dm-actions'},
          button('启用运行', () => mutate('start'), locked || !canExecute || run.status !== 'prepared', true),
          button(pausing ? '正在暂停…' : '暂停', () => continuousBusy ? pauseContinuous() : mutate('pause'), pausing || !writable || run.status !== 'running' || (!continuousBusy && locked)),
          button('核对中断状态', () => mutate('reconcile'), locked || !actions.some(action => action.status === 'running')),
          !v2 && button('准备续接运行', () => mutate('continue'), locked || !['paused', 'needs_reconcile', 'completed', 'cancelled'].includes(run.status)),
          v2 && button(run.chat_execution?.allowed && run.chat_execution.session_id === sessionId ? '撤销当前会话执行权' : '允许当前会话执行此运行', () => mutate('grant_chat'), locked || actions.some(action => ['running', 'proposed'].includes(action.status)))),
        v2 && h('p', {className: 'dm-local-hint'}, '会话执行权仅绑定此运行，不来自自动记录偏好。中断运行保留证据；当前工具运行时不自动恢复未知调用，也不重置物理额度。'),
        v2 && h('details', {className: 'dm-details'}, h('summary', null, '工具、候选与核验记录'),
          h('p', null, `能力：${run.spec.tools.map(tool => `${tool.name} (${tool.effect})`).join('、')}`),
          h('p', null, `策略：${run.spec.execution.verification_policy} · 已知 token 小计 ${run.usage?.known_total_tokens ?? '未知'} · 未知用量请求 ${run.usage?.unknown_requests ?? '未知'} · 尚未对账调用 ${run.usage?.unreconciled_calls ?? '未知'}`),
          ...(run.execution_events || []).filter(event => ['candidate_archived', 'check_completed', 'submit_completed', 'verification_completed', 'model_failed', 'native_stop'].includes(event.type)).map((event, index) => h('section', {key: index, className: 'dm-editor-section'},
            h('h4', null, event.type),
            ...['evidence_ref', 'note_ref', 'obligation_ref', 'receipt_evidence_ref'].filter(key => event[key]).map(key => button(`${event[key].id}@${event[key].rev}`, () => onInspect?.(event[key]), !onInspect)),
            h('pre', {className: 'dm-code'}, json(event))))),
        h('label', {className: 'dm-field'}, '本步动作', h('select', {className: 'dm-input', value: kind, disabled: locked, onChange: event => setKind(event.target.value)}, ...kinds.map(([value, label]) => h('option', {key: value, value}, label)))),
        button(kind === 'stop' ? '结束流程' : '执行一步', () => step(false), locked || run.status !== 'running' || (kind !== 'stop' && !canExecute), true),
        button(continuousBusy ? '正在按预算执行…' : '按预算连续执行', () => step(true), locked || run.status !== 'running' || !canExecute || kind === 'stop'),
        h('p', {className: 'dm-local-hint'}, `连续执行会串行消耗已冻结预算（最多 ${run.spec?.budget?.requests ?? '未知'} 次请求、${run.spec?.budget?.total_tokens === null ? '不设累计上限' : run.spec?.budget?.total_tokens ?? '未知'} tokens，含已用额度）；遇停止动作、连续两次无进展、预算限制、错误或用户暂停时结束。暂停可能需要等待当前调用返回。`),
        h('p', {className: 'dm-panel-meta'}, '启用运行只改变授权状态；单步结果仍是候选，人工审核在正式记录审核入口进行。'),
        actions.length > 0 && h('details', {className: 'dm-details'}, h('summary', null, `动作记录（${actions.length}）`), ...actions.map(action => h('section', {key: action.action_id, className: 'dm-editor-section'},
          h('h4', null, `${action.kind} · ${action.status}`),
          action.status === 'proposed' && button('重试归档', () => mutate('commit', action.action_id), locked),
          h('pre', {className: 'dm-code'}, json({action_id: action.action_id, usage: action.usage, output_refs: action.output_refs, proposal: action.proposal})))))));
  }
  return {WorkflowPanel};
};
if (typeof module !== 'undefined') module.exports = globalThis.DshMathWorkflowUI;
