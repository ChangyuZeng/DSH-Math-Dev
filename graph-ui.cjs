/* A native, read-only viewer for the audited projection. No log or model APIs. */
globalThis.DshMathGraphUI = function (React, Graph) {
  const {createElement: h, useState, useEffect, useRef} = React;
  const labels = {project: '研究项目', source: '记录来源', event: '已记录动作', record: '版本化产物'};
  const button = (text, onClick, props = {}) => h('button', {type: 'button', className: 'dm-button dm-small dm-ghost', onClick, ...props}, text);
  return function ProcessGraph({graph, inspect}) {
    const [selected, setSelected] = useState(null), [collapsed, setCollapsed] = useState({}), [limits, setLimits] = useState({});
    const [download, setDownload] = useState(''), [message, setMessage] = useState('');
    const [mode, setMode] = useState('summary'), [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState(''), [kindFilter, setKindFilter] = useState(''), [listLimits, setListLimits] = useState({});
    const detailHeading = useRef(null), originButton = useRef(null);
    const byId = new Map(graph.nodes.map(node => [node.id, node]));
    const children = new Map();
    for (const node of graph.nodes) {
      const list = children.get(node.parentId) || [];
      list.push(node); children.set(node.parentId, list);
    }
    useEffect(() => {
      if (selected) {
        detailHeading.current?.focus();
        detailHeading.current?.scrollIntoView({block: 'nearest'});
      }
    }, [selected]);
    useEffect(() => {
      // Owned by this graph snapshot. Replaced/unmounted views revoke their URL.
      // The same React key can receive a new snapshot; never retain a revoked URL
      // or a previous snapshot's download failure/success message.
      setDownload(''); setMessage(''); setListLimits({});
      let url;
      try {
        url = URL.createObjectURL(new Blob([Graph.serialize(graph)], {type: 'application/json;charset=utf-8'}));
        setDownload(url);
      } catch (_) { setMessage('当前客户端不能创建下载；可展开下方 JSON 快照手动保存。'); }
      return () => { if (url) URL.revokeObjectURL(url); };
    }, [graph]);
    const choose = (node, element) => { if (element) originButton.current = element; setSelected(node.id); };
    const close = () => { setSelected(null); originButton.current?.focus(); };
    const focused = byId.get(selected);
    const related = focused ? graph.edges.filter(edge => edge.relation !== 'contains' && edge.relation !== 'produces' && (edge.from === selected || edge.to === selected)) : [];
    const products = focused?.kind === 'event' ? (children.get(focused.id) || []).filter(node => node.kind === 'record' && node.ref) : [];
    function nodeCard(node) {
      return h('button', {type: 'button', className: 'dm-graph-node', 'data-selected': selected === node.id, 'data-status': node.status,
        'aria-pressed': selected === node.id, onClick: event => choose(node, event.currentTarget)},
        h('span', {className: 'dm-graph-kicker'}, labels[node.kind], node.subtitle && h('span', null, node.subtitle)),
        h('strong', {className: 'dm-graph-label'}, node.title),
        node.summary && h('span', {className: 'dm-graph-summary'}, node.summary),
        h('span', {className: 'dm-graph-status'}, h('span', {className: 'dm-graph-dot', 'aria-hidden': true}), node.statusLabel));
    }
    function entryList() {
      // Use only the audited projection. Source order is the graph's own order;
      // seq is local to each source, never a clock or a cross-source timeline.
      const groups = graph.nodes.filter(node => node.kind === 'source').map(source => ({source, rows: [source]}));
      const groupById = new Map(groups.map(group => [group.source.id, group]));
      for (const node of graph.nodes) {
        if (node.kind !== 'event' && node.kind !== 'record') continue;
        let parent = byId.get(node.parentId);
        const seen = new Set([node.id]);
        while (parent && parent.kind !== 'source' && !seen.has(parent.id)) {
          seen.add(parent.id); parent = byId.get(parent.parentId);
        }
        groupById.get(parent?.id)?.rows.push(node);
      }
      const query = search.trim().toLowerCase();
      const matches = node => {
        if ((statusFilter && node.status !== statusFilter) || (kindFilter && node.kind !== kindFilter)) return false;
        // Search only fields already shown by this viewer, not arbitrary payloads.
        const values = [labels[node.kind], node.kind, node.title, node.subtitle, node.summary, node.status, node.statusLabel,
          node.ref && `${node.ref.id}@${node.ref.rev}`, node.source?.path, node.source?.seq,
          ...node.facts.flatMap(fact => [fact.label, fact.value])];
        return !query || values.some(value => value != null && String(value).toLowerCase().includes(query));
      };
      const seq = node => Number.isSafeInteger(node.source?.seq) ? node.source.seq : Infinity;
      let total = 0, matched = 0, shown = 0;
      const sections = groups.map(({source, rows}) => {
        total += rows.length;
        // Sort a new array, not graph.nodes or its children. Stable ties retain
        // the projection's product order; missing seq stays explicitly unknown.
        const filtered = rows.filter(matches).sort((a, b) => a === b ? 0 : a === source ? -1 : b === source ? 1 : seq(a) === seq(b) ? 0 : seq(a) - seq(b));
        matched += filtered.length;
        if (!filtered.length) return null;
        const limit = listLimits[source.id] || 40;
        const visible = filtered.slice(0, limit); shown += visible.length;
        return h('section', {className: 'dm-graph-source-group', key: source.id, 'aria-label': source.title},
          h('div', {className: 'dm-graph-source-header'}, h('h4', {className: 'dm-panel-title'}, source.title),
            h('p', {className: 'dm-local-hint'}, source.source?.path || source.subtitle),
            h('span', {className: 'dm-graph-list-count'}, `${visible.length} / ${filtered.length} 项`)),
          h('ul', {className: 'dm-graph-rows', 'aria-label': `${source.title} 的逐条记录`}, ...visible.map(node =>
            h('li', {className: 'dm-graph-row', key: node.id, 'data-kind': node.kind, 'data-status': node.status},
              node.kind !== 'source' && h('span', {className: 'dm-graph-row-meta'}, Number.isFinite(seq(node)) ? `来源内 seq ${seq(node)}` : '来源内 seq 未知'),
              nodeCard(node)))),
          filtered.length > limit && button(`显示更多（${limit} / ${filtered.length}）`, () => setListLimits(previous => ({...previous, [source.id]: limit + 40})),
            {className: 'dm-button dm-small dm-ghost dm-graph-list-more', 'aria-label': `显示更多 ${source.title} 的逐条记录`}));
      });
      const change = setter => event => { setter(event.currentTarget.value); setListLimits({}); };
      return h('div', {className: 'dm-graph-list'},
        h('div', {className: 'dm-graph-filters'},
          h('label', {className: 'dm-graph-search'}, '搜索已收录记录', h('input', {type: 'search', value: search, 'aria-label': '搜索已收录记录', placeholder: '标题、摘要、精确引用或来源', onChange: change(setSearch)})),
          h('label', {className: 'dm-graph-filter'}, '记录状态', h('select', {value: statusFilter, 'aria-label': '记录状态', onChange: change(setStatusFilter)},
            h('option', {value: ''}, '全部状态'), h('option', {value: 'recorded'}, '已记录'), h('option', {value: 'error'}, '错误已记录'), h('option', {value: 'unknown'}, '未知（含未确认结束）'))),
          h('label', {className: 'dm-graph-filter'}, '节点类型', h('select', {value: kindFilter, 'aria-label': '节点类型', onChange: change(setKindFilter)},
            h('option', {value: ''}, '全部类型'), ...['event', 'record', 'source'].map(kind => h('option', {key: kind, value: kind}, labels[kind])))),
          button('清除筛选', () => { setSearch(''); setStatusFilter(''); setKindFilter(''); setListLimits({}); }, {disabled: !search && !statusFilter && !kindFilter})),
        h('p', {className: 'dm-local-hint dm-graph-list-count', 'aria-live': 'polite'}, `${matched} / ${total} 项${shown < matched ? ` · 已显示 ${shown} 项` : ''}`),
        matched ? sections : h('p', {className: 'dm-graph-empty'}, '没有匹配记录，请调整筛选。'));
    }
    function branch(node) {
      const descendants = children.get(node.id) || [];
      const folded = collapsed[node.id] ?? (node.kind === 'event' && descendants.length > 0);
      const limit = limits[node.id] || 40;
      return h('li', {className: `dm-graph-branch dm-graph-${node.kind}`, key: node.id},
        h('div', {className: 'dm-graph-node-wrap'},
          nodeCard(node),
          descendants.length > 0 && button(`${folded ? '展开' : '收起'} ${descendants.length} 项`, () => setCollapsed(previous => ({...previous, [node.id]: !folded})),
            {'aria-expanded': !folded, 'aria-label': `${folded ? '展开' : '收起'} ${node.title} 的子节点`, className: 'dm-button dm-small dm-ghost dm-graph-toggle'})),
        !folded && descendants.length > 0 && h('ul', {className: node.kind === 'project' ? 'dm-graph-sources' : 'dm-graph-children', 'aria-label': `${node.title} 的子节点`},
          ...descendants.slice(0, limit).map(branch),
          descendants.length > limit && h('li', {className: 'dm-graph-more'}, button(`显示更多（${limit} / ${descendants.length}）`, () => setLimits(previous => ({...previous, [node.id]: limit + 40}))))));
    }
    const routeRecord = row => h('li', {key: `${row.ref.id}@${row.ref.rev}`, className: 'dm-route-record'},
      button(row.title, () => inspect(row.ref), {className: 'dm-button dm-small'}),
      h('span', {className: 'dm-panel-meta'}, `${row.ref.id}@${row.ref.rev}`),
      row.summary && h('p', {className: 'dm-record-text'}, row.summary),
      row.gaps?.length > 0 && h('section', {className: 'dm-route-gaps'}, h('h5', null, '待解决问题'), h('ul', null, ...row.gaps.map((gap, index) => h('li', {key: index}, gap)))),
      row.next_action && h('p', {className: 'dm-record-text'}, `下一步：${row.next_action}`),
      row.failure && h('details', {className: 'dm-details'}, h('summary', null, '已登记失败范围'),
        ...Object.entries(row.failure).map(([key, value]) => h('p', {key, className: 'dm-record-text'}, `${{inputs: '输入', location: '位置', observed: '观察', negated_scope: '否定范围', retry_change: '重试需改变'}[key] || key}：${value}`))));
    const recordList = (title, rows) => h('section', {className: 'dm-route-section'}, h('h5', null, `${title} · ${rows.length}`),
      rows.length ? h('ul', {className: 'dm-route-records'}, ...rows.map(routeRecord)) : h('p', {className: 'dm-empty'}, '暂无已登记记录'));
    const routeView = Array.isArray(graph.routes) && h('section', {className: 'dm-route-process', 'aria-label': '按研究目标和路线分组'},
      h('h3', {className: 'dm-panel-title'}, '研究过程'),
      h('p', {className: 'dm-local-hint'}, '按笔记明确关联的问题与路线标识分组，仅显示各记录最新版本。分组不代表推理因果或审核通过；待证事项仅由精确引用关联。'),
      ...graph.routes.map(group => h('section', {className: 'dm-route-problem', key: `${group.problem.id}@${group.problem.rev}`},
        h('h4', null, button(group.title || '研究目标', () => inspect(group.problem))),
        ...group.routes.map((route, index) => h('section', {className: 'dm-route', key: index},
          h('h4', null, route.id ? `路线 ${route.id}` : '未分配路线'),
          recordList('推导与观察笔记', route.notes.filter(note => note.note_type !== 'failed_attempt')),
          recordList('失败尝试', route.notes.filter(note => note.note_type === 'failed_attempt')),
          recordList('相关待证事项', route.obligations))),
        !group.routes.length && h('p', {className: 'dm-empty'}, '尚无路线笔记，可先写笔记并明确路线标识。'),
        group.obligations.length > 0 && recordList('未关联路线的待证事项', group.obligations))),
      graph.unresolved_notes?.length > 0 && recordList('目标精确引用无法解析的笔记', graph.unresolved_notes));
    return h('section', {className: 'dm-process-graph', 'aria-label': '已记录研究过程图'}, routeView,
      h('details', {className: 'dm-details dm-operation-history', ...(routeView ? {} : {open: true})}, h('summary', null, '操作历史与来源'),
      h('div', {className: 'dm-graph-caption'},
        h('div', null, h('h3', {className: 'dm-panel-title'}, '研究过程'),
          h('p', {className: 'dm-record-text'}, `${graph.counts.events} 个动作 · ${graph.counts.records} 个版本产物`))),
      h('p', {className: 'dm-local-hint dm-graph-notice'}, '点击节点查看详情；连线表示归属，不代表推理因果。'),
      h('div', {className: 'dm-graph-mode-switch', role: 'group', 'aria-label': '过程显示方式'},
        ...[['summary', '概要'], ['entries', '逐条']].map(([value, label]) => button(label, () => setMode(value),
          {key: value, className: 'dm-button dm-small dm-graph-mode-button', 'aria-pressed': mode === value, 'data-active': mode === value}))),
      graph.nodes.some(node => node.status === 'error' || node.status === 'unknown') && h('p', {className: 'dm-operation-status', role: 'status'},
        `错误 ${graph.nodes.filter(node => node.status === 'error').length} 项 · 未知 ${graph.nodes.filter(node => node.status === 'unknown').length} 项；未知不表示正在运行。`),
      focused && h('section', {className: 'dm-graph-detail', 'aria-label': '过程节点详情', onKeyDown: event => { if (event.key === 'Escape') { event.stopPropagation(); close(); } }},
        h('div', {className: 'dm-panel-header'}, h('h3', {className: 'dm-panel-title', tabIndex: -1, ref: detailHeading}, focused.title), button('关闭节点详情', close)),
        h('p', {className: 'dm-record-text'}, `${labels[focused.kind]} · ${focused.statusLabel}`),
        focused.summary && h('p', {className: 'dm-record-text'}, focused.summary),
        h('dl', {className: 'dm-graph-facts'}, ...focused.facts.map((fact, index) => h('div', {key: index}, h('dt', null, fact.label), h('dd', null, fact.value)))),
        focused.source && h('details', {className: 'dm-details'}, h('summary', null, '原始记录定位与哈希'), h('pre', {className: 'dm-code'}, JSON.stringify(focused.source, null, 2))),
        focused.ref && button(`打开记录 ${focused.ref.id}@${focused.ref.rev}`, () => inspect(focused.ref), {className: 'dm-button dm-small'}),
        products.length > 0 && h('div', {className: 'dm-graph-products'}, h('p', {className: 'dm-record-text'}, '本动作的精确产物（仅实际归属）'),
          ...products.map(node => h('div', {key: node.id}, h('span', null, node.title), button(`打开记录 ${node.ref.id}@${node.ref.rev}`, () => inspect(node.ref))))),
        related.length > 0 && h('div', {className: 'dm-graph-relations'}, h('p', {className: 'dm-record-text'}, '显式引用（可能有交叉或环，不强行树化）'),
          ...related.map((edge, index) => {
            const next = byId.get(edge.from === selected ? edge.to : edge.from);
            return h('div', {key: index}, h('span', {className: 'dm-panel-meta'}, `${edge.from === selected ? '→' : '←'} ${edge.label || edge.relation} `),
              next ? button(next.title, () => choose(next)) : h('span', null, '未收录的精确引用'));
          }))),
      mode === 'summary' ? h('div', {className: 'dm-graph-board'}, h('ul', {className: 'dm-graph-roots', 'aria-label': '研究过程节点'}, ...(children.get(null) || []).map(branch))) : entryList(),
      h('details', {className: 'dm-details dm-graph-warnings'}, h('summary', null, `覆盖范围${graph.warnings.length ? ` · ${graph.warnings.length} 条说明` : ''}`),
        h('p', {className: 'dm-local-hint'}, graph.notice),
        h('p', {className: 'dm-local-hint'}, '逐条视图按来源分组、来源内 seq 排列；缺失 seq 标为未知，没有跨来源统一时间或推断因果。'),
        graph.warnings.length > 0 && h('ul', null, ...graph.warnings.map((warning, index) => h('li', {key: index}, warning)))),
      h('details', {className: 'dm-details'}, h('summary', null, 'JSON 快照与下载'),
        h('p', {className: 'dm-local-hint'}, `研究 seq ${graph.research_seq} · 筛选不改变完整快照。快照不是审核包或数学证明；包含摘要与来源路径，分享前请检查。`),
        download && h('a', {className: 'dm-button dm-small', href: download, download: `dsh-math-process-${graph.project_id}-seq-${graph.research_seq}.json`,
          onClick: () => setMessage('已请求浏览器下载 JSON；请在下载列表确认保存结果。')}, '保存 JSON 快照'),
        message && h('p', {className: 'dm-operation-status', role: 'status'}, message),
        h('pre', {className: 'dm-code'}, Graph.serialize(graph)))));
  };
};
