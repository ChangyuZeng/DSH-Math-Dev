/* Native React/SVG research canvas; view operations only, no data writes or requests. */
(function (publish) {
  'use strict';
  const CARD_W = 272, CARD_H = 192, GAP_X = 76, GAP_Y = 28;
  const labels = {Problem: '研究问题', route: '研究路线', Note: '研究笔记', Obligation: '待证事项', Evidence: '参考材料', Review: '审核记录'};
  function layout(graph, {collapsed = {}, showSupporting = false, showHistory = false} = {}) {
    const nodes = graph.nodes || [], byId = new Map(nodes.map(n => [n.id, n])), wanted = new Set();
    for (const node of nodes) {
      if ((!showSupporting && ['Evidence', 'Review'].includes(node.kind)) || (!showHistory && node.historical && node.kind !== 'Problem' && node.kind !== 'route')) continue;
      let current = node; const seen = new Set();
      while (current && !seen.has(current.id)) { seen.add(current.id); wanted.add(current.id); current = byId.get(current.parentId); }
    }
    const children = new Map();
    for (const node of nodes) if (wanted.has(node.id)) { const siblings = children.get(node.parentId) || []; siblings.push(node); children.set(node.parentId, siblings); }
    const positions = [], visible = new Set(); let cursor = 24, maxDepth = 0;
    function visit(node, depth) {
      if (visible.has(node.id)) return null;
      visible.add(node.id); maxDepth = Math.max(maxDepth, depth);
      const start = cursor, descendants = collapsed[node.id] ? [] : (children.get(node.id) || []);
      const childPositions = descendants.map(n => visit(n, depth + 1)).filter(Boolean);
      if (!childPositions.length) cursor += CARD_W + GAP_X;
      const x = childPositions.length ? (childPositions[0].x + childPositions.at(-1).x) / 2 : start;
      const position = {node, x, y: 24 + depth * (CARD_H + GAP_Y), width: CARD_W, height: CARD_H, childCount: (children.get(node.id) || []).length};
      positions.push(position); return position;
    }
    for (const node of children.get(null) || []) visit(node, 0);
    const ids = new Set(positions.map(p => p.node.id));
    return {positions, hierarchy: (graph.hierarchy || []).filter(e => ids.has(e.from) && ids.has(e.to)), relations: (graph.relations || []).filter(e => ids.has(e.from) && ids.has(e.to)),
      width: Math.max(320, cursor + 24), height: positions.length ? 48 + maxDepth * (CARD_H + GAP_Y) + CARD_H : 280};
  }
  function factory(React, RecordReader) {
    const {createElement: h, useState, useRef, useEffect} = React;
    const button = (caption, onClick, props = {}) => h('button', {type: 'button', className: 'dm-button dm-small dm-ghost', onClick, ...props}, caption);
    return function ResearchGraph({graph, view, inspect}) {
      const [selected, setSelected] = useState(null), [collapsed, setCollapsed] = useState({}), [supporting, setSupporting] = useState(false), [history, setHistory] = useState(false), [relations, setRelations] = useState(false);
      const [camera, setCamera] = useState({x: 20, y: 20, scale: 0.85});
      const viewport = useRef(null), drag = useRef(null), origin = useRef(null);
      useEffect(() => { setSelected(null); setCollapsed({}); setCamera({x: 20, y: 20, scale: 0.85}); }, [graph.project_id]);
      const scene = layout(graph, {collapsed, showSupporting: supporting, showHistory: history}), positions = new Map(scene.positions.map(p => [p.node.id, p]));
      const selectedNode = graph.nodes.find(n => n.id === selected);
      const selectedObject = selectedNode?.ref && (view?.objects || []).find(o => o.id === selectedNode.ref.id && o.rev === selectedNode.ref.rev);
      const zoom = (factor, point) => setCamera(previous => {
        const scale = Math.max(0.2, Math.min(2, previous.scale * factor));
        const center = point || {x: (viewport.current?.clientWidth || 900) / 2, y: 260};
        return {scale, x: center.x - (center.x - previous.x) * scale / previous.scale, y: center.y - (center.y - previous.y) * scale / previous.scale};
      });
      // React delegates wheel events passively. Bind here so zoom does not scroll the page.
      useEffect(() => {
        const element = viewport.current;
        if (!element) return;
        const wheel = event => { event.preventDefault(); const rect = element.getBoundingClientRect(); zoom(event.deltaY < 0 ? 1.1 : 1 / 1.1, {x: event.clientX - rect.left, y: event.clientY - rect.top}); };
        element.addEventListener('wheel', wheel, {passive: false});
        return () => element.removeEventListener('wheel', wheel);
      }, []);
      const fit = () => {
        const width = viewport.current?.clientWidth || 900, height = viewport.current?.clientHeight || 560;
        const scale = Math.max(0.2, Math.min(1, (width - 40) / scene.width, (height - 40) / scene.height));
        setCamera({scale, x: (width - scene.width * scale) / 2, y: (height - scene.height * scale) / 2});
      };
      const choose = (node, element) => { origin.current = element; setSelected(node.id); };
      const locate = (position, element) => {
        choose(position.node, element);
        const width = viewport.current?.clientWidth || 900, height = viewport.current?.clientHeight || 560;
        setCamera(previous => {
          const scale = Math.max(0.65, previous.scale);
          return {scale, x: width / 2 - (position.x + position.width / 2) * scale, y: height / 2 - (position.y + position.height / 2) * scale};
        });
      };
      const close = () => { setSelected(null); origin.current?.focus?.(); };
      const inlineInspect = object => {
        const node = graph.nodes.find(n => n.ref?.id === object.id && n.ref?.rev === object.rev);
        if (node) setSelected(node.id);
      };
      const edge = (value, semantic, index) => {
        const from = positions.get(value.from), to = positions.get(value.to);
        const x1 = from.x + CARD_W / 2, y1 = from.y + CARD_H, x2 = to.x + CARD_W / 2, y2 = to.y, mid = (y1 + y2) / 2;
        return h('g', {key: `${semantic}:${index}`, className: semantic ? 'dm-rg-relation' : 'dm-rg-branch'},
          h('path', {d: `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`, fill: 'none'}),
          semantic && h('text', {x: (x1 + x2) / 2, y: mid - 7, textAnchor: 'middle'}, value.label || value.relation),
          h('title', {}, semantic ? `显式引用：${value.label || value.relation}` : '显示归属（非时间因果）'));
      };
      const cards = scene.positions.map(({node, x, y, childCount}) => h('foreignObject', {key: node.id, x, y, width: CARD_W, height: CARD_H, className: 'dm-rg-object'},
        h('article', {className: 'dm-rg-card', 'data-kind': node.kind, 'data-selected': selected === node.id, 'data-status': node.status},
          h('button', {type: 'button', className: 'dm-rg-card-main', 'aria-pressed': selected === node.id, onClick: event => choose(node, event.currentTarget)},
            h('span', {className: 'dm-rg-kicker'}, labels[node.kind] || node.kind, node.historical && ' · 历史目标/版本'),
            h('strong', {className: 'dm-rg-title'}, node.title || labels[node.kind]),
            h('span', {className: 'dm-rg-ref'}, node.ref ? `${node.ref.id}@${node.ref.rev}` : `${node.problem_ref.id}@${node.problem_ref.rev}`),
            h('span', {className: 'dm-rg-summary'}, node.summary || (node.kind === 'route' ? node.next_action : '') || '未登记摘要'),
            h('span', {className: 'dm-rg-status'}, node.note_type === 'failed_attempt' ? '失败尝试 · ' + node.statusLabel : node.statusLabel)),
          childCount > 0 && button(collapsed[node.id] ? `展开 ${childCount} 项` : `折叠 ${childCount} 项`, () => setCollapsed(previous => ({...previous, [node.id]: !previous[node.id]})), {'aria-expanded': !collapsed[node.id], 'aria-label': `${collapsed[node.id] ? '展开' : '折叠'} ${node.title}`, className: 'dm-rg-collapse'}))));
      return h('section', {className: 'dm-research-graph', 'aria-label': '数学研究树'},
        h('div', {className: 'dm-rg-toolbar'}, h('strong', {}, '研究树'),
          button('缩小', () => zoom(1 / 1.2)), h('output', {'aria-label': '缩放比例'}, `${Math.round(camera.scale * 100)}%`), button('放大', () => zoom(1.2)), button('适应画布', fit), button('展开全部', () => setCollapsed({})),
          h('label', {}, h('input', {type: 'checkbox', checked: relations, onChange: e => setRelations(e.target.checked)}), '显式关系'),
          h('label', {}, h('input', {type: 'checkbox', checked: supporting, onChange: e => setSupporting(e.target.checked)}), '材料与审核'),
          h('label', {}, h('input', {type: 'checkbox', checked: history, onChange: e => setHistory(e.target.checked)}), '历史版本')),
        h('details', {className: 'dm-rg-outline'}, h('summary', {}, `节点目录 · ${scene.positions.length}`),
          h('nav', {'aria-label': '当前可见节点目录'}, ...scene.positions.slice().sort((a,b) => a.y - b.y || a.x - b.x).map(position => {
            const node = position.node, reference = node.ref || node.problem_ref;
            return button(`${node.title || labels[node.kind]} · ${reference.id}@${reference.rev}`, event => locate(position, event.currentTarget), {key: node.id, className: 'dm-rg-outline-item', 'aria-current': selected === node.id ? 'true' : undefined});
          }))),
        h('p', {className: 'dm-rg-legend'}, '实线：显示归属 · 虚线：显式引用 · 拖动空白平移，滚轮缩放；方向键平移，+ / − 缩放，0 适应画布。'),
        h('svg', {className: 'dm-rg-viewport', ref: viewport, tabIndex: 0, role: 'group', 'aria-label': '可平移缩放研究树画布',
          onKeyDown: event => {
            if (event.target !== event.currentTarget) return;
            const moves = {ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40]};
            if (moves[event.key]) { event.preventDefault(); const [dx, dy] = moves[event.key]; setCamera(c => ({...c, x: c.x + dx, y: c.y + dy})); }
            else if (['+', '=', '-','0'].includes(event.key)) { event.preventDefault(); if (event.key === '0') fit(); else zoom(event.key === '-' ? 1 / 1.2 : 1.2); }
          },
          onPointerDown: event => { if (event.button !== 0 || event.target.closest?.('foreignObject')) return; drag.current = {id: event.pointerId, x: event.clientX, y: event.clientY, camera}; event.currentTarget.setPointerCapture?.(event.pointerId); },
          onPointerMove: event => { const d = drag.current; if (d && event.pointerId === d.id) setCamera({...d.camera, x: d.camera.x + event.clientX - d.x, y: d.camera.y + event.clientY - d.y}); },
          onPointerUp: event => { if (drag.current?.id === event.pointerId) { drag.current = null; event.currentTarget.releasePointerCapture?.(event.pointerId); } }, onPointerCancel: () => { drag.current = null; }},
          h('g', {transform: `translate(${camera.x} ${camera.y}) scale(${camera.scale})`}, ...scene.hierarchy.map((e,i) => edge(e,false,i)), ...(relations ? scene.relations.map((e,i) => edge(e,true,i)) : []), ...cards)),
        !scene.positions.length && h('p', {role: 'status'}, '当前快照暂无可显示的研究记录。'),
        h('p', {className: 'dm-local-hint'}, graph.notice),
        graph.warnings.length > 0 && h('details', {className: 'dm-details'}, h('summary', {}, `读取说明 · ${graph.warnings.length}`), h('ul', {}, ...graph.warnings.map((warning,i) => h('li', {key:i}, warning)))),
        selected && h('section', {className: 'dm-rg-detail', 'aria-label': '研究树内联详情'}, h('div', {className: 'dm-rg-toolbar'}, button('关闭详情', close),
          selectedObject && typeof inspect === 'function' && button('打开完整记录', () => inspect(selectedObject))),
          selectedObject && typeof RecordReader === 'function' ? h(RecordReader, {key: `${graph.project_id}:${selectedObject.id}@${selectedObject.rev}`, object: selectedObject, view, inspect: inlineInspect, autoFocus: true})
            : selectedNode ? h('div', {}, h('h3', {}, selectedNode.title), h('p', {}, selectedNode.statusLabel), h('p', {}, selectedNode.summary), h('p', {}, selectedNode.next_action), selectedNode.kind === 'route' && h('p', {}, `目标：${selectedNode.problem_ref.id}@${selectedNode.problem_ref.rev} · 路线控制版本：${selectedNode.control_rev ?? '未登记'}`))
              : h('p', {role:'status'}, '当前快照无选中精确版本；未替换为新版。')));
    };
  }
  factory.layout = layout;
  publish(factory);
})(factory => { if (typeof module !== 'undefined' && module.exports) module.exports = factory; else globalThis.DshMathResearchGraphUI = factory; });
