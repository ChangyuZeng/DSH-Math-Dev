(function (publish) {
  const retained = new Map(), retainedImports = new Map();
  const clone = value => JSON.parse(JSON.stringify(value));
  const hasCandidate = state => !!(state.candidateText || state.candidateSource);
  const appendCandidate = (body, candidate, source) => `${body}${body ? '\n\n' : ''}---\n\n候选（未经审核）${source ? `\n\n来源备注：${source}` : ''}\n\n${candidate}`;
  let unloadInstalled = false;
  function installUnload() {
    if (unloadInstalled || typeof window === 'undefined' || !window.addEventListener) return;
    unloadInstalled = true;
    window.addEventListener('beforeunload', event => {
      if ([...retained.values(), ...retainedImports.values()].some(state => state.dirty || state.attempt || hasCandidate(state))) { event.preventDefault(); event.returnValue = ''; }
    });
  }
  function createScratchpadController({request, sessionId, projectId, writable, uuid, notify = () => {}, setTimer = setTimeout, clearTimer = clearTimeout}) {
    const key = JSON.stringify([sessionId, projectId]), previous = retained.get(key);
    let state = previous ? {...clone(previous), busy: false, loading: false, preview: null} : {
      pad: null, title: '', body: '', history: [], preview: null, loaded: false, dirty: false,
      busy: false, loading: false, error: '', notice: '', incoming: null, attempt: null, blocked: false,
      candidateText: '', candidateSource: '', candidatePreview: null,
    };
    let active = true, generation = 0, reading = 0, historyRead = 0, pending = null, timer = null;
    const current = token => active && token === generation;
    const emit = patch => { if (!active) return; state = {...state, ...patch}; retained.set(key, clone(state)); notify(state); };
    const cancelTimer = () => { if (timer !== null) clearTimer(timer); timer = null; };
    function schedule() { cancelTimer(); if (active && writable && state.loaded && state.dirty && !state.blocked && !state.busy) timer = setTimer(() => { timer = null; save(); }, 800); }
    const accept = value => ({pad: value.scratchpad, title: value.scratchpad?.title || '', body: value.scratchpad?.body || '', history: value.history || [], loaded: true,
      dirty: false, incoming: null, attempt: null, blocked: false, preview: null});
    async function load() {
      if (!active || state.busy) return false;
      const token = generation, serial = ++reading;
      emit({loading: true});
      try {
        const value = await request('scratchpad-read', {session_id: sessionId, project_id: projectId});
        if (!current(token) || serial !== reading) return false;
        if (state.dirty || state.busy || state.attempt) {
          const changed = value.scratchpad?.rev !== state.pad?.rev;
          emit({loaded: true, history: value.history || [], incoming: changed ? value : state.incoming,
            blocked: state.blocked || changed, notice: changed ? '服务器已有更新；本地文字完整保留，请选择如何处理。' : state.notice});
        } else emit({...accept(value), error: '', notice: value.scratchpad ? `已保存 · v${value.scratchpad.rev}` : '尚无草稿本；开始输入后 800ms 自动保存。'});
        schedule(); return true;
      } catch (error) { if (current(token) && serial === reading) emit({error: error.message, blocked: true}); return false; }
      finally { if (current(token) && serial === reading) emit({loading: false}); }
    }
    function edit(field, value) {
      if (!active || !writable || !['title', 'body'].includes(field)) return;
      emit({[field]: value, dirty: true, ...(field === 'body' ? {candidatePreview: null} : {}), notice: '未保存 · 停止输入 800ms 后保存'}); schedule();
    }
    function editCandidate(field, value) {
      if (!active || !writable || !['candidateText', 'candidateSource'].includes(field)) return;
      emit({[field]: String(value), candidatePreview: null});
    }
    const candidateAllowed = () => active && writable && state.loaded && !state.loading && !state.busy && !state.blocked && !state.incoming && !state.attempt;
    function previewCandidate() {
      if (!candidateAllowed() || !state.candidateText?.trim()) return false;
      emit({candidatePreview: {body: state.body, rev: state.pad?.rev || 0, text: state.candidateText, source: state.candidateSource || '',
        result: appendCandidate(state.body, state.candidateText, state.candidateSource)}, error: ''});
      return true;
    }
    function adoptCandidate() {
      if (!candidateAllowed()) return false;
      const preview = state.candidatePreview;
      if (!preview || preview.body !== state.body || preview.rev !== (state.pad?.rev || 0) || preview.text !== state.candidateText || preview.source !== (state.candidateSource || '')) {
        emit({candidatePreview: null, error: '原稿或候选已改变，请重新预览后再追加。'}); return false;
      }
      // Append through the ordinary edit/autosave path; no formal-record or review write.
      edit('body', appendCandidate(state.body, state.candidateText, state.candidateSource));
      emit({candidateText: '', candidateSource: '', candidatePreview: null, error: '', notice: '候选已追加到草稿，未经审核；将按原有自动保存规则保存，不会登记正式记录。'});
      return true;
    }
    function cancelCandidate() {
      if (!active) return;
      emit({candidateText: '', candidateSource: '', candidatePreview: null});
    }
    function write(action, args) {
      if (!active || !writable || pending) return pending || Promise.resolve(false);
      if (!state.loaded) { emit({error: '请先成功读取草稿本，避免覆盖未知版本。'}); return Promise.resolve(false); }
      const token = generation, attempt = state.attempt || {action, args: {...args, operation_id: uuid()}, local: {title: state.title, body: state.body}};
      reading++; historyRead++;
      cancelTimer(); emit({busy: true, loading: false, error: '', attempt});
      const captured = attempt.local;
      pending = Promise.resolve().then(async () => {
        try {
          if (!current(token)) return false;
          const value = await request(attempt.action, attempt.args);
          if (!current(token)) return false;
          const pad = value.scratchpad;
          if (!pad || !Number.isInteger(pad.rev)) throw Error('草稿本保存返回缺少版本，未确认保存。');
          const changedDuringRestore = state.title !== captured.title || state.body !== captured.body;
          const isRestore = attempt.action === 'scratchpad-restore';
          const keep = isRestore ? changedDuringRestore : state.title !== pad.title || state.body !== pad.body;
          emit({pad, ...(isRestore && !keep ? {title: pad.title, body: pad.body} : {}), dirty: keep,
            history: value.history || [{rev: pad.rev, title: pad.title, saved_at: pad.saved_at, actor: pad.actor}, ...state.history.filter(item => item.rev !== pad.rev)],
            attempt: null, blocked: false, incoming: null, preview: isRestore ? null : state.preview,
            notice: keep ? `v${pad.rev} 已保存；还有新的本地修改。` : `已保存 · v${pad.rev}`, error: ''});
          return true;
        } catch (error) {
          if (current(token)) emit({error: `${error.message}；本地内容仍保留，未自动覆盖服务器。`, blocked: true});
          return false;
        } finally { if (current(token)) { pending = null; emit({busy: false}); schedule(); } }
      });
      return pending;
    }
    function save() {
      if (!active || !writable) return Promise.resolve(false);
      if (pending) return pending;
      if (!state.dirty && !state.attempt) return Promise.resolve(state.loaded);
      return write('scratchpad-save', {session_id: sessionId, project_id: projectId, expected_rev: state.pad?.rev || 0, title: state.title, body: state.body});
    }
    async function flush() {
      cancelTimer();
      if (!active || !writable) return false;
      if (pending && !await pending) return false;
      if (!active) return false;
      if (state.dirty || state.attempt) { if (!await save()) return false; if (state.dirty) return flush(); }
      return state.loaded && !state.dirty && !state.attempt;
    }
    async function history(rev) {
      if (!active) return false;
      const token = generation, serial = ++historyRead;
      try {
        const value = await request('scratchpad-read', {session_id: sessionId, project_id: projectId, rev});
        if (!current(token) || serial !== historyRead) return false;
        emit({preview: value.scratchpad, history: value.history || state.history}); return true;
      } catch (error) { if (current(token) && serial === historyRead) emit({error: error.message}); return false; }
    }
    async function restore(rev) {
      if (!active || !writable || pending) return false;
      // Never retry an unrelated uncertain write as if it were a successful restore.
      if (state.attempt) { emit({error: '请先重试或处理上次保存冲突，再恢复历史。'}); return false; }
      if (state.dirty) { emit({error: '请先保存当前本地修改，再恢复历史。'}); return false; }
      if (!Number.isInteger(rev) || !state.history.some(item => item.rev === rev)) return false;
      return write('scratchpad-restore', {session_id: sessionId, project_id: projectId, expected_rev: state.pad?.rev || 0, restore_rev: rev});
    }
    function useIncoming(keepLocal) {
      if (!active || state.busy || !state.incoming) return;
      cancelTimer(); const value = state.incoming;
      emit({...accept(value), ...(keepLocal ? {title: state.title, body: state.body, dirty: true, blocked: true} : {}), error: '',
        notice: keepLocal ? '本地内容保留，已采用服务器新基线；请核对后点击“立即保存”。' : '已采用服务器草稿本。'});
    }
    installUnload();
    return {get: () => state, load, edit, save, flush, history, restore, useIncoming, editCandidate, previewCandidate, adoptCandidate, cancelCandidate,
      clearPreview() { historyRead++; emit({preview: null}); },
      canLeave: async () => { if (hasCandidate(state)) return false; const saved = !state.dirty && !state.attempt || await flush(); return saved && !hasCandidate(state); },
      dispose() { if (!active) return; cancelTimer(); retained.set(key, clone(state)); active = false; generation++; reading++; historyRead++; },
    };
  }
  function createImportController({request, sessionId, uuid, notify = () => {}}) {
    let state = retainedImports.has(sessionId) ? {...clone(retainedImports.get(sessionId)), busy: false} : {path: '', preview: null, busy: false, error: '', attempt: null};
    let active = true, serial = 0, pending = null;
    const emit = patch => { if (active) { state = {...state, ...patch}; if (state.attempt) retainedImports.set(sessionId, clone(state)); else retainedImports.delete(sessionId); notify(state); } };
    function edit(path) { if (!active || state.busy || state.attempt) return; serial++; emit({path, preview: null, attempt: null, result: null, error: ''}); }
    function run(confirm) {
      if (!active || pending) return Promise.resolve(false);
      if (confirm && state.result) return Promise.resolve(state.result);
      if (!confirm && state.attempt) { emit({error: '上次导入结果尚未确认；请先重试确认，不会重复创建副本。'}); return Promise.resolve(false); }
      const token = ++serial, path = state.path;
      if (!path.trim() || confirm && !state.preview) return Promise.resolve(false);
      emit({busy: true, error: ''});
      pending = Promise.resolve().then(async () => {
        try {
          if (!active || token !== serial) return false;
          if (confirm) {
            const args = state.attempt || {session_id: sessionId, campaign: path, expected_fingerprint: state.preview.fingerprint, operation_id: uuid()};
            emit({attempt: args});
            const project = await request('import-legacy-confirm', args);
            if (!active || token !== serial) return false;
            emit({attempt: null, result: project}); return project;
          }
          const value = await request('import-legacy-preview', {campaign: path});
          if (!active || token !== serial) return false;
          emit({preview: value.preview, attempt: null}); return true;
        } catch (error) {
          if (active && token === serial) emit({error: error.code === 'SOURCE_CHANGED' ? '原研究库已改变，本次未导入；请重新预览后确认。' : error.message,
            ...(error.code === 'SOURCE_CHANGED' ? {preview: null, attempt: null} : {})});
          return false;
        }
        finally { if (active && token === serial) { pending = null; emit({busy: false}); } }
      });
      return pending;
    }
    installUnload();
    return {get: () => state, edit, preview: () => run(false), confirm: () => run(true), dispose() { active = false; serial++; }};
  }
  function factory(React, F, Preview) {
    const {createElement: h, useState, useEffect, useRef} = React;
    const Comparison = globalThis.DshMathComparisonUI?.(React);
    const compare = props => Comparison ? h(Comparison.FieldComparison, props) : h('div', {className: 'dm-comparison-fields'},
      ...props.fields.map(field => h('section', {key: field.key}, h('h4', null, field.label),
        h('p', null, `${props.localLabel || '我的内容'} · ${props.localVersion || ''}`), h('pre', {className: 'dm-code'}, props.local[field.key] || '（空）'),
        h('p', null, `${props.remoteLabel || '服务器内容'} · ${props.remoteVersion || ''}`), h('pre', {className: 'dm-code'}, props.remote[field.key] || '（空）'))));
    const button = (text, onClick, disabled = false, primary = false) => h('button', {type: 'button', className: `dm-button${primary ? ' dm-primary' : ''}`, disabled, onClick}, text);
    const renderPreview = text => Preview ? h(Preview, {text}) : h('pre', {className: 'dm-code'}, text);
    function Scratchpad({request, sessionId, projectId, writable, onPromote, registerGuard, active = true}) {
      const [state, setState] = useState(null), [allowLeave, setAllowLeave] = useState(false), [previewMode, setPreviewMode] = useState(false);
      const controller = useRef(null), promoting = useRef(false), callbacks = useRef({onPromote}); callbacks.current = {onPromote};
      useEffect(() => {
        const control = createScratchpadController({request, sessionId, projectId, writable, uuid: () => crypto.randomUUID(), notify: setState});
        controller.current = control; setAllowLeave(false); setState(control.get()); control.load();
        const focus = () => control.load();
        if (typeof window !== 'undefined') window.addEventListener?.('focus', focus);
        return () => { control.dispose(); controller.current = null; if (typeof window !== 'undefined') window.removeEventListener?.('focus', focus); };
      }, [sessionId, projectId, writable]);
      useEffect(() => registerGuard?.('scratchpad', async () => allowLeave || await controller.current?.canLeave()), [registerGuard, allowLeave]);
      const promote = async kind => {
        if (promoting.current) return;
        promoting.current = true; const control = controller.current;
        try { if (await control?.flush() && controller.current === control) callbacks.current.onPromote?.(kind, clone(control.get().pad)); }
        finally { promoting.current = false; }
      };
      if (!state) return h('section', {hidden: !active, role: 'status'}, '正在读取草稿本…');
      const edit = (field, value) => { setAllowLeave(false); controller.current?.edit(field, value); };
      const editCandidate = (field, value) => { setAllowLeave(false); controller.current?.editCandidate(field, value); };
      const candidateBlocked = !writable || !state.loaded || state.loading || state.busy || state.blocked || !!state.incoming || !!state.attempt;
      const candidateCurrent = state.candidatePreview && state.candidatePreview.body === state.body && state.candidatePreview.rev === (state.pad?.rev || 0);
      return h('section', {hidden: !active, className: 'dm-scratchpad', 'aria-label': '项目草稿本', 'aria-busy': state.busy},
        h('div', {className: 'dm-panel-header'}, h('h3', {className: 'dm-panel-title'}, '草稿本'), h('span', {className: 'dm-panel-meta', role: 'status'}, state.busy ? '正在保存…' : state.dirty ? '未保存' : state.pad ? `v${state.pad.rev} 已保存` : '尚无版本')),
        h('p', {className: 'dm-record-text'}, writable ? '写下想法，自动保存。' : '此项目只读。'),
        h('label', {className: 'dm-field'}, '草稿本标题', h(/[\r\n]/.test(state.title) ? 'textarea' : 'input', {className: 'dm-input', value: state.title, maxLength: 2000, disabled: !writable || !state.loaded, onChange: event => edit('title', event.target.value)})),
        h('div', {className: 'dm-actions'}, button(previewMode ? '继续编辑' : '预览', () => setPreviewMode(!previewMode)),
          writable && (state.dirty || state.attempt) && button('立即保存', () => controller.current?.flush(), state.busy || !state.loaded)),
        previewMode ? renderPreview(state.body) : h('label', {className: 'dm-field'}, '草稿正文', h('textarea', {className: 'dm-input dm-scratch-body', rows: 14, value: state.body, disabled: !writable || !state.loaded, placeholder: '支持 Markdown 和 $公式$；不确定的想法也可以先记录。', onChange: event => edit('body', event.target.value)})),
        state.notice && (state.blocked || state.incoming) && h('p', {role: 'status', className: 'dm-record-text'}, state.notice),
        state.error && h('p', {role: 'alert', className: 'dm-error'}, state.error),
        state.incoming && h('div', {className: 'dm-conflict'}, h('p', null, '版本冲突：本地内容未丢失，自动保存已暂停。比较后选择；不会自动合并。保留我的修改后，请点击“立即保存”。'),
          compare({fields: [{key: 'title', label: '标题'}, {key: 'body', label: '正文'}], local: state, remote: state.incoming.scratchpad || {},
            localVersion: `v${state.pad?.rev || 0}（本地编辑）`, remoteVersion: `v${state.incoming.scratchpad?.rev || 0}`}),
          button('保留我的修改，稍后重新保存', () => controller.current?.useIncoming(true), state.busy || !writable),
          button('使用服务器版本', () => { if (window.confirm('确定用服务器版本替换本地未保存内容？')) controller.current?.useIncoming(false); }, state.busy)),
        (writable || hasCandidate(state)) && h('details', {className: 'dm-details dm-candidate', 'aria-label': '粘贴候选到草稿'},
          h('summary', null, hasCandidate(state) ? '粘贴候选（有未采用内容）' : '粘贴候选到草稿'),
          h('p', {className: 'dm-record-text'}, '只处理你粘贴的内容，不读取其他会话、不调用模型。先预览，再明确追加到草稿；未经审核，不会直接登记正式记录或提交审核。未采用候选仅在本窗口保留，刷新或关闭可能丢失。'),
          h('label', {className: 'dm-field'}, '候选内容', h('textarea', {className: 'dm-input dm-candidate-input', rows: 5, value: state.candidateText || '', disabled: !writable, onChange: event => editCandidate('candidateText', event.target.value)})),
          h('label', {className: 'dm-field'}, '来源备注（可选）', h('textarea', {className: 'dm-input', rows: 2, value: state.candidateSource || '', disabled: !writable, onChange: event => editCandidate('candidateSource', event.target.value)})),
          h('div', {className: 'dm-actions'}, button('预览原稿与追加结果', () => controller.current?.previewCandidate(), candidateBlocked || !state.candidateText?.trim()),
            hasCandidate(state) && button('取消并丢弃候选', () => { if (window.confirm('确定丢弃尚未采用的候选及来源备注？原稿不会改变。')) controller.current?.cancelCandidate(); })),
          state.candidatePreview && h('div', {className: 'dm-candidate-preview'},
            compare({fields: [{key: 'body', label: '正文'}], local: {body: state.candidatePreview.body}, remote: {body: state.candidatePreview.result}, localLabel: '原稿', remoteLabel: '追加后的草稿（未经审核）', localVersion: `v${state.candidatePreview.rev} + 本地编辑`}),
            !candidateCurrent && h('p', {role: 'alert', className: 'dm-local-hint'}, '原稿版本已改变，请重新预览。'),
            button('追加到草稿（未经审核）', () => { setAllowLeave(false); controller.current?.adoptCandidate(); }, candidateBlocked || !candidateCurrent, true))),
        writable && h('details', {className: 'dm-details'}, h('summary', null, '整理为研究记录'),
          h('p', {className: 'dm-record-text'}, '草稿不进入正式记录或默认导出。整理后补全字段再保存。'),
          h('div', {className: 'dm-actions dm-promotion'}, button('整理为笔记', () => promote('Note'), state.busy || !state.loaded || !state.body.trim()),
            button('整理为待证事项', () => promote('Obligation'), state.busy || !state.loaded || !state.body.trim()))),
        h('details', {className: 'dm-details'}, h('summary', null, `历史版本（${state.history.length}）`),
          button('读取最新版本', () => controller.current?.load(), state.loading || state.busy),
          ...[...state.history].sort((a, b) => b.rev - a.rev).map(item => h('div', {key: item.rev, className: 'dm-history-row'}, button(`查看 v${item.rev}`, () => controller.current?.history(item.rev)), h('span', {className: 'dm-record-text'}, `${item.title || '无标题'} · ${item.saved_at || ''}`))),
          !state.history.length && h('p', {className: 'dm-empty'}, '尚无历史版本')),
        state.preview && h('section', {className: 'dm-panel', 'aria-label': '草稿本历史预览'}, h('h4', null, `历史 v${state.preview.rev} · ${state.preview.title}`), renderPreview(state.preview.body),
          h('div', {className: 'dm-actions'}, writable && button('恢复为新版本', () => controller.current?.restore(state.preview.rev), state.busy || state.dirty || !!state.attempt), button('关闭历史预览', () => controller.current?.clearPreview())),
          state.dirty && h('p', {className: 'dm-local-hint'}, '请先保存当前本地修改，恢复不会抹去历史。')),
        (state.blocked || state.attempt || hasCandidate(state)) && h('p', {className: 'dm-local-hint'}, hasCandidate(state) ? '尚有未采用的候选。请追加、明确丢弃，或选择仅保留本窗口再离开；候选不会自动写入草稿。' : '切换项目前会尝试保存；失败时不会离开。',
          button('仅保留本窗口并允许离开', () => setAllowLeave(true)), allowLeave && ' 本地稿已保留在本窗口；刷新或关闭窗口可能丢失。'));
    }
    function LegacyImport({request, sessionId, close, onImported}) {
      const [state, setState] = useState({path: '', preview: null, busy: false, error: ''});
      const controller = useRef(null), root = useRef(null), input = useRef(null), callbacks = useRef({close, onImported}); callbacks.current = {close, onImported};
      useEffect(() => {
        const previous = typeof document !== 'undefined' ? document.activeElement : null;
        const control = createImportController({request, sessionId, uuid: () => crypto.randomUUID(), notify: setState});
        controller.current = control; setState(control.get());
        if (root.current?.showModal) root.current.showModal(); else root.current?.setAttribute?.('open', '');
        input.current?.focus();
        return () => { control.dispose(); controller.current = null; root.current?.close?.(); previous?.focus?.(); };
      }, [sessionId]);
      function cancel() { if (!controller.current?.get().busy) callbacks.current.close(); }
      function keydown(event) {
        if (event.key === 'Escape' && !controller.current?.get().busy) { event.preventDefault(); cancel(); }
        if (event.key !== 'Tab') return;
        const controls = Array.from(root.current?.querySelectorAll('button:not(:disabled), input:not(:disabled), [tabindex="0"]') || []);
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
      return h('dialog', {className: 'dm-modal-backdrop', ref: root, 'aria-label': '导入旧研究库', 'aria-modal': true, onKeyDown: keydown,
        onCancel: event => { event.preventDefault(); cancel(); }}, h('section', {className: 'dm-modal'},
        h('h3', null, '导入旧研究库'), h('p', {className: 'dm-record-text'}, '仅适用于 schema 3。先只读预览，再确认导入副本；原始数据不变，副本不可编辑。'),
        h('label', {className: 'dm-field'}, 'schema 3 研究库完整路径', h('input', {ref: input, className: 'dm-input', value: state.path, disabled: state.busy || !!state.attempt, onChange: event => controller.current?.edit(event.target.value)})),
        state.error && h('p', {role: 'alert', className: 'dm-error'}, state.error),
        state.attempt && h('p', {className: 'dm-local-hint'}, '上次确认结果尚未收到，请使用同一次请求重试。关闭弹窗后本窗口仍保留，刷新可能丢失恢复信息。'),
        state.preview && h('dl', {className: 'dm-import-facts'}, ...[['标题', state.preview.title], ['研究库', state.preview.campaign_id], ['版本', `schema ${state.preview.schema_version} · seq ${state.preview.seq}`], ['版本记录数', state.preview.object_count], ['归档附件数', state.preview.attachment_count], ['指纹', state.preview.fingerprint]].map(([label, value]) => h('div', {key: label}, h('dt', null, label), h('dd', null, value)))),
        h('div', {className: 'dm-actions'}, button(state.busy ? '处理中…' : '只读预览', () => controller.current?.preview(), state.busy || !!state.attempt || !state.path.trim()),
          state.preview && button(state.result ? '打开已导入副本' : state.attempt ? '重试确认导入' : '确认导入只读副本', async () => { const control = controller.current; const project = await control?.confirm(); if (project && controller.current === control) callbacks.current.onImported(project); }, state.busy, true),
          button('取消导入', cancel, state.busy))));
    }
    return {Scratchpad, LegacyImport};
  }
  factory.createScratchpadController = createScratchpadController;
  factory.createImportController = createImportController;
  publish(factory);
})(value => { if (typeof module !== 'undefined') module.exports = value; else globalThis.DshMathWorkbenchUI = value; });
