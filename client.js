(function (publish) {
  const lines = value => value.split('\n').map(v => v.trim()).filter(Boolean);
  const nullable = value => value.trim() || null;
  const refKey = r => `${r.id}@${r.rev}`;
  function choices(objects, kinds, problem, self) {
    return objects.filter(o => kinds.includes(o.kind) && o.id !== self && o.data.problem_ref && refKey(o.data.problem_ref) === refKey(problem));
  }
  function draft(kind, problem, uuid, object) {
    const data = object ? JSON.parse(JSON.stringify(object.data)) : kind === 'Note' ? {
      note_type:'observation',title:'',summary:'',body:'',statement:'',assumptions:[],gaps:[],problem_ref:problem,route_id:null,uses:[],candidate_for:[],evidence:[],failure:null,
    } : kind === 'Obligation' ? {
      statement:'',scope:'',completion_criteria:[],next_action:'',reason:'',check_method:'human',checker_id:null,problem_ref:problem,uses:[],candidates:[],evidence:[],state:'open',
    } : {
      evidence_type:'source_excerpt',problem_ref:problem,attachments:[],source:{locator:'',version:null,accessed_at:null,conditions:[]},source_status:'unverified_source',support_scope:'',limits:[],production:{method:'human registration',version:'1'},citation:null,
    };
    return {kind,id:object?.id || `${kind[0]}-${uuid()}`,expected_rev:object?.rev || 0,operation_id:uuid(),data,excerpt:null,files:[],logical_name:'excerpt.txt'};
  }
  function request(draft) {
    const d = JSON.parse(JSON.stringify(draft));
    if (d.kind !== 'Evidence') return {operation_id:d.operation_id,changes:[{kind:d.kind,id:d.id,expected_rev:d.expected_rev,data:d.data}]};
    return {operation_id:d.operation_id,id:d.id,expected_rev:d.expected_rev,data:d.data,excerpt:d.excerpt,logical_name:d.logical_name,files:d.files};
  }
  publish({lines,nullable,refKey,choices,draft,request});
})(value => { if (typeof module !== 'undefined') module.exports = value; else globalThis.DshMathForms = value; });

globalThis.DshMathFormUI = function (React, F) {
  const {createElement:h,useState,useEffect,useRef} = React;
  function Text({label,value,change,required=false,multi=true}) {const multiline=multi||/[\r\n]/.test(value||'');return h('label',{className:'dm-field'},label,h(multiline?'textarea':'input',{className:'dm-input',value:value??'',required,onChange:e=>change(e.target.value),rows:multiline?3:undefined}));}
  function Select({label,value,change,items}) {return h('label',{className:'dm-field'},label,h('select',{className:'dm-input',value,onChange:e=>change(e.target.value)},...items.map(([v,t])=>h('option',{key:v,value:v},t))));}
  function Refs({label,objects,kinds,problem,self,value,change,single=false,inspect}) {
    const [search,setSearch]=useState(''),[history,setHistory]=useState(false);
    const options=F.choices(objects,kinds,problem,self);
    const newest=new Map(); for(const o of options)newest.set(o.id,Math.max(o.rev,newest.get(o.id)||0));
    const shown=options.filter(o=>(history||o.rev===newest.get(o.id)||value.some(r=>F.refKey(r)===F.refKey(o)))&&`${o.id} ${o.data.title||o.data.statement||o.data.source?.version||''}`.toLowerCase().includes(search.toLowerCase()));
    return h('fieldset',{className:'dm-fieldset'},h('legend',null,label),
      h(Text,{label:'搜索 '+label,value:search,change:setSearch,multi:false}),h('label',{className:'dm-check-label'},h('input',{type:'checkbox',checked:history,onChange:e=>setHistory(e.target.checked)}),h('span',null,'显示历史版本（不会自动升级引用）')),
      h('div',{className:'dm-ref-options'},...shown.map(o=>{const selected=value.some(r=>F.refKey(r)===F.refKey(o));return h('div',{key:F.refKey(o),className:'dm-ref-option'},h('label',{className:'dm-check-label'},h('input',{type:single?'radio':'checkbox',name:label,checked:selected,onChange:()=>change(selected?value.filter(r=>F.refKey(r)!==F.refKey(o)):single?[{id:o.id,rev:o.rev}]:[...value,{id:o.id,rev:o.rev}])}),h('span',null,`${F.refKey(o)} · ${o.data.title||o.data.statement||o.data.source?.version||o.kind} ${o.rev<newest.get(o.id)?'（历史）':''}`)),inspect&&h('button',{type:'button',className:'dm-button dm-small',onClick:()=>inspect(o)},'查看'));})),
      !shown.length&&h('p',{className:'dm-empty'},'没有同目标版本的可选记录'),h('p',{className:'dm-record-text'},'已选：'+(value.map(F.refKey).join('、')||'无')),
      single&&value.length>0&&h('button',{type:'button',className:'dm-button dm-small',onClick:()=>change([])},'清除选择'));
  }
  function Editor({initial,objects,save,cancel,inspect,busy}) {
    const [draft,setDraft]=useState(initial),[error,setError]=useState(''),[uploading,setUploading]=useState(false);
    const heading=useRef(null);
    useEffect(()=>{heading.current?.focus();},[initial.operation_id]);
    const data=draft.data;
    const set=patch=>setDraft(prev=>({...prev,data:{...prev.data,...patch}}));
    const text=(key,label,required=true,multi=true)=>h(Text,{key,label,value:data[key],required,multi,change:v=>set({[key]:v})});
    const list=(key,label,required=false)=>h(Text,{key,label:label+'（每行一项）',value:data[key].join('\n'),required,change:v=>set({[key]:v.split('\n')})});
    const refs=(key,label,kinds)=>h(Refs,{key,label,objects,kinds,problem:data.problem_ref,self:draft.id,value:data[key],change:v=>set({[key]:v}),inspect});
    const source=(key,label,required=false)=>h(Text,{key,label,value:data.source[key],required,multi:false,change:v=>set({source:{...data.source,[key]:v||null}})});
    const citation=(key,label)=>h(Text,{key,label,value:data.citation?.[key],multi:false,change:v=>set({citation:{...data.citation,[key]:v||null}})});
    const conditions=data.citation?.condition_map||[];
    function condition(index,patch){const updated=conditions.map((c,i)=>i===index?{...c,...patch}:c);set({citation:{...data.citation,condition_map:updated},source:{...data.source,conditions:updated.map(c=>c.condition)}});}
    async function files(event){setUploading(true);setError('');try{const selected=Array.from(event.target.files);let total=0;const encoded=[];for(const file of selected){total+=file.size;if(total>384*1024)throw Error('附件总计最多384 KiB；大文件请先选取相关原文片段');const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);encoded.push({logical_name:file.name,base64:btoa(binary)});}setDraft(prev=>({...prev,files:encoded}));}catch(e){setError(e.message);}finally{setUploading(false);}}
    function submit(event){event.preventDefault();setError('');try{const ready=JSON.parse(JSON.stringify(draft));for(const key of ['assumptions','gaps','completion_criteria','limits'])if(Array.isArray(ready.data[key]))ready.data[key]=ready.data[key].map(x=>x.trim()).filter(Boolean);if(ready.kind==='Note')ready.data.route_id=ready.data.route_id?.trim()||null;if(ready.kind==='Obligation'){ready.data.state=ready.data.state==='candidate'?'candidate':'open';if(ready.data.check_method!=='human')throw Error('程序检查义务必须通过其专用入口修订');}if(ready.kind==='Evidence'&&ready.data.evidence_type==='source_excerpt'&&ready.data.source_status==='provided'&&!ready.excerpt?.trim()&&!ready.data.citation?.excerpt_sha256)throw Error('已提供来源必须粘贴可审核的原文摘录；PDF附件不能替代摘录');save(F.request(ready));}catch(e){setError(e.message);}}
    return h('form',{onSubmit:submit,className:'dm-editor','aria-label':'编辑研究记录','aria-busy':busy||uploading},h('h3',{ref:heading,tabIndex:-1,className:'dm-editor-title'},`${draft.expected_rev?'修订':'登记'} ${ {Note:'笔记',Obligation:'证明义务',Evidence:'证据'}[draft.kind]}`),h('p',{className:'dm-record-text'},`${draft.id} · 目标 ${F.refKey(data.problem_ref)} · 基于版本 ${draft.expected_rev}`),
      draft.kind==='Note'&&h(React.Fragment,null,h(Select,{label:'笔记类型',value:data.note_type,change:v=>set({note_type:v,failure:v==='failed_attempt'?(data.failure||{inputs:'',location:'',observed:'',negated_scope:'',retry_change:''}):null}),items:[['observation','观察'],['argument','论证'],['computation','计算记录'],['source','文献笔记'],['failed_attempt','失败尝试']]}),text('title','笔记标题',true,false),text('summary','简述'),text('body','完整正文'),text('statement','主张'),list('assumptions','假设'),list('gaps','尚存缺口'),text('route_id','路线标识',false,false),refs('uses','依赖前提',['Note','Obligation']),refs('candidate_for','拟解决的义务',['Obligation']),refs('evidence','支撑证据',['Evidence']),data.failure&&Object.entries({inputs:'失败输入',location:'失败位置',observed:'观察结果',negated_scope:'否定范围',retry_change:'再次尝试应改变什么'}).map(([key,label])=>h(Text,{key,label,value:data.failure[key],required:true,change:v=>set({failure:{...data.failure,[key]:v}})}))),
      draft.kind==='Obligation'&&h(React.Fragment,null,text('statement','义务陈述'),text('scope','适用范围'),list('completion_criteria','完成条件',true),text('next_action','下一步'),text('reason','登记原因'),h(Select,{label:'当前提交状态',value:data.state==='candidate'?'candidate':'open',change:v=>set({state:v}),items:[['open','开放'],['candidate','已有候选，等待审核']]}),h('p',null,'由人工审核；接受和待复核状态由系统派生。'),refs('uses','依赖前提',['Note','Obligation']),refs('candidates','候选笔记',['Note']),refs('evidence','支撑证据',['Evidence'])),
      draft.kind==='Evidence'&&h(React.Fragment,null,h(Select,{label:'证据类型',value:data.evidence_type,change:v=>set({evidence_type:v,citation:null}),items:[['source_excerpt','来源摘录'],['proof_text','证明原文'],['computation','计算记录'],['witness','有限见证'],['figure','示意图']]}),source('locator','来源地址或出处',true),source('version','版本 / commit'),source('accessed_at','访问日期'),text('support_scope','支持范围'),list('limits','局限与未核实项',true),h(Select,{label:'来源材料状态',value:data.source_status,change:v=>set({source_status:v}),items:[['unverified_source','待核实来源线索'],['provided','已归档材料（不代表审核通过）']]}),
        h(Text,{label:'生产或导入方式',value:data.production.method,required:true,change:v=>set({production:{...data.production,method:v}})}),h(Text,{label:'生产方式版本',value:data.production.version,required:true,multi:false,change:v=>set({production:{...data.production,version:v}})}),
        h(Text,{label:'原文摘录 / 证明全文',value:draft.excerpt||'',change:v=>setDraft(prev=>({...prev,excerpt:v||null}))}),h(Text,{label:'摘录附件名称',value:draft.logical_name,multi:false,required:true,change:v=>setDraft(prev=>({...prev,logical_name:v}))}),
        h('label',{className:'dm-field'},'附加文件（总计384 KiB，保留原始字节）',h('input',{type:'file',multiple:true,onChange:files})),h('p',{className:'dm-record-text'},draft.files.map(f=>f.logical_name).join('、')),
        data.evidence_type==='source_excerpt'&&h(React.Fragment,null,h('label',null,h('input',{type:'checkbox',checked:!!data.citation,onChange:e=>set({citation:e.target.checked?{title:'',page:null,section:null,theorem:null,excerpt_sha256:'0'.repeat(64),condition_map:[]}:null,source:{...data.source,conditions:[]}})}),' 添加精确定位与适用条件'),data.citation&&h(React.Fragment,null,citation('title','文献标题'),citation('page','页码'),citation('section','章节'),citation('theorem','命题 / 定理 / 开放问题编号'),...conditions.map((c,i)=>h('fieldset',{key:i,className:'dm-fieldset'},h(Text,{label:`条件 ${i+1}`,value:c.condition,required:true,change:v=>condition(i,{condition:v})}),h(Select,{label:`条件 ${i+1} 的适用判断`,value:c.assessment,change:v=>condition(i,{assessment:v}),items:[['unresolved','未解决'],['not_satisfied','不满足'],['claimed_satisfied','提交者声称满足（仍需审核）']]}),h(Text,{label:`条件 ${i+1} 的解释`,value:c.explanation,required:true,change:v=>condition(i,{explanation:v})}),h(Refs,{label:`条件 ${i+1} 对应的义务`,objects,kinds:['Obligation'],problem:data.problem_ref,value:c.obligation?[c.obligation]:[],single:true,change:v=>condition(i,{obligation:v[0]||null}),inspect}),h('button',{type:'button',className:'dm-button dm-small',onClick:()=>{const updated=conditions.filter((_,j)=>j!==i);set({citation:{...data.citation,condition_map:updated},source:{...data.source,conditions:updated.map(x=>x.condition)}});}},'移除此条件'))),h('button',{type:'button',className:'dm-button dm-small',onClick:()=>{const updated=[...conditions,{condition:'',assessment:'unresolved',explanation:'',obligation:null}];set({citation:{...data.citation,condition_map:updated},source:{...data.source,conditions:updated.map(c=>c.condition)}});}},'添加适用条件')))),
      error&&h('p',{role:'alert',className:'dm-error'},error),h('div',{className:'dm-actions'},h('button',{type:'submit',className:'dm-button dm-primary',disabled:busy||uploading},busy?'保存中':uploading?'读取附件中…':'保存记录'),h('button',{type:'button',className:'dm-button',onClick:cancel,disabled:busy},'取消')));
  }
  return {Editor,Refs,Text,Select};
};

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

globalThis.DshMathTheme = `
.dsh-math-view, .dm-dialog, .dm-button {
  --dm-bg: var(--dsw-alias-bg-base);
  --dm-panel: var(--dsw-alias-bg-layer-1);
  --dm-text: var(--dsw-alias-label-primary);
  --dm-muted: var(--dsw-alias-label-secondary);
  --dm-border: var(--dsw-alias-border-l1);
  --dm-soft: var(--dsw-alias-bg-layer-2);
  --dm-accent: var(--dsw-alias-brand-primary);
  --dm-green: var(--dsw-alias-state-success-primary);
  --dm-red: var(--dsw-alias-state-error-primary);
}
.dsh-math-view {
  container: dsh-math / inline-size;
  width: 100%; min-width: 0; height: 100%; overflow: auto; box-sizing: border-box; padding: 18px;
  background: var(--dm-bg); color: var(--dm-text);
  font: 13px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.dsh-math-view *, .dsh-math-view *::before, .dsh-math-view *::after { box-sizing: border-box; }
.dsh-math-view.dm-sidebar { padding: 14px; }
.dsh-math-view button, .dsh-math-view input, .dsh-math-view textarea, .dsh-math-view select { font: inherit; }
.dsh-math-view button { cursor: pointer; }
.dsh-math-view button:disabled, .dsh-math-view select:disabled { cursor: not-allowed; opacity: .48; }
.dsh-math-view button:focus-visible, .dsh-math-view input:focus-visible,
.dsh-math-view textarea:focus-visible, .dsh-math-view select:focus-visible, .dsh-math-view summary:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--dm-accent) 38%, transparent); outline-offset: 2px;
}
.dm-header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding-bottom: 12px; margin-bottom: 12px; border-bottom: 1px solid var(--dm-border); }
.dm-header > * { min-width: 0; }
.dm-title { display: flex; align-items: center; gap: 7px; margin: 0; font-size: 14px; font-weight: 650; line-height: 1.4; }
.dm-title > .dm-icon { color: var(--dm-muted); }
.dm-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.dm-header .dm-actions { gap: 3px; }
.dm-button {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  max-width: 100%; min-height: 30px; padding: 5px 10px; border: 1px solid var(--dm-border);
  border-radius: 6px; background: var(--dm-panel); color: var(--dm-text); overflow-wrap: anywhere;
  transition: border-color .15s, background .15s, color .15s;
}
.dm-button:hover:not(:disabled) { border-color: color-mix(in srgb, var(--dm-accent) 40%, var(--dm-border)); background: var(--dm-soft); }
.dm-button.dm-primary { border-color: color-mix(in srgb, var(--dm-accent) 40%, var(--dm-border)); background: color-mix(in srgb, var(--dm-accent) 12%, var(--dm-panel)); color: var(--dm-accent); }
.dm-button.dm-primary:hover:not(:disabled) { background: color-mix(in srgb, var(--dm-accent) 20%, var(--dm-panel)); }
.dm-button.dm-ghost { border-color: transparent; background: transparent; color: var(--dm-muted); }
.dm-button.dm-small { min-height: 28px; padding: 3px 7px; font-size: 12px; }
.dm-icon { width: 14px; height: 14px; flex: none; }
.dm-panel { min-width: 0; margin-bottom: 12px; padding: 12px; border: 1px solid var(--dm-border); border-radius: 8px; background: var(--dm-panel); }
.dm-panel-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.dm-panel-title { min-width: 0; margin: 0; font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
.dm-panel-meta { color: var(--dm-muted); font-size: 11px; }
.dm-project-picker { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; margin-bottom: 12px; }
.dm-project-picker > .dm-panel-meta { white-space: nowrap; }
.dm-input, .dsh-math-view input, .dsh-math-view textarea, .dsh-math-view select {
  width: 100%; min-width: 0; max-width: 100%; border: 1px solid var(--dm-border); border-radius: 6px;
  background: var(--dm-bg); color: var(--dm-text); padding: 7px 9px;
}
.dsh-math-view input[type="checkbox"], .dsh-math-view input[type="radio"] { width: auto; accent-color: var(--dm-accent); }
.dsh-math-view textarea { resize: vertical; line-height: 1.5; }
.dm-field { display: block; min-width: 0; margin: 10px 0; color: var(--dm-muted); font-size: 12px; overflow-wrap: anywhere; }
.dm-field > input, .dm-field > textarea, .dm-field > select { display: block; margin-top: 5px; color: var(--dm-text); font-size: 13px; }
.dm-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 10px; border: 1px solid var(--dm-border); border-radius: 7px; background: var(--dm-panel); }
.dm-stat { display: flex; align-items: baseline; justify-content: center; flex-wrap: wrap; gap: 3px 6px; min-width: 0; padding: 7px 5px; }
.dm-stat + .dm-stat { border-left: 1px solid var(--dm-border); }
.dm-stat-value { font-size: 14px; font-weight: 650; line-height: 1.3; }
.dm-stat-label { color: var(--dm-muted); font-size: 11px; }
.dm-tabs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 0 0 14px; border-bottom: 1px solid var(--dm-border); }
.dm-tab { position: relative; min-width: 0; padding: 7px 0 9px; border: 0; background: transparent; color: var(--dm-muted); }
.dm-tab[data-active="true"] { color: var(--dm-accent); font-weight: 600; }
.dm-tab[data-active="true"]::after { content: ""; position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; border-radius: 2px; background: var(--dm-accent); }
.dm-record { padding: 10px 0; border-top: 1px solid var(--dm-border); }
.dm-record:first-of-type { border-top: 0; }
.dsh-math-view .dm-record-title { display: block; width: 100%; min-width: 0; padding: 0; border: 0; background: transparent; color: var(--dm-text); text-align: left; font-weight: 600; overflow-wrap: anywhere; }
.dm-record-title:hover { color: var(--dm-accent); }
.dm-record-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 5px; }
.dm-record-text { margin: 5px 0 0; color: var(--dm-muted); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-ref { display: inline-flex; min-width: 0; max-width: 100%; padding: 1px 5px; border: 0; border-radius: 4px; background: color-mix(in srgb, var(--dm-accent) 6%, transparent); color: var(--dm-muted); font: 10px/1.6 ui-monospace, monospace; text-align: left; white-space: normal; overflow-wrap: anywhere; }
.dm-ref:hover { color: var(--dm-accent); }
.dm-badge { display: inline-flex; align-items: center; padding: 1px 6px; border-radius: 999px; background: var(--dm-soft); color: var(--dm-muted); font-size: 10px; white-space: nowrap; }
.dm-badge.dm-success { background: color-mix(in srgb, var(--dm-green) 11%, transparent); color: var(--dm-green); }
.dm-empty { margin: 7px 0 2px; color: var(--dm-muted); font-size: 12px; }
.dm-error { margin: 0 0 12px; padding: 9px 11px; border: 1px solid color-mix(in srgb, var(--dm-red) 28%, var(--dm-border)); border-radius: 8px; background: color-mix(in srgb, var(--dm-red) 7%, var(--dm-panel)); color: var(--dm-red); overflow-wrap: anywhere; }
.dm-operation-status { margin: 0 0 12px; color: var(--dm-muted); font-size: 12px; }
.dm-entry-error { max-width: 260px; color: var(--dsw-alias-state-error-primary); font-size: 12px; overflow-wrap: anywhere; }
.dm-code { max-height: 460px; overflow: auto; padding: 10px; border: 1px solid var(--dm-border); border-radius: 7px; background: var(--dm-bg); font: 11px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-details { margin: 10px 0; }
.dm-details > summary { cursor: pointer; color: var(--dm-muted); font-size: 12px; }
.dm-import { margin-top: 16px; padding: 12px 0 0; border-top: 1px solid var(--dm-border); }
.dm-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 10px; }
.dm-form-grid .dm-wide { grid-column: 1 / -1; }
.dm-sidebar .dm-form-grid { grid-template-columns: 1fr; }
.dm-editor { min-width: 0; scroll-margin-top: 12px; }
.dm-editor-title { margin: 0 0 6px; font-size: 14px; }
.dm-editor .dm-actions { margin-top: 14px; }
.dm-fieldset { min-inline-size: 0; margin: 12px 0; padding: 10px; border: 1px solid var(--dm-border); border-radius: 8px; }
.dm-fieldset > legend { max-width: 100%; padding: 0 4px; font-size: 12px; overflow-wrap: anywhere; }
.dm-ref-options { max-height: 220px; overflow: auto; margin-top: 8px; }
.dm-ref-option { display: flex; align-items: flex-start; gap: 8px; padding: 7px 0; }
.dm-ref-option > label { flex: 1; }
.dm-check-label { display: flex; min-width: 0; align-items: flex-start; gap: 6px; font-size: 12px; }
.dm-check-label > input { flex: none; margin-top: 4px; }
.dm-check-label > span { min-width: 0; overflow-wrap: anywhere; }
.dm-welcome { max-width: 460px; margin: 0 auto; padding: 24px 8px 12px; text-align: center; }
.dm-welcome-mark { display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px; border: 1px solid var(--dm-border); border-radius: 12px; background: var(--dm-panel); color: var(--dm-accent); }
.dm-welcome-mark .dm-icon { width: 24px; height: 24px; }
.dm-welcome h3 { margin: 14px 0 8px; font-size: 18px; font-weight: 650; letter-spacing: -.02em; }
.dm-welcome-text { max-width: 36em; margin: 0 auto 18px; font-size: 12px; line-height: 1.8; color: var(--dm-muted); }
.dm-start-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; list-style: none; padding: 0; margin: 26px 0 18px; text-align: left; }
.dm-start-steps li { display: flex; align-items: flex-start; gap: 7px; min-width: 0; }
.dm-step-number { flex: none; color: var(--dm-muted); font-size: 11px; line-height: 20px; }
.dm-start-steps strong, .dm-start-steps li div > span { display: block; }
.dm-start-steps strong { font-size: 12px; font-weight: 500; }
.dm-start-steps li div > span { margin-top: 3px; font-size: 11px; color: var(--dm-muted); }
.dm-local-hint { margin: 12px 0 0; color: var(--dm-muted); font-size: 11px; line-height: 1.6; }
.dm-goal { min-width: 0; margin-bottom: 16px; padding: 14px; border: 1px solid var(--dm-border); border-radius: 8px; background: var(--dm-panel); }
.dm-section-label { display: flex; justify-content: space-between; gap: 8px; color: var(--dm-muted); font-size: 11px; }
.dm-goal-title { margin: 9px 0 6px; font-size: 16px; line-height: 1.5; overflow-wrap: anywhere; }
.dm-goal-statement { margin: 0 0 9px; font-size: 13px; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-next-actions { margin-top: 16px; }
.dm-dialog { position: fixed; inset: 28px; z-index: 1000; overflow: hidden; border: 1px solid var(--dm-border); border-radius: 14px; box-shadow: 0 18px 60px color-mix(in srgb, var(--dm-text) 20%, transparent); }
.dm-dialog > .dsh-math-view { height: 100%; }
.dm-sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
.dm-graph-tools { margin-bottom: 8px; }
.dm-process-graph { margin-top: 14px; min-width: 0; }
.dm-graph-caption { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.dm-graph-caption .dm-button { text-decoration: none; }
.dm-graph-notice { margin: 10px 0; }
.dm-graph-warnings { padding: 8px 10px; border: 1px solid var(--dm-border); border-radius: 6px; color: var(--dm-muted); font-size: 11px; }
.dm-graph-warnings ul { padding-left: 18px; overflow-wrap: anywhere; }
.dm-graph-board { max-height: 70vh; min-height: 160px; margin: 14px 0; padding: 14px 10px; overflow: auto; border: 1px solid var(--dm-border); border-radius: 8px; background: var(--dm-bg); }
.dm-graph-roots, .dm-graph-sources, .dm-graph-children { list-style: none; padding: 0; margin: 0; }
.dm-graph-branch { position: relative; min-width: 0; }
.dm-graph-node-wrap { min-width: 0; position: relative; }
.dm-graph-node { display: block; width: 100%; padding: 0; text-align: left; color: var(--dm-text); background: var(--dm-panel); border: 1px solid var(--dm-border); border-radius: 7px; overflow: hidden; }
.dm-graph-node:hover { border-color: color-mix(in srgb, var(--dm-accent) 50%, var(--dm-border)); }
.dm-graph-node[data-selected="true"] { border-color: var(--dm-accent); box-shadow: 0 0 0 1px color-mix(in srgb, var(--dm-accent) 35%, transparent); }
.dm-graph-project > .dm-graph-node-wrap { max-width: 340px; margin: 0 auto; }
.dm-graph-project > .dm-graph-node-wrap > .dm-graph-node { background: color-mix(in srgb, var(--dm-accent) 7%, var(--dm-panel)); }
.dm-graph-kicker { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px; padding: 9px 11px 0; color: var(--dm-muted); font-size: 10px; }
.dm-graph-kicker > span { overflow-wrap: anywhere; }
.dm-graph-label { display: block; padding: 5px 11px 8px; font-size: 12px; line-height: 1.5; font-weight: 550; overflow-wrap: anywhere; }
.dm-graph-summary { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; padding: 0 11px; margin-bottom: 9px; color: var(--dm-muted); font-size: 11px; overflow-wrap: anywhere; }
.dm-graph-status { display: flex; align-items: center; gap: 6px; padding: 5px 11px; border-top: 1px solid var(--dm-border); background: var(--dm-bg); color: var(--dm-muted); font-size: 10px; }
.dm-graph-dot { width: 5px; height: 5px; flex: none; border-radius: 50%; background: var(--dm-muted); }
.dm-graph-node[data-status="error"] .dm-graph-dot { background: var(--dm-red); }
.dm-graph-node[data-status="unknown"] .dm-graph-dot { background: var(--dsw-alias-state-warn-primary); }
.dm-graph-toggle { margin-top: 3px; font-size: 10px; }
.dm-graph-sources { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 250px), 1fr)); gap: 24px; margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--dsw-alias-border-l2); position: relative; }
.dm-graph-sources::before { content: ""; position: absolute; left: 50%; top: -24px; height: 24px; border-left: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-source::before { content: ""; position: absolute; top: -20px; left: 50%; height: 20px; border-left: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-children { padding: 2px 0 0 15px; margin: 0 0 0 10px; border-left: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-children > .dm-graph-branch { margin-top: 12px; }
.dm-graph-children > .dm-graph-branch::before { content: ""; position: absolute; left: -16px; top: 24px; width: 15px; border-top: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-record .dm-graph-label { font-size: 11px; }
.dm-graph-more { margin-top: 10px; }
.dm-graph-detail { padding: 12px; margin: 12px 0; border: 1px solid color-mix(in srgb, var(--dm-accent) 40%, var(--dm-border)); border-radius: 8px; background: var(--dm-panel); }
.dm-graph-detail h3:focus { outline: none; }
.dm-graph-facts { margin: 10px 0; font-size: 11px; }
.dm-graph-facts > div { display: grid; grid-template-columns: minmax(65px, 1fr) minmax(0, 3fr); gap: 8px; padding: 4px 0; }
.dm-graph-facts dt { color: var(--dm-muted); }
.dm-graph-facts dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-graph-relations { border-top: 1px solid var(--dm-border); margin-top: 10px; padding-top: 6px; }
@container dsh-math (max-width: 520px) {
  .dm-form-grid { grid-template-columns: 1fr; }
  .dm-tab { font-size: 12px; }
}
@container dsh-math (max-width: 380px) {
  .dm-tabs { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 3px 12px; }
  .dm-graph-board { padding: 10px 5px; }
  .dm-graph-children { padding-left: 10px; margin-left: 5px; }
  .dm-graph-children > .dm-graph-branch::before { left: -11px; width: 10px; }
  .dm-start-steps { grid-template-columns: 1fr; gap: 12px; max-width: 230px; margin-left: auto; margin-right: auto; }
  .dm-start-steps li > div { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; }
  .dm-start-steps li div > span { margin-top: 0; }
  .dm-stat { flex-direction: column; align-items: center; }
  .dm-welcome { padding-top: 16px; }
}
@media (max-width: 680px) {
  .dsh-math-view { padding: 12px; }
  .dm-dialog { inset: 8px; }
}
`;

window.__ModuleLoader__.load({id: 'dsh-math', factory: require => {
  const React = require('react');
  const {createElement: h, useState, useEffect, useRef} = React;
  const pretty = value => JSON.stringify(value, null, 2);
  const F = globalThis.DshMathForms;
  const {Editor, Refs, Select} = globalThis.DshMathFormUI(React, F);
  const Graph = globalThis.DshMathProcessGraph;
  const ProcessGraph = globalThis.DshMathGraphUI(React, Graph);
  const TAB_ID = 'dsh-math:projects';
  async function request(action, args = {}) {
    const response = await fetch('/dsh-math/api', {method: 'POST', headers: {'content-type': 'application/json', 'x-dsh-math': 'human-ui-v1'}, body: JSON.stringify({action, args})});
    const result = await response.json();
    if (!result.ok) throw Error(result.error);
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
    const [form, setForm] = useState({title: '', statement: '', quantifiers: '', domain: '', conventions: '', completion: '', sources: '', target: 'prove_or_disprove'});
    const [focus, setFocus] = useState(null), [attachment, setAttachment] = useState(null), [legacyPath, setLegacyPath] = useState(''), [editor, setEditor] = useState(null);
    const [sources, setSources] = useState([]), [candidate, setCandidate] = useState([]), [replay, setReplay] = useState(null), [preview, setPreview] = useState(null), [verdict, setVerdict] = useState(''), [findings, setFindings] = useState(''), [receipt, setReceipt] = useState(null);
    const [process, setProcess] = useState(null);
    const lifetime = useRef({active: true, serial: 0, loading: 0, selected: '', pending: false});
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
    const update = key => value => setForm({...form, [key]: value});
    const lines = value => value.split('\n').filter(v => v.trim());
    const ref = obj => ({id: obj.id, rev: obj.rev});
    const link = value => h('button', {className: 'dm-ref', type: 'button', onClick: () => inspect(view.objects.find(o => o.id === value.id && o.rev === value.rev))}, `${value.id}@${value.rev}`);
    function edit(kind, object) {
      setEditor(F.draft(kind, ref(view.groups.targets[0].object), () => crypto.randomUUID(), object));
      setPreview(null); setTab('records');
    }
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
          (projects.length > 0 || creating) && h(Button, {variant: 'ghost', size: 'small', onClick: () => setCreating(!creating)}, creating ? '取消新建' : '新建项目'),
          h(Button, {variant: 'ghost', size: 'small', onClick: () => run(() => load()), disabled: busy}, '刷新'),
          openView && h(Button, {variant: 'ghost', size: 'small', onClick: () => openView('trajectory', '')}, '原生轨迹'),
          onClose && h(Button, {variant: 'ghost', size: 'small', onClick: onClose}, '关闭'))),
      projects.length > 0 && h('div', {className: 'dm-project-picker'},
        h('select', {className: 'dm-input', value: selected, disabled: busy, onChange: e => run(() => load(e.target.value)), 'aria-label': '选择研究项目'},
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
      creating && h(Panel, {title: '精确研究目标', meta: '创建过程不调用模型'}, h('form', {onSubmit: e => { e.preventDefault(); run(async () => {
        const result = await api('create', {session_id: sessionId, problem: {title: form.title, statement: form.statement, quantifiers: form.quantifiers, domain: form.domain, conventions: lines(form.conventions), target_type: form.target, parent: null, relation: '', completion_criteria: lines(form.completion), sources: lines(form.sources), knowledge_status: 'unverified_open'}});
        setCreating(false); await load(result.id);
      }); }},
        h('div', {className: 'dm-form-grid'}, ...[
          ['title', '题目', false], ['domain', '定义域', false], ['statement', '精确陈述', true], ['quantifiers', '量词', false],
          ['conventions', '假设与约定（每行一项）', true], ['completion', '完成条件（每行一项）', true], ['sources', '参考材料（每行一项）', true],
        ].map(([key, label, wide]) => h(Field, {key, label, wide, multiline: wide, value: form[key], change: update(key)}))),
        h('label', {className: 'dm-field'}, '任务类型', h('select', {className: 'dm-input', value: form.target, onChange: e => update('target')(e.target.value)}, ...['construct', 'prove_or_disprove', 'exploratory'].map(v => h('option', {key: v}, v)))),
        h(Button, {type: 'submit', variant: 'primary', disabled: busy}, '创建并关联当前会话'))),
      view && h(React.Fragment, null,
        h('div', {className: 'dm-stats'}, ...stats.map(([label, value]) => h('div', {className: 'dm-stat', key: label}, h('span', {className: 'dm-stat-value'}, value), h('span', {className: 'dm-stat-label'}, label)))),
        h('nav', {className: 'dm-tabs', 'aria-label': '研究工作台视图'}, ...[['overview', '研究概览'], ['process', '研究过程'], ['records', '记录与审核'], ['replay', '回放与导出']].map(([id, label]) => h('button', {key: id, className: 'dm-tab', 'data-active': tab === id, onClick: () => changeTab(id)}, label))),
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
            h('div', {className: 'dm-actions dm-next-actions'},
              h(Button, {variant: 'primary', onClick: () => edit('Note')}, '登记笔记'), h(Button, {onClick: () => edit('Obligation')}, '登记义务'), h(Button, {onClick: () => edit('Evidence')}, '登记证据')),
            h('p', {className: 'dm-local-hint'}, '登记的是研究记录，不是证明。程序检查不替代人工审核。')),
          groupPanels(3),
          h('details', {className: 'dm-details dm-project-details'}, h('summary', null, '项目与关联会话'),
            h('p', {className: 'dm-record-text'}, `campaign ${view.project.campaign_id}`),
            h('p', {className: 'dm-record-text'}, '开放性与数学结论不由软件自动判定；当前状态由版本化记录推导。'),
            navigateSession && h(Button, {onClick: () => navigateSession(view.project.session_id)}, '进入关联普通会话'))),
        tab === 'records' && h(React.Fragment, null,
          h(Panel, {title: '研究记录', meta: '点击版本号查看详情'}, h('div', {className: 'dm-actions'}, h(Button, {onClick: () => edit('Note')}, '笔记'), h(Button, {onClick: () => edit('Obligation')}, '义务'), h(Button, {onClick: () => edit('Evidence')}, '证据'))),
          editor && h(Panel, {title: '编辑研究记录'}, h(Editor, {key: editor.operation_id, initial: editor, objects: view.objects, busy, inspect, cancel: () => setEditor(null), save: request => run(async () => { await api(editor.kind === 'Evidence' ? 'evidence' : 'commit', {project_id: selected, request}); setEditor(null); await load(); })})),
          groupPanels(),
          h(Panel, {title: '证据与版本', meta: '并列保留'},
            ...view.objects.filter(o => o.kind === 'Evidence').map(o => h('div', {className: 'dm-record', key: `${o.id}@${o.rev}`}, link(ref(o)), h('span', {className: 'dm-record-text'}, ` ${o.data.source.version || '未知版本'} · ${o.data.source_status}`))),
            !view.objects.some(o => o.kind === 'Evidence') && h('p', {className: 'dm-empty'}, '暂无证据')),
          focus && h(Panel, {title: `${focus.id}@${focus.rev}`, meta: focus.kind},
            h('pre', {className: 'dm-code'}, pretty(focus)),
            h('p', {className: 'dm-record-text'}, '关联记录（双向）'),
            ...view.links.filter(l => [l.from, l.to].some(v => v.id === focus.id && v.rev === focus.rev)).map((l, i) => h('div', {className: 'dm-record', key: i}, link(l.from), ` ${l.relation} → `, link(l.to))),
            h('div', {className: 'dm-actions'},
              ['Note', 'Obligation', 'Evidence'].includes(focus.kind) && view.project.schema_version === 4 && h(Button, {onClick: () => edit(focus.kind, focus)}, '修订此记录'),
              ['Note', 'Obligation'].includes(focus.kind) && h(Button, {onClick: () => run(async () => { setPreview(await api('review-preview', {project_id: selected, request: {target: ref(focus), candidate: candidate[0] || null}})); setVerdict(''); setFindings(''); })}, '人工审核预览'),
              h(Button, {variant: 'ghost', onClick: () => { setFocus(null); setAttachment(null); }}, '关闭详情')),
            focus.kind === 'Obligation' && h(Refs, {label: '审核所选候选', objects: view.objects.filter(o => focus.data.candidates.some(r => F.refKey(r) === F.refKey(o))), kinds: ['Note'], problem: focus.data.problem_ref, value: candidate, single: true, change: v => { setCandidate(v); setPreview(null); }, inspect}),
            focus.kind === 'Evidence' && focus.data.attachments.map(a => h(Button, {key: a.sha256, onClick: () => run(async () => setAttachment(await api('attachment', {project_id: selected, object_id: focus.id, rev: focus.rev, sha256: a.sha256})))}, `查看原文 ${a.logical_name}`)),
            attachment && h('pre', {className: 'dm-code'}, attachment.text === null ? pretty(attachment) : attachment.text)),
          preview && h(Panel, {title: '人工审核预览', meta: preview.eligible_for_pass ? '满足提交条件' : '仍需补充'},
            h('pre', {className: 'dm-code'}, pretty(preview)),
            h('label', {className: 'dm-field'}, '人工审核结论', h('select', {className: 'dm-input', value: verdict, onChange: e => setVerdict(e.target.value)}, h('option', {value: ''}, '由本人选择结论'), ...['pass', 'fail', 'inconclusive'].map(v => h('option', {key: v, value: v, disabled: v === 'pass' && !preview.eligible_for_pass}, v)))),
            h(Field, {label: '本人审核发现（每行一项）', value: findings, change: setFindings}),
            h(Button, {variant: 'primary', disabled: busy || !verdict || !findings.trim(), onClick: () => run(async () => { await api('review', {project_id: selected, request: {operation_id: crypto.randomUUID(), review_id: 'R-' + crypto.randomUUID(), target: preview.basis.target, candidate: preview.basis.candidate, verdict, scope: preview.required_scope, findings: lines(findings), basis_hash: preview.basis_hash}}); setPreview(null); await load(); })}, '本人提交审核记录'))),
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
      h('details', {className: 'dm-details dm-import'}, h('summary', null, '导入旧研究库（只读副本）'),
        h('p', {className: 'dm-record-text'}, '仅适用于已有 schema 3 研究库；导入不会改写原始数据。'),
        h(Field, {label: 'schema 3 研究库完整路径', value: legacyPath, multiline: false, change: setLegacyPath}),
        h(Button, {disabled: busy || !legacyPath.trim(), onClick: () => run(async () => { const p = await api('import-legacy', {session_id: sessionId, campaign: legacyPath}); await load(p.id); })}, '导入只读副本')));
  }
  function Entry({sessionId, navigateSession, openSidebar}) {
    const [opened, setOpened] = useState(false), [error, setError] = useState('');
    const navigate = id => { setOpened(false); navigateSession(id); };
    const open = () => {
      setError('');
      try { if (!openSidebar?.(sessionId)) setOpened(true); }
      catch (failure) { setError(failure.message); }
    };
    return h(React.Fragment, null,
      h(Button, {icon: true, onClick: open}, '数学项目'),
      error && h('span', {className: 'dm-entry-error', role: 'alert'}, error),
      opened && h('div', {role: 'dialog', 'aria-label': '数学研究工作台', className: 'dm-dialog'}, h(MathView, {sessionId, navigateSession: navigate, onClose: () => setOpened(false)})));
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
