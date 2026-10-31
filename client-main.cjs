window.__ModuleLoader__.load({id: 'dsh-math', factory: require => {
  const React = require('react');
  const {createElement: h, useState, useEffect, useRef} = React;
  const pretty = value => JSON.stringify(value, null, 2);
  const F = globalThis.DshMathForms;
  const {Editor, Refs, Select} = globalThis.DshMathFormUI(React, F);
  const {Preview} = globalThis.DshMathPreview(React, undefined);
  const {ProjectForm} = globalThis.DshMathProjectForm(React, F, Preview);
  const {Scratchpad, LegacyImport} = globalThis.DshMathWorkbenchUI(React, F, Preview);
  const Graph = globalThis.DshMathProcessGraph;
  const ProcessGraph = globalThis.DshMathGraphUI(React, Graph);
  const TAB_ID = 'dsh-math:projects';
  const pendingPromotionLinks = new Map();
  const promotionKey = (session, project) => JSON.stringify([session, project]);
  async function request(action, args = {}) {
    const response = await fetch('/dsh-math/api', {method: 'POST', headers: {'content-type': 'application/json', 'x-dsh-math': 'human-ui-v1'}, body: JSON.stringify({action, args})});
    const result = await response.json();
    if (!result.ok) { const error = Error([result.error, result.message].filter(Boolean).join(': ') || '本地请求失败'); error.code = result.error; throw error; }
    return result.value;
  }
  function MathIcon({size = 14}) {
    return h('svg', {className: 'dm-icon', width: size, height: size, viewBox: '0 0 20 20', fill: 'none', 'aria-hidden': 'true'},
      h('path', {d: 'M3 15.5 7.2 4.5h2.3l2 6.2c.5 1.7 1 2.7 1.7 2.7.8 0 1.4-1 2-3.1l1.1-4h2.5', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round'}),
      h('path', {d: 'M3 17h14', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round'}));
  }
  function ImportIcon() {
    return h('svg', {className: 'dm-icon', width: 20, height: 20, viewBox: '0 0 20 20', fill: 'none', 'aria-hidden': 'true'},
      h('path', {d: 'M3.5 8v7.5a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V8M2.5 3.5h15v4h-15zM10 9.5v4m-2-2 2 2 2-2', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round'}));
  }
  function Button({children, onClick, disabled, variant = '', size = '', icon = false, title, type = 'button'}) {
    return h('button', {type, onClick, disabled, title, className: `dm-button ${variant ? `dm-${variant}` : ''} ${size ? `dm-${size}` : ''}`}, icon && h(MathIcon), children);
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
  function ProjectView({sessionId, openView, navigateSession, sidebar = false, onClose}) {
    const [projects, setProjects] = useState([]), [selected, setSelected] = useState(''), [view, setView] = useState(null);
    const [error, setError] = useState(''), [busy, setBusy] = useState(false), [creating, setCreating] = useState(false), [tab, setTab] = useState('overview');
    const [importing, setImporting] = useState(false), [promotionLink, setPromotionLink] = useState(null);
    const [focus, setFocus] = useState(null), [attachment, setAttachment] = useState(null), [editor, setEditor] = useState(null);
    const [sources, setSources] = useState([]), [candidate, setCandidate] = useState([]), [replay, setReplay] = useState(null), [preview, setPreview] = useState(null), [verdict, setVerdict] = useState(''), [findings, setFindings] = useState(''), [receipt, setReceipt] = useState(null);
    const [process, setProcess] = useState(null);
    const lifetime = useRef({active: true, serial: 0, loading: 0, selected: '', pending: false});
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
    async function run(operation) {
      const state = lifetime.current;
      // Do not dispatch duplicate mutations between a click and React's disabled render.
      if (!state.active || state.pending) return;
      const serial = ++state.serial;
      state.pending = true; setBusy(true); setError('');
      const current = () => state.active && serial === state.serial;
      try { await operation(); }
      catch (e) { if (current()) setError(e.message); }
      finally { if (current()) { state.pending = false; setBusy(false); } }
    }
    async function load(id = selected, knownList) {
      const state = lifetime.current, loading = ++state.loading;
      const list = knownList || await api('list');
      const nextView = id ? await api('overview', {project_id: id}) : null;
      if (!state.active || loading !== state.loading) return;
      setProjects(list); setView(nextView); setSelected(id);
      setPromotionLink(pendingPromotionLinks.get(promotionKey(sessionId, id)) || null);
      setFocus(null); setAttachment(null); setPreview(null); setReplay(null); setReceipt(null); setProcess(null); setVerdict(''); setFindings('');
      // Compare against the last accepted project, not an older render's closure.
      if (id !== state.selected) { setEditor(null); setCandidate([]); setSources([]); }
      state.selected = id;
    }
    useEffect(() => {
      const state = lifetime.current;
      state.active = true; state.pending = false;
      run(async () => {
        const list = await api('list');
        const current = list.find(p => p.session_id === sessionId);
        await load(current?.id || '', list);
      });
      return () => { state.active = false; state.serial++; state.loading++; };
    }, [sessionId]);
    const lines = value => value.split('\n').filter(v => v.trim());
    const writable = F.writable(view);
    const ref = obj => ({id: obj.id, rev: obj.rev});
    const link = value => h('button', {className: 'dm-ref', type: 'button', onClick: () => inspect(view.objects.find(o => o.id === value.id && o.rev === value.rev))}, `${value.id}@${value.rev}`);
    function edit(kind, object, scratchpad) {
      if (!lifetime.current.active || !writable || busy || promotionLink) return;
      setEditor(scratchpad ? F.promoteDraft(kind, ref(view.groups.targets[0].object), () => crypto.randomUUID(), scratchpad) : F.draft(kind, ref(view.groups.targets[0].object), () => crypto.randomUUID(), object));
      setPreview(null); setTab('records');
    }
    async function linkPromotion(link) {
      await api('scratchpad-promote-link', {session_id: sessionId, ...link});
      pendingPromotionLinks.delete(promotionKey(sessionId, link.project_id)); setPromotionLink(null);
    }
    const saveRecord = recordRequest => run(async () => {
      if (!F.writable(view)) throw Error('此项目只读，不能写入研究记录。');
      const result = await api(editor.kind === 'Evidence' ? 'evidence' : 'commit', {project_id: selected, request: recordRequest});
      if (editor.promotion) {
        const target = result.versions?.find(value => value.id === editor.id);
        if (!target) throw Error('记录可能已保存，但未返回精确版本；请刷新核对，不能猜测来源链接。');
        const link = {...editor.promotion, target: {id: target.id, rev: target.rev}};
        pendingPromotionLinks.set(promotionKey(sessionId, selected), link);
        setEditor(null); setPromotionLink(link);
        try { await linkPromotion(link); }
        catch (error) { throw Error(`正式记录 ${target.id}@${target.rev} 已保存；来源链接未完成，请重试链接（不要重复保存记录）：${error.message}`); }
      } else setEditor(null);
      await load();
    });
    const inspect = object => { if (!object) return; setFocus(object); setAttachment(null); setPreview(null); setCandidate([]); setTab('records'); };
    const groups = {targets: '当前目标', accepted: '已接受 / 已关闭', candidates: '候选结论', obligations: '证明义务', failed_routes: '失败路线', notes: '研究笔记'};
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
      setProcess(Graph.build(view, result)); // Reject a changed research head rather than mix snapshots.
    });
    const changeTab = id => {
      setTab(id);
      if (id === 'process' && !process && !busy) return readProcess([]);
    };
    const goal = view?.groups.targets[0]?.object;
    const stats = view ? [
      ['研究记录', view.objects.length], ['证明义务', view.groups.obligations.length], ['研究笔记', view.groups.notes.length], ['证据材料', view.objects.filter(o => o.kind === 'Evidence').length],
    ] : [];
    return h('div', {className: `dsh-math-view ${sidebar ? 'dm-sidebar' : ''}`, 'aria-busy': busy},
      h('header', {className: 'dm-header'},
        h('h2', {className: 'dm-title'}, h(MathIcon), '数学研究'),
        h('div', {className: 'dm-actions'},
          (projects.length > 0 || creating) && h(Button, {variant: 'ghost', size: 'small', disabled: busy, onClick: () => creating ? leave(() => setCreating(false)) : setCreating(true)}, creating ? '取消新建' : '新建项目'),
          h(Button, {variant: 'ghost', size: 'small', onClick: () => run(() => load()), disabled: busy}, '刷新'),
          openView && h(Button, {variant: 'ghost', size: 'small', onClick: () => openView('trajectory', '')}, '原生轨迹'),
          onClose && h(Button, {variant: 'ghost', size: 'small', title: '关闭数学工作台', onClick: () => leave(onClose), disabled: busy}, '关闭'))),
      projects.length > 0 && h('div', {className: 'dm-project-picker'},
        h('select', {className: 'dm-input', value: selected, disabled: busy, onChange: e => { const id = e.target.value; return leave(() => run(() => load(id))); }, 'aria-label': '选择研究项目'},
          h('option', {value: ''}, '选择研究项目'), ...projects.map(p => h('option', {key: p.id, value: p.id}, p.title))),
        h('span', {className: 'dm-panel-meta'}, `${projects.length} 个项目`)),
      !view && !creating && h('section', {className: 'dm-welcome', 'aria-label': '研究起点'},
        h('div', {className: 'dm-welcome-mark'}, h(MathIcon, {size: 24})),
        h('h3', null, projects.length ? '继续你的研究' : '从一个明确的问题开始'),
        h('p', {className: 'dm-welcome-text'}, projects.length ? '在上方选择项目，继续整理推导、证据与待验证结论。' : '把问题、推导和证据整理在一起。每一步有据可查，结论留给验证与人工审核。'),
        h(Button, {variant: 'primary', icon: true, onClick: () => setCreating(true)}, '新建项目'),
        h('ol', {className: 'dm-start-steps'},
          ...[['明确目标', '写下陈述与约定'], ['积累证据', '保留尝试与来源'], ['人工审核', '核对范围与结论']].map(([title, detail], index) => h('li', {key: title}, h('span', {className: 'dm-step-number'}, index + 1), h('div', null, h('strong', null, title), h('span', null, detail))))),
        h('p', {className: 'dm-local-hint'}, '新建与浏览不调用模型，也不会自动开始研究。')), 
      error && h('p', {className: 'dm-error', role: 'alert'}, error),
      busy && h('p', {className: 'dm-operation-status', role: 'status'}, '正在处理本地操作，请稍候…'),
      creating && h(Panel, {title: '项目准备', meta: '聊天草稿与手动填写统一保存'}, h(ProjectForm, {
        key: sessionId, request: workbenchRequest.current, sessionId, registerGuard,
        onCreated: project => run(async () => { setCreating(false); await load(project.id); setTab('overview'); }),
      })),
      promotionLink && h('div', {className: 'dm-conflict', role: 'status'},
        `正式记录 ${promotionLink.target.id}@${promotionLink.target.rev} 已保存；来源草稿 v${promotionLink.scratchpad_rev} 链接待登记。`,
        h(Button, {disabled: busy || !writable, onClick: () => run(async () => { await linkPromotion(promotionLink); await load(); })}, '重试来源链接')),
      view && h(React.Fragment, null,
        h('div', {className: 'dm-stats'}, ...stats.map(([label, value]) => h('div', {className: 'dm-stat', key: label}, h('span', {className: 'dm-stat-value'}, value), h('span', {className: 'dm-stat-label'}, label)))),
        !writable && h('p', {className: 'dm-readonly', role: 'status'}, '只读项目 · 不可编辑草稿本、登记／修订记录或提交审核。'),
        h('nav', {className: 'dm-tabs', 'aria-label': '研究工作台视图'}, ...[['overview', '概览', '研究概览'], ['scratchpad', '草稿本', '项目草稿本'], ['process', '过程', '研究过程'], ['records', '记录', '记录与审核'], ['replay', '回放', '回放与导出']].map(([id, label, title]) => h('button', {key: id, className: 'dm-tab', title, 'aria-label': title, 'data-active': tab === id, onClick: () => changeTab(id)}, label))),
        h(Scratchpad, {key: `${sessionId}:${selected}`, request: workbenchRequest.current, sessionId, projectId: selected, writable,
          active: tab === 'scratchpad', registerGuard, onPromote: (kind, pad) => edit(kind, null, pad)}),
        tab === 'process' && h(React.Fragment, null,
          h('div', {className: 'dm-actions dm-graph-tools'},
            h(Button, {size: 'small', disabled: busy, onClick: () => readProcess([])}, '读取本项目记录'),
            h(Button, {size: 'small', disabled: busy || !sources.length, onClick: () => readProcess(sources)}, `加入所选来源（${sources.length}）`),
            h(Button, {size: 'small', variant: 'ghost', onClick: () => setTab('replay')}, '选择工具账本')),
          h('p', {className: 'dm-record-text'}, '只读快照，不自动扫描会话或启动研究。额外来源在“回放与导出”中明确选择后加入。'),
          process ? h(ProcessGraph, {key: pretty([process.research_head, process.sources]), graph: process,
            inspect: value => inspect(view.objects.find(object => object.id === value.id && object.rev === value.rev))}) :
            h('p', {className: 'dm-empty'}, busy ? '正在读取已记录过程…' : '尚未读取。点击“读取本项目记录”生成过程图。')),
        tab === 'overview' && h(React.Fragment, null,
          goal && h('section', {className: 'dm-goal'},
            h('div', {className: 'dm-section-label'}, '当前目标', h('span', {className: 'dm-panel-meta'}, `seq ${view.snapshot.state.seq}`)),
            h('h3', {className: 'dm-goal-title'}, goal.data.title || view.project.title),
            h('p', {className: 'dm-goal-statement'}, goal.data.statement),
            h('div', {className: 'dm-record-meta'}, link(ref(goal))),
            writable && h('div', {className: 'dm-actions dm-next-actions'},
              h(Button, {variant: 'primary', disabled: busy, onClick: () => edit('Note')}, '登记笔记'), h(Button, {disabled: busy, onClick: () => edit('Obligation')}, '登记义务'), h(Button, {disabled: busy, onClick: () => edit('Evidence')}, '登记证据')),
            F.nextActions(view).length > 0 && h('div', {className: 'dm-next-action-list'}, h('h4', null, '下一步'), ...F.nextActions(view).map(item => h('div', {key: F.refKey(item.ref), className: 'dm-next-action'}, h('span', null, item.text), link(item.ref)))),
            h('p', {className: 'dm-local-hint'}, '登记的是研究记录，不是证明。程序检查不替代人工审核。')),
          groupPanels(3),
          h('details', {className: 'dm-details dm-project-details'}, h('summary', null, '项目与关联会话'),
            h('p', {className: 'dm-record-text'}, `campaign ${view.project.campaign_id}`),
            h('p', {className: 'dm-record-text'}, '开放性与数学结论不由软件自动判定；当前状态由版本化记录推导。'),
            navigateSession && h(Button, {onClick: () => leave(() => navigateSession(view.project.session_id))}, '进入关联普通会话'))),
        tab === 'records' && h(React.Fragment, null,
          h(Panel, {title: '研究记录', meta: '点击版本号查看详情'}, writable && h('div', {className: 'dm-actions'}, h(Button, {disabled: busy, onClick: () => edit('Note')}, '笔记'), h(Button, {disabled: busy, onClick: () => edit('Obligation')}, '义务'), h(Button, {disabled: busy, onClick: () => edit('Evidence')}, '证据'))),
          editor && writable && h(Panel, {title: '编辑研究记录'}, editor.promotion && h('p', {className: 'dm-record-text'}, `整理自草稿本 v${editor.promotion.scratchpad_rev}；请补全正式字段。保存后登记精确来源链接，原草稿保留。`), h(Editor, {key: editor.operation_id, initial: editor, objects: view.objects, busy, inspect, cancel: () => setEditor(null), save: saveRecord})),
          groupPanels(),
          h(Panel, {title: '证据与版本', meta: '并列保留'},
            ...view.objects.filter(o => o.kind === 'Evidence').map(o => h('div', {className: 'dm-record', key: `${o.id}@${o.rev}`}, link(ref(o)), h('span', {className: 'dm-record-text'}, ` ${o.data.source.version || '未知版本'} · ${o.data.source_status}`))),
            !view.objects.some(o => o.kind === 'Evidence') && h('p', {className: 'dm-empty'}, '暂无证据')),
          focus && h(Panel, {title: `${focus.id}@${focus.rev}`, meta: focus.kind},
            h('pre', {className: 'dm-code'}, pretty(focus)),
            h('p', {className: 'dm-record-text'}, '关联记录（双向）'),
            ...view.links.filter(l => [l.from, l.to].some(v => v.id === focus.id && v.rev === focus.rev)).map((l, i) => h('div', {className: 'dm-record', key: i}, link(l.from), ` ${l.relation} → `, link(l.to))),
            h('div', {className: 'dm-actions'},
              ['Note', 'Obligation', 'Evidence'].includes(focus.kind) && writable && h(Button, {disabled: busy, onClick: () => edit(focus.kind, focus)}, '修订此记录'),
              ['Note', 'Obligation'].includes(focus.kind) && writable && h(Button, {disabled: busy, onClick: () => run(async () => { if (!F.writable(view)) throw Error('只读项目'); setPreview(await api('review-preview', {project_id: selected, request: {target: ref(focus), candidate: candidate[0] || null}})); setVerdict(''); setFindings(''); })}, '人工审核预览'),
              h(Button, {variant: 'ghost', onClick: () => { setFocus(null); setAttachment(null); }}, '关闭详情')),
            focus.kind === 'Obligation' && writable && h(Refs, {label: '审核所选候选', objects: view.objects.filter(o => focus.data.candidates.some(r => F.refKey(r) === F.refKey(o))), kinds: ['Note'], problem: focus.data.problem_ref, value: candidate, single: true, change: v => { setCandidate(v); setPreview(null); }, inspect}),
            focus.kind === 'Evidence' && focus.data.attachments.map(a => h(Button, {key: a.sha256, onClick: () => run(async () => setAttachment(await api('attachment', {project_id: selected, object_id: focus.id, rev: focus.rev, sha256: a.sha256})))}, `查看原文 ${a.logical_name}`)),
            attachment && h('pre', {className: 'dm-code'}, attachment.text === null ? pretty(attachment) : attachment.text)),
          preview && writable && h(Panel, {title: '人工审核预览', meta: preview.eligible_for_pass ? '满足提交条件' : '仍需补充'},
            h('pre', {className: 'dm-code'}, pretty(preview)),
            h('label', {className: 'dm-field'}, '人工审核结论', h('select', {className: 'dm-input', value: verdict, onChange: e => setVerdict(e.target.value)}, h('option', {value: ''}, '由本人选择结论'), ...['pass', 'fail', 'inconclusive'].map(v => h('option', {key: v, value: v, disabled: v === 'pass' && !preview.eligible_for_pass}, v)))),
            h(Field, {label: '本人审核发现（每行一项）', value: findings, change: setFindings}),
            h(Button, {variant: 'primary', disabled: busy || !verdict || !findings.trim(), onClick: () => run(async () => { if (!F.writable(view)) throw Error('只读项目'); await api('review', {project_id: selected, request: {operation_id: crypto.randomUUID(), review_id: 'R-' + crypto.randomUUID(), target: preview.basis.target, candidate: preview.basis.candidate, verdict, scope: preview.required_scope, findings: lines(findings), basis_hash: preview.basis_hash}}); setPreview(null); await load(); })}, '本人提交审核记录'))),
        tab === 'replay' && h(Panel, {title: '联合只读回放与导出', meta: '零模型操作'},
          h('p', {className: 'dm-record-text'}, '仅合并明确选择的文件。未采集用量保留 unknown；不恢复推理、不解包归档。'),
          ...sources.map((s, i) => h('div', {className: 'dm-record', key: i},
            h(Select, {label: `来源 ${i + 1} 格式`, value: s.adapter, items: [['native-v4', '原生会话 v4'], ['audit-js-v1', '传输 / DSH 审计 v1'], ['ledger-py-v1', '工具账本 v1']], change: v => setSources(sources.map((x, j) => j === i ? {...x, adapter: v} : x))}),
            h(Field, {label: `来源 ${i + 1} 完整路径`, value: s.path, multiline: false, change: v => setSources(sources.map((x, j) => j === i ? {...x, path: v} : x))}),
            h(Button, {size: 'small', onClick: () => setSources(sources.filter((_, j) => j !== i))}, '移除来源'))),
          h('div', {className: 'dm-actions'},
            h(Button, {onClick: () => setSources([...sources, {adapter: 'native-v4', path: ''}])}, '添加回放来源'),
            h(Button, {disabled: busy, onClick: () => run(async () => setReplay(await api('replay', {project_id: selected, sources})))}, '回放所选来源'),
            h(Button, {variant: 'primary', disabled: busy, onClick: () => run(async () => setReceipt(await api('export', {project_id: selected, sources, expected_seq: view.snapshot.state.seq})))}, '生成本地审核包')),
          replay && h('pre', {className: 'dm-code'}, pretty(replay)), receipt && h('pre', {className: 'dm-code'}, pretty(receipt)))),
      h('section', {className: 'dm-import', 'aria-label': '导入旧研究库（只读副本）'},
        h('div', {className: 'dm-import-mark', 'aria-hidden': 'true'}, h(ImportIcon)),
        h('div', {className: 'dm-import-copy'},
          h('div', {className: 'dm-import-heading'}, h('h3', null, '延续已有研究'), h('span', {className: 'dm-badge'}, '只读副本')),
          h('p', null, '导入本地旧研究库，保留原始文件与记录。')),
        h('div', {className: 'dm-import-action'}, h(Button, {disabled: busy, title: '导入旧研究库（只读副本）', onClick: () => leave(() => setImporting(true))}, '导入旧研究库')),
        h('p', {className: 'dm-import-hint'}, '仅供查阅与回放，不会自动开始研究。')),
      importing && h(LegacyImport, {key: sessionId, request: workbenchRequest.current, sessionId, close: () => setImporting(false),
        onImported: project => leave(() => run(async () => { setImporting(false); await load(project.id); setTab('overview'); }))}));
  }
  function Entry({sessionId, navigateSession, openSidebar}) {
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
      opened && h('dialog', {ref: dialogRef, role: 'dialog', 'aria-modal': true, 'aria-label': '数学研究工作台', className: 'dm-dialog', onCancel: cancel}, h(MathView, {sessionId, navigateSession: navigate, onClose: () => setOpened(false)})));
  }
  return {name: 'dsh-math-client', inject: ['slots', 'uiConversation', 'uiWorkspace'], apply(ctx) {
    ctx.effect(() => {
      if (typeof document === 'undefined') return;
      const style = document.createElement('style');
      style.id = 'dsh-math-theme'; style.textContent = globalThis.DshMathTheme || '';
      document.head.appendChild(style);
      return () => style.remove(); // Remove only the node owned by this plugin generation.
    }, 'dsh-math: theme');
    ctx.uiConversation.views.register({target: 'math', create: () => ({empty: null, replace: () => null, apply: () => null})});
    const navigateSession = id => ctx.uiWorkspace.openSession(id);
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
          id: TAB_ID, title: '数学研究', description: '管理研究目标、笔记、证据与人工审核。', icon: size => h(MathIcon, {size}), single: true, order: 20,
          component: ({scope}) => h(MathView, {sessionId: scope.sessionId, navigateSession, sidebar: true}),
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
    const inject = () => ({navigateSession, openSidebar});
    ctx.slots.inject('conversation.view', () => ctx.slots.register({name: 'conversation.view', id: 'math', order: 20, label: () => '数学研究', inject}, MathView));
    for (const slot of ['conversation.hero.options', 'conversation.session.header.utilities']) {
      ctx.slots.inject(slot, () => ctx.slots.register({name: slot, id: 'math-projects', order: 20, inject}, Entry));
    }
  }};
}});
