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
  // The descriptor is supplied by the build; no second field list or hidden completion.
  function projectDefaults(descriptor) {
    return Object.fromEntries(descriptor.fields.map(field => [field.key,
      Object.hasOwn(field, 'default') ? field.default : field.type === 'string[]' ? [] : '']));
  }
  function projectPatch(descriptor, values) {
    return Object.fromEntries(descriptor.fields.map(field => [field.key, field.type === 'string[]'
      ? (values[field.key] || []).filter(value => value.trim()) : values[field.key] ?? '']));
  }
  function projectValidation(descriptor, values) {
    const missing_fields = [], errors = [];
    for (const field of descriptor.fields) {
      const value = values[field.key];
      const missing = field.type === 'string[]' ? !Array.isArray(value) || !value.some(v => typeof v === 'string' && v.trim()) : typeof value !== 'string' || !value.trim();
      if (field.required && missing) { missing_fields.push(field.key); errors.push({field: field.key, code: 'REQUIRED', message: `请填写${field.label}`}); }
      const options = field.key === 'target_type' ? descriptor.target_types : field.key === 'knowledge_status' ? descriptor.knowledge_statuses : null;
      if (!missing && options && !options.some(option => option.value === value)) errors.push({field: field.key, code: 'INVALID_ENUM', message: `请选择有效的${field.label}`});
    }
    return {complete: !errors.length, missing_fields, errors, questions: []};
  }
  function writable(view) {
    // Fail closed for incomplete reads, metadata-only upgrades, and legacy imports.
    return view?.project?.schema_version === 4 && view.project.mode === 'daily_research' &&
      view.snapshot?.usable === true && !!view.objects?.some(object => object.kind === 'Problem') && view.objects.every(object => object.schema_version === 4);
  }
  function promoteDraft(kind, problem, uuid, scratchpad) {
    if (!['Note', 'Obligation'].includes(kind) || !scratchpad?.rev) throw Error('请先保存草稿本，再整理为笔记或义务');
    const initial = draft(kind, problem, uuid);
    if (kind === 'Note') Object.assign(initial.data, {title: scratchpad.title, body: scratchpad.body});
    else initial.data.statement = scratchpad.body;
    // A source is not evidence of a claim. Required formal fields remain for the user.
    initial.promotion = {project_id: scratchpad.project_id, scratchpad_rev: scratchpad.rev, operation_id: uuid()};
    return initial;
  }
  function recordErrors(value) {
    const data = value.data, errors = [];
    const text = (value, label) => { if (typeof value !== 'string' || !value.trim()) errors.push(`请填写${label}`); };
    const list = (value, label) => { if (!Array.isArray(value) || !value.some(item => typeof item === 'string' && item.trim())) errors.push(`请填写${label}`); };
    if (value.kind === 'Note') {
      for (const [key, label] of [['title', '笔记标题'], ['summary', '简述'], ['body', '完整正文'], ['statement', '主张']]) text(data[key], label);
      if (data.note_type === 'failed_attempt') for (const [key, label] of [['inputs', '失败输入'], ['location', '失败位置'], ['observed', '观察结果'], ['negated_scope', '否定范围'], ['retry_change', '再次尝试应改变什么']]) text(data.failure?.[key], label);
    } else if (value.kind === 'Obligation') {
      for (const [key, label] of [['statement', '义务陈述'], ['scope', '适用范围'], ['next_action', '下一步'], ['reason', '登记原因']]) text(data[key], label);
      list(data.completion_criteria, '完成条件');
    } else if (value.kind === 'Evidence') {
      text(data.source?.locator, '来源地址或出处'); text(data.support_scope, '支持范围'); list(data.limits, '局限与未核实项');
      text(data.production?.method, '生产或导入方式'); text(data.production?.version, '生产方式版本');
      for (const condition of data.citation?.condition_map || []) { text(condition.condition, '适用条件'); text(condition.explanation, '条件解释'); }
    }
    return errors;
  }
  function nextActions(view, limit = 3) {
    return (view?.groups?.obligations || []).filter(row => row.object.data.next_action?.trim())
      .slice(0, limit).map(row => ({ref: {id: row.object.id, rev: row.object.rev}, text: row.object.data.next_action, statement: row.object.data.statement}));
  }
  publish({lines,nullable,refKey,choices,draft,request,projectDefaults,projectPatch,projectValidation,writable,promoteDraft,recordErrors,nextActions});
})(value => { if (typeof module !== 'undefined') module.exports = value; else globalThis.DshMathForms = value; });
