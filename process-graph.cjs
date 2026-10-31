/* A read-only projection of backend overview + replay.aggregate, not an audit export.
 * No I/O, clocks, model calls, or traversal of external payloads. */
(function (publish) {
  'use strict';

  const FORMAT = 'dsh-math-process-graph-v1';
  const RESEARCH = 'research/events.jsonl';
  const ZERO = '0'.repeat(64);
  const NOTICE = '这是已记录公开研究产物的只读投影，非实时运行状态、非内部思维链、非审核证据或证明。父子仅表示来源分组和实际产物归属，不表示执行因果；引用边只来自显式引用。未选来源就是未采集；本次读取不调用模型，但整个会话模型请求总数仍未知。';
  const KINDS = {Problem: '研究目标', Note: '研究笔记', Obligation: '待办义务', Evidence: '证据记录', Review: '审查记录', Run: '离线运行记录'};
  const RESEARCH_KINDS = {commit: '研究提交', review: '审查提交', control: '研究控制记录', recovery: '日志恢复记录'};
  const LEDGER_KINDS = {
    'tool.started': '工具开始记录', 'tool.finished': '工具结束记录',
    'request.reserved': '请求预留记录', 'request.finished': '请求结束记录',
    'request.settled': '请求结算记录', 'request.error': '请求错误记录',
    opened: '运行开启记录', stopped: '运行停止记录', sealed: '运行封存记录',
  };
  const RELATIONS = {
    parent_problem: '父目标引用', problem_ref: '研究目标引用', uses: '使用',
    candidate_for: '候选用于', candidates: '候选引用', evidence: '证据引用',
    dependencies: '依赖引用', review_target: '审查对象', review_candidate: '审查候选', condition_gap: '条件缺口',
  };
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const text = value => typeof value === 'string' && value.trim().length > 0;
  const identifier = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
  const identity = value => identifier(value) || (integer(value) && !Object.is(value, -0));
  const hash = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
  const reference = value => object(value) && identifier(value.id) && integer(value.rev) && value.rev > 0;
  const id = (kind, ...parts) => kind + ':' + JSON.stringify(parts);
  const key = ref => id('ref', ref.id, ref.rev);
  const excerpt = (value, limit = 280) => typeof value !== 'string' ? '' : value.length > limit ? value.slice(0, limit) + '…（节选）' : value;
  const fact = (label, value) => ({label, value: String(value)});
  const assert = (condition, message) => { if (!condition) throw new Error('过程图：' + message); };
  const own = (value, name) => Object.prototype.hasOwnProperty.call(value, name);

  // Sort keys only; array order is the source sequence, never a wall-clock ordering.
  function stable(value) {
    if (Array.isArray(value)) return value.map(stable);
    if (!object(value)) return value;
    const out = Object.create(null);
    for (const name of Object.keys(value).sort()) out[name] = stable(value[name]);
    return out;
  }
  function serialize(graph) {
    assert(object(graph) && graph.format === FORMAT, '不是受支持的过程图。');
    return JSON.stringify(stable(graph), null, 2) + '\n';
  }

  function build(view, replay) {
    assert(object(view) && object(view.project) && object(view.snapshot) && object(view.snapshot.state), '缺少项目或研究快照，请刷新。');
    const project = view.project, state = view.snapshot.state;
    assert(identifier(project.id) && identifier(project.campaign_id) && text(project.title), '项目身份或标题缺失，请刷新。');
    assert([3, 4].includes(project.schema_version), '不支持该研究 schema，不能生成过程图。');
    assert(view.snapshot.usable === true && integer(state.seq) && hash(state.head_hash), '研究快照不可用或序列缺失，请刷新。');
    assert(state.campaign_id === project.campaign_id, '项目与研究 campaign 不一致，请刷新。');
    assert(object(replay) && replay.format === 'dsh-math-replay-v1', '缺少受支持的回放，请刷新。');
    assert(replay.research_head === state.head_hash, '回放与概览的研究 head 不一致，请刷新后重试。');
    assert(!own(replay, 'project_id') || replay.project_id === project.id, '回放属于另一项目，请刷新。');
    assert(Array.isArray(replay.events) && Array.isArray(replay.sources) && Array.isArray(view.objects) && object(state.objects), '缺少事件、来源或精确对象列表，请刷新。');

    const graph = {
      format: FORMAT, project_id: project.id, project_title: excerpt(project.title, 200),
      research_head: state.head_hash, research_seq: state.seq,
      nodes: [], edges: [], sources: [], warnings: [], counts: {events: 0, records: 0, omitted: 0}, notice: NOTICE,
    };
    const warningSet = new Set(), edgeSet = new Set();
    const warn = value => { if (!warningSet.has(value)) { warningSet.add(value); graph.warnings.push(value); } };
    function edge(from, to, relation, label) {
      const token = id('edge', from, to, relation);
      if (!edgeSet.has(token)) { edgeSet.add(token); graph.edges.push({from, to, relation, label}); }
    }
    function node(value) {
      graph.nodes.push(value);
      if (value.parentId !== null) edge(value.parentId, value.id, 'contains', value.kind === 'record' ? '实际产物归属' : '来源分组');
      if (value.kind === 'event') graph.counts.events++;
      if (value.kind === 'record') graph.counts.records++;
      return value;
    }
    function omit(source, count = 1) { source.omitted += count; graph.counts.omitted += count; }
    function location(row, descriptor, event) {
      const value = {path: descriptor.path};
      if (integer(row.seq)) value.seq = row.seq;
      if (hash(row.hash)) value.hash = row.hash;
      if (hash(descriptor.file_sha256)) value.file_sha256 = descriptor.file_sha256;
      if (event && identifier(event.event_id)) value.event_id = event.event_id;
      return value;
    }
    const rootId = id('project', project.id);
    node({id: rootId, parentId: null, kind: 'project', title: excerpt(project.title, 120), subtitle: '已保存的研究过程',
      status: 'recorded', statusLabel: '只读快照', summary: '仅呈现已记录事件和明确归属的公开研究产物。',
      facts: [fact('项目', project.id), fact('campaign', project.campaign_id), fact('研究序列', state.seq), fact('研究 head', state.head_hash), fact('schema', project.schema_version)]});

    const byPath = new Map();
    function addSource(descriptor, title, summary) {
      graph.sources.push(descriptor);
      byPath.set(descriptor.path, descriptor);
      descriptor.node = node({id: descriptor.id, parentId: rootId, kind: 'source', title, subtitle: descriptor.path,
        status: 'recorded', statusLabel: '来源记录', summary,
        facts: [fact('适配器', descriptor.adapter), fact('完整性范围', descriptor.integrity)], source: location({}, descriptor)});
      return descriptor;
    }
    const researchSource = addSource({id: id('source', RESEARCH), adapter: 'research', path: RESEARCH,
      integrity: 'backend_verified_research', selected: true, events: 0, omitted: 0, unmapped_records: 0},
    '研究事件', '来源已由后端读取校验；本投影仅核对身份、序列和对应 revision，不重新验证哈希或数学结论。');
    if (project.schema_version === 3) warn('schema 3 只读兼容：只映射可与事件 changes 精确对应的 revision；不迁移、不推断缺失事件。');

    for (const raw of replay.sources) {
      assert(object(raw) && text(raw.path) && !/[\u0000-\u001f]/.test(raw.path), '来源路径缺失或无效。');
      assert(!byPath.has(raw.path), '来源路径重复，无法唯一定位。');
      const adapter = ['ledger-py-v1', 'audit-js-v1', 'native-v4'].includes(raw.adapter) ? raw.adapter : 'unsupported';
      const native = adapter === 'native-v4';
      const integrity = native ? 'byte_hash_only_no_native_chain' : raw.integrity === 'verified_chain' ? 'verified_chain' : 'unknown';
      const descriptor = {id: id('source', raw.path), adapter, path: raw.path, integrity, selected: true, events: 0, omitted: 0};
      if (hash(raw.sha256)) descriptor.file_sha256 = raw.sha256;
      if (integer(raw.bytes)) descriptor.bytes = raw.bytes;
      const supported = adapter === 'ledger-py-v1' && integrity === 'verified_chain' && hash(raw.sha256);
      addSource(descriptor, adapter === 'ledger-py-v1' ? '显式选择的运行账本' : '仅保留来源描述',
        supported ? '仅接入本项目且 session/run 明确的请求和工具白名单字段；不含输入输出原文。' : '此来源的消息不纳入过程节点，仅保留描述与未纳入计数。');
      if (!supported) {
        descriptor.node.status = 'unknown'; descriptor.node.statusLabel = '消息未纳入';
        warn(native ? 'native 来源只有文件字节哈希，不是事件链；原生消息未解析、未纳入图。' : adapter === 'audit-js-v1' ? 'audit-js 来源仅保留描述，消息与 service.frame 未解析、未纳入图。' : '来源适配器或完整性信息不受支持，事件未纳入图。');
      }
      if (native && raw.integrity !== 'byte_hash_only_no_native_chain') warn('native 来源的链完整性声明不可用；仅保留 byte_hash_only_no_native_chain。');
    }
    warn(replay.sources.length ? '只读取显式选择的外部来源；未选择的来源未采集，会话请求总数未知。' : '未选择外部来源：工具、请求及原生会话消息均未采集，会话请求总数未知。');

    // Group without reading any payload fields of unsupported/native sources.
    const rowsByPath = new Map([...byPath.keys()].map(path => [path, []]));
    for (const row of replay.events) {
      if (!object(row) || !rowsByPath.has(row.source)) {
        graph.counts.omitted++; warn('发现无显式来源描述的事件，未纳入图。'); continue;
      }
      rowsByPath.get(row.source).push(row);
    }
    const researchRows = rowsByPath.get(RESEARCH).slice().sort((a, b) => a.seq - b.seq);
    assert(researchRows.length === state.seq, '研究事件数量与快照序列不一致，请刷新。');
    const objects = new Map(), snapshotObjects = new Map();
    for (const versions of Object.values(state.objects)) {
      assert(Array.isArray(versions), '快照 revision 列表无效，请刷新。');
      for (const obj of versions) {
        assert(reference(obj) && !snapshotObjects.has(key(obj)), '快照含缺失或重复 revision，请刷新。');
        snapshotObjects.set(key(obj), obj);
      }
    }
    for (const obj of view.objects) {
      assert(reference(obj) && object(obj.data) && !objects.has(key(obj)), '概览含缺失或重复 revision，请刷新。');
      assert(snapshotObjects.has(key(obj)) && JSON.stringify(stable(obj)) === JSON.stringify(stable(snapshotObjects.get(key(obj)))), '概览与快照 revision 不一致，请刷新。');
      objects.set(key(obj), obj);
    }
    assert(objects.size === snapshotObjects.size, '概览 revision 不完整，请刷新。');
    const recordNodes = new Map(), recordObjects = [], researchOrigins = [], eventIds = new Set();
    let previous = ZERO;
    for (let index = 0; index < researchRows.length; index++) {
      const row = researchRows[index], event = row.data;
      assert(object(event) && row.seq === index + 1 && event.seq === row.seq && hash(row.hash) && event.event_hash === row.hash && event.previous_hash === previous,
        '研究事件序列或 head 链接不一致，请刷新。');
      assert(event.campaign_id === project.campaign_id && event.schema_version === project.schema_version, '研究事件 campaign/schema 与项目不一致，请刷新。');
      assert(identifier(event.event_id) && !eventIds.has(event.event_id) && typeof event.kind === 'string' && row.kind === 'research.' + event.kind && Array.isArray(event.changes), '研究事件字段缺失或不一致，请刷新。');
      previous = row.hash; eventIds.add(event.event_id);
      if (!own(RESEARCH_KINDS, event.kind)) {
        omit(researchSource); warn('未知研究事件类型未纳入图；不将其 changes 伪装为已识别产物。'); continue;
      }
      assert(['commit', 'review'].includes(event.kind) || event.changes.length === 0, '控制/恢复事件不应产生 revision，请刷新。');
      const eventNode = node({id: id('research-event', project.id, event.event_id), parentId: researchSource.id, kind: 'event',
        title: RESEARCH_KINDS[event.kind], subtitle: '#' + row.seq, status: 'recorded', statusLabel: '事件已记录',
        summary: '记录存在不等于数学结论通过。', facts: [fact('事件类型', row.kind), fact('原始序列', row.seq), fact('产物 revision 数', event.changes.length)],
        source: location(row, researchSource, event)});
      researchSource.events++;
      const actorLabels = {human: '人类登记', model: '模型登记', system: '系统登记'};
      const actorKind = object(event.actor) && own(actorLabels, event.actor.kind) ? event.actor.kind : null;
      const actorLabel = actorKind ? actorLabels[actorKind] : '登记来源未知';
      if (actorKind) eventNode.facts.push(fact('actor.kind', actorKind), fact('登记来源类别', actorLabel));
      else warn('研究事件缺少已知 actor.kind，登记来源类别未知。');
      if (object(event.origin)) researchOrigins.push({node: eventNode, origin: event.origin});
      const produced = [];
      for (const change of event.changes) {
        assert(reference(change), '事件缺少精确 revision，请刷新。');
        const refKey = key(change), obj = objects.get(refKey);
        if (!obj || obj.schema_version !== project.schema_version || JSON.stringify(stable(obj)) !== JSON.stringify(stable(change))) {
          if (project.schema_version === 3) {
            if (!obj) researchSource.unmapped_records++;
            warn('schema 3 兼容受限：部分 changes 无法精确映射概览 revision，未伪造记录节点。'); continue;
          }
          assert(false, '事件 changes 无法精确对应概览 revision，请刷新。');
        }
        assert(!recordNodes.has(refKey), '同一 revision 被多个事件重复声明，请刷新。');
        if (!own(KINDS, obj.kind)) { warn('未知研究对象类型未纳入图。'); continue; }
        const data = obj.data, facts = [fact('对象类型', obj.kind), fact('对象 ID', obj.id), fact('revision', obj.rev)];
        function enumFact(label, value, choices) { if (choices.includes(value)) facts.push(fact(label, value)); }
        if (obj.kind === 'Problem') {
          enumFact('目标类型', data.target_type, ['construct', 'prove_or_disprove', 'exploratory', 'engineering_fixture']);
          enumFact('知识登记', data.knowledge_status, ['unverified_open', 'known_result', 'synthetic_fixture']);
        }
        if (obj.kind === 'Note') enumFact('笔记类型', data.note_type, ['argument', 'observation', 'computation', 'source', 'failed_attempt']);
        if (obj.kind === 'Obligation') enumFact('对象登记状态', data.state, ['open', 'candidate', 'closed', 'needs_recheck']);
        if (obj.kind === 'Evidence') enumFact('证据类型', data.evidence_type, ['proof_text', 'source_excerpt', 'computation', 'witness', 'figure']);
        if (obj.kind === 'Review') {
          enumFact('登记的审查方法', data.method, ['human', 'program', 'model']);
          enumFact('登记的审查 verdict（非本图判定）', data.verdict, ['pass', 'fail', 'inconclusive', 'error']);
        }
        const publicTitle = ['Problem', 'Note'].includes(obj.kind) && text(data.title) ? data.title : KINDS[obj.kind];
        const publicSummary = obj.kind === 'Note' ? (typeof data.summary === 'string' ? data.summary : data.statement)
          : ['Problem', 'Obligation'].includes(obj.kind) ? data.statement : obj.kind === 'Evidence' ? data.support_scope : '';
        const record = node({id: id('record', project.id, obj.id, obj.rev), parentId: eventNode.id, kind: 'record',
          title: excerpt(publicTitle, 120), subtitle: obj.id + '@' + obj.rev,
          status: 'recorded', statusLabel: '产物已登记', summary: excerpt(publicSummary), facts,
          ref: {id: obj.id, rev: obj.rev}, source: location(row, researchSource, event)});
        recordNodes.set(refKey, record); recordObjects.push(obj); produced.push({obj, record});
      }
      // Describe only products whose complete revision matched the snapshot above.
      // These are saved public artifacts, not an inferred account of model reasoning.
      if (produced.length === 1) {
        const {obj, record} = produced[0], action = obj.rev > 1 ? '修订' : '登记';
        eventNode.title = excerpt(action + KINDS[obj.kind] + (record.title === KINDS[obj.kind] ? '' : ' · ' + record.title), 160);
        eventNode.summary = actorLabel + '：' + action + ' ' + obj.id + '@' + obj.rev + '。' + excerpt(record.summary, 180) + ' 仅记录产物，不代表数学通过。';
      } else if (produced.length > 1) {
        const revised = produced.filter(({obj}) => obj.rev > 1).length;
        eventNode.title = (revised === produced.length ? '修订' : revised ? '登记/修订' : '登记') + ' ' + produced.length + ' 个研究产物';
        eventNode.summary = actorLabel + '：' + produced.slice(0, 3).map(({obj, record}) => KINDS[obj.kind] + ' · ' + excerpt(record.title, 60)).join('；')
          + (produced.length > 3 ? '；另 ' + (produced.length - 3) + ' 个（详见子节点）' : '') + '。仅记录产物，不代表数学通过。';
      } else {
        eventNode.summary = actorLabel + '：' + (event.changes.length ? '没有可精确映射的公开产物，兼容受限。' : RESEARCH_KINDS[event.kind] + '，未产生新 revision。');
      }
      if (produced.length !== event.changes.length) { eventNode.status = 'unknown'; eventNode.statusLabel = '产物映射不完整'; }
      eventNode.facts.push(fact('精确映射产物数', produced.length));
    }
    assert(previous === state.head_hash, '末尾研究 head 与快照不一致，请刷新。');
    for (const refKey of objects.keys()) if (!recordNodes.has(refKey)) researchSource.unmapped_records++;
    if (researchSource.unmapped_records) {
      graph.counts.omitted += researchSource.unmapped_records;
      warn('部分概览 revision 没有可精确映射的已识别事件，未将概览对象伪装为事件或产物。');
    }

    function linkRef(from, ref, relation) {
      if (ref === null || ref === undefined) return;
      if (!reference(ref) || !recordNodes.has(key(ref))) { warn('显式研究引用无法解析到已纳入的精确 revision，未推断或伪造目标。'); return; }
      edge(from, recordNodes.get(key(ref)).id, relation, RELATIONS[relation]);
    }
    for (const obj of recordObjects) {
      const from = recordNodes.get(key(obj)).id, data = obj.data;
      if (obj.kind === 'Problem') linkRef(from, data.parent, 'parent_problem');
      if (['Note', 'Obligation', 'Evidence'].includes(obj.kind)) linkRef(from, data.problem_ref, 'problem_ref');
      const lists = obj.kind === 'Note' ? ['uses', 'candidate_for', 'evidence'] : obj.kind === 'Obligation' ? ['uses', 'candidates', 'evidence'] : obj.kind === 'Review' ? ['evidence', 'dependencies'] : [];
      for (const relation of lists) {
        if (!Array.isArray(data[relation])) { warn('研究引用列表缺失或无效，兼容受限；未推断引用。'); continue; }
        for (const ref of data[relation]) linkRef(from, ref, relation);
      }
      if (obj.kind === 'Review') { linkRef(from, data.target, 'review_target'); linkRef(from, data.candidate, 'review_candidate'); }
      if (obj.kind === 'Evidence' && object(data.citation)) {
        if (!Array.isArray(data.citation.condition_map)) warn('来源条件引用列表无效，未推断引用。');
        else for (const condition of data.citation.condition_map) if (object(condition)) linkRef(from, condition.obligation, 'condition_gap');
      }
    }
    // overview.links contains explicit references too. Never infer edges from clocks.
    if (Array.isArray(view.links)) for (const link of view.links) {
      if (!object(link) || !['evidence', 'condition_gap'].includes(link.relation) || !reference(link.from) || !recordNodes.has(key(link.from))) {
        warn('概览中存在不支持或无法定位的显式引用，未纳入图。'); continue;
      }
      linkRef(recordNodes.get(key(link.from)).id, link.to, link.relation);
    }

    const toolGroups = [], originGroups = new Map();
    for (const descriptor of graph.sources) {
      if (descriptor === researchSource) continue;
      const rows = rowsByPath.get(descriptor.path);
      if (descriptor.adapter !== 'ledger-py-v1' || descriptor.integrity !== 'verified_chain' || !hash(descriptor.file_sha256)) { omit(descriptor, rows.length); continue; }
      const groups = new Map(), seqCounts = new Map(), runSessions = new Map();
      const ordered = rows.slice().sort((a, b) => (integer(a.seq) ? a.seq : Infinity) - (integer(b.seq) ? b.seq : Infinity));
      for (const row of ordered) seqCounts.set(row.seq, (seqCounts.get(row.seq) || 0) + 1);
      // A run/session drift makes every matching run row ambiguous, including earlier rows.
      for (const row of ordered) {
        const data = row.data;
        if (object(data) && data.project_id === project.id && identifier(data.run_id) && identifier(data.session_id)) {
          if (!runSessions.has(data.run_id)) runSessions.set(data.run_id, new Set());
          runSessions.get(data.run_id).add(data.session_id);
        }
      }
      for (const row of ordered) {
        const data = row.data;
        if (!object(data) || data.project_id !== project.id || !identifier(data.run_id) || !identifier(data.session_id) || runSessions.get(data.run_id)?.size !== 1) {
          omit(descriptor); warn('外部 ledger 的 project/session/run 缺失、不匹配或有歧义，相关事件未附着到项目。'); continue;
        }
        if (!integer(row.seq) || row.seq < 1 || row.seq !== data.seq || seqCounts.get(row.seq) !== 1 || !hash(row.hash) || row.hash !== data.sha256 || row.file_sha256 !== descriptor.file_sha256 || row.kind !== data.kind) {
          omit(descriptor); warn('外部 ledger 事件缺少唯一 seq/hash/文件 SHA 定位或字段不一致，未纳入图。'); continue;
        }
        if (!own(LEDGER_KINDS, row.kind)) { omit(descriptor); warn('外部 ledger 含未知或不支持的事件（包括 service.frame）；仅计入未纳入数量，不读取原文。'); continue; }
        const family = row.kind.startsWith('tool.') ? 'tool' : row.kind.startsWith('request.') ? 'request' : 'run';
        const value = family === 'tool' ? data.call_id : family === 'request' ? data.request_id : data.run_id;
        if (!identity(value)) { omit(descriptor); warn('请求或工具缺少有类型的有效 identity，未纳入图。'); continue; }
        const groupKey = id('operation', descriptor.path, data.run_id, data.session_id, family, typeof value, value);
        const facts = [fact('事件类型', row.kind), fact('run', data.run_id), fact('session', data.session_id), fact('原始序列', row.seq)];
        if (family !== 'run') facts.push(fact(family === 'tool' ? 'call_id' : 'request_id', typeof value + ':' + value));
        const eventNode = node({id: id('ledger-event', groupKey, row.seq), parentId: descriptor.id, kind: 'event', title: LEDGER_KINDS[row.kind], subtitle: '#' + row.seq,
          status: 'unknown', statusLabel: '结果未知', summary: '仅为历史记录，非实时状态；记录结束不等于数学通过。', facts, source: location(row, descriptor)});
        descriptor.events++;
        if (family === 'run') {
          if (row.kind !== 'opened') { eventNode.status = 'recorded'; eventNode.statusLabel = '停止/封存已记录'; }
          continue;
        }
        if (!groups.has(groupKey)) groups.set(groupKey, {family, value, run: data.run_id, session: data.session_id, entries: []});
        groups.get(groupKey).entries.push({node: eventNode, row, data});
        if (family === 'tool') {
          if (identifier(data.name)) { facts.push(fact('工具名称', data.name)); eventNode.title += ' · ' + data.name; }
          else if (row.kind === 'tool.started') warn('工具开始记录缺少有效名称，仅显示可定位的未知工具记录。');
          if (row.kind === 'tool.finished') {
            // Only read one known receipt status and classify the value's JS type.
            // Never recursively project result/value, request messages, or arguments.
            const receipt = data.result;
            if (object(receipt) && ['completed', 'error', 'unknown', 'started'].includes(receipt.status)) facts.push(fact('工具回执状态', receipt.status));
            const result = object(receipt) && own(receipt, 'value') ? receipt.value : receipt;
            facts.push(fact('结果类型（无原文）', result === null ? 'null' : Array.isArray(result) ? 'array' : typeof result));
          }
        }
      }
      for (const group of groups.values()) {
        const startKind = group.family === 'tool' ? 'tool.started' : 'request.reserved';
        const starts = group.entries.filter(entry => entry.row.kind === startKind);
        const ends = group.entries.filter(entry => ['tool.finished', 'request.finished', 'request.settled'].includes(entry.row.kind));
        const errors = group.entries.filter(entry => entry.row.kind === 'request.error');
        const names = group.family === 'tool' ? new Set(group.entries.filter(entry => identifier(entry.data.name)).map(entry => entry.data.name)) : new Set();
        const paired = starts.length === 1 && ends.length === 1 && starts[0].row.seq < ends[0].row.seq && names.size <= 1;
        if (names.size > 1) warn('同一工具 identity 的名称冲突，结果未知；未合并为成功调用。');
        if (paired) {
          if (group.family === 'tool' && identifier(starts[0].data.name) && !identifier(ends[0].data.name)) {
            ends[0].node.facts.push(fact('工具名称', starts[0].data.name));
            ends[0].node.title += ' · ' + starts[0].data.name;
          }
          const end = ends[0], result = end.data.result;
          const failed = errors.length > 0 || (group.family === 'tool' ? object(result) && result.status === 'error' : end.data.error !== null && end.data.error !== undefined);
          const known = group.family === 'tool' ? object(result) && result.status === 'completed' : end.data.complete !== false && (end.data.complete === undefined || end.data.complete === true);
          for (const entry of group.entries) {
            entry.node.status = failed ? 'error' : known ? 'recorded' : 'unknown';
            entry.node.statusLabel = failed ? '错误已记录（非数学否定）' : known ? '开始/结束已记录（非数学通过）' : '回执结果未知';
          }
          edge(starts[0].node.id, end.node.id, 'same_' + group.family, '同一有类型 identity 的开始/结束记录');
        } else {
          warn('部分工具/请求缺少唯一匹配的开始或结束记录；结果未知，不显示为实时运行或成功。');
        }
        if (group.family === 'tool') toolGroups.push(group);
      }
    }
    // Research origin explicitly names run/session/call; source is not guessed if ambiguous.
    for (const group of toolGroups) {
      const token = id('origin', group.run, group.session, typeof group.value, group.value);
      if (!originGroups.has(token)) originGroups.set(token, []);
      originGroups.get(token).push(group);
    }
    for (const {node: eventNode, origin} of researchOrigins) {
      if (!identifier(origin.run_id) || !identifier(origin.session_id) || !identity(origin.tool_call_id)) continue;
      const matches = originGroups.get(id('origin', origin.run_id, origin.session_id, typeof origin.tool_call_id, origin.tool_call_id)) || [];
      if (matches.length === 1) {
        const starts = matches[0].entries.filter(entry => entry.row.kind === 'tool.started');
        if (starts.length === 1) edge(eventNode.id, starts[0].node.id, 'origin_tool', '事件显式 origin 引用（非推测执行因果）');
      } else if (matches.length > 1) warn('研究 origin 在多个外部来源中命中相同工具 identity；未猜测来源关联。');
    }
    for (const descriptor of graph.sources) {
      descriptor.node.facts.push(fact('已纳入事件', descriptor.events), fact('未纳入事件', descriptor.omitted));
      if (descriptor.unmapped_records) descriptor.node.facts.push(fact('未映射 revision', descriptor.unmapped_records));
      delete descriptor.node;
    }
    return graph;
  }
  publish({build, serialize});
})(value => { if (typeof module !== 'undefined' && module.exports) module.exports = value; else globalThis.DshMathProcessGraph = value; });
