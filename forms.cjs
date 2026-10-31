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
