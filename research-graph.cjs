/* Pure research projection. Caller supplies process-graph.build(view, replay)'s verified gate. */
(function (publish) {
  'use strict';
  const FORMAT = 'dsh-math-research-graph-v1';
  const kinds = new Set(['Problem', 'Note', 'Obligation', 'Evidence', 'Review']);
  const list = value => Array.isArray(value) ? value : [];
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const ref = value => object(value) && typeof value.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value.id) && Number.isSafeInteger(value.rev) && value.rev > 0;
  const key = value => JSON.stringify([value.id, value.rev]);
  const text = value => typeof value === 'string' ? value : '';
  const excerpt = value => Array.from(text(value)).slice(0, 180).join('');
  const stable = value => Array.isArray(value) ? value.map(stable) : object(value) ? Object.fromEntries(Object.keys(value).sort().map(k => [k, stable(value[k])])) : value;
  const equal = (a, b) => JSON.stringify(stable(a)) === JSON.stringify(stable(b));
  function build(view, verifiedProcessGraph) {
    const gate = verifiedProcessGraph, state = view?.snapshot?.state;
    const assert = (ok, message) => { if (!ok) throw Error('研究树：' + message); };
    assert(gate?.format === 'dsh-math-process-graph-v1' && view?.snapshot?.usable === true && state && Array.isArray(view.objects), '需要已校验的过程图与可用快照。');
    assert(gate.project_id === view.project?.id && gate.research_head === state.head_hash && gate.research_seq === state.seq && state.campaign_id === view.project?.campaign_id, '过程图与当前项目/head/序列不一致，请刷新。');
    assert(Array.isArray(gate.nodes) && Array.isArray(gate.edges) && object(state.objects), '完整性 gate 缺少精确对象。');
    const snapshot = Object.values(state.objects).flat(), objects = new Map(), latest = new Map();
    for (const value of view.objects) {
      assert(ref(value) && object(value.data) && !objects.has(key(value)), '概览 revision 缺失或重复。');
      objects.set(key(value), value); latest.set(value.id, Math.max(latest.get(value.id) || 0, value.rev));
    }
    assert(snapshot.length === objects.size && snapshot.every(value => ref(value) && equal(objects.get(key(value)), value)), '概览与快照 revision 不一致。');
    const graph = {format: FORMAT, project_id: gate.project_id, research_head: gate.research_head, research_seq: gate.research_seq,
      nodes: [], hierarchy: [], relations: [], roots: [], warnings: list(gate.warnings).slice(),
      notice: '已保存研究记录的只读树。连线分为显示归属与显式引用；不表示时间因果。审核仅适用于精确版本与登记范围，local_claim、R1/R2 不升级为全局证明。'};
    const byRef = new Map(), byProcess = new Map(), assessments = new Map();
    function assess(value, expected) {
      if (!ref(value?.ref) || (expected && key(expected) !== key(value.ref))) return;
      const token = key(value.ref), previous = assessments.get(token);
      if (previous && !equal(previous, value)) { assessments.set(token, null); graph.warnings.push('精确版本审核评估冲突，未选择其中一个。'); }
      else if (previous !== null) assessments.set(token, value);
    }
    for (const rows of Object.values(view.groups || {})) for (const row of list(rows)) if (ref(row?.object)) assess(row.assessment, row.object);
    for (const claim of list(state.research?.claims)) assess(claim);
    for (const record of gate.nodes) {
      if (record.kind !== 'record' || !ref(record.ref)) continue;
      const value = objects.get(key(record.ref));
      assert(value && !byRef.has(key(value)), 'gate revision 无法唯一解析。');
      if (!kinds.has(value.kind)) continue;
      const d = value.data, assessment = assessments.get(key(value));
      const node = {id: record.id, kind: value.kind, ref: {id: value.id, rev: value.rev}, title: excerpt(d.title || d.statement || record.title),
        summary: excerpt(d.summary || d.statement || d.support_scope), historical: value.rev < latest.get(value.id), parentId: null,
        status: typeof assessment?.state === 'string' ? assessment.state : 'unassessed',
        statusLabel: assessment ? '已登记：' + assessment.state + '（仅此版本与范围）' : '无此版本审核评估',
        reasons: list(assessment?.reasons).filter(v => typeof v === 'string'), review_id: text(assessment?.review_id),
        note_type: text(d.note_type), gaps: list(d.gaps).filter(v => typeof v === 'string').map(excerpt), next_action: excerpt(d.next_action),
        failure: d.note_type === 'failed_attempt' && object(d.failure) ? Object.fromEntries(Object.entries(d.failure).filter(([,v]) => typeof v === 'string').map(([k,v]) => [k, excerpt(v)])) : null,
        source: record.source ? {...record.source} : null};
      graph.nodes.push(node); byRef.set(key(value), node); byProcess.set(record.id, node);
    }
    const problem = value => ref(value) && byRef.get(key(value))?.kind === 'Problem' ? byRef.get(key(value)) : null;
    const routeRows = value => Array.isArray(value) ? value : object(value) ? Object.values(value) : [];
    const savedRoutes = routeRows(state.research?.routes), overviewRoutes = view.routes === undefined ? savedRoutes : routeRows(view.routes);
    assert(equal(stable(savedRoutes), stable(overviewRoutes)), '路线概览与快照不一致，请刷新。');
    const controls = new Map();
    for (const control of savedRoutes) {
      if (!object(control) || typeof control.route_id !== 'string' || !ref(control.problem_ref) || !['active', 'paused', 'abandoned'].includes(control.status)) { graph.warnings.push('路线控制字段无效，未推断状态。'); continue; }
      const token = JSON.stringify([key(control.problem_ref), control.route_id]);
      assert(!controls.has(token), '路线控制重复。'); controls.set(token, control);
    }
    const routes = new Map();
    function route(parent, routeId) {
      const token = JSON.stringify([key(parent.ref), routeId]);
      if (routes.has(token)) return routes.get(token);
      const control = controls.get(token), registered = !!control;
      const node = {id: 'research-route:' + token, kind: 'route', parentId: parent.id, ref: null, problem_ref: {...parent.ref}, route_id: routeId,
        title: routeId === null ? '未指定路线' : routeId, summary: excerpt(control?.reason), next_action: excerpt(control?.next_action),
        status: control?.status || 'unregistered', statusLabel: registered ? ({active: '路线已登记进行', paused: '路线已登记暂停', abandoned: '路线已登记放弃'})[control.status] : routeId === null ? '未指定路线 · 无控制状态' : '路线未登记 · 状态未知',
        registered, control_rev: control?.rev ?? null, event_id: text(control?.event_id), historical: parent.historical};
      routes.set(token, node); graph.nodes.push(node); return node;
    }
    for (const control of savedRoutes) {
      if (!object(control) || !ref(control.problem_ref) || !controls.has(JSON.stringify([key(control.problem_ref), control.route_id]))) continue;
      const parent = problem(control.problem_ref);
      if (parent) route(parent, control.route_id); else graph.warnings.push('路线引用的精确 Problem 未纳入，未替换为新版。');
    }
    for (const node of graph.nodes.slice()) {
      if (!node.ref) continue;
      const value = objects.get(key(node.ref)), d = value.data;
      if (value.kind === 'Problem') {
        const parent = problem(d.parent); if (parent) node.parentId = parent.id;
        else if (d.parent != null) graph.warnings.push('父 Problem 精确引用未解析，保留独立目标。');
      } else if (['Note', 'Obligation', 'Evidence'].includes(value.kind)) {
        const parent = problem(d.problem_ref);
        if (parent) node.parentId = value.kind === 'Note' ? route(parent, typeof d.route_id === 'string' && d.route_id.trim() ? d.route_id : null).id : parent.id;
        else { node.unresolved = true; graph.warnings.push('记录未关联可解析的精确 Problem，未猜测归属。'); }
      } else if (value.kind === 'Review') {
        const target = ref(d.target) && byRef.get(key(d.target));
        if (target && target.kind !== 'Review') node.parentId = target.id;
        else node.unresolved = true;
      }
    }
    const ids = new Map(graph.nodes.map(node => [node.id, node]));
    for (const node of graph.nodes) {
      const seen = new Set([node.id]); let parent = ids.get(node.parentId), cycle = false;
      while (parent) { if (seen.has(parent.id)) { cycle = true; break; } seen.add(parent.id); parent = ids.get(parent.parentId); }
      if (cycle) { node.parentId = null; graph.warnings.push('显示归属存在循环，已保留独立卡片。'); }
      if (node.parentId) graph.hierarchy.push({from: node.parentId, to: node.id, relation: 'display_parent', label: '显示归属'});
      else graph.roots.push(node.id);
    }
    for (const edge of gate.edges) if (edge.relation !== 'contains' && byProcess.has(edge.from) && byProcess.has(edge.to)) graph.relations.push({...edge});
    graph.warnings = [...new Set(graph.warnings)];
    return graph;
  }
  publish({build});
})(value => { if (typeof module !== 'undefined' && module.exports) module.exports = value; else globalThis.DshMathResearchGraph = value; });
