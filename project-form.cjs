(function (publish) {
  const retained = new Map();
  const clone = value => JSON.parse(JSON.stringify(value));
  let unloadInstalled = false;
  function installUnload() {
    if (unloadInstalled || typeof window === 'undefined' || !window.addEventListener) return;
    unloadInstalled = true;
    window.addEventListener('beforeunload', event => {
      if ([...retained.values()].some(state => state.dirty || state.uncertain)) { event.preventDefault(); event.returnValue = ''; }
    });
  }
  function createController({request, sessionId, descriptor, F, uuid, notify = () => {}}) {
    const key = sessionId || '', previous = retained.get(key);
    let state = previous ? {...clone(previous), busy: false, loading: false} : {
      fields: F.projectDefaults(descriptor), draft: null, validation: null, dirty: false, loaded: false,
      busy: false, loading: false, error: '', notice: '', incoming: null, attempt: null, finalizeAttempt: null, uncertain: false,
    };
    let active = true, generation = 0, reading = 0, edits = 0, pending = null;
    const emit = patch => { if (!active) return; state = {...state, ...patch}; retained.set(key, clone(state)); notify(state); };
    const current = token => active && token === generation;
    const recovery = value => value.pending_finalization && value.draft ? {session_id: sessionId, draft_id: value.draft.id,
      operation_id: value.pending_finalization.operation_id, expected_rev: value.pending_finalization.expected_rev} : null;
    const accept = value => ({draft: value.draft, fields: {...F.projectDefaults(descriptor), ...(value.draft?.fields || {})},
      validation: value.validation, incoming: null, dirty: false, loaded: true, attempt: null, finalizeAttempt: recovery(value), uncertain: !!recovery(value)});
    async function load() {
      if (!active || state.busy) return false;
      if (!sessionId) { emit({error: '请先打开一个普通会话。'}); return false; }
      const token = generation, serial = ++reading;
      emit({loading: true});
      try {
        const value = await request('project-draft-read', {session_id: sessionId});
        if (!current(token) || serial !== reading) return false;
        if (state.dirty || state.busy || state.uncertain) {
          const changed = value.draft?.id !== state.draft?.id || value.draft?.rev !== state.draft?.rev;
          emit({loaded: true, incoming: changed || recovery(value) ? value : state.incoming,
            ...(recovery(value) ? {finalizeAttempt: recovery(value), uncertain: true} : {}),
            notice: recovery(value) ? '上次确认创建尚未完成。已冻结编辑；只有你点击继续确认才会恢复。' : changed ? '检测到会话草稿更新；你的本地编辑未被覆盖。' : state.notice});
        } else emit({...accept(value), error: '', notice: recovery(value) ? '上次确认创建尚未完成。已冻结编辑；请核对后继续确认。' : value.draft ? `已读取本会话草稿 · v${value.draft.rev}` : '尚无会话草稿；可手动填写，也可先在聊天中澄清。'});
        return true;
      } catch (error) { if (current(token) && serial === reading) emit({error: error.message}); return false; }
      finally { if (current(token) && serial === reading) emit({loading: false}); }
    }
    function edit(field, value) {
      if (!active || state.finalizeAttempt || state.draft?.status === 'committed' || !descriptor.fields.some(item => item.key === field)) return;
      edits++;
      emit({fields: {...state.fields, [field]: value}, dirty: true, validation: null, error: '', notice: ''});
    }
    async function saveExact(token, snapshot) {
      if (!state.loaded) throw Error('请先成功读取会话草稿，再保存。');
      if (state.draft?.status === 'committed') throw Error('此草稿已确认建项，请另建空白草稿。');
      const patch = F.projectPatch(descriptor, snapshot.fields), captured = snapshot.edits;
      const args = {session_id: sessionId, draft_id: state.draft?.id || null, expected_rev: state.draft?.rev || 0, patch};
      const fingerprint = JSON.stringify(args);
      // A failed transport is ambiguous. Retry the same operation before sending changed content.
      if (state.attempt && state.attempt.fingerprint !== fingerprint) throw Error('上次保存结果未确认；请先重试原保存，或读取服务器草稿后明确选择。');
      const attempt = state.attempt || {fingerprint, args: {...args, operation_id: uuid()}};
      emit({attempt, uncertain: true});
      const value = await request('project-draft-save', attempt.args);
      if (!current(token)) return null;
      emit({draft: value.draft, validation: edits === captured ? value.validation : null, dirty: edits !== captured, attempt: null, finalizeAttempt: recovery(value), uncertain: !!recovery(value),
        incoming: null, notice: `草稿已保存 · v${value.draft.rev}；尚未创建项目。`, error: ''});
      return {value, captured};
    }
    function write(finalize) {
      if (!active || pending) return Promise.resolve(false);
      const token = generation, snapshot = {fields: clone(state.fields), edits}; reading++;
      emit({busy: true, loading: false, error: ''});
      pending = Promise.resolve().then(async () => {
        try {
          if (!current(token)) return false;
          let result;
          if (state.finalizeAttempt) {
            // Recover an ambiguous finalize with its exact id/revision/operation identity.
            if (!finalize) throw Error('建项结果尚未确认，请重试“确认创建项目”。');
          } else {
            result = await saveExact(token, snapshot);
            if (!current(token) || !result) return false;
            if (!finalize) return true;
            if (result.captured !== edits) throw Error('保存期间内容已修改；请检查后再次确认创建项目。');
            if (!result.value.validation?.complete) { emit({notice: '草稿已保存，请补全标注字段后再确认创建。'}); return false; }
            if (!state.finalizeAttempt) emit({finalizeAttempt: {session_id: sessionId, draft_id: state.draft.id, expected_rev: state.draft.rev, operation_id: uuid()}, uncertain: true});
          }
          const value = await request('finalize-draft', state.finalizeAttempt);
          if (!current(token)) return false;
          // A different window may have frozen an older revision while this one had
          // unsaved edits. Recover that exact project, retaining these edits as a new draft.
          emit({draft: state.dirty ? null : value.draft, incoming: null, attempt: null, finalizeAttempt: null, uncertain: false,
            notice: state.dirty ? '项目已创建；未保存的本地修改仍作为新草稿保留。' : '项目已创建；研究执行仍未授权。'});
          return value.project;
        } catch (error) { if (current(token)) emit({error: error.message}); return false; }
        finally { if (current(token)) { pending = null; emit({busy: false}); } }
      });
      return pending;
    }
    async function retrySave() {
      if (!active || pending || state.finalizeAttempt || !state.attempt) return false;
      const token = generation, attempt = state.attempt; reading++;
      emit({busy: true, loading: false, error: ''});
      pending = Promise.resolve().then(async () => {
        try {
          if (!current(token)) return false;
          const value = await request('project-draft-save', attempt.args);
          if (!current(token)) return false;
          const same = JSON.stringify(F.projectPatch(descriptor, state.fields)) === JSON.stringify(attempt.args.patch);
          emit({draft: value.draft, validation: same ? value.validation : null, dirty: !same, attempt: null, uncertain: false, notice: '已确认上次保存；当前本地修改仍保留。'});
          return true;
        } catch (error) { if (current(token)) emit({error: error.message}); return false; }
        finally { if (current(token)) { pending = null; emit({busy: false}); } }
      });
      return pending;
    }
    function useIncoming(keepLocal) {
      if (!active || state.busy || !state.incoming) return;
      edits++;
      const value = state.incoming;
      if (keepLocal && value.draft?.status === 'committed') { emit({error: '服务器草稿已建项；请另建空白草稿。'}); return; }
      emit({...accept(value), ...(keepLocal ? {fields: state.fields, dirty: true} : {}), error: '', notice: keepLocal ? '已采用服务器版本作为保存基线；本地内容未变，需手动保存。' : '已采用服务器草稿。'});
    }
    function fresh() {
      if (!active || state.busy || state.finalizeAttempt) return;
      edits++; reading++;
      emit({...accept({draft: null, validation: null}), loading: false, fields: F.projectDefaults(descriptor), dirty: true, error: '', notice: '新的空白草稿；未保存，不会覆盖已有草稿。'});
    }
    installUnload();
    return {get: () => state, load, edit, save: () => write(false), finalize: () => write(true), retrySave, useIncoming, fresh,
      canLeave: async () => { if (pending) await pending; if (!active) return false; return !state.dirty && !state.uncertain || await write(false) === true; },
      dispose() { if (!active) return; retained.set(key, clone(state)); active = false; generation++; reading++; },
    };
  }
  function factory(React, F, Preview) {
    const {createElement: h, useState, useEffect, useRef} = React;
    const Comparison = globalThis.DshMathComparisonUI?.(React);
    function ProjectForm({request, sessionId, onCreated, onDraftState, registerGuard, descriptor = globalThis.DshMathProjectFields}) {
      const [state, setState] = useState(null), [showErrors, setShowErrors] = useState(false), [allowLeave, setAllowLeave] = useState(false);
      const controller = useRef(null), callbacks = useRef({onCreated, onDraftState}); callbacks.current = {onCreated, onDraftState};
      const heading = useRef(null);
      // Read-only notification; never routes state updates through a save/finalize action.
      useEffect(() => { if (state) callbacks.current.onDraftState?.(clone(state)); }, [state]);
      useEffect(() => {
        const control = createController({request, sessionId, descriptor, F, uuid: () => crypto.randomUUID(), notify: setState});
        controller.current = control; setAllowLeave(false); setState(control.get()); control.load(); heading.current?.focus();
        const focus = () => control.load();
        if (typeof window !== 'undefined') window.addEventListener?.('focus', focus);
        return () => { control.dispose(); controller.current = null; if (typeof window !== 'undefined') window.removeEventListener?.('focus', focus); };
      }, [sessionId]);
      useEffect(() => registerGuard?.('project-form', async () => allowLeave || await controller.current?.canLeave()), [registerGuard, allowLeave]);
      if (!state) return h('p', {role: 'status'}, '正在读取会话草稿…');
      const validation = state.validation || F.projectValidation(descriptor, state.fields);
      const disabled = state.busy || !!state.finalizeAttempt || !state.loaded || state.draft?.status === 'committed';
      const edit = (key, value) => { setAllowLeave(false); controller.current?.edit(key, value); };
      const primaryKeys = new Set(['title', 'statement', 'completion_criteria']);
      const detailedIssues = showErrors ? descriptor.fields.filter(item => !primaryKeys.has(item.key) && ((validation.errors || []).some(error => error.field === item.key) || validation.missing_fields?.includes(item.key))) : [];
      const issue = key => showErrors ? (validation.errors || []).filter(error => error.field === key).map(error => error.message).join('；') || (validation.missing_fields?.includes(key) ? '此项必填' : '') : '';
      function field(field) {
        const value = state.fields[field.key], error = issue(field.key), helpId = `dm-project-${field.key}-help`;
        const label = {title: '项目标题（可选）', statement: '问题描述', completion_criteria: '研究目标'}[field.key] || field.label;
        const props = {className: 'dm-input', value: field.type === 'string[]' ? (value || []).join('\n') : value || '',
          disabled, required: field.required, 'aria-invalid': !!error, 'aria-describedby': helpId,
          placeholder: field.placeholder, onChange: event => edit(field.key, field.type === 'string[]' ? event.target.value.split('\n') : event.target.value)};
        let input;
        if (field.key === 'target_type') input = h('div', {className: 'dm-target-cards', role: 'radiogroup', 'aria-label': field.label, 'aria-describedby': helpId},
          ...descriptor.target_types.map(option => h('label', {className: 'dm-target-card', key: option.value, 'data-active': value === option.value},
            h('input', {type: 'radio', name: 'target_type', value: option.value, checked: value === option.value, disabled, onChange: () => edit(field.key, option.value)}),
            h('span', null, h('strong', null, option.label), h('span', null, option.description)))));
        else if (field.key === 'knowledge_status') input = h('select', props, ...descriptor.knowledge_statuses.map(option => h('option', {key: option.value, value: option.value}, option.label)));
        else input = h(field.type === 'string[]' || field.key === 'statement' || /[\r\n]/.test(value || '') ? 'textarea' : 'input', {...props, rows: 3});
        return h('div', {key: field.key, className: 'dm-project-field'},
          field.key === 'target_type' ? h('div', {className: 'dm-field-label'}, field.label, field.required && h('span', {className: 'dm-required'}, '必填'), input) :
            h('label', {className: 'dm-field'}, label, field.required && field.key !== 'title' && h('span', {className: 'dm-required'}, '必填'), input),
          h('span', {className: 'dm-sr-only', id: helpId}, field.help), error && h('p', {className: 'dm-field-error', role: 'alert'}, error));
      }
      return h('form', {className: 'dm-project-form', noValidate: true, 'aria-label': '项目准备草稿', onSubmit: async event => {
        event.preventDefault(); setShowErrors(true); const control = controller.current;
        const project = await control?.finalize(); if (project && controller.current === control) callbacks.current.onCreated?.(project);
      }},
        h('h3', {ref: heading, tabIndex: -1}, '明确研究目标'),
        h('p', {className: 'dm-record-text'}, '先存草稿，确认后才建项；不会自动研究。'),
        h('div', {className: 'dm-actions'}, h('button', {type: 'button', className: 'dm-button dm-small', disabled: state.busy || state.loading, onClick: () => controller.current?.load()}, '读取会话草稿'),
          h('button', {type: 'button', className: 'dm-button dm-ghost dm-small', disabled: state.busy || !!state.finalizeAttempt, onClick: () => {
            if (!(state.dirty || state.uncertain) || typeof window !== 'undefined' && window.confirm('本地修改尚未保存。确定另建空白草稿？')) { controller.current?.fresh(); setShowErrors(false); setAllowLeave(false); }
          }}, '另建空白草稿')),
        h('section', {className: 'dm-project-primary', 'aria-label': '问题与研究目标'}, ...descriptor.fields.filter(item => primaryKeys.has(item.key)).map(field)),
        h('details', {className: 'dm-details dm-project-conditions', ...(detailedIssues.length ? {open: true} : {})},
          h('summary', null, `详细条件与来源${detailedIssues.length ? ` · 需补全或修正 ${detailedIssues.length} 项` : ''}`),
          h('p', {className: 'dm-record-text'}, '量词、范围和来源需如实填写。可先保存不完整草稿；确认建项会检查全部必填项。'),
          ...[['problem', '问题与范围'], ['goal', '目标类型'], ['source', '来源与状态']].map(([group, title]) =>
            h('fieldset', {className: 'dm-fieldset', key: group}, h('legend', null, title), ...descriptor.fields.filter(item => item.group === group && !primaryKeys.has(item.key)).map(field)))),
        Preview && h('details', {className: 'dm-details'}, h('summary', null, '预览问题与数学公式'), h(Preview, {text: state.fields.statement || ''})),
        state.notice && h('p', {role: 'status', className: 'dm-record-text'}, state.notice),
        state.error && h('p', {role: 'alert', className: 'dm-error'}, state.error),
        state.finalizeAttempt && h('div', {className: 'dm-conflict'}, h('p', null, `待恢复确认：${state.finalizeAttempt.draft_id} · v${state.finalizeAttempt.expected_rev}。只会继续这次已确认的版本，不会另建重复项目。`),
          state.dirty && h('p', null, '当前未保存修改不会纳入这次建项；它们会作为本地新草稿保留。'),
          Preview && h('details', null, h('summary', null, '查看已冻结的问题陈述'), h(Preview, {text: state.incoming?.draft?.fields.statement || state.draft?.fields.statement || ''}))),
        state.incoming && h('div', {className: 'dm-conflict'}, h('p', null, '服务器草稿与本地版本不同；先比较再选择，不会自动合并。保留我的修改后，还需点击“保存草稿”。'),
          Comparison ? h(Comparison.FieldComparison, {fields: descriptor.fields, local: state.fields, remote: state.incoming.draft?.fields || {},
            localVersion: `${state.draft?.id || '未保存'} · v${state.draft?.rev || 0}（本地编辑）`, remoteVersion: `${state.incoming.draft?.id || '无草稿'} · v${state.incoming.draft?.rev || 0}`}) :
            h('div', {className: 'dm-comparison-fields'}, ...descriptor.fields.map(field => h('section', {key: field.key}, h('h4', null, field.label),
              h('p', null, `我的内容 · v${state.draft?.rev || 0}`), h('pre', {className: 'dm-code'}, Array.isArray(state.fields[field.key]) ? state.fields[field.key].join('\n') : state.fields[field.key] || '（空）'),
              h('p', null, `服务器内容 · v${state.incoming.draft?.rev || 0}`), h('pre', {className: 'dm-code'}, Array.isArray(state.incoming.draft?.fields[field.key]) ? state.incoming.draft.fields[field.key].join('\n') : state.incoming.draft?.fields[field.key] || '（空）')))),
          h('button', {type: 'button', className: 'dm-button', disabled: state.busy, onClick: () => controller.current?.useIncoming(true)}, '保留我的修改，稍后重新保存'),
          h('button', {type: 'button', className: 'dm-button', disabled: state.busy, onClick: () => { if (window.confirm('确定用服务器草稿替换本地未保存内容？')) controller.current?.useIncoming(false); }}, '使用服务器版本')),
        state.attempt && !state.finalizeAttempt && !state.busy && h('button', {type: 'button', className: 'dm-button', onClick: () => controller.current?.retrySave()}, '重试上次保存'),
        h('div', {className: 'dm-actions'},
          h('button', {type: 'button', className: 'dm-button', disabled, onClick: () => { setShowErrors(true); controller.current?.save(); }}, state.busy ? '处理中…' : '保存草稿'),
          h('button', {type: 'submit', className: 'dm-button dm-primary', disabled: state.busy || !state.loaded || state.draft?.status === 'committed'}, state.finalizeAttempt ? '继续确认创建' : '确认创建项目')),
        (state.dirty || state.uncertain) && h('p', {className: 'dm-local-hint'}, '尚有未确认保存的内容；切换前会尝试保存。',
          h('button', {type: 'button', className: 'dm-button dm-ghost dm-small', onClick: () => setAllowLeave(true)}, '仅保留本窗口并允许离开'), allowLeave && ' 本地稿仅在本窗口保留，刷新或关闭窗口可能丢失。'));
    }
    return {ProjectForm};
  }
  factory.createController = createController;
  publish(factory);
})(value => { if (typeof module !== 'undefined') module.exports = value; else globalThis.DshMathProjectForm = value; });
