/* A native, read-only viewer for the audited projection. No log or model APIs. */
globalThis.DshMathGraphUI = function (React, Graph) {
  const {createElement: h, useState, useEffect, useRef} = React;
  const labels = {project: '研究项目', source: '记录来源', event: '已记录动作', record: '版本化产物'};
  const button = (text, onClick, props = {}) => h('button', {type: 'button', className: 'dm-button dm-small dm-ghost', onClick, ...props}, text);
  return function ProcessGraph({graph, inspect}) {
    const [selected, setSelected] = useState(null), [collapsed, setCollapsed] = useState({}), [limits, setLimits] = useState({});
    const [download, setDownload] = useState(''), [message, setMessage] = useState(''), [showJSON, setShowJSON] = useState(false);
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
      setDownload(''); setMessage('');
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
    function branch(node) {
      const descendants = children.get(node.id) || [];
      const folded = collapsed[node.id] ?? (node.kind === 'event' && descendants.length > 0);
      const limit = limits[node.id] || 40;
      return h('li', {className: `dm-graph-branch dm-graph-${node.kind}`, key: node.id},
        h('div', {className: 'dm-graph-node-wrap'},
          h('button', {type: 'button', className: 'dm-graph-node', 'data-selected': selected === node.id, 'data-status': node.status,
            'aria-pressed': selected === node.id, onClick: event => choose(node, event.currentTarget)},
          h('span', {className: 'dm-graph-kicker'}, labels[node.kind], node.subtitle && h('span', null, node.subtitle)),
          h('strong', {className: 'dm-graph-label'}, node.title),
          node.summary && h('span', {className: 'dm-graph-summary'}, node.summary),
          h('span', {className: 'dm-graph-status'}, h('span', {className: 'dm-graph-dot', 'aria-hidden': true}), node.statusLabel)),
          descendants.length > 0 && button(`${folded ? '展开' : '收起'} ${descendants.length} 项`, () => setCollapsed(previous => ({...previous, [node.id]: !folded})),
            {'aria-expanded': !folded, 'aria-label': `${folded ? '展开' : '收起'} ${node.title} 的子节点`, className: 'dm-button dm-small dm-ghost dm-graph-toggle'})),
        !folded && descendants.length > 0 && h('ul', {className: node.kind === 'project' ? 'dm-graph-sources' : 'dm-graph-children', 'aria-label': `${node.title} 的子节点`},
          ...descendants.slice(0, limit).map(branch),
          descendants.length > limit && h('li', {className: 'dm-graph-more'}, button(`显示更多（${limit} / ${descendants.length}）`, () => setLimits(previous => ({...previous, [node.id]: limit + 40}))))));
    }
    return h('section', {className: 'dm-process-graph', 'aria-label': '已记录研究过程图'},
      h('div', {className: 'dm-graph-caption'},
        h('div', null, h('h3', {className: 'dm-panel-title'}, '每一步，都有出处'),
          h('p', {className: 'dm-record-text'}, `${graph.counts.events} 个动作 · ${graph.counts.records} 个版本产物 · seq ${graph.research_seq}`)),
        download && h('a', {className: 'dm-button dm-small', href: download, download: `dsh-math-process-${graph.project_id}-seq-${graph.research_seq}.json`,
          onClick: () => setMessage('已请求浏览器下载 JSON；请在下载列表确认保存结果。')}, '保存 JSON 快照')),
      h('p', {className: 'dm-local-hint dm-graph-notice'}, '连线表示来源分组与产物归属，不代表推理因果。引用关系见节点详情；点击卡片可查看记录。'),
      graph.warnings.length > 0 && h('details', {className: 'dm-details dm-graph-warnings'}, h('summary', null, `${graph.warnings.length} 条覆盖范围说明`),
        h('ul', null, ...graph.warnings.map((warning, index) => h('li', {key: index}, warning)))),
      focused && h('section', {className: 'dm-graph-detail', 'aria-label': '过程节点详情', onKeyDown: event => { if (event.key === 'Escape') { event.stopPropagation(); close(); } }},
        h('div', {className: 'dm-panel-header'}, h('h3', {className: 'dm-panel-title', tabIndex: -1, ref: detailHeading}, focused.title), button('关闭节点详情', close)),
        h('p', {className: 'dm-record-text'}, `${labels[focused.kind]} · ${focused.statusLabel}`),
        focused.summary && h('p', {className: 'dm-record-text'}, focused.summary),
        h('dl', {className: 'dm-graph-facts'}, ...focused.facts.map((fact, index) => h('div', {key: index}, h('dt', null, fact.label), h('dd', null, fact.value)))),
        focused.source && h('details', {className: 'dm-details'}, h('summary', null, '原始记录定位与哈希'), h('pre', {className: 'dm-code'}, JSON.stringify(focused.source, null, 2))),
        focused.ref && button(`打开记录 ${focused.ref.id}@${focused.ref.rev}`, () => inspect(focused.ref), {className: 'dm-button dm-small'}),
        related.length > 0 && h('div', {className: 'dm-graph-relations'}, h('p', {className: 'dm-record-text'}, '显式引用（可能有交叉或环，不强行树化）'),
          ...related.map((edge, index) => {
            const next = byId.get(edge.from === selected ? edge.to : edge.from);
            return h('div', {key: index}, h('span', {className: 'dm-panel-meta'}, `${edge.from === selected ? '→' : '←'} ${edge.label || edge.relation} `),
              next ? button(next.title, () => choose(next)) : h('span', null, '未收录的精确引用'));
          }))),
      h('div', {className: 'dm-graph-board'}, h('ul', {className: 'dm-graph-roots', 'aria-label': '研究过程节点'}, ...(children.get(null) || []).map(branch))),
      h('p', {className: 'dm-local-hint'}, graph.notice),
      h('p', {className: 'dm-local-hint'}, '快照不是审核包或数学证明，包含记录摘要与来源路径，分享前请检查。此处不展示工具输入/输出原文或模型推理流。'),
      message && h('p', {className: 'dm-operation-status', role: 'status'}, message),
      h('details', {className: 'dm-details', onToggle: event => setShowJSON(event.currentTarget.open)}, h('summary', null, '查看可保存的 JSON 快照'), showJSON && h('pre', {className: 'dm-code'}, Graph.serialize(graph))));
  };
};
