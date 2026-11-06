window.__ModuleLoader__.load({id: 'dsh-math', factory: require => {
  const React = require('react');
  const {createElement: h, useState, useEffect, useRef} = React;
  const pretty = value => JSON.stringify(value, null, 2);
  const F = globalThis.DshMathForms;
  const {Preview} = globalThis.DshMathPreview(React, undefined, {sharedStyles: true});
  const {Editor, Refs, Select} = globalThis.DshMathFormUI(React, F, Preview);
  const {RecordReader, RecordBrowser, ReviewSummary, ExportReceipt, ContextPack} = globalThis.DshMathRecordUI(React, F, Preview);
  const {ProjectForm} = globalThis.DshMathProjectForm(React, F, Preview);
  const {MaterialsImport, LegacyImport} = globalThis.DshMathWorkbenchUI(React, F, Preview);
  const {LibraryBrowser, LibraryManager} = globalThis.DshMathLibraryUI(React, Preview);
  const {ExportPanel} = globalThis.DshMathExportUI(React);
  const {WorkflowPanel} = globalThis.DshMathWorkflowUI(React);
  const {ProjectManager} = globalThis.DshMathProjectManagerUI(React);
  // This viewer never instantiates the old autosaving Scratchpad controller.
  function HistoricalDraft({request, sessionId, projectId, writable, onPromote}) {
    const [value, setValue] = useState(null), [error, setError] = useState(''), [opened, setOpened] = useState(false);
    const lifetime = useRef({active: true, serial: 0});
    useEffect(() => {
      const state = lifetime.current; state.active = true; const serial = ++state.serial;
      request('scratchpad-read', {session_id: sessionId, project_id: projectId}).then(result => {
        if (state.active && state.serial === serial) { setValue(result); setError(''); }
      }).catch(failure => { if (state.active && state.serial === serial) setError(failure.message); });
      return () => { state.active = false; state.serial++; };
    }, [sessionId, projectId]);
    async function read(rev) {
      const state = lifetime.current, serial = ++state.serial;
      try { const next = await request('scratchpad-read', {session_id: sessionId, project_id: projectId, rev});
        if (state.active && state.serial === serial) { setValue(next); setError(''); }
      } catch (failure) { if (state.active && state.serial === serial) setError(failure.message); }
    }
    const pad = value?.scratchpad;
    if (!pad && !error && !value?.history?.length) return null;
    return h('details', {className: 'dm-details dm-historical-draft', onToggle: event => setOpened(event.currentTarget.open)},
      h('summary', null, '历史草稿（只读）'),
      error && h('p', {role: 'alert', className: 'dm-error'}, error),
      opened && h(React.Fragment, null,
        h('p', {className: 'dm-record-text'}, '仅查看已有版本；整理为笔记后仍需补全并明确保存。'),
        pad && h('section', null, h('h4', null, pad.title || '无标题历史草稿'), h('p', {className: 'dm-panel-meta'}, `v${pad.rev}`), h(Preview, {text: pad.body || ''}),
          writable && h('button', {type: 'button', className: 'dm-button', disabled: !pad.body?.trim(), onClick: () => onPromote('Note', pad)}, '将此版本整理为笔记')),
        h('div', {className: 'dm-actions'}, ...(value?.history || []).map(item => h('button', {type: 'button', className: 'dm-button dm-small', key: item.rev, onClick: () => read(item.rev)}, `查看 v${item.rev}`)))));
  }
  const Graph = globalThis.DshMathProcessGraph;
  const ProcessGraph = globalThis.DshMathGraphUI(React, Graph);
  const Research = globalThis.DshMathResearchGraph;
  const ResearchGraph = globalThis.DshMathResearchGraphUI(React, RecordReader);
  const ProjectAccess = globalThis.DshMathProjectAccessUI(React);
  const TAB_ID = 'dsh-math:projects';
  const pendingPromotionLinks = new Map(), retainedDocuments = new Map(), activeDocuments = new Map();
  const promotionKey = (session, project) => JSON.stringify([session, project]);
  const clone = value => JSON.parse(JSON.stringify(value));
  let surfaceSequence = 0;
  const hasLocalWork = value => !!(value?.dirty || value?.attempt || value?.review?.verdict || value?.review?.findings || value?.review?.attempt);
  function UnsavedPrompt({decision, finish}) {
    const root = useRef(null);
    useEffect(() => {
      const previous = typeof document !== 'undefined' ? document.activeElement : null;
      root.current?.showModal?.();
      return () => { root.current?.close?.(); if (previous?.isConnected && previous.getClientRects?.().length) previous.focus?.(); };
    }, []);
    return h('dialog', {ref: root, className: 'dm-modal-backdrop', 'aria-label': '未保存内容', onCancel: event => { event.preventDefault(); finish('cancel'); }},
      h('section', {className: 'dm-modal'}, h('h3', null, '先处理未保存内容'),
        h('p', {className: 'dm-record-text'}, decision.uncertain ? '上次提交结果尚未确认。只能保留原请求以便重试，不能直接丢弃或换成新请求。' : '正式记录或审核发现尚未提交。切换标签不会丢失；离开前请选择如何处理。'),
        h('div', {className: 'dm-actions'}, h(Button, {variant: 'primary', onClick: () => finish('cancel')}, '继续编辑'),
          decision.keep && h(Button, {onClick: () => finish('keep')}, '保留本窗口并离开'),
          !decision.uncertain && h(Button, {onClick: () => finish('discard')}, '放弃未保存修改')),
        h('p', {className: 'dm-local-hint'}, '保留只在本窗口有效，刷新或重启可能丢失。不会自动登记记录或提交审核。')));
  }
  async function request(action, args = {}) {
    const response = await fetch('/dsh-math/api', {method: 'POST', headers: {'content-type': 'application/json', 'x-dsh-math': 'human-ui-v1'}, body: JSON.stringify({action, args})});
    const result = await response.json();
    if (!result.ok) { const error = Error([result.error, result.message].filter(Boolean).join(': ') || '本地请求失败'); error.code = result.code ?? result.error; throw error; }
    return result.value;
  }
  function MathIcon({size = 14}) {
    return h('svg', {className: 'dm-icon', width: size, height: size, viewBox: '0 0 20 20', fill: 'none', 'aria-hidden': 'true'},
      h('path', {d: 'M3 15.5 7.2 4.5h2.3l2 6.2c.5 1.7 1 2.7 1.7 2.7.8 0 1.4-1 2-3.1l1.1-4h2.5', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round'}),
      h('path', {d: 'M3 17h14', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round'}));
  }
  function Button({children, onClick, disabled, variant = '', size = '', icon = false, title, type = 'button'}) {
    return h('button', {type, onClick, disabled, title, className: `dm-button ${variant ? `dm-${variant}` : ''} ${size ? `dm-${size}` : ''}`}, icon && h(MathIcon), children);
  }
  function recordActions({busy, create}) {
    return h('div', {className: 'dm-actions dm-record-actions'}, h(Button, {variant: 'primary', disabled: busy, onClick: create}, '新建记录'));
  }
  function Field({label, value, change, multiline = true, wide = false}) {
    const multi = multiline || /[\r\n]/.test(value || ''); // Preserve legacy multiline values verbatim.
    return h('label', {className: `dm-field ${wide ? 'dm-wide' : ''}`}, label,
      h(multi ? 'textarea' : 'input', {className: 'dm-input', value, onChange: e => change(e.target.value), rows: multi ? 3 : undefined}));
  }
  function Panel({title, meta, actions, children}) {
    return h('section', {className: 'dm-panel'},
      (title || actions) && h('div', {className: 'dm-panel-header'}, h('h3', {className: 'dm-panel-title'}, title), actions || (meta && h('span', {className: 'dm-panel-meta'}, meta))), children);
  }
  // A scope change owns a fresh view. Old writes may finish on the Host, but cannot
  // populate a new session's UI or trigger follow-up operations there.
  function MathView(props) { return h(ProjectView, {...props, key: props.sessionId || ''}); }
  function ProjectView({sessionId, openView, navigateSession, organizeMaterials, sidebar = false, onClose}) {
    const [projects, setProjects] = useState([]), [selected, setSelected] = useState(''), [view, setView] = useState(null);
    const [error, setError] = useState(''), [busy, setBusy] = useState(false), [creating, setCreating] = useState(false), [tab, setTab] = useState('overview');
    const [importing, setImporting] = useState(false), [promotionLink, setPromotionLink] = useState(null);
    const [focus, setFocus] = useState(null), [attachment, setAttachment] = useState(null), [editor, setEditor] = useState(null);
    const [sources, setSources] = useState([]), [candidate, setCandidate] = useState([]), [replay, setReplay] = useState(null), [preview, setPreview] = useState(null), [verdict, setVerdict] = useState(''), [findings, setFindings] = useState(''), [receipt, setReceipt] = useState(null);
    const [process, setProcess] = useState(null), [operation, setOperation] = useState('');
    const [access, setAccess] = useState(null);
    const [newRecord, setNewRecord] = useState(false), [newKind, setNewKind] = useState('Note');
    const [importToken, setImportToken] = useState(0), [libraryToken, setLibraryToken] = useState(0), [librarySnapshot, setLibrarySnapshot] = useState(null);
    const [projectManagement, setProjectManagement] = useState(false);
    const [downloadNotice, setDownloadNotice] = useState(''); const downloadUrls = useRef([]);
    const [draftSummary, setDraftSummary] = useState(null), [draftError, setDraftError] = useState('');
    const [decision, setDecision] = useState(null), [localNotice, setLocalNotice] = useState('');
    const [pinned, setPinned] = useState(null), [contextObject, setContextObject] = useState(null), [reviewStale, setReviewStale] = useState(false);
    const lifetime = useRef({active: true, serial: 0, loading: 0, selected: '', pending: false});
    const local = useRef({draft: null, dirty: false, busy: false, attempt: null, review: null});
    const decisionResolver = useRef(null), root = useRef(null), origin = useRef(null), restoreOrigin = useRef(false), readingDraft = useRef(0), surface = useRef(null);
    if (!surface.current) surface.current = `dm-surface-${++surfaceSequence}`;
    const documentKey = project => JSON.stringify([sessionId, project, sidebar ? 'sidebar' : onClose ? 'dialog' : 'conversation']);
    function remember() {
      const project = lifetime.current.selected;
      if (!project) return;
      const key = documentKey(project);
      if (hasLocalWork(local.current)) retainedDocuments.set(key, clone({...local.current, busy: false, owner: surface.current}));
      else if (retainedDocuments.get(key)?.owner === surface.current) retainedDocuments.delete(key);
    }
    function changeEditor(next, meta = {}) {
      local.current.draft = next; local.current.dirty = !!meta.dirty; setEditor(next); remember();
    }
    function changeReview(patch) {
      local.current.review = {...(local.current.review || {preview, verdict, findings, candidate, focus}), ...patch};
      if ('verdict' in patch) setVerdict(patch.verdict);
      if ('findings' in patch) setFindings(patch.findings);
      remember();
    }
    function clearReview() { local.current.review = null; setPreview(null); setVerdict(''); setFindings(''); setReviewStale(false); remember(); }
    function clearEditor() { local.current.draft = null; local.current.dirty = false; local.current.attempt = null; local.current.busy = false; setEditor(null); remember(); }
    function finishDecision(choice) { const resolve = decisionResolver.current; decisionResolver.current = null; setDecision(null); resolve?.(choice); }
    async function protectLocal(keep = true, scope = 'all') {
      if (local.current.busy || lifetime.current.pending || decisionResolver.current) return false;
      const value = scope === 'review' ? {review: local.current.review} : local.current;
      if (!hasLocalWork(value)) return true;
      const choice = await new Promise(resolve => { decisionResolver.current = resolve; setDecision({keep, uncertain: !!(value.attempt || value.review?.attempt)}); });
      if (!lifetime.current.active || choice === 'cancel') return false;
      if (choice === 'discard') { if (scope === 'all') clearEditor(); clearReview(); }
      else remember();
      return true;
    }
    function replaceLocal(action, scope = 'all') {
      if (!lifetime.current.active || local.current.busy || lifetime.current.pending) return;
      const value = scope === 'review' ? {review: local.current.review} : local.current;
      if (!hasLocalWork(value)) return action();
      return protectLocal(false, scope).then(allowed => { if (allowed && lifetime.current.active) return action(); });
    }
    async function readDraftSummary() {
      if (!sessionId) return;
      const generation = ++readingDraft.current;
      try {
        const result = await workbenchRequest.current('project-draft-read', {session_id: sessionId});
        if (lifetime.current.active && generation === readingDraft.current) { setDraftSummary(result); setDraftError(''); }
      } catch (error) { if (lifetime.current.active && generation === readingDraft.current) setDraftError(error.message); }
    }
    const guards = useRef(new Map()), guardRegistration = useRef(null), leaving = useRef(false);
    if (!guardRegistration.current) guardRegistration.current = (name, guard) => { guards.current.set(name, guard); return () => { if (guards.current.get(name) === guard) guards.current.delete(name); }; };
    const registerGuard = guardRegistration.current;
    async function leave(operation) {
      const state = lifetime.current, serial = state.serial;
      if (!state.active || state.pending || leaving.current) return;
      leaving.current = true;
      try {
        if (promotionLink) { setError('正式记录已保存，但草稿来源链接尚未登记；请先重试来源链接。'); return; }
        for (const guard of guards.current.values()) if (!await guard()) { if (state.active) setError('仍有未保存内容；请处理草稿中的提示，或明确选择仅保留本窗口。'); return; }
        if (!await protectLocal(true)) return;
        if (state.active && serial === state.serial) return await operation();
      } finally { leaving.current = false; }
    }
    // Child workbench requests have their own mutation/read serials: autosave must
    // neither invalidate a record operation nor be invalidated by an overview read.
    const workbenchRequest = useRef(null);
    if (!workbenchRequest.current) workbenchRequest.current = async (action, args) => {
      if (!lifetime.current.active) throw Error('View changed');
      return request(action, args);
    };
    async function api(action, args) {
      const state = lifetime.current, serial = state.serial;
      if (!state.active) throw Error('View changed');
      const value = await request(action, args);
      if (!state.active || serial !== state.serial) throw Error('View changed');
      return value;
    }
    async function run(action, label = '处理本地操作', propagate = false) {
      const state = lifetime.current;
      // Do not dispatch duplicate mutations between a click and React's disabled render.
      if (!state.active || state.pending) return;
      const serial = ++state.serial;
      state.pending = true; setBusy(true); setOperation(label); setError('');
      const current = () => state.active && serial === state.serial;
      try { return await action(); }
      catch (e) { if (current()) setError(e.message); if (propagate) throw e; }
      finally { if (current()) { state.pending = false; setBusy(false); setOperation(''); } }
    }
    async function load(id = selected, knownList) {
      const state = lifetime.current, loading = ++state.loading;
      const list = knownList || await api('list');
      const nextView = id ? await api('overview', {project_id: id}) : null;
      const context = sessionId ? await api('project-context', {session_id: sessionId, query: '', offset: 0, limit: 50}) : null;
      const library = id && nextView?.project?.schema_version === 4 ? await api('library-list', {session_id: sessionId, project_id: id, offset: 0, limit: 100, include_removed: false}) : null;
      if (!state.active || loading !== state.loading) return;
      setProjects(list); setView(nextView); setSelected(id); setAccess(context); setLibrarySnapshot(library); setLibraryToken(value => value + 1); setNewRecord(false);
      setPromotionLink(pendingPromotionLinks.get(promotionKey(sessionId, id)) || null);
      setAttachment(null); setReplay(null); setReceipt(null); setProcess(null); setContextObject(null); setDownloadNotice('');
      // Switching scopes restores only that session/project's in-window document.
      if (id !== state.selected) {
        remember();
        const saved = retainedDocuments.get(documentKey(id));
        local.current = saved ? {...clone(saved), busy: false, owner: surface.current} : {draft: null, dirty: false, busy: false, attempt: null, review: null};
        if (saved) retainedDocuments.set(documentKey(id), clone(local.current));
        setEditor(local.current.draft); setFocus(saved?.review?.focus || null); setPinned(null);
        setPreview(saved?.review?.preview || null); setVerdict(saved?.review?.verdict || ''); setFindings(saved?.review?.findings || '');
        setReviewStale(!!saved?.review?.preview); setCandidate(saved?.review?.candidate || []); setSources([]);
        setLocalNotice(saved ? '已恢复此会话／项目在本窗口保留的内容。尚未提交；刷新或重启仍可能丢失。' : '');
        if (saved) setTab('records');
      } else if (local.current.review?.preview) {
        setReviewStale(true); // A fresh overview cannot silently rebind an old review basis.
      }
      state.selected = id;
    }
    useEffect(() => {
      const state = lifetime.current;
      state.active = true; state.pending = false;
      activeDocuments.set(surface.current, () => local.current);
      run(async () => {
        const list = await api('list');
        const context = sessionId ? await api('project-context', {session_id: sessionId, query: '', offset: 0, limit: 50}) : null;
        const current = list.find(p => p.id === context?.current_project_id) || list.find(p => p.session_id === sessionId);
        await load(current?.id || '', list);
      }, '读取研究项目');
      readDraftSummary();
      const onFocus = () => readDraftSummary();
      if (typeof window !== 'undefined') window.addEventListener?.('focus', onFocus);
      return () => {
        remember(); activeDocuments.delete(surface.current); state.active = false; state.serial++; state.loading++; readingDraft.current++;
        for (const url of downloadUrls.current) URL.revokeObjectURL(url); downloadUrls.current = [];
        decisionResolver.current?.('cancel'); decisionResolver.current = null;
        if (typeof window !== 'undefined') window.removeEventListener?.('focus', onFocus);
      };
    }, [sessionId]);
    const lines = value => value.split('\n').filter(v => v.trim());
    const writable = F.writable(view);
    const ref = obj => ({id: obj.id, rev: obj.rev});
    const labelObject = object => globalThis.DshMathRecordUI.title?.(object) || object?.data?.title || object?.data?.statement || '记录';
    const link = value => { const object = view.objects.find(o => o.id === value.id && o.rev === value.rev); return h('button', {className: 'dm-ref', type: 'button', onClick: () => inspect(object)}, `${labelObject(object)} · 版本 ${value.rev}`); };
    const openCreate = () => replaceLocal(() => { setNewRecord(true); setTab('records'); });
    const openImport = () => setImportToken(value => value + 1);
    function edit(kind, object, scratchpad) {
      if (!lifetime.current.active || !writable || busy || promotionLink) return;
      return replaceLocal(() => {
        const next = scratchpad ? F.promoteDraft(kind, ref(view.groups.targets[0].object), () => crypto.randomUUID(), scratchpad) : F.draft(kind, ref(view.groups.targets[0].object), () => crypto.randomUUID(), object);
        clearReview(); local.current.attempt = null; changeEditor(next, {dirty: !!scratchpad}); setTab('records');
      });
    }
    async function linkPromotion(link) {
      await api('scratchpad-promote-link', {session_id: sessionId, ...link});
      pendingPromotionLinks.delete(promotionKey(sessionId, link.project_id)); setPromotionLink(null);
    }
    const downloadMaterial = object => run(async () => {
      const state = lifetime.current, serial = state.serial, projectId = selected;
      const isCurrent = () => state.active && state.serial === serial && state.selected === projectId;
      const helper = globalThis.DshMathMaterialsUI?.downloadOriginal;
      if (!helper) throw Error('当前材料下载组件尚未就绪。');
      setDownloadNotice('');
      const file = await helper({request: workbenchRequest.current, sessionId, projectId, ref: ref(object), isCurrent});
      if (!isCurrent()) return;
      const url = URL.createObjectURL(file.blob); downloadUrls.current.push(url);
      const link = document.createElement('a'); link.href = url; link.download = file.name; link.click();
      setDownloadNotice(`已发起下载：${file.name}；请在浏览器下载列表确认。`);
    }, '读取归档原件');
    const saveRecord = recordRequest => run(async () => {
      if (!F.writable(view)) throw Error('此项目只读，不能写入研究记录。');
      const document = local.current.draft || editor;
      const result = await api(document.kind === 'Evidence' ? 'evidence' : 'commit', {project_id: selected, request: recordRequest});
      if (document.promotion) {
        const target = result.versions?.find(value => value.id === document.id);
        if (!target) throw Error('记录可能已保存，但未返回精确版本；请刷新核对，不能猜测来源链接。');
        const link = {...document.promotion, target: {id: target.id, rev: target.rev}};
        pendingPromotionLinks.set(promotionKey(sessionId, selected), link);
        clearEditor(); setPromotionLink(link);
        try { await linkPromotion(link); }
        catch (error) { throw Error(`正式记录 ${target.id}@${target.rev} 已保存；来源链接未完成，请重试链接（不要重复保存记录）：${error.message}`); }
      } else clearEditor();
      await load(); return result;
    }, '保存研究记录', true);
    const inspect = object => {
      if (!object) return;
      return replaceLocal(() => {
        if (!focus && typeof document !== 'undefined') origin.current = {element: document.activeElement, scroll: root.current?.scrollTop || 0};
        setFocus(object); setAttachment(null); clearReview(); setCandidate([]); setTab('records');
      }, 'review');
    };
    const closeDetail = () => replaceLocal(() => {
      restoreOrigin.current = true; setFocus(null); setAttachment(null); clearReview();
    }, 'review');
    useEffect(() => {
      // The list was display:none on a narrow panel. Restore only after React
      // reveals it; focusing synchronously before that commit leaves focus on body.
      if (focus || tab !== 'records' || !restoreOrigin.current) return;
      restoreOrigin.current = false;
      if (root.current && origin.current) root.current.scrollTop = origin.current.scroll;
      const previous = origin.current?.element;
      const target = previous?.isConnected && previous.getClientRects?.().length ? previous : root.current?.querySelector('.dm-record-browser input');
      target?.focus?.({preventScroll: true});
    }, [focus, tab]);
    const requestReview = (refresh = false) => run(async () => {
      if (!F.writable(view)) throw Error('只读项目');
      const value = await api('review-preview', {project_id: selected, request: {target: ref(focus), candidate: candidate[0] || null}});
      setPreview(value); setReviewStale(false);
      changeReview({preview: value, focus, candidate, ...(refresh ? {} : {verdict: '', findings: ''}), attempt: null});
    }, '核对审核依据');
    const submitReview = () => run(async () => {
      if (!F.writable(view)) throw Error('只读项目');
      const current = local.current.review;
      if (!current?.preview || reviewStale && !current.attempt) throw Error('请先重新核对审核依据，填写的发现已保留。');
      const value = current.preview;
      const payload = current.attempt || {operation_id: crypto.randomUUID(), review_id: 'R-' + crypto.randomUUID(), target: value.basis.target,
        candidate: value.basis.candidate, verdict: current.verdict, scope: value.required_scope, findings: lines(current.findings), basis_hash: value.basis_hash};
      changeReview({attempt: payload});
      try {
        await api('review', {project_id: selected, request: payload});
        clearReview(); await load();
      } catch (error) {
        if (['VERSION_CONFLICT', 'REVIEW_CONFLICT', 'REVIEW_BLOCKED', 'SCOPE_MISMATCH', 'INVALID_SCHEMA', 'FORBIDDEN'].includes(error.code)) { changeReview({attempt: null}); setReviewStale(true); }
        throw error;
      }
    }, '提交本人审核记录');
    const groups = {targets: '当前目标', accepted: '已接受 / 已关闭', candidates: '候选结论', obligations: '待证事项', failed_routes: '失败路线', notes: '笔记'};
    function record(row) {
      const object = row.object;
      return h('div', {className: 'dm-record', key: `${object.id}@${object.rev}`},
        h('button', {className: 'dm-record-title', type: 'button', onClick: () => inspect(object)}, object.data.title || object.data.statement || object.kind),
        h('div', {className: 'dm-record-meta'}, link(ref(object)), h('span', {className: 'dm-panel-meta'}, object.kind)),
        h('p', {className: 'dm-record-text'}, row.assessment ? `${row.assessment.state} · ${row.assessment.reasons.join('；')}` : object.data.summary || object.data.support_scope || '打开记录查看完整内容'));
    }
    function groupPanels(limit) {
      // Overview is a digest, not six empty shelves. The records view remains complete.
      return Object.entries(groups).filter(([key]) => !limit || (key !== 'targets' && view.groups[key].length)).map(([key, title]) => h(Panel, {key, title, actions: h('span', {className: 'dm-badge'}, view.groups[key].length)},
        key === 'accepted' && h('p', {className: 'dm-record-text'}, '按记录状态归类。程序检查通过仅限对应检查范围，不代表全局证明或 R1/R2 已通过。'),
        view.groups[key].length ? view.groups[key].slice(0, limit || undefined).map(record) : h('p', {className: 'dm-empty'}, '暂无记录'),
        limit && view.groups[key].length > limit && h(Button, {variant: 'ghost', size: 'small', onClick: () => setTab('records')}, `查看全部 ${view.groups[key].length} 条`)));
    }
    const readProcess = selectedSources => run(async () => {
      // Read only. Never discover logs or invoke run_control/model tools here.
      const result = await api('replay', {project_id: selected, sources: selectedSources});
      const verified = Graph.build(view, result); // Reject a changed research head rather than mix snapshots.
      setProcess({operations: verified, research: Research.build(view, verified)});
    });
    const tabs = [['overview', '工作台', '工作台'], ['records', '知识库', '知识库'], ['process', '研究过程', '研究过程'], ['replay', '导出', '导出']];
    const changeTab = id => {
      setTab(id);
      if (id === 'process' && !process && !busy) return readProcess([]);
    };
    const navigateTabs = event => {
      const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
      if (!keys.includes(event.key)) return;
      const buttons = [...event.currentTarget.querySelectorAll('[role="tab"]')];
      const current = buttons.indexOf(event.target); if (current < 0) return;
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      event.preventDefault(); buttons[next].focus(); changeTab(tabs[next][0]);
    };
    const panelProps = id => ({id: `${surface.current}-${id}-panel`, role: 'tabpanel', 'aria-labelledby': `${surface.current}-${id}-tab`, hidden: tab !== id});
    const draft = draftSummary?.draft;
    const creationLabel = draftSummary?.pending_finalization ? '查看待恢复确认' : draft && draft.status !== 'committed' ?
      (draftSummary.validation?.complete ? '查看并确认草稿' : draftSummary.validation?.missing_fields?.length ? `继续准备 · 还缺 ${draftSummary.validation.missing_fields.length} 项` : '继续准备 · 检查待修正项') : '新建项目';
    const goal = view?.groups.targets[0]?.object;
    const stats = view ? [
      ['研究记录', view.objects.length], ['待证事项', view.groups.obligations.length], ['笔记', view.groups.notes.length], ['参考材料', view.objects.filter(o => o.kind === 'Evidence').length],
    ] : [];
    return h('div', {ref: root, className: `dsh-math-view ${sidebar ? 'dm-sidebar' : ''}`, 'aria-busy': busy},
      decision && h(UnsavedPrompt, {decision, finish: finishDecision}),
      h('header', {className: 'dm-header'},
        h('div', {className: 'dm-heading'},
          h('span', {className: 'dm-header-mark', 'aria-hidden': 'true'}, h(MathIcon)),
          h('div', {className: 'dm-heading-copy'}, h('span', {className: 'dm-eyebrow'}, 'RESEARCH / MATH'), h('h2', {className: 'dm-title'}, '数学研究'))),
        h('div', {className: 'dm-actions'},
          (projects.length > 0 || creating) && h(Button, {variant: 'ghost', size: 'small', disabled: busy, onClick: () => creating ? leave(() => setCreating(false)) : setCreating(true)}, creating ? '取消新建' : creationLabel),
          h(Button, {variant: 'ghost', size: 'small', onClick: () => { readDraftSummary(); return run(() => load(), '刷新研究记录'); }, disabled: busy}, '刷新'),
          openView && h(Button, {variant: 'ghost', size: 'small', onClick: () => openView('trajectory', '')}, '原生轨迹'),
          onClose && h(Button, {variant: 'ghost', size: 'small', title: '关闭数学工作台', onClick: () => leave(onClose), disabled: busy}, '关闭'))),
      projects.length > 0 && h('div', {className: 'dm-project-picker'},
        h('select', {className: 'dm-input', value: selected, disabled: busy, onChange: e => { const id = e.target.value; return leave(() => run(() => load(id))); }, 'aria-label': '选择研究项目'},
          h('option', {value: ''}, '选择研究项目'), ...projects.map(p => h('option', {key: p.id, value: p.id}, `${p.title}${p.mode === 'legacy_readonly_copy' ? ' · 只读' : ''}${p.session_id === sessionId ? ' · 当前会话' : ''}`))),
        h('span', {className: 'dm-panel-meta'}, `${projects.length} 个项目`)),
      sessionId && h(ProjectAccess, {request: workbenchRequest.current, sessionId, project: view?.project || null, context: access, onChange: result => setAccess(previous => ({...previous, ...result})), busy}),
      !view && !creating && h('section', {className: 'dm-welcome', 'aria-label': '研究起点'},
        h('div', {className: 'dm-welcome-overline'}, h('span', {className: 'dm-welcome-line', 'aria-hidden': 'true'}), '一个问题，一条清晰的路径'),
        h('h3', null, projects.length ? '继续你的研究。' : '从一个明确的\n问题开始。'),
        h('p', {className: 'dm-welcome-text'}, projects.length ? '选择一个项目，继续整理推导、证据与待验证结论。' : '让问题有边界，让尝试有来源。把推导与证据放在一起，结论留给验证和人工审核。'),
        h('div', {className: 'dm-welcome-actions'}, h(Button, {variant: 'primary', onClick: () => setCreating(true)}, creationLabel, h('span', {className: 'dm-button-arrow', 'aria-hidden': 'true'}, '→'))),
        draft && draft.status !== 'committed' && h('p', {className: 'dm-draft-summary'}, `${draft.fields?.title || '当前会话草稿'} · v${draft.rev} · 尚未创建项目`),
        draftError && h('p', {className: 'dm-field-error', role: 'status'}, `会话草稿暂未读到：${draftError}。可点击刷新重试。`),
        h('ol', {className: 'dm-start-steps'},
          ...[['明确目标', '写下陈述与约定'], ['积累证据', '保留尝试与来源'], ['人工审核', '核对范围与结论']].map(([title, detail], index) => h('li', {key: title}, h('span', {className: 'dm-step-number'}, index + 1), h('div', null, h('strong', null, title), h('span', null, detail))))),
        h('p', {className: 'dm-local-hint'}, '新建与浏览不调用模型，也不会自动开始研究。')), 
      h('details', {className: 'dm-details'}, h('summary', null, '项目管理'), h(Button, {onClick: () => setProjectManagement(value => !value)}, '管理项目与回收站')),
      !view && sessionId && h('details', {className: 'dm-details'}, h('summary', null, '会话草稿与回收站'), h(LibraryBrowser, {request: workbenchRequest.current, sessionId, projectId: null, view: null, writable: true, refreshToken: libraryToken, onChange: () => { readDraftSummary(); setLibraryToken(value => value + 1); }})),
      projectManagement && h(ProjectManager, {request: workbenchRequest.current, sessionId, onChange: () => run(async () => { const list = await api('list'); await load(list.some(item => item.id === selected) ? selected : '', list); })}),
      error && h('p', {className: 'dm-error', role: 'alert'}, error),
      busy && h('p', {className: 'dm-operation-status', role: 'status'}, `正在${operation || '处理本地操作'}，请稍候…`),
      localNotice && h('p', {className: 'dm-local-hint', role: 'status'}, localNotice),
      creating && h(Panel, {title: '项目准备'}, h(ProjectForm, {
        key: sessionId, request: workbenchRequest.current, sessionId, registerGuard,
        onDraftState: state => { if (lifetime.current.active && state.loaded) setDraftSummary({draft: state.draft, validation: state.validation || F.projectValidation(globalThis.DshMathProjectFields, state.fields), pending_finalization: state.finalizeAttempt}); },
        onCreated: project => run(async () => { setCreating(false); await load(project.id); setTab('overview'); }),
      })),
      promotionLink && h('div', {className: 'dm-conflict', role: 'status'},
        `正式记录 ${promotionLink.target.id}@${promotionLink.target.rev} 已保存；来源草稿 v${promotionLink.scratchpad_rev} 链接待登记。`,
        h(Button, {disabled: busy || !writable, onClick: () => run(async () => { await linkPromotion(promotionLink); await load(); })}, '重试来源链接')),
      view && h(React.Fragment, null,
        tab === 'overview' && h('div', {className: 'dm-stats'}, ...stats.map(([label, value]) => h('div', {className: 'dm-stat', key: label}, h('span', {className: 'dm-stat-value'}, value), h('span', {className: 'dm-stat-label'}, label)))),
        !writable && h('p', {className: 'dm-readonly', role: 'status'}, '只读项目 · 不可编辑草稿本、登记／修订记录或提交审核。'),
        h('nav', {className: 'dm-tabs', role: 'tablist', 'aria-label': '研究工作台视图', onKeyDown: navigateTabs}, ...tabs.map(([id, label, title]) => h('button', {key: id, type: 'button', role: 'tab', id: `${surface.current}-${id}-tab`, 'aria-controls': `${surface.current}-${id}-panel`, 'aria-selected': tab === id, tabIndex: tab === id ? 0 : -1, className: 'dm-tab', title, 'aria-label': title, 'data-active': tab === id, onClick: () => changeTab(id)}, label))),
        h('section', panelProps('process'),
          h('div', {className: 'dm-actions dm-graph-tools'},
            h(Button, {size: 'small', variant: 'primary', disabled: busy, onClick: () => readProcess([])}, process ? '刷新过程' : '查看研究过程')),

          process ? h(React.Fragment, {key: pretty([process.operations.research_head, process.operations.sources])},
            h(ResearchGraph, {graph: process.research, view, inspect: value => inspect(view.objects.find(object => object.id === value.id && object.rev === value.rev))}),
            h('details', {className: 'dm-details'}, h('summary', null, '操作来源与版本历史'), h(ProcessGraph, {graph: process.operations,
              inspect: value => inspect(view.objects.find(object => object.id === value.id && object.rev === value.rev))}))) :
            h('p', {className: 'dm-empty'}, busy ? '正在读取已记录过程…' : '点击“查看研究过程”，查看问题、研究路线与笔记之间的联系。')),
        h('section', panelProps('overview'),
          h('section', {className: 'dm-workbench-actions', 'aria-label': '常用功能'},
            h(Button, {variant: 'primary', disabled: busy || !writable, onClick: openImport}, '导入研究材料'),
            writable && recordActions({busy, create: openCreate}),
            h(Button, {onClick: () => changeTab('records')}, '打开知识库'),
            h(Button, {onClick: () => changeTab('process')}, '查看研究过程'),
            h(Button, {onClick: () => changeTab('replay')}, '导出内容'),
            h(Button, {onClick: () => setCreating(true), disabled: busy}, creationLabel)),
          goal && h('section', {className: 'dm-goal'},
            h('div', {className: 'dm-section-label'}, '当前目标'),
            h('h3', {className: 'dm-goal-title'}, goal.data.title || view.project.title),
            h('div', {className: 'dm-goal-statement'}, h(Preview, {text: goal.data.statement || ''})),


            F.nextActions(view).length > 0 && h('div', {className: 'dm-next-action-list'}, h('h4', null, '下一步'), ...F.nextActions(view).map(item => h('div', {key: F.refKey(item.ref), className: 'dm-next-action'}, h('span', null, item.text), link(item.ref)))),
            h('p', {className: 'dm-local-hint'}, '登记的是研究记录，不是证明。程序检查不替代人工审核。')),
          goal && view.project.schema_version === 4 && h(WorkflowPanel, {
            request: workbenchRequest.current, projectId: selected, sessionId,
            problemRef: {id: goal.id, rev: goal.rev}, writable,
            onInspect: ref => inspect(view.objects.find(object => object.id === ref.id && object.rev === ref.rev)),
            onChanged: () => lifetime.current.active && lifetime.current.selected === selected ? run(() => load(), '刷新研究流程记录', true) : undefined,
          }),
          h(Panel, {title: '最近内容', actions: h(Button, {size: 'small', onClick: () => changeTab('records')}, '查看全部')},
            ...(librarySnapshot?.items || []).slice(0, 5).map(item => h('div', {key: item.key || JSON.stringify(item.entry), className: 'dm-recent-item'},
              h(Button, {variant: 'ghost', onClick: () => { setTab('records'); const object = item.ref && view.objects.find(value => value.id === item.ref.id && value.rev === item.ref.rev); if (object && item.kind !== 'material') inspect(object); }}, item.title || '未命名内容'),
              h('span', {className: 'dm-panel-meta'}, [item.created_at ? new Date(item.created_at).toLocaleString() : '时间未知', Number.isFinite(item.bytes) ? item.bytes < 1024 ? `${item.bytes} B` : `${(item.bytes / 1024).toFixed(1)} KB` : ''].filter(Boolean).join(' · ')))),
            !librarySnapshot?.items?.length && h('p', {className: 'dm-empty'}, '导入文件或新建记录，开始积累研究资料。')),
          h('details', {className: 'dm-details dm-project-details'}, h('summary', null, '更多功能'),
            h('div', {className: 'dm-actions'},
              h(Button, {onClick: () => leave(() => setImporting(true))}, '导入旧研究库'),
              h(Button, {onClick: () => setProjectManagement(value => !value)}, '管理项目与回收站'),
              navigateSession && h(Button, {onClick: () => leave(() => navigateSession(view.project.session_id))}, '进入关联会话')),
            h(HistoricalDraft, {key: `${sessionId}:${selected}`, request: workbenchRequest.current, sessionId, projectId: selected, writable, onPromote: (kind, pad) => edit(kind, null, pad)}))),
        h('section', {...panelProps('records'), className: 'dm-records'},
          h(Panel, {title: '知识库'}, writable && recordActions({busy, create: openCreate})),
          newRecord && writable && h(Panel, {title: '新建记录'},
            h(Select, {label: '内容类型', value: newKind, items: [['Note', '笔记'], ['Obligation', '待证事项'], ['Evidence', '参考资料']], change: setNewKind}),
            h('div', {className: 'dm-actions'}, h(Button, {variant: 'primary', onClick: () => { setNewRecord(false); edit(newKind); }}, '继续'), h(Button, {onClick: () => setNewRecord(false)}, '取消'))),
          editor && writable && h(Panel, {title: '编辑研究记录'}, editor.promotion && h('p', {className: 'dm-record-text'}, `整理自草稿本 v${editor.promotion.scratchpad_rev}；请补全正式字段。保存后登记精确来源链接，原草稿保留。`),
            h(Editor, {key: editor.operation_id, initial: editor, initialDirty: local.current.dirty, initialAttempt: local.current.attempt, objects: view.objects, busy, inspect,
              onDraftChange: (next, meta) => { if (lifetime.current.active && local.current.draft?.operation_id === next.operation_id) changeEditor(next, meta); },
              onBusyChange: value => { if (lifetime.current.active && local.current.draft?.operation_id === editor.operation_id) local.current.busy = value; },
              onAttemptChange: value => { if (lifetime.current.active && local.current.draft?.operation_id === editor.operation_id) { local.current.attempt = value; remember(); } },
              cancel: () => replaceLocal(clearEditor), save: saveRecord})),
          h('div', {className: 'dm-record-layout', 'data-detail': !!focus},
            h('div', {className: 'dm-record-list'}, view.project.schema_version === 4 ? h(LibraryBrowser, {key: `${sessionId}:${selected}`, request: workbenchRequest.current, sessionId, projectId: selected, view, writable, refreshToken: libraryToken, inspect, onEdit: object => edit(object.kind, object), onChange: () => run(() => load()), onImport: openImport,
              organizeMaterials: (prompt, requestId) => organizeMaterials(sessionId, prompt, requestId)}) : h(RecordBrowser, {key: selected, view, inspect})),
            focus && h('section', {className: 'dm-record-detail', title: labelObject(focus)},
              h(RecordReader, {object: focus, view, inspect, onClose: closeDetail, onPin: object => setPinned(pinned && F.refKey(pinned) === F.refKey(object) ? null : object), pinned: !!pinned && F.refKey(pinned) === F.refKey(focus)},
                h('div', {className: 'dm-actions'},
                  ['Note', 'Obligation', 'Evidence'].includes(focus.kind) && writable && h(Button, {disabled: busy, onClick: () => edit(focus.kind, focus)}, '修订此记录'),
                  ['Note', 'Obligation'].includes(focus.kind) && writable && h(Button, {disabled: busy || !!local.current.review?.attempt, onClick: () => replaceLocal(() => requestReview(), 'review')}, '人工审核预览'),
                  h('details', {className: 'dm-details'}, h('summary', null, '更多操作'), h(Button, {onClick: () => setContextObject(focus)}, '准备上下文包'))),
                focus.kind === 'Obligation' && writable && h(Refs, {label: '审核所选候选', objects: view.objects.filter(o => (focus.data.candidates || []).some(r => F.refKey(r) === F.refKey(o))), kinds: ['Note'], problem: focus.data.problem_ref, value: candidate, single: true, change: v => replaceLocal(() => { setCandidate(v); clearReview(); }, 'review'), inspect}),
                focus.kind === 'Evidence' && focus.data.production?.method === 'dsh-math-material-import' && h(Button, {disabled: busy, onClick: () => downloadMaterial(focus)}, '下载归档原件'),
                 downloadNotice && h('p', {className: 'dm-local-hint', role: 'status'}, downloadNotice),
                 focus.kind === 'Evidence' && (focus.data.attachments || []).map(a => h(Button, {key: a.sha256, onClick: () => run(async () => setAttachment(await api('attachment', {project_id: selected, object_id: focus.id, rev: focus.rev, sha256: a.sha256})), '读取归档原文')}, `查看原文 ${a.logical_name}`)),
                attachment && h('pre', {className: 'dm-code'}, attachment.text === null ? pretty(attachment) : attachment.text)),
              preview && writable && h(Panel, {title: '人工审核预览', meta: '审核范围与保存状态分开显示'},
                h(ReviewSummary, {preview}),
                reviewStale && h('p', {role: 'status', className: 'dm-conflict'}, '记录已刷新；你的审核发现仍保留。提交前请重新核对审核依据。'),
                h('fieldset', {className: 'dm-editor-fields', disabled: busy || !!local.current.review?.attempt},
                  h('label', {className: 'dm-field'}, '人工审核结论', h('select', {className: 'dm-input', value: verdict, onChange: e => changeReview({verdict: e.target.value})}, h('option', {value: ''}, '由本人选择结论'), ...[['pass', '通过（限本次审核范围）'], ['fail', '不通过'], ['inconclusive', '暂不能判断']].map(([value, label]) => h('option', {key: value, value, disabled: value === 'pass' && !preview.eligible_for_pass}, label)))),
                  h(Field, {label: '本人审核发现（每行一项）', value: findings, change: value => changeReview({findings: value})})),
                h('div', {className: 'dm-actions'},
                  reviewStale && !local.current.review?.attempt && h(Button, {disabled: busy, onClick: () => requestReview(true)}, '重新核对审核依据'),
                  h(Button, {variant: 'primary', disabled: busy || !verdict || !findings.trim() || reviewStale && !local.current.review?.attempt || verdict === 'pass' && !preview.eligible_for_pass, onClick: submitReview}, local.current.review?.attempt ? '重试原审核提交' : '本人提交审核记录')),
                local.current.review?.attempt && h('p', {className: 'dm-local-hint'}, '提交结果未确认；只重试相同请求，不会另建审核记录。')))),
          pinned && h('aside', {className: 'dm-pinned-record', 'aria-label': '固定参考'}, h('div', {className: 'dm-reader-actions'}, h('h3', {className: 'dm-panel-title'}, '固定参考 · 保持精确版本'), h(Button, {variant: 'ghost', size: 'small', onClick: () => setPinned(null)}, '取消固定参考')), h(RecordReader, {object: pinned, view, inspect, autoFocus: false, pinned: true})),
          contextObject && h(ContextPack, {key: `${selected}:${F.refKey(contextObject)}`, view, selected: contextObject, onClose: () => setContextObject(null)})),
        h('section', panelProps('replay'),
          view.project.schema_version === 4 && h(ExportPanel, {key: `${selected}:${libraryToken}`, request: workbenchRequest.current, sessionId, projectId: selected, view}),
          h('details', {className: 'dm-details'}, h('summary', null, '完整审核包（高级）'),
            h('p', {className: 'dm-record-text'}, '包含完整研究历史与附件，可能包含回收站中的记录。'),
            h(Button, {disabled: busy, onClick: () => { setReceipt(null); return run(async () => setReceipt(await api('export', {project_id: selected, sources: [], expected_seq: view.snapshot.state.seq})), '生成完整审核包'); }}, '生成完整审核包'),
            receipt && h(ExportReceipt, {receipt})))),
      view && tab === 'overview' && h('div', {className: 'dm-import-compact'}, h(Button, {variant: 'ghost', size: 'small', disabled: busy, onClick: () => leave(() => setImporting(true))}, '导入旧研究库（只读副本）')),
      !view && h('section', {className: 'dm-import', 'aria-label': '导入旧研究库（只读副本）'},
        h('div', {className: 'dm-import-copy'},
          h('div', {className: 'dm-import-heading'}, h('h3', null, '延续已有研究'), h('span', {className: 'dm-badge'}, '只读副本')),
          h('p', null, '原库不变；不自动开始研究。')),
        h('div', {className: 'dm-import-action'}, h(Button, {disabled: busy, title: '导入旧研究库（只读副本）', onClick: () => leave(() => setImporting(true))}, '导入旧研究库'))),
      MaterialsImport && h(MaterialsImport, {request: workbenchRequest.current, sessionId, projectId: selected || null, triggerToken: importToken || null,
        onBrowse: () => { setTab('records'); setFocus(null); }, onNeedProject: () => setCreating(true), onImported: () => run(() => load(), '刷新导入材料'),
        onOrganize: (prompt, requestId) => { if (!organizeMaterials) throw Error('当前宿主无法发送材料整理请求。'); return organizeMaterials(sessionId, prompt, requestId); }}),
      importing && h(LegacyImport, {key: sessionId, request: workbenchRequest.current, sessionId, close: () => setImporting(false),
        onImported: project => leave(() => run(async () => { setImporting(false); await load(project.id); setTab('overview'); }))}));
  }
  function Entry({sessionId, navigateSession, openSidebar, organizeMaterials}) {
    const [opened, setOpened] = useState(false), [error, setError] = useState('');
    const dialogRef = useRef(null);
    useEffect(() => {
      const dialog = dialogRef.current;
      if (!opened || !dialog) return;
      const trigger = document.activeElement;
      try {
        if (typeof dialog.showModal !== 'function') throw Error('当前宿主不支持原生模态窗口。');
        if (!dialog.open) dialog.showModal(); // Top layer: cannot be occluded by native sidebar/composer stacking contexts.
      } catch (failure) { setOpened(false); setError(failure.message); return; }
      return () => { if (dialog.open) dialog.close(); if (trigger?.isConnected) trigger.focus(); };
    }, [opened]);
    const cancel = event => {
      if (event.target !== event.currentTarget) return; // A nested legacy-import dialog owns its own Escape.
      event.preventDefault();
      const closeButton = dialogRef.current?.querySelector('button[title="关闭数学工作台"]');
      if (closeButton) closeButton.click(); // Reuse ProjectView.leave(): flush or retain dirty/conflicting drafts.
      else setOpened(false);
    };
    const navigate = id => { setOpened(false); navigateSession(id); };
    const open = () => {
      setError('');
      try { if (!openSidebar?.(sessionId)) setOpened(true); }
      catch (failure) { setError(failure.message); }
    };
    return h(React.Fragment, null,
      h(Button, {icon: true, onClick: open}, '数学项目'),
      error && h('span', {className: 'dm-entry-error', role: 'alert'}, error),
      opened && h('dialog', {ref: dialogRef, role: 'dialog', 'aria-modal': true, 'aria-label': '数学研究工作台', className: 'dm-dialog', onCancel: cancel}, h(MathView, {sessionId, navigateSession: navigate, organizeMaterials, onClose: () => setOpened(false)})));
  }
  return {name: 'dsh-math-client', inject: ['slots', 'uiConversation', 'uiWorkspace', 'remote'], apply(ctx) {
    ctx.effect(() => {
      if (typeof document === 'undefined') return;
      const style = document.createElement('style');
      style.id = 'dsh-math-theme'; style.textContent = (globalThis.DshMathTheme || '') + (globalThis.DshMathResearchGraphTheme || '') + (globalThis.DshMathPreview.styles?.() || '');
      document.head.appendChild(style);
      const beforeUnload = event => { if ([...retainedDocuments.values()].some(hasLocalWork) || [...activeDocuments.values()].some(get => get().busy || hasLocalWork(get()))) { event.preventDefault(); event.returnValue = ''; } };
      if (typeof window !== 'undefined') window.addEventListener?.('beforeunload', beforeUnload);
      return () => { style.remove(); if (typeof window !== 'undefined') window.removeEventListener?.('beforeunload', beforeUnload); }; // Only this plugin generation's resources.
    }, 'dsh-math: theme');
    ctx.uiConversation.views.register({target: 'math', create: () => ({empty: null, replace: () => null, apply: () => null})});
    const navigateSession = id => ctx.uiWorkspace.openSession(id);
    const organizeMaterials = async (sessionId, prompt, requestId) => {
      if (!ctx.remote?.session?.prompt) throw Error('当前宿主不支持发送材料整理请求。');
      const response = await ctx.remote.session.prompt({requestId, sessionId, mode: 'queue', content: [{type: 'text', text: prompt}]});
      if (response?.ok === false) throw Object.assign(Error(response.error?.message || '材料整理请求未被接受。'), {code: response.error?.code});
      const receipt = response?.ok === true ? response.value : response;
      if (receipt?.accepted !== true) throw Error('材料整理请求回执未知；请核对原请求，勿重复发送。');
      return receipt;
    };
    const messages = {
      en: {session: 'Open a session before using Math Projects.', missing: 'The math tab is not ready. Reopen the sidebar.', disabled: 'The math tab is disabled. Enable it in Better Sidebar settings.'},
      zh: {session: '请先打开一个会话，再使用数学项目。', missing: '数学标签页尚未就绪，请重新打开侧栏。', disabled: '数学标签页已禁用，请在 Better Sidebar 设置中启用。'},
    };
    const locale = ctx.get('locale');
    let translate = key => messages.zh[key];
    if (locale) {
      ctx.effect(() => {
        const disposers = Object.entries(messages).map(([language, dictionary]) => locale.register('dsh-math-ui', language, dictionary));
        return () => disposers.reverse().forEach(dispose => dispose());
      }, 'dsh-math: entry messages');
      translate = locale.bind('dsh-math-ui');
    }
    let sidebar;
    ctx.inject(['betterSidebar'], child => {
      const service = child.get('betterSidebar');
      child.effect(() => {
        const dispose = service.registerTab({
          id: TAB_ID, title: '数学研究', description: '管理研究目标、笔记、参考材料与人工审核。', icon: size => h(MathIcon, {size}), single: true, order: 20,
          component: ({scope}) => h(MathView, {sessionId: scope.sessionId, navigateSession, organizeMaterials, sidebar: true}),
        });
        sidebar = service;
        return () => { if (sidebar === service) sidebar = undefined; dispose(); };
      }, 'dsh-math: Better Sidebar tab');
    });
    const openSidebar = sessionId => {
      if (!sidebar) return false; // Service absent/unloaded: retain the original fallback.
      if (!sessionId) throw Error(translate('session'));
      if (!sidebar.getTab(TAB_ID)) throw Error(translate('missing'));
      if (!sidebar.isTabEnabled(TAB_ID)) throw Error(translate('disabled'));
      sidebar.openTab({type: TAB_ID, title: '数学研究'}, {sessionId});
      return true; // Dispatch only: openTab is void, not a visible-success receipt.
    };
    const inject = () => ({navigateSession, openSidebar, organizeMaterials});
    ctx.slots.inject('conversation.view', () => ctx.slots.register({name: 'conversation.view', id: 'math', order: 20, label: () => '数学研究', inject}, MathView));
    for (const slot of ['conversation.hero.options', 'conversation.session.header.utilities']) {
      ctx.slots.inject(slot, () => ctx.slots.register({name: slot, id: 'math-projects', order: 20, inject}, Entry));
    }
  }};
}});
