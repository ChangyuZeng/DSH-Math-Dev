/* Native React/SVG research canvas; view operations only, no data writes or requests. */
(function (publish) {
  'use strict';
  const CARD_W = 272, CARD_H = 166, EXPANDED_H = 486, GAP_X = 76, GAP_Y = 36;
  const labels = {Problem: '研究问题', route: '研究路线', Note: '研究笔记', Obligation: '待证事项', Evidence: '参考材料', Review: '审核记录'};
  function layout(graph, {collapsed = {}, expanded = {}, heights = {}, showSupporting = false, showHistory = false} = {}) {
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
      const height = Math.max(CARD_H, Math.min(600, heights[node.id] || (expanded[node.id] ? EXPANDED_H : CARD_H)));
      const position = {node, x, depth, y: 0, width: CARD_W, height, childCount: (children.get(node.id) || []).length};
      positions.push(position); return position;
    }
    for (const node of children.get(null) || []) visit(node, 0);
    // Keep each generation below the tallest card in the preceding generation.
    // This also leaves edge endpoints outside every expanded card's rectangle.
    const rows = Array.from({length: maxDepth + 1}, () => ({height: 0, y: 24}));
    for (const position of positions) rows[position.depth].height = Math.max(rows[position.depth].height, position.height);
    for (let depth = 1; depth < rows.length; depth++) rows[depth].y = rows[depth - 1].y + rows[depth - 1].height + GAP_Y;
    for (const position of positions) position.y = rows[position.depth].y;
    const ids = new Set(positions.map(p => p.node.id));
    return {positions, hierarchy: (graph.hierarchy || []).filter(e => ids.has(e.from) && ids.has(e.to)), relations: (graph.relations || []).filter(e => ids.has(e.from) && ids.has(e.to)),
      width: Math.max(320, cursor + 24), height: positions.length ? rows.at(-1).y + rows.at(-1).height + 24 : 280};
  }
  function factory(React, RecordReader) {
    const {createElement: h, useState, useRef, useEffect} = React;
    const button = (caption, onClick, props = {}) => h('button', {type: 'button', className: 'dm-button dm-small dm-ghost', onClick, ...props}, caption);
    return function ResearchGraph({graph, view, inspect}) {
      const [selected, setSelected] = useState(null), [collapsed, setCollapsed] = useState({}), [supporting, setSupporting] = useState(false), [history, setHistory] = useState(false), [relations, setRelations] = useState(false);
      const [camera, setCamera] = useState({x: 20, y: 20, scale: 0.85});
      const [expanded, setExpanded] = useState({}), [heights, setHeights] = useState({});
      const viewport = useRef(null), drag = useRef(null), origin = useRef(new Map()), cardElements = useRef(new Map());
      useEffect(() => { setSelected(null); setCollapsed({}); setExpanded({}); setHeights({}); setCamera({x: 20, y: 20, scale: 0.85}); }, [graph.project_id]);
      const scene = layout(graph, {collapsed, expanded, heights, showSupporting: supporting, showHistory: history}), positions = new Map(scene.positions.map(p => [p.node.id, p]));
      useEffect(() => {
        const measure = () => setHeights(previous => {
          const next = {...previous}; let changed = false;
          for (const [id, element] of cardElements.current) {
            const height = Math.ceil(element.offsetHeight);
            if (height > 0 && next[id] !== height) { next[id] = height; changed = true; }
          }
          return changed ? next : previous;
        });
        measure();
        if (typeof ResizeObserver === 'undefined') return;
        const observer = new ResizeObserver(measure);
        for (const element of cardElements.current.values()) observer.observe(element);
        return () => observer.disconnect();
      }, [graph, expanded, collapsed, supporting, history]);
      const title = node => {
        if (node.kind === 'route' && (!node.title || /^[A-Za-z0-9_.:-]+$/.test(node.title))) {
          const siblings = graph.nodes.filter(n => n.kind === 'route' && n.parentId === node.parentId);
          return node.route_id === null ? '待定路线' : `路线 ${siblings.indexOf(node) + 1}`;
        }
        return node.title && node.title !== node.ref?.id ? node.title : labels[node.kind] || '研究记录';
      };
      const version = node => `第 ${(node.ref || node.problem_ref)?.rev || 1} 版`;
      const zoom = (factor, point) => setCamera(previous => {
        const scale = Math.max(0.2, Math.min(2, previous.scale * factor));
        const center = point || {x: (viewport.current?.clientWidth || 900) / 2, y: 260};
        return {scale, x: center.x - (center.x - previous.x) * scale / previous.scale, y: center.y - (center.y - previous.y) * scale / previous.scale};
      });
      // React delegates wheel events passively. Bind here so zoom does not scroll the page.
      useEffect(() => {
        const element = viewport.current;
        if (!element) return;
        const wheel = event => { if (event.target?.closest?.('.dm-rg-card-detail')) return; event.preventDefault(); const rect = element.getBoundingClientRect(); zoom(event.deltaY < 0 ? 1.1 : 1 / 1.1, {x: event.clientX - rect.left, y: event.clientY - rect.top}); };
        element.addEventListener('wheel', wheel, {passive: false});
        return () => element.removeEventListener('wheel', wheel);
      }, []);
      const fit = () => {
        const width = viewport.current?.clientWidth || 900, height = viewport.current?.clientHeight || 560;
        const scale = Math.max(0.2, Math.min(1, (width - 40) / scene.width, (height - 40) / scene.height));
        setCamera({scale, x: (width - scene.width * scale) / 2, y: (height - scene.height * scale) / 2});
      };
      const choose = (node, element) => {
        if (element) origin.current.set(node.id, element);
        setSelected(node.id);
        setExpanded(previous => ({...previous, [node.id]: true}));
        setHeights(previous => { const next = {...previous}; delete next[node.id]; return next; });
      };
      const locate = (position, element) => {
        choose(position.node, element);
        const width = viewport.current?.clientWidth || 900, height = viewport.current?.clientHeight || 560;
        const nextHeights = {...heights}; delete nextHeights[position.node.id];
        const target = layout(graph, {collapsed, expanded: {...expanded, [position.node.id]: true}, heights: nextHeights, showSupporting: supporting, showHistory: history}).positions.find(p => p.node.id === position.node.id);
        setCamera(previous => {
          const scale = Math.max(0.65, previous.scale);
          return {scale, x: width / 2 - (target.x + target.width / 2) * scale, y: height / 2 - (target.y + target.height / 2) * scale};
        });
      };
      const close = id => {
        setExpanded(previous => ({...previous, [id]: false}));
        setHeights(previous => { const next = {...previous}; delete next[id]; return next; });
        if (selected === id) setSelected(null);
        origin.current.get(id)?.focus?.();
      };
      const inlineInspect = object => {
        const node = graph.nodes.find(n => n.ref?.id === object.id && n.ref?.rev === object.rev);
        if (node && positions.has(node.id)) locate(positions.get(node.id));
      };
      const edge = (value, semantic, index) => {
        const from = positions.get(value.from), to = positions.get(value.to);
        const x1 = from.x + from.width / 2, y1 = from.y + from.height, x2 = to.x + to.width / 2, y2 = to.y, mid = (y1 + y2) / 2;
        return h('g', {key: `${semantic}:${index}`, className: semantic ? 'dm-rg-relation' : 'dm-rg-branch'},
          h('path', {d: `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`, fill: 'none'}),
          semantic && h('text', {x: (x1 + x2) / 2, y: mid - 7, textAnchor: 'middle'}, value.label || value.relation),
          h('title', {}, semantic ? `显式引用：${value.label || value.relation}` : '显示归属（非时间因果）'));
      };
      const cards = scene.positions.map(({node, x, y, width, height, childCount}) => {
        const object = node.ref && (view?.objects || []).find(o => o.id === node.ref.id && o.rev === node.ref.rev);
        return h('foreignObject', {key: node.id, x, y, width, height, className: 'dm-rg-object'},
          h('article', {className: 'dm-rg-card', 'data-kind': node.kind, 'data-node': node.id, 'data-selected': selected === node.id,
            ref: element => { if (element) cardElements.current.set(node.id, element); else cardElements.current.delete(node.id); }},
            h('button', {type: 'button', className: 'dm-rg-card-main', 'aria-expanded': !!expanded[node.id], 'aria-label': `${title(node)}，${expanded[node.id] ? '收起' : '展开'}详情`, onClick: event => expanded[node.id] ? close(node.id) : choose(node, event.currentTarget)},
              h('span', {className: 'dm-rg-kicker'}, labels[node.kind] || '研究记录', ' · ', version(node), node.historical && ' · 历史'),
              h('strong', {className: 'dm-rg-title'}, title(node)),
              h('span', {className: 'dm-rg-summary'}, node.summary || (node.kind === 'route' ? node.next_action : '') || '暂无摘要')),
            h('div', {className: 'dm-rg-card-actions'},
              button(expanded[node.id] ? '收起详情' : '查看详情', event => expanded[node.id] ? close(node.id) : choose(node, event.currentTarget), {'aria-expanded': !!expanded[node.id]}),
              childCount > 0 && button(collapsed[node.id] ? `展开 ${childCount} 项` : `折叠 ${childCount} 项`, () => setCollapsed(previous => ({...previous, [node.id]: !previous[node.id]})), {'aria-expanded': !collapsed[node.id], 'aria-label': `${collapsed[node.id] ? '展开' : '折叠'}子项：${title(node)}`, className: 'dm-rg-collapse'})),
            expanded[node.id] && h('section', {className: 'dm-rg-card-detail', 'aria-label': `${title(node)}详情`, tabIndex: 0},
              object && typeof inspect === 'function' && button('完整记录', () => inspect(object)),
              object && typeof RecordReader === 'function' ? h(RecordReader, {key: `${graph.project_id}:${object.id}@${object.rev}`, object, view, inspect: inlineInspect, compact: true})
                : h('div', {}, h('p', {}, node.summary), node.next_action && h('p', {}, '下一步：', node.next_action),
                  h('details', {}, h('summary', {}, '版本信息'), h('p', {}, node.statusLabel),
                    h('code', {}, node.ref ? `${node.ref.id}@${node.ref.rev}` : `${node.problem_ref.id}@${node.problem_ref.rev}`))))));
      });
      return h('section', {className: 'dm-research-graph', 'aria-label': '数学研究树'},
        h('div', {className: 'dm-rg-toolbar'}, h('strong', {}, '研究树'),
          button('缩小', () => zoom(1 / 1.2)), h('output', {'aria-label': '缩放比例'}, `${Math.round(camera.scale * 100)}%`), button('放大', () => zoom(1.2)), button('适应画布', fit), button('展开全部', () => setCollapsed({})),
          h('label', {}, h('input', {type: 'checkbox', checked: relations, onChange: e => setRelations(e.target.checked)}), '显式关系'),
          h('label', {}, h('input', {type: 'checkbox', checked: supporting, onChange: e => setSupporting(e.target.checked)}), '材料与审核'),
          h('label', {}, h('input', {type: 'checkbox', checked: history, onChange: e => setHistory(e.target.checked)}), '历史版本')),
        h('details', {className: 'dm-rg-outline'}, h('summary', {}, `节点目录 · ${scene.positions.length}`),
          h('nav', {'aria-label': '当前可见节点目录'}, ...scene.positions.slice().sort((a,b) => a.y - b.y || a.x - b.x).map(position => {
            const node = position.node;
            return button(`${title(node)} · ${version(node)}`, event => locate(position, event.currentTarget), {key: node.id, className: 'dm-rg-outline-item', 'aria-current': selected === node.id ? 'true' : undefined});
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
        graph.warnings.length > 0 && h('details', {className: 'dm-details'}, h('summary', {}, `读取说明 · ${graph.warnings.length}`), h('ul', {}, ...graph.warnings.map((warning,i) => h('li', {key:i}, warning)))));
    };
  }
  factory.layout = layout;
  publish(factory);
})(factory => { if (typeof module !== 'undefined' && module.exports) module.exports = factory; else globalThis.DshMathResearchGraphUI = factory; });
