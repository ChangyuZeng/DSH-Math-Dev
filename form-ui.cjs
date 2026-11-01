globalThis.DshMathFormUI = function (React, F, Preview) {
  const {createElement:h,useState,useEffect,useRef} = React;
  function Text({label,value,change,required=false,multi=true,field,invalid=false}) {
    const multiline=multi||/[\r\n]/.test(value||'');
    return h('label',{className:'dm-field'},label,h(multiline?'textarea':'input',{className:'dm-input',value:value??'',required,'data-dm-field':field,'aria-invalid':invalid||undefined,onChange:e=>change(e.target.value),rows:multiline?3:undefined}));
  }
  function Select({label,value,change,items,field}) {return h('label',{className:'dm-field'},label,h('select',{className:'dm-input',value,'data-dm-field':field,onChange:e=>change(e.target.value)},...items.map(([v,t])=>h('option',{key:v,value:v},t))));}
  function Refs({label,objects,kinds,problem,self,value,change,single=false,inspect,field}) {
    const [search,setSearch]=useState(''),[history,setHistory]=useState(false);
    const options=F.choices(objects,kinds,problem,self);
    const newest=new Map(); for(const o of options)newest.set(o.id,Math.max(o.rev,newest.get(o.id)||0));
    // A selected exact revision remains visible even when search/history filters change.
    const shown=options.filter(o=>value.some(r=>F.refKey(r)===F.refKey(o))||((history||o.rev===newest.get(o.id))&&`${o.id} ${o.data.title||o.data.statement||o.data.source?.version||''}`.toLowerCase().includes(search.toLowerCase())));
    return h('fieldset',{className:'dm-fieldset','data-dm-field':field,tabIndex:-1},h('legend',null,label),
      h(Text,{label:'搜索 '+label,value:search,change:setSearch,multi:false}),h('label',{className:'dm-check-label'},h('input',{type:'checkbox',checked:history,onChange:e=>setHistory(e.target.checked)}),h('span',null,'显示历史版本（不会自动升级引用）')),
      h('div',{className:'dm-ref-options'},...shown.map(o=>{const selected=value.some(r=>F.refKey(r)===F.refKey(o));return h('div',{key:F.refKey(o),className:'dm-ref-option'},h('label',{className:'dm-check-label'},h('input',{type:single?'radio':'checkbox',name:label,checked:selected,onChange:()=>change(selected?value.filter(r=>F.refKey(r)!==F.refKey(o)):single?[{id:o.id,rev:o.rev}]:[...value,{id:o.id,rev:o.rev}])}),h('span',null,`${F.refKey(o)} · ${o.data.title||o.data.statement||o.data.source?.version||o.kind} ${o.rev<newest.get(o.id)?'（历史）':''}`)),inspect&&h('button',{type:'button',className:'dm-button dm-small',onClick:()=>inspect(o)},'查看'));})),
      !shown.length&&h('p',{className:'dm-empty'},'没有同目标版本的可选记录'),h('p',{className:'dm-record-text'},'已选：'+(value.map(F.refKey).join('、')||'无')),
      single&&value.length>0&&h('button',{type:'button',className:'dm-button dm-small',onClick:()=>change([])},'清除选择'));
  }
  function Editor({initial,objects,save,cancel,inspect,busy=false,readOnly=false,onDraftChange,onBusyChange,initialAttempt=null,onAttemptChange,initialDirty=false}) {
    const [,setDraft]=useState(initial),[errors,setErrors]=useState([]),[uploading,setUploading]=useState(false),[submitting,setSubmitting]=useState(false),[attempt,setAttempt]=useState(initialAttempt);
    const heading=useRef(null),form=useRef(null),latest=useRef(initial),baseline=useRef(JSON.stringify(initial)),dirtyFloor=useRef(!!initialDirty);
    const life=useRef({active:true,serial:0,uploading:false,submitting:false,attempt:initialAttempt,operation:initial.operation_id});
    const callbacks=useRef({}); callbacks.current={onDraftChange,onBusyChange,onAttemptChange,busy,readOnly,save,cancel};
    const lastBusy=useRef(false);
    // Parent echoes of this operation are not a fresh baseline. A new operation must
    // be keyed by operation_id in the parent; the effect also supports direct reuse.
    useEffect(()=>{
      const state=life.current;
      if(state.operation!==initial.operation_id){state.serial++;state.operation=initial.operation_id;state.uploading=false;state.submitting=false;state.attempt=initialAttempt;latest.current=initial;baseline.current=JSON.stringify(initial);dirtyFloor.current=!!initialDirty;setDraft(initial);setErrors([]);setUploading(false);setSubmitting(false);setAttempt(initialAttempt);}
      state.active=true;heading.current?.focus();
      return()=>{state.active=false;state.serial++;};
    },[initial.operation_id]);
    function notifyBusy(){const value=!!(callbacks.current.busy||life.current.uploading||life.current.submitting);if(value!==lastBusy.current){lastBusy.current=value;callbacks.current.onBusyChange?.(value);}}
    useEffect(()=>{notifyBusy();},[busy]);
    useEffect(()=>()=>{if(lastBusy.current){lastBusy.current=false;callbacks.current.onBusyChange?.(false);}},[]);
    const draft=latest.current,data=draft.data;
    const operation=draft.operation_id;
    const current=()=>life.current.active&&life.current.operation===operation;
    const editable=()=>current()&&!callbacks.current.busy&&!callbacks.current.readOnly&&!life.current.submitting&&!life.current.uploading&&!life.current.attempt;
    function update(change,fromUpload=false){
      // A read already in flight must not lose its bytes because the parent starts
      // an unrelated overview/refresh read. Scope and mutation guards still apply.
      if(!(fromUpload?current()&&!callbacks.current.readOnly&&!life.current.submitting&&!life.current.attempt:editable()))return;
      const next=typeof change==='function'?change(latest.current):change;
      if(JSON.stringify(next)===JSON.stringify(latest.current))return;
      latest.current=next;setDraft(next);
      // Never call a parent setter from a React updater (Strict Mode may replay it).
      callbacks.current.onDraftChange?.(next,{dirty:dirtyFloor.current||JSON.stringify(next)!==baseline.current});
    }
    const set=patch=>update(prev=>({...prev,data:{...prev.data,...(typeof patch==='function'?patch(prev.data):patch)}}));
    const fields=[];
    const field=(path,label,required=false)=>{fields.push({path,label,required});return {field:path,invalid:errors.some(e=>e.field===path)};};
    const text=(key,label,required=true,multi=true)=>h(Text,{key,label,value:data[key],required,multi,...field(key,label,required),change:v=>set({[key]:v})});
    const list=(key,label,required=false)=>h(Text,{key,label:label+'（每行一项）',value:(data[key]||[]).join('\n'),required,...field(key,label,required),change:v=>set({[key]:v.split('\n')})});
    const refs=(key,label,kinds)=>h(Refs,{key,label,objects,kinds,problem:data.problem_ref,self:draft.id,value:data[key],...field(key,label),change:v=>set({[key]:v}),inspect});
    const source=(key,label,required=false)=>h(Text,{key,label,value:data.source[key],required,multi:false,...field('source.'+key,label,required),change:v=>set(prev=>({source:{...prev.source,[key]:v||null}}))});
    const citation=(key,label)=>h(Text,{key,label,value:data.citation?.[key],multi:false,...field('citation.'+key,label),change:v=>set(prev=>({citation:{...prev.citation,[key]:v||null}}))});
    const conditions=data.citation?.condition_map||[];
    function changeConditions(change){set(prev=>{const updated=change(prev.citation?.condition_map||[]);return {citation:{...prev.citation,condition_map:updated},source:{...prev.source,conditions:updated.map(c=>c.condition)}};});}
    function condition(index,patch){changeConditions(items=>items.map((c,i)=>i===index?{...c,...patch}:c));}
    function focusField(path){
      const target=path&&form.current?.querySelector(`[data-dm-field="${path}"]`);
      if(!target)return;
      for(let parent=target.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
      target.focus();target.scrollIntoView?.({block:'nearest'});
    }
    async function files(event){
      const state=life.current;if(!editable())return;
      const selected=Array.from(event.target.files||[]);if(!selected.length)return;
      const token=++state.serial;state.uploading=true;setUploading(true);setErrors([]);notifyBusy();
      try{let total=0;const encoded=[];
        for(const file of selected){total+=file.size;if(total>384*1024)throw Error('附件总计最多384 KiB；大文件请先选取相关原文片段');
          const bytes=new Uint8Array(await file.arrayBuffer());if(!current()||token!==state.serial)return;
          let binary='';for(const b of bytes)binary+=String.fromCharCode(b);encoded.push({logical_name:file.name,base64:btoa(binary)});}
        if(current()&&token===state.serial)update(prev=>({...prev,files:encoded}),true);
      }catch(e){if(current()&&token===state.serial)setErrors([{field:'files',message:e.message}]);}
      finally{if(current()&&token===state.serial){state.uploading=false;setUploading(false);notifyBusy();}}
    }
    function validation(ready){
      const issues=F.recordErrors(ready).map(message=>({message,field:fields.find(f=>message===`请填写${f.label}`)?.path}));
      // condition_map's shared validator uses generic labels; associate each actual
      // missing condition with its own control, without dropping schema validation.
      const mapped=issues.filter(e=>!['请填写适用条件','请填写条件解释'].includes(e.message));
      for(const [i,c] of (ready.data.citation?.condition_map||[]).entries())for(const [key,label] of [['condition','适用条件'],['explanation','条件解释']])if(!c[key]?.trim())mapped.push({field:`citation.condition_map.${i}.${key}`,message:`请填写条件 ${i+1} 的${label}`});
      if(ready.kind==='Evidence'){
        if(!ready.logical_name?.trim())mapped.push({field:'logical_name',message:'请填写摘录附件名称'});
        if(ready.data.evidence_type==='source_excerpt'&&ready.data.source_status==='provided'&&!ready.excerpt?.trim()&&(!ready.data.citation?.excerpt_sha256||/^0+$/.test(ready.data.citation.excerpt_sha256)))mapped.push({field:'excerpt',message:'已提供来源必须粘贴可审核的原文摘录；PDF附件不能替代摘录'});
      }
      if(ready.kind==='Obligation'&&ready.data.check_method!=='human')mapped.push({field:'statement',message:'程序检查义务必须通过其专用入口修订'});
      return mapped;
    }
    async function submit(event){
      event.preventDefault();const state=life.current;if(!current()||callbacks.current.busy||callbacks.current.readOnly||state.uploading||state.submitting)return;
      let request=state.attempt;
      if(!request){
        const ready=JSON.parse(JSON.stringify(latest.current));
        for(const key of ['assumptions','gaps','completion_criteria','limits'])if(Array.isArray(ready.data[key]))ready.data[key]=ready.data[key].map(x=>x.trim()).filter(Boolean);
        const issues=validation(ready);if(issues.length){setErrors(issues);focusField(issues[0].field);return;}
        if(ready.kind==='Note')ready.data.route_id=ready.data.route_id?.trim()||null;
        if(ready.kind==='Obligation')ready.data.state=ready.data.state==='candidate'?'candidate':'open';
        request=F.request(ready);state.attempt=request;setAttempt(request);callbacks.current.onAttemptChange?.(request);
      }
      const token=state.serial;state.submitting=true;setSubmitting(true);setErrors([]);notifyBusy();
      try{
        // Keep the exact request for retries: an interrupted reply is not evidence
        // that the backend did not commit. Only the parent confirms/ends the edit.
        await callbacks.current.save(JSON.parse(JSON.stringify(request)));
      }catch(e){if(current()&&token===state.serial){
        const rejected=['INVALID_SCHEMA','DANGLING_REFERENCE','REFERENCE_KIND','TARGET_MISMATCH','DEPENDENCY_CYCLE','VERSION_CONFLICT'].includes(e.code);
        if(rejected){state.attempt=null;setAttempt(null);callbacks.current.onAttemptChange?.(null);}
        setErrors([{message:`${e.message}${e.code==='VERSION_CONFLICT'?'；版本冲突，内容已保留，基于版本未自动更新。请先核对最新版本，再明确放弃本次修改或重新打开新版修订。':rejected?'；请求已被拒绝，请修改后保存。':'；请重试原请求核对结果，勿重复登记。'}`}]);
      }}
      finally{if(current()&&token===state.serial){state.submitting=false;setSubmitting(false);notifyBusy();}}
    }
    const section=(key,title,children,fold=false)=>h(fold?'details':'section',{key,className:'dm-editor-section','aria-label':title},h(fold?'summary':'h4',{className:'dm-editor-section-heading'},title),...children);
    const preview=body=>h('details',{className:'dm-editor-preview'},h('summary',null,'预览正文 / 公式'),Preview?h(Preview,{text:body||''}):h('pre',{className:'dm-code'},body||''));
    let content=[],basis=[],scope=[];
    if(draft.kind==='Note'){
      content=[text('title','笔记标题',true,false),text('body','完整正文'),preview(data.body),text('summary','简述'),text('statement','主张'),h(Select,{label:'笔记类型',field:'note_type',value:data.note_type,change:v=>set(prev=>({note_type:v,failure:v==='failed_attempt'?(prev.failure||{inputs:'',location:'',observed:'',negated_scope:'',retry_change:''}):null})),items:[['observation','观察'],['argument','论证'],['computation','计算记录'],['source','文献笔记'],['failed_attempt','失败尝试']]})];
      basis=[refs('uses','依赖前提',['Note','Obligation']),refs('candidate_for','拟解决的义务',['Obligation']),refs('evidence','支撑证据',['Evidence'])];
      scope=[list('assumptions','假设'),list('gaps','尚存缺口'),text('route_id','路线标识',false,false)];
      if(data.failure)scope.push(...Object.entries({inputs:'失败输入',location:'失败位置',observed:'观察结果',negated_scope:'否定范围',retry_change:'再次尝试应改变什么'}).map(([key,label])=>h(Text,{key,label,value:data.failure[key],required:true,...field('failure.'+key,label,true),change:v=>set(prev=>({failure:{...prev.failure,[key]:v}}))})));
    }else if(draft.kind==='Obligation'){
      content=[text('statement','义务陈述'),preview(data.statement),text('reason','登记原因'),text('next_action','下一步'),h(Select,{label:'当前提交状态',field:'state',value:data.state==='candidate'?'candidate':'open',change:v=>set({state:v}),items:[['open','开放'],['candidate','已有候选，等待审核']]}),h('p',{className:'dm-record-text'},'由人工审核；接受和待复核状态由系统派生。')];
      basis=[refs('uses','依赖前提',['Note','Obligation']),refs('candidates','候选笔记',['Note']),refs('evidence','支撑证据',['Evidence'])];
      scope=[text('scope','适用范围'),list('completion_criteria','完成条件',true)];
    }else if(draft.kind==='Evidence'){
      content=[h(Select,{label:'证据类型',field:'evidence_type',value:data.evidence_type,change:v=>set({evidence_type:v,citation:null}),items:[['source_excerpt','来源摘录'],['proof_text','证明原文'],['computation','计算记录'],['witness','有限见证'],['figure','示意图']]}),h(Text,{label:'原文摘录 / 证明全文',value:draft.excerpt||'',...field('excerpt','原文摘录 / 证明全文'),change:v=>update(prev=>({...prev,excerpt:v||null}))}),preview(draft.excerpt)];
      basis=[source('locator','来源地址或出处',true),source('version','版本 / commit'),source('accessed_at','访问日期'),h(Select,{label:'来源材料状态',field:'source_status',value:data.source_status,change:v=>set({source_status:v}),items:[['unverified_source','待核实来源线索'],['provided','已归档材料（不代表审核通过）']]}),
        h(Text,{label:'生产或导入方式',value:data.production.method,required:true,...field('production.method','生产或导入方式',true),change:v=>set(prev=>({production:{...prev.production,method:v}}))}),h(Text,{label:'生产方式版本',value:data.production.version,required:true,multi:false,...field('production.version','生产方式版本',true),change:v=>set(prev=>({production:{...prev.production,version:v}}))}),
        h(Text,{label:'摘录附件名称',value:draft.logical_name,multi:false,required:true,...field('logical_name','摘录附件名称',true),change:v=>update(prev=>({...prev,logical_name:v}))}),h('label',{className:'dm-field'},'附加文件（总计384 KiB，保留原始字节）',h('input',{type:'file',multiple:true,'data-dm-field':'files',onChange:files})),h('p',{className:'dm-record-text'},(draft.files||[]).map(f=>f.logical_name).join('、')),
        data.attachments?.length>0&&h('details',null,h('summary',null,`已有归档附件（${data.attachments.length}）`),h('pre',{className:'dm-code'},JSON.stringify(data.attachments,null,2)))];
      scope=[text('support_scope','支持范围'),list('limits','局限与未核实项',true)];
      if(data.evidence_type==='source_excerpt'){
        scope.push(h('label',{className:'dm-check-label'},h('input',{type:'checkbox',checked:!!data.citation,'data-dm-field':'citation',onChange:e=>set(prev=>({citation:e.target.checked?{title:'',page:null,section:null,theorem:null,excerpt_sha256:'0'.repeat(64),condition_map:[]}:null,source:{...prev.source,conditions:[]}}))}),h('span',null,'添加精确定位与适用条件')));
        if(data.citation)scope.push(citation('title','文献标题'),citation('page','页码'),citation('section','章节'),citation('theorem','命题 / 定理 / 开放问题编号'),...conditions.map((c,i)=>h('fieldset',{key:i,className:'dm-fieldset'},h('legend',null,`适用条件 ${i+1}`),h(Text,{label:`条件 ${i+1}`,value:c.condition,required:true,...field(`citation.condition_map.${i}.condition`,`条件 ${i+1}`,true),change:v=>condition(i,{condition:v})}),h(Select,{label:`条件 ${i+1} 的适用判断`,field:`citation.condition_map.${i}.assessment`,value:c.assessment,change:v=>condition(i,{assessment:v}),items:[['unresolved','未解决'],['not_satisfied','不满足'],['claimed_satisfied','提交者声称满足（仍需审核）']]}),h(Text,{label:`条件 ${i+1} 的解释`,value:c.explanation,required:true,...field(`citation.condition_map.${i}.explanation`,`条件 ${i+1} 的解释`,true),change:v=>condition(i,{explanation:v})}),h(Refs,{label:`条件 ${i+1} 对应的义务`,field:`citation.condition_map.${i}.obligation`,objects,kinds:['Obligation'],problem:data.problem_ref,value:c.obligation?[c.obligation]:[],single:true,change:v=>condition(i,{obligation:v[0]||null}),inspect}),h('button',{type:'button',className:'dm-button dm-small',onClick:()=>changeConditions(items=>items.filter((_,j)=>j!==i))},'移除此条件'))),h('button',{type:'button',className:'dm-button dm-small',onClick:()=>changeConditions(items=>[...items,{condition:'',assessment:'unresolved',explanation:'',obligation:null}])},'添加适用条件'));
      }
    }
    const locked=busy||uploading||submitting;
    return h('form',{ref:form,onSubmit:submit,noValidate:true,className:'dm-editor','aria-label':'编辑研究记录','aria-busy':locked},
      h('h3',{ref:heading,tabIndex:-1,className:'dm-editor-title'},`${draft.expected_rev?'修订':'登记'} ${{Note:'笔记',Obligation:'证明义务',Evidence:'证据'}[draft.kind]}`),h('p',{className:'dm-record-text'},`${draft.id} · 目标 ${F.refKey(data.problem_ref)} · 基于版本 ${draft.expected_rev}`),
      h('p',{className:'dm-editor-status',role:'status'},readOnly?'只读：不能保存正式记录。':uploading?'正在读取附件；尚未保存正式记录。':submitting?'保存请求处理中…':attempt?'保存请求已发送；如未收到确认，请重试原请求核对结果。':dirtyFloor.current?'恢复或整理的内容尚未保存；仅保留在当前界面内存中，需明确保存或放弃。':JSON.stringify(draft)!==baseline.current?'有未保存的修改；仅保留在当前界面内存中。':'填写并明确保存后才会登记正式记录。'),
      errors.length>0&&h('div',{role:'alert',className:'dm-error dm-editor-errors'},h('p',null,`请检查以下 ${errors.length} 项：`),h('ul',null,...errors.map((error,i)=>h('li',{key:i},error.field?h('button',{type:'button',className:'dm-ref',onClick:()=>focusField(error.field)},error.message):error.message)))),
      h('fieldset',{className:'dm-editor-fields',disabled:locked||readOnly||!!attempt},section('content','内容',content),section('basis','依据',basis,draft.kind!=='Evidence'),section('scope','范围与缺口',scope)),
      h('div',{className:'dm-actions'},h('button',{type:'submit',className:'dm-button dm-primary',disabled:locked||readOnly},busy||submitting?'保存中':uploading?'读取附件中…':attempt?'重试原保存请求':'保存记录'),h('button',{type:'button',className:'dm-button',onClick:()=>{if(current()&&!callbacks.current.busy&&!life.current.submitting&&!life.current.uploading)callbacks.current.cancel();},disabled:locked},'取消')));
  }
  return {Editor,Refs,Text,Select};
};
