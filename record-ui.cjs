/* Read-only, project-local record navigation. No API, attachment fetch, or model calls. */
(function (publish) {
  'use strict';
  const PAGE_SIZE = 60;
  const CONTEXT_WARNING_CHARS = 64000;
  const own = (o, k) => o != null && Object.prototype.hasOwnProperty.call(o, k);
  const list = v => Array.isArray(v) ? v : [];
  const text = v => typeof v === 'string' ? v : v == null ? '' : typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v);
  const pretty = v => JSON.stringify(v, null, 2) ?? 'null';
  const validRef = r => !!r && typeof r.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(r.id) && Number.isSafeInteger(r.rev) && r.rev > 0;
  const refKey = r => validRef(r) ? `${r.id}@${r.rev}` : '无有效精确版本';
  const objects = view => list(view?.objects).filter(o => validRef(o) && typeof o.kind === 'string' && o.data && typeof o.data === 'object');
  const exactObject = (view, r) => validRef(r) ? objects(view).find(o => o.id === r.id && o.rev === r.rev) || null : null;
  const kindLabels = {Problem: '研究问题', Note: '笔记', Obligation: '待证事项', Evidence: '参考材料', Review: '审核记录'};
  const stateLabels = {unreviewed: '未审核', open: '未关闭', candidate: '候选', accepted: '已登记接受', closed: '已登记关闭', needs_recheck: '需重新检查', rejected: '已登记拒绝', failed_attempt: '失败尝试'};
  const kindLabel = kind => own(kindLabels, kind) ? kindLabels[kind] : text(kind);
  const stateLabel = state => own(stateLabels, state) ? stateLabels[state] : text(state);
  const materialName = o => o?.kind === 'Evidence' && o.data?.production?.method === 'dsh-math-material-import' ? text(list(o.data.attachments)[0]?.logical_name) : '';
  function title(o) {
    if (o?.data?.title) return text(o.data.title);
    const statement = materialName(o) || text(o?.data?.statement);
    if (!statement) return kindLabel(o?.kind) || '未知记录';
    // A visual label only. Keep searchable statements, body, raw JSON and context byte-for-byte.
    const characters = Array.from(statement);
    return characters.length > 160 ? characters.slice(0, 159).join('') + '…' : statement;
  }
  const groupLabels = {targets: '研究目标', accepted: '已登记接受/关闭', candidates: '候选', obligations: '待证事项', failed_routes: '失败路线', notes: '笔记'};
  function references(object) {
    const d = object?.data || {}, out = [];
    const add = (relation, value) => { if (value != null) out.push({relation, ref: value, valid: validRef(value)}); };
    for (const k of ['parent', 'problem_ref', 'target', 'candidate']) if (own(d, k)) add(k, d[k]);
    for (const k of ['uses', 'candidate_for', 'candidates', 'evidence', 'dependencies']) for (const r of list(d[k])) add(k, r);
    for (const c of list(d.citation?.condition_map)) if (c?.obligation) add('condition_gap', c.obligation);
    // Review basis is persisted data, not an inferred relationship.
    for (const b of list(d.basis?.objects)) if (b?.ref) add('basis', b.ref);
    const seen = new Set();
    return out.filter(r => { const k = `${r.relation}:${pretty(r.ref)}`; if (seen.has(k)) return false; seen.add(k); return true; });
  }
  function recordRows(view) {
    const groups = new Map();
    for (const [group, rows] of Object.entries(view?.groups || {})) for (const row of list(rows)) {
      if (!validRef(row?.object)) continue;
      const key = refKey(row.object), previous = groups.get(key) || {groups: [], assessment: null};
      previous.groups.push(group);
      const a = row.assessment;
      if (a && validRef(a.ref) && refKey(a.ref) === key) previous.assessment = a;
      groups.set(key, previous);
    }
    const all = objects(view), latest = new Map();
    for (const o of all) latest.set(o.id, Math.max(o.rev, latest.get(o.id) || 0));
    return all.map(object => {
      const metadata = groups.get(refKey(object)) || {groups: [], assessment: null};
      return {object, ...metadata, latest: object.rev === latest.get(object.id), latestRev: latest.get(object.id), status: metadata.assessment?.state || 'unassessed'};
    });
  }
  function filterRecords(view, {query = '', kind = '', group = '', status = '', history = 'latest', limit = PAGE_SIZE} = {}) {
    const needle = String(query).trim().toLocaleLowerCase();
    const rows = recordRows(view).filter(row => {
      const o = row.object, d = o.data;
      return (history === 'all' || (history === 'historical' ? !row.latest : row.latest)) &&
        (!kind || o.kind === kind) && (!group || row.groups.includes(group)) && (!status || row.status === status) &&
        (!needle || [o.id, refKey(o), d.title, materialName(o), d.body, d.statement, d.summary, d.support_scope, ...list(d.findings)].some(v => text(v).toLocaleLowerCase().includes(needle)));
    });
    const count = Number.isSafeInteger(limit) && limit > 0 ? limit : PAGE_SIZE;
    return {rows: rows.slice(0, count), total: rows.length, hasMore: rows.length > count};
  }
  function backlinks(view, object) {
    if (!validRef(object)) return [];
    const key = refKey(object);
    return objects(view).flatMap(o => references(o).filter(r => r.valid && refKey(r.ref) === key).map(r => ({object: o, relation: r.relation})));
  }
  function currentProblem(view, selected) {
    if (selected?.kind === 'Problem') return exactObject(view, selected);
    if (validRef(selected?.data?.problem_ref)) {
      const p = exactObject(view, selected.data.problem_ref); return p?.kind === 'Problem' ? p : null;
    }
    if (selected?.kind === 'Review') {
      const target = exactObject(view, selected.data.target);
      if (target && target.kind !== 'Review') return currentProblem(view, target);
    }
    return null; // Multiple roots and titles are not authority to guess a target.
  }
  function memberObject(view, object) {
    const found = exactObject(view, object);
    return found && found.kind === object.kind && pretty(found.data) === pretty(object.data) ? found : null;
  }
  function initialContextRefs(view, selected) {
    const obj = memberObject(view, selected);
    if (!obj) return [];
    const problem = currentProblem(view, obj);
    return [...new Set([refKey(obj), ...(problem ? [refKey(problem)] : [])])];
  }
  const contextFields = {
    Problem: ['title', 'statement', 'quantifiers', 'domain', 'conventions', 'target_type', 'parent', 'relation', 'completion_criteria', 'knowledge_status'],
    Note: ['note_type', 'title', 'summary', 'body', 'statement', 'assumptions', 'gaps', 'problem_ref', 'route_id', 'uses', 'candidate_for', 'evidence', 'failure'],
    Obligation: ['statement', 'scope', 'completion_criteria', 'next_action', 'reason', 'check_method', 'checker_id', 'problem_ref', 'uses', 'candidates', 'evidence', 'state'],
    Evidence: ['evidence_type', 'problem_ref', 'source_status', 'support_scope', 'limits', 'production', 'citation'],
    Review: ['target', 'evidence', 'dependencies', 'method', 'verdict', 'error_code', 'scope', 'findings', 'reviewer', 'checker_version', 'candidate', 'basis', 'basis_hash'],
  };
  function contextRecord(object, includeLocators) {
    const d = object.data, data = {};
    for (const k of contextFields[object.kind] || []) if (own(d, k)) data[k] = d[k];
    if (object.kind === 'Evidence') {
      if (d.source) {
        data.source = {};
        for (const k of ['version', 'accessed_at', 'conditions']) if (own(d.source, k)) data.source[k] = d.source[k];
        if (includeLocators && own(d.source, 'locator')) data.source.locator = d.source.locator;
      }
      // Identifiers only: never include attachment paths, logical names, or bytes/text payloads.
      data.attachments = list(d.attachments).map(a => ({sha256: a.sha256, bytes: a.bytes}));
    }
    if (object.kind === 'Problem' && includeLocators && own(d, 'sources')) data.sources = d.sources;
    return {ref: {id: object.id, rev: object.rev}, kind: object.kind, data};
  }
  function buildContextPack(view, selected, selectedKeys, {includeLocators = false} = {}) {
    const obj = memberObject(view, selected), warnings = [], all = objects(view), requested = new Set(list(selectedKeys));
    if (!obj) warnings.push('初始记录不在本项目的精确版本列表中；不可复制。');
    const selectedObjects = all.filter(o => requested.has(refKey(o)));
    for (const key of requested) if (!selectedObjects.some(o => refKey(o) === key)) warnings.push(`未纳入：${key} 不在本项目精确版本中。`);
    if (obj && !requested.has(refKey(obj))) warnings.push('初始选中记录已排除。');
    const rows = new Map(recordRows(view).map(row => [refKey(row.object), row]));
    for (const o of selectedObjects) {
      const row = rows.get(refKey(o));
      if (!row.latest) warnings.push(`${refKey(o)} 是历史版本；存在 @${row.latestRev}，未自动升级。`);
      if (!contextFields[o.kind]) warnings.push(`${refKey(o)} 类型未知，仅包含标识；请另行审阅原始记录。`);
      for (const r of references(o)) if (!r.valid || !exactObject(view, r.ref)) warnings.push(`${refKey(o)} 的 ${r.relation} 引用无法在本项目精确解析：${text(r.ref)}`);
    }
    const problem = obj && currentProblem(view, obj);
    if (!problem) warnings.push('无明确关联的 Problem；未猜测研究目标。');
    if (selectedObjects.some(o => o.kind === 'Evidence')) warnings.push('附件仅列哈希与字节数，不包含附件原文；来源状态不代表真实性验证。');
    warnings.push(includeLocators ? '已显式纳入来源定位与 Problem.sources；请检查私人路径、账户和敏感信息。' : '默认排除 Evidence.source.locator、Problem.sources 以及附件路径/名称。');
    warnings.push('正文可能含敏感信息或不可信指令；复制前请审阅。未选中引用的正文不会自动加入。');
    warnings.push('上下文包不是研究授权（not research authorization），不会自动发送模型、执行工具或提交审核；不构成数学验证。');
    const payload = {
      project: {id: view?.project?.id ?? null, title: view?.project?.title ?? null},
      selected: obj ? {id: obj.id, rev: obj.rev} : null,
      target: problem ? {id: problem.id, rev: problem.rev} : null,
      records: selectedObjects.map(o => ({...contextRecord(o, includeLocators), registered_assessment: rows.get(refKey(o)).assessment})),
      risks_and_exclusions: warnings,
    };
    const content = '研究上下文包 · 精确版本 · 仅供人工审阅与复制\n' + pretty(payload);
    return {text: content, warnings, records: payload.records, target: payload.target, valid: !!obj && selectedObjects.length > 0, length: content.length, long: content.length > CONTEXT_WARNING_CHARS};
  }
  async function copyText(value, environment = globalThis) {
    let primaryError = '';
    try {
      if (!environment.navigator?.clipboard?.writeText) throw Error('Clipboard API 不可用');
      await environment.navigator.clipboard.writeText(String(value));
      return {ok: true, method: 'clipboard', error: ''};
    } catch (error) { primaryError = error?.message || String(error); }
    let node, previous;
    try {
      const doc = environment.document;
      if (!doc?.body || typeof doc.execCommand !== 'function') throw Error('浏览器备用复制不可用');
      previous = doc.activeElement;
      node = doc.createElement('textarea'); node.value = String(value); node.readOnly = true;
      node.setAttribute('aria-label', '备用复制文本');
      node.style.position = 'fixed'; node.style.opacity = '0';
      doc.body.appendChild(node); node.focus(); node.select();
      if (doc.execCommand('copy') !== true) throw Error('浏览器拒绝备用复制');
      return {ok: true, method: 'fallback', error: '', warning: primaryError};
    } catch (error) { return {ok: false, method: null, error: `${primaryError}；${error?.message || String(error)}`}; }
    finally { node?.remove(); previous?.focus?.(); }
  }
  function factory(React, F, Preview) {
    const h = React.createElement, {useState, useRef, useEffect} = React;
    const button = (label, onClick, props = {}) => h('button', {type: 'button', className: 'dm-button', onClick, ...props}, label);
    const raw = value => h('details', {className: 'dm-record-raw'}, h('summary', {}, '原始 JSON（完整）'), h('pre', {className: 'dm-code'}, pretty(value)));
    function Body({value}) {
      const source = text(value);
      if (!source) return h('p', {className: 'dm-muted'}, '未登记');
      if (source.length > 64000) return h('div', {}, h('p', {role: 'status'}, `长文本（${source.length} 字符），以下显示完整原文，未截断。`), h('pre', {className: 'dm-code'}, source));
      return typeof Preview === 'function' ? h(Preview, {text: source}) : h('pre', {className: 'dm-code'}, source);
    }
    function Field({label, value}) {
      return h('section', {className: 'dm-record-section'}, h('h4', {}, label), Array.isArray(value) ? (value.length ? h('ul', {}, ...value.map((v, i) => h('li', {key: i}, h(Body, {value: v})))) : h('p', {className: 'dm-muted'}, '无已登记项')) : h(Body, {value}));
    }
    function Copy({value, label = '复制', disabled = false}) {
      const [result, setResult] = useState(null), [busy, setBusy] = useState(false), epoch = useRef(0);
      useEffect(() => { epoch.current++; setResult(null); setBusy(false); return () => { epoch.current++; }; }, [value]);
      const copy = async () => { const turn = ++epoch.current; setBusy(true); setResult(null); const next = await copyText(value); if (turn === epoch.current) { setResult(next); setBusy(false); } };
      return h('div', {className: 'dm-copy'}, button(busy ? '正在复制…' : label, copy, {disabled: disabled || busy}),
        result && h('p', {role: result.ok ? 'status' : 'alert'}, result.ok ? (result.method === 'fallback' ? '已通过浏览器备用方式复制。' : '已复制。') : `复制失败：${result.error}。请手动选择下方完整文本复制。`),
        result && !result.ok && h('textarea', {className: 'dm-copy-fallback', readOnly: true, value, 'aria-label': '手动复制完整文本', onFocus: event => event.target.select()}));
    }
    function Ref({value, view, inspect, relation}) {
      const object = exactObject(view, value);
      return h('span', {className: 'dm-record-reference'}, relation && h('span', {className: 'dm-muted'}, `${({parent: '上级问题', problem_ref: '研究问题', target: '审核目标', candidate: '候选', uses: '依据', candidate_for: '待证事项', candidates: '候选笔记', evidence: '材料', dependencies: '依赖', condition_gap: '条件缺口', basis: '审核依据'})[relation] || '关联'} · `),
        object ? (typeof inspect === 'function' ? button(`${title(object)} · ${kindLabel(object.kind)} · 版本${object.rev}`, () => inspect(object), {className: 'dm-ref'}) : h('span', {}, `${title(object)} · ${kindLabel(object.kind)} · 版本${object.rev}`)) : h('span', {}, validRef(value) ? `版本${value.rev} · 本项目未找到该记录` : '无效引用'));
    }
    function Registered({row}) {
      return row?.assessment ? h('div', {className: 'dm-record-meta'}, h('p', {}, `审核状态：${stateLabel(row.assessment.state)}（仅此版本，非数学证明）`),
        list(row.assessment.reasons).length > 0 && h(Field, {label: '已登记原因', value: row.assessment.reasons})) : h('p', {className: 'dm-muted'}, '此版本暂无审核评估。');
    }
    function RecordReader({object, view, inspect, children, onClose, onPin, pinned = false, autoFocus = true, compact = false}) {
      const heading = useRef(null), key = refKey(object);
      useEffect(() => { if (autoFocus) { heading.current?.focus?.({preventScroll: true}); heading.current?.scrollIntoView?.({block: 'nearest'}); } }, [key, autoFocus]);
      if (!object) return h('p', {className: 'dm-muted'}, '选择一条记录阅读。');
      const d = object.data || {}, refs = references(object), incoming = backlinks(view, object), row = recordRows(view).find(r => refKey(r.object) === key);
      const hasContent = value => value != null && (typeof value === 'string' ? value.trim().length > 0 : Array.isArray(value) ? value.some(hasContent) : typeof value === 'object' ? Object.values(value).some(hasContent) : true);
      const field = (label, value) => hasContent(value) ? h(Field, {key: label, label, value}) : null;
      let fields;
      switch (object.kind) {
        case 'Problem': fields = [field('问题陈述', d.statement), field('量词', d.quantifiers), field('对象与范围', d.domain), field('约定', d.conventions), field('完成条件', d.completion_criteria), field('目标类型（登记值）', d.target_type), field('知识状态（登记值）', d.knowledge_status), field('与父问题的关系', d.relation), field('来源（未核实为真）', d.sources)]; break;
        case 'Note': fields = [field('简述', d.summary), field('正文', d.body), field('主张', d.statement), field('假设', d.assumptions), field('缺口', d.gaps), field('笔记类型', d.note_type), ...(d.route_id ? [field('路线', d.route_id)] : []), ...(d.failure ? Object.entries({inputs: '失败输入', location: '失败位置', observed: '观察结果', negated_scope: '否定范围', retry_change: '再次尝试应改变什么'}).map(([k, label]) => field(label, d.failure[k])) : [])]; break;
        case 'Obligation': fields = [field('待证陈述', d.statement), field('适用范围', d.scope), field('完成条件', d.completion_criteria), field('下一步', d.next_action), field('登记原因', d.reason), field('记录中的状态（非验证结论）', stateLabel(d.state)), field('检查方式（登记值）', d.check_method), ...(d.checker_id ? [field('固定 checker', d.checker_id)] : [])]; break;
        case 'Evidence': fields = [field('材料类型', d.evidence_type), field('支持范围', d.support_scope), field('局限与未核实项', d.limits), field('来源状态（非真实性验证）', d.source_status), field('来源定位', d.source?.locator), field('来源版本', d.source?.version), field('访问时间', d.source?.accessed_at), field('来源条件', d.source?.conditions), field('生产或导入方式', d.production?.method), field('生产方式版本', d.production?.version), ...(d.citation ? [field('引文标题', d.citation.title), field('页码', d.citation.page), field('章节', d.citation.section), field('定理', d.citation.theorem), field('摘录 SHA256', d.citation.excerpt_sha256), field('来源条件映射（登记值）', d.citation.condition_map)] : []), field('附件元数据（原文未自动加载）', d.attachments)]; break;
        case 'Review': fields = [field('审核方式', d.method), field('登记结论（仅指定范围）', d.verdict), field('审核范围', d.scope), field('审核发现', d.findings), field('审核者', d.reviewer), field('checker 版本', d.checker_version), field('错误码', d.error_code), field('依据哈希', d.basis_hash), hasContent(d.basis) && h(ReviewBasis, {key: 'basis', basis: d.basis}), ...(d.execution ? [field('执行记录（非全局证明）', d.execution)] : [])]; break;
        default: fields = [h('p', {key: 'unknown', className: 'dm-muted'}, '未知记录类型，未猜测字段含义；完整内容保留在原始 JSON。')];
      }
      const technicalLabels = new Set(['目标类型（登记值）', '知识状态（登记值）', '笔记类型', '路线', '检查方式（登记值）', '固定 checker', '材料类型', '来源定位', '来源版本', '访问时间', '生产或导入方式', '生产方式版本', '页码', '章节', '定理', '摘录 SHA256', '附件元数据（原文未自动加载）', '审核者', 'checker 版本', '依据哈希', '执行记录（非全局证明）']);
      technicalLabels.add('来源条件映射（登记值）');
      const technical = value => value.type === ReviewBasis || technicalLabels.has(value.props?.label);
      const content = fields.filter(value => value && !technical(value));
      const metadata = fields.filter(value => value && technical(value));
      return h('article', {className: 'dm-record-reader'}, typeof onClose === 'function' && h('div', {className: 'dm-reader-actions'}, button('返回记录列表', onClose)),
        !compact && h('h3', {className: 'dm-record-heading', ref: heading, tabIndex: -1}, title(object)), !compact && h('p', {className: 'dm-record-meta'}, `${kindLabel(object.kind)} · 版本${object.rev}`, pinned && ' · 已固定'),
        !compact && h('details', {className: 'dm-details'}, h('summary', {}, '技术操作'), h('code', {}, key), h(Copy, {value: key, label: '复制 ID@rev', disabled: !validRef(object)}), typeof onPin === 'function' && button(pinned ? '取消固定此版本' : '固定此精确版本', () => onPin(object), {'aria-pressed': pinned})),
        row && !row.latest && h('p', {role: 'status'}, `正在阅读历史版本${object.rev}；最新为版本${row.latestRev}，未自动替换。`), object.kind === 'Note' ? h('details', {className: 'dm-details'}, h('summary', {}, '登记与审核状态'), h(Registered, {row})) : h(Registered, {row}), ...content,
        refs.length > 0 && h(object.kind === 'Note' ? 'details' : 'section', {className: 'dm-record-section dm-record-refs'}, h(object.kind === 'Note' ? 'summary' : 'h4', {}, '引用与参考材料（精确版本）'), h('ul', {}, ...refs.map((r, i) => h('li', {key: i}, h(Ref, {value: r.ref, relation: r.relation, view, inspect}))))),
        incoming.length > 0 && h('details', {className: 'dm-details dm-record-refs'}, h('summary', {}, `被引用位置 · ${incoming.length}（含历史记录）`), h('ul', {}, ...incoming.map((r, i) => h('li', {key: i}, h(Ref, {value: r.object, relation: r.relation, view, inspect}))))),
        metadata.length > 0 && h('details', {className: 'dm-details'}, h('summary', {}, '技术与来源信息'), ...metadata),
        children, !compact && raw(object));
    }
    function RecordBrowser({view, inspect}) {
      const [query, setQuery] = useState(''), [kind, setKind] = useState(''), [group, setGroup] = useState(''), [status, setStatus] = useState(''), [history, setHistory] = useState('latest'), [limit, setLimit] = useState(PAGE_SIZE), [selected, setSelected] = useState(null);
      const project = view?.project?.id;
      useEffect(() => { setQuery(''); setKind(''); setGroup(''); setStatus(''); setHistory('latest'); setLimit(PAGE_SIZE); setSelected(null); }, [project]);
      const change = setter => event => { setter(event.target.value); setLimit(PAGE_SIZE); };
      const result = filterRecords(view, {query, kind, group, status, history, limit}), allRows = recordRows(view);
      const select = (label, value, setter, options) => h('label', {}, label, h('select', {value, onChange: change(setter)}, ...options.map(([v, caption]) => h('option', {key: v, value: v}, caption))));
      const open = object => { setSelected({id: object.id, rev: object.rev}); if (typeof inspect === 'function') inspect(object); };
      const selectedObject = exactObject(view, selected);
      const extraFilters = Number(!!group) + Number(!!status) + Number(history !== 'latest');
      const resetExtraFilters = () => { setGroup(''); setStatus(''); setHistory('latest'); setLimit(PAGE_SIZE); };
      return h('section', {className: 'dm-record-browser', 'aria-label': '项目内记录检索'},
        h('div', {className: 'dm-record-filters'}, h('label', {}, '搜索记录', h('input', {type: 'search', value: query, onChange: change(setQuery), placeholder: '标题或正文'})),
          select('类型', kind, setKind, [['', '全部类型'], ...[...new Set(allRows.map(r => r.object.kind))].map(k => [k, kindLabel(k)])])),
        h('details', {className: 'dm-details dm-record-advanced'}, h('summary', {}, `更多筛选${extraFilters ? ` · ${extraFilters} 项生效` : ''}`),
          h('div', {className: 'dm-record-filters'},
            select('登记分组', group, setGroup, [['', '全部分组'], ...Object.keys(view?.groups || {}).map(k => [k, groupLabels[k] || k])]),
            select('登记审核状态', status, setStatus, [['', '全部状态'], ...[...new Set(allRows.map(r => r.status))].map(k => [k, k === 'unassessed' ? '无此版本评估' : stateLabel(k)])]),
            select('版本', history, setHistory, [['latest', '每条记录的最新版本'], ['all', '全部版本'], ['historical', '仅历史版本']])),
          button('重置更多筛选', resetExtraFilters, {disabled: !extraFilters})),
        selected && h('div', {className: 'dm-record-selected'}, h('p', {}, `保留选中：${selectedObject ? title(selectedObject) : '记录'} · 版本${selected.rev}`), selectedObject ? button('重新打开选中版本', () => open(selectedObject)) : h('p', {role: 'status'}, '本次快照无此精确版本；未替换为新版。')),
        h('p', {role: 'status'}, `匹配 ${result.total} 条${result.hasMore ? ` · 已显示 ${result.rows.length} 条` : ''}`),
        h('ul', {className: 'dm-record-results'}, ...result.rows.map(row => h('li', {key: refKey(row.object), className: 'dm-record-item'}, button(title(row.object), () => open(row.object), {'aria-current': selected && refKey(selected) === refKey(row.object) ? 'true' : undefined}), h('p', {className: 'dm-record-meta'}, `${kindLabel(row.object.kind)} · 版本${row.object.rev} · ${row.latest ? '最新' : '历史'} · ${row.assessment ? stateLabel(row.assessment.state) : '暂无审核评估'}`)))),
        !result.total && h('p', {className: 'dm-muted'}, '没有匹配记录，请调整搜索或筛选。'), result.hasMore && button('再显示 60 条', () => setLimit(n => n + PAGE_SIZE)));
    }
    function ReviewBasis({basis}) {
      if (!basis || typeof basis !== 'object') return h(Field, {label: '精确审核依据', value: basis});
      return h('section', {className: 'dm-record-section'}, h('h4', {}, '精确审核依据'),
        ...['target', 'candidate'].filter(k => own(basis, k)).map(k => h('p', {key: k}, `${k === 'target' ? '审核目标' : '所选候选'}：`, basis[k] == null ? '未选择' : refKey(basis[k]))),
        ...[['objects', '依据对象与哈希'], ['dependencies', '依赖版本'], ['evidence', '材料版本'], ['decisions', '所依赖审核记录']].filter(([k]) => own(basis, k)).map(([k, label]) => h('section', {key: k}, h('h5', {}, label),
          list(basis[k]).length ? h('ul', {}, ...basis[k].map((entry, i) => h('li', {key: i}, k === 'objects' ? `${refKey(entry.ref)} · SHA256 ${text(entry.sha256) || '未返回'}` : k === 'decisions' ? `${refKey(entry.target)} · Review ID ${text(entry.review_id) || '无'} · withdrawn=${own(entry, 'withdrawn') ? text(entry.withdrawn) : '未返回'}` : refKey(entry)))) : h('p', {className: 'dm-muted'}, '返回列表为空'))));
    }
    function ReviewSummary({preview}) {
      if (!preview) return null;
      const labels = {required_scope: '要求的审核范围', eligible_for_pass: '技术提交资格（非证明正确性）', eligible: '返回的资格字段', blockers: '阻碍原因', reasons: '返回原因', basis_hash: '依据哈希', notice: '服务提示'};
      return h('section', {className: 'dm-review-summary'}, h('h3', {}, '人工审核预览'), h('p', {}, '以下仅解释返回的审核条件与依据，不是数学验证，也不替人选择结论。'),
        ...Object.entries(labels).filter(([key]) => key !== 'basis_hash' && own(preview, key)).map(([key, label]) => h(Field, {key, label, value: typeof preview[key] === 'boolean' ? (preview[key] ? 'true（返回值）' : 'false（返回值）') : preview[key]})), (own(preview, 'basis') || own(preview, 'basis_hash')) && h('details', {className: 'dm-details'}, h('summary', {}, '技术审核依据'), own(preview, 'basis_hash') && h(Field, {label: labels.basis_hash, value: preview.basis_hash}), own(preview, 'basis') && h(ReviewBasis, {basis: preview.basis})), raw(preview));
    }
    function ExportReceipt({receipt}) {
      if (!receipt) return null;
      return h('section', {className: 'dm-export-receipt'}, h('h3', {}, '导出回执'), h(Field, {label: '返回的本地目录', value: receipt.directory}),
        typeof receipt.directory === 'string' && receipt.directory && h(Copy, {value: receipt.directory, label: '复制导出路径'}),
        h(Field, {label: '捕获的记录序号 captured_seq', value: receipt.captured_seq == null ? '未知' : receipt.captured_seq}),
        h(Field, {label: '本次操作模型请求数', value: receipt.operation_model_requests == null ? '未知' : receipt.operation_model_requests}),
        h(Field, {label: '会话模型请求数', value: receipt.session_model_requests == null ? '未知（未提供会话统计，不能视为 0）' : receipt.session_model_requests}),
        ...['status', 'error', 'format'].filter(k => own(receipt, k)).map(k => h(Field, {key: k, label: `返回字段 ${k}`, value: receipt[k]})),
        h('p', {}, '此回执不是独立复验结果，也不是数学证明。审核包搬移后仍需在目标目录运行 python3 -B verify_bundle.py .，保留真实输出；未在此执行或声称复验通过。'), raw(receipt));
    }
    function ContextPack({view, selected, onClose}) {
      // selected is an initial full object. Keep that exact revision; parent should key by project/selection when opening a new pack.
      const [initial] = useState(() => selected), [origin] = useState(() => view?.project?.id), [keys, setKeys] = useState(() => initialContextRefs(view, selected));
      const [includeLocators, setIncludeLocators] = useState(false), [query, setQuery] = useState(''), [limit, setLimit] = useState(PAGE_SIZE), [longConfirmed, setLongConfirmed] = useState(false);
      const heading = useRef(null);
      useEffect(() => { heading.current?.focus?.({preventScroll: true}); heading.current?.scrollIntoView?.({block: 'nearest'}); }, []);
      const sameProject = origin === view?.project?.id, scopedView = sameProject ? view : {objects: []};
      const pack = buildContextPack(scopedView, initial, keys, {includeLocators});
      useEffect(() => { setLongConfirmed(false); }, [pack.text]);
      const choices = filterRecords(scopedView, {query, history: 'all', limit});
      const toggle = key => setKeys(previous => previous.includes(key) ? previous.filter(v => v !== key) : [...previous, key]);
      return h('section', {className: 'dm-context-pack'}, h('div', {className: 'dm-reader-actions'}, h('h3', {className: 'dm-record-heading', ref: heading, tabIndex: -1}, '研究上下文包 · 仅预览与复制'), typeof onClose === 'function' && button('关闭上下文包', onClose)),
        h('p', {}, '不会自动发送到模型。复制不是研究授权（not research authorization）；请先审阅正文、精确引用与风险。'),
        !sameProject && h('p', {role: 'alert'}, '项目已切换，此上下文包不可复制；请关闭后从当前项目重新打开。'),
        h('p', {}, `初始记录：${title(initial)} · 版本${initial?.rev}。默认包含此记录与关联研究问题；其他正文须逐条选择。`),
        h('div', {className: 'dm-context-options'}, h('label', {}, '搜索可选精确版本', h('input', {type: 'search', value: query, onChange: e => { setQuery(e.target.value); setLimit(PAGE_SIZE); }})),
          h('ul', {}, ...choices.rows.map(({object}) => h('li', {key: refKey(object)}, h('label', {}, h('input', {type: 'checkbox', checked: keys.includes(refKey(object)), onChange: () => toggle(refKey(object))}), `${title(object)} · ${kindLabel(object.kind)} · 版本${object.rev}`)))),
          choices.hasMore && button('再显示 60 条可选记录', () => setLimit(n => n + PAGE_SIZE)),
          h('label', {}, h('input', {type: 'checkbox', checked: includeLocators, onChange: e => setIncludeLocators(e.target.checked)}), '明确纳入来源定位与 Problem.sources（可能包含敏感本地路径）')),
        h('p', {}, `已选 ${pack.records.length} 条记录 · 完整内容 ${pack.length} 字符。`),
        h('ul', {className: 'dm-context-risks'}, ...pack.warnings.map((warning, i) => h('li', {key: i}, warning))),
        pack.long && h('label', {}, h('input', {type: 'checkbox', checked: longConfirmed, onChange: e => setLongConfirmed(e.target.checked)}), `内容较长（${pack.length} 字符）；我已审阅并确认复制完整内容，目标输入框可能有长度限制。`),
        h('details', {className: 'dm-details'}, h('summary', {}, '完整上下文与技术引用'), h('pre', {className: 'dm-code dm-context-preview', 'aria-label': '完整上下文包预览'}, pack.text)),
        h(Copy, {value: pack.text, label: '明确复制完整上下文包', disabled: !sameProject || !pack.valid || (pack.long && !longConfirmed)}));
    }
    return {RecordReader, RecordBrowser, ReviewSummary, ExportReceipt, ContextPack};
  }
  Object.assign(factory, {PAGE_SIZE, CONTEXT_WARNING_CHARS, title, kindLabel, stateLabel, validRef, refKey, exactObject, references, recordRows, filterRecords, backlinks, currentProblem, initialContextRefs, contextRecord, buildContextPack, copyText});
  publish(factory);
})(factory => { if (typeof module !== 'undefined' && module.exports) module.exports = factory; else globalThis.DshMathRecordUI = factory; });
