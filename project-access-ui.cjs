(function(root) {
  function factory(React) {
    const {createElement: h, useState, useRef, useEffect} = React;
    return function ProjectAccess({request, sessionId, project, context, onChange, busy}) {
      const [attempt, setAttempt] = useState(null), [pending, setPending] = useState(false), [error, setError] = useState('');
      const life = useRef({active: true, pending: false});
      useEffect(() => { life.current.active = true; return () => { life.current.active = false; }; }, []);
      const current = !!project && context?.current_project_id === project.id;
      async function submit(action, extra = {}) {
        const state = life.current;
        if (!state.active || state.pending || busy || !context) return;
        const operation = attempt || {action, args: {session_id: sessionId, expected_rev: context.rev, operation_id: crypto.randomUUID(), ...extra}};
        state.pending = true; setPending(true); setAttempt(operation); setError('');
        try {
          await request(operation.action, operation.args);
          if (!state.active) return;
          // Idempotent receipts may describe an older revision; reload current settings.
          const fresh = await request('project-context', {session_id: sessionId, query: '', offset: 0, limit: 50});
          if (!state.active) return;
          setAttempt(null); onChange(fresh);
        } catch (failure) {
          if (!state.active) return;
          const code = /^([A-Z][A-Z0-9_]*)(?::|$)/.exec(String(failure.code || ''))?.[1];
          if (['VERSION_CONFLICT', 'INVALID_SCHEMA', 'OPERATION_CONFLICT', 'PROJECT_NOT_FOUND'].includes(code)) {
            setAttempt(null);
            try { const fresh = await request('project-context', {session_id: sessionId, query: '', offset: 0, limit: 50}); if (state.active) onChange(fresh); } catch (_) { /* Original failure remains visible. */ }
          }
          if (state.active) setError(failure.message);
        } finally { state.pending = false; if (state.active) setPending(false); }
      }
      const disabled = busy || pending || !context;
      return h('section', {className: 'dm-chat-project', 'aria-label': '当前会话研究设置'},
        h('div', {className: 'dm-actions'},
          h('span', {className: 'dm-panel-meta', role: 'status'}, current ? `当前会话研究项目：${project.title}` : context?.current_project_id ? '正在浏览其他项目' : '当前会话尚未选定研究项目'),
          project && !current && h('button', {type: 'button', className: 'dm-button dm-small', disabled: disabled || !!attempt, onClick: () => submit('project-associate', {project_id: project.id})}, '在当前会话继续此项目'),
          current && h('button', {type: 'button', className: 'dm-button dm-small dm-ghost', disabled: disabled || !!attempt, onClick: () => submit('project-disassociate', {project_id: project.id})}, '取消当前会话关联')),
        h('label', {className: 'dm-record-text'}, h('input', {type: 'checkbox', checked: context?.auto_record !== false, disabled: disabled || !!attempt, onChange: event => submit('project-preferences', {auto_record: event.target.checked})}), ' 自动记录数学研究阶段成果'),
        h('p', {className: 'dm-local-hint'}, '保存研究论证、依据与未决问题，均待人工审核。简单讲解不记录；关闭后仍可手动要求保存。'),
        project?.mode === 'legacy_readonly_copy' && h('p', {className: 'dm-local-hint'}, '旧研究库副本只读，可查阅，不能续写。'),
        pending && h('p', {role: 'status', className: 'dm-operation-status'}, '正在保存会话设置…'),
        error && h('p', {role: 'alert', className: 'dm-error'}, error),
        attempt && !pending && h('div', {className: 'dm-actions'}, h('p', {className: 'dm-local-hint'}, '设置结果未确认，请重试原请求。'), h('button', {type: 'button', className: 'dm-button dm-small', disabled, onClick: () => submit(attempt.action)}, '重试保存会话设置')));
    };
  }
  if (typeof module !== 'undefined') module.exports = factory;
  root.DshMathProjectAccessUI = factory;
})(globalThis);
