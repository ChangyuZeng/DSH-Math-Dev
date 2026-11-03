import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const F=createRequire(import.meta.url)('../forms.cjs');
const source=readFileSync(new URL('../form-ui.cjs',import.meta.url),'utf8');
const plain=value=>JSON.parse(JSON.stringify(value));
function hooks(){
  let cursor=0,pending=[];const slots=[];
  const react={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity).filter(x=>x!==null&&x!==false&&x!==undefined)}),
    useState(initial){const i=cursor++;if(!slots[i])slots[i]={value:typeof initial==='function'?initial():initial};return [slots[i].value,value=>{assert.notEqual(typeof value,'function','Editor must not hide notifications inside React updaters');slots[i].value=value;}];},
    useRef(initial){const i=cursor++;if(!slots[i])slots[i]={current:initial};return slots[i];},
    useEffect(effect,deps){const i=cursor++,before=slots[i];if(!before||deps.some((value,n)=>!Object.is(value,before.deps[n]))){pending.push(()=>{before?.cleanup?.();slots[i]={deps,cleanup:effect()};});}},
    reset(){cursor=0;},flush(){const queued=pending;pending=[];queued.forEach(effect=>effect());},unmount(){slots.forEach(slot=>slot?.cleanup?.());}};
  return react;
}
function nodes(tree){return !tree||typeof tree!=='object'?[]:[tree,...tree.children.flatMap(nodes)];}
function field(tree,label){const found=nodes(tree).find(node=>node.props.label===label);assert.ok(found,`Missing field: ${label}`);return found;}
function button(tree,label){const found=nodes(tree).find(node=>node.type==='button'&&node.children.includes(label));assert.ok(found,`Missing button: ${label}`);return found;}
function fixture(kind='Note',extras={}){
  let serial=0;const initial=F.draft(kind,{id:'P-synthetic',rev:3},()=>`synthetic-${++serial}`);
  if(kind==='Note')Object.assign(initial.data,{title:'标题',summary:'摘要',body:'正文',statement:'主张'});
  if(kind==='Obligation')Object.assign(initial.data,{statement:'陈述',scope:'范围',completion_criteria:['条件'],next_action:'下一步',reason:'原因'});
  if(kind==='Evidence')Object.assign(initial.data,{source:{...initial.data.source,locator:'合成来源'},support_scope:'局部范围',limits:['未核实']});
  const react=hooks(),context={btoa:value=>Buffer.from(value,'binary').toString('base64')};vm.runInNewContext(source,context);
  const ui=context.DshMathFormUI(react,F,extras.Preview);let props={initial,objects:[],save:async()=>{},cancel:()=>{},...extras};
  return {react,ui,initial,set(patch){props={...props,...patch};},render(){react.reset();const tree=ui.Editor(props);react.flush();return tree;}};
}
const submit=tree=>tree.props.onSubmit({preventDefault(){}});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

for(const [kind,labels] of [['Note',['笔记标题','完整正文','主张']],['Obligation',['待证陈述','适用范围','下一步']],['Evidence',['原文摘录 / 证明全文','来源地址或出处','支持范围']]])test(`${kind}: synchronous edits survive stale handlers and parent draft echoes`,async()=>{
  const changed=[],saved=[];const t=fixture(kind,{onDraftChange:(draft,meta)=>changed.push({draft,meta}),save:r=>saved.push(r)});
  const first=t.render();
  for(const [i,label] of labels.entries())field(first,label).props.change(`  合成 ${i}\r\nΩ $x$  `);
  assert.equal(changed.length,3);assert.ok(changed.every(change=>change.meta.dirty));
  const latest=changed.at(-1).draft;t.set({initial:plain(latest)});const echoed=t.render();
  for(const [i,label] of labels.entries())assert.equal(field(echoed,label).props.value,`  合成 ${i}\r\nΩ $x$  `);
  assert.ok(nodes(echoed).some(node=>node.children.includes('修改未保存，仅暂存在本窗口。')));
  await submit(echoed);assert.equal(saved.length,1);assert.equal(saved[0].operation_id,t.initial.operation_id);
  assert.equal(JSON.stringify(saved[0]).includes('合成'),true);
});

test('unchanged events are silent and restoring initial bytes clears dirty despite parent echoes',()=>{
  const changes=[];const t=fixture('Note',{onDraftChange:(draft,meta)=>changes.push({draft,meta})});
  field(t.render(),'笔记标题').props.change('标题');assert.equal(changes.length,0);
  field(t.render(),'笔记标题').props.change('改动');t.set({initial:plain(changes[0].draft)});
  field(t.render(),'笔记标题').props.change('标题');assert.equal(changes.at(-1).meta.dirty,false);
  assert.equal(changes.length,2);
});

test('restored and promoted unsaved drafts keep a per-operation dirty floor across parent echoes',()=>{
  let retained;const original=fixture('Note',{onDraftChange:(draft,meta)=>{retained={draft:plain(draft),dirty:meta.dirty};}});
  field(original.render(),'笔记标题').props.change('尚未保存的T1');original.react.unmount();
  const updates=[];const restored=fixture('Note',{initial:retained.draft,initialDirty:retained.dirty,onDraftChange:(draft,meta)=>updates.push({draft,meta})});
  assert.ok(nodes(restored.render()).some(node=>node.children.includes('恢复的内容尚未保存，仅暂存在本窗口。')));
  field(restored.render(),'笔记标题').props.change('临时T2');
  restored.set({initial:plain(updates.at(-1).draft),initialDirty:false});
  field(restored.render(),'笔记标题').props.change('尚未保存的T1');assert.equal(updates.at(-1).meta.dirty,true);
  const promoted=fixture('Note',{initialDirty:true,onDraftChange:(draft,meta)=>updates.push({draft,meta})});
  field(promoted.render(),'笔记标题').props.change('临时');field(promoted.render(),'笔记标题').props.change('标题');assert.equal(updates.at(-1).meta.dirty,true);
  // An ordinary clean operation does not acquire a dirty floor from echoed props.
  const clean=fixture('Note',{onDraftChange:(draft,meta)=>updates.push({draft,meta})});
  field(clean.render(),'笔记标题').props.change('改动');clean.set({initialDirty:true});
  field(clean.render(),'笔记标题').props.change('标题');assert.equal(updates.at(-1).meta.dirty,false);
});

test('attachment completion survives an unrelated parent read lock without auto-saving',async()=>{
  const gate=deferred(),updates=[],busy=[];let saves=0;
  const t=fixture('Evidence',{onDraftChange:draft=>updates.push(draft),onBusyChange:value=>busy.push(value),save:()=>saves++});
  const pending=nodes(t.render()).find(node=>node.props.type==='file').props.onChange({target:{files:[{name:'during-refresh.bin',size:2,arrayBuffer:()=>gate.promise}]}});
  t.set({busy:true});t.render();gate.resolve(Uint8Array.from([3,255]).buffer);await pending;
  assert.deepEqual(plain(updates[0].files),[{logical_name:'during-refresh.bin',base64:'A/8='}]);assert.equal(saves,0);assert.deepEqual(busy,[true]);
  t.set({busy:false});t.render();assert.deepEqual(busy,[true,false]);
});

test('all editable fields remain accessible with Note details folded',()=>{
  const expected={Note:['笔记标题','完整正文','简述','主张','笔记类型','依赖前提','拟解决的待证事项','参考材料','假设（每行一项）','尚存缺口（每行一项）','路线标识'],Obligation:['待证陈述','登记原因','下一步','当前提交状态','依赖前提','候选笔记','参考材料','适用范围','完成条件（每行一项）'],Evidence:['材料类型','原文摘录 / 证明全文','来源地址或出处','版本 / commit','访问日期','来源材料状态','生产或导入方式','生产方式版本','摘录附件名称','支持范围','局限与未核实项（每行一项）']};
  for(const kind of Object.keys(expected)){
    const t=fixture(kind),tree=t.render();expected[kind].forEach(label=>field(tree,label));
    assert.deepEqual(nodes(tree).filter(node=>node.props.className==='dm-editor-section').map(node=>node.props['aria-label']),['内容','依据','范围与缺口']);
    const scope=nodes(tree).find(node=>node.props['aria-label']==='范围与缺口');assert.equal(scope.type,kind==='Note'?'details':'section');
    const basis=nodes(tree).find(node=>node.props['aria-label']==='依据');assert.equal(basis.type,kind==='Evidence'?'section':'details');
  }
});

test('nested field events compose against latest draft, preserving all untouched schema data',async()=>{
  let saved;const t=fixture('Evidence',{save:r=>{saved=r;}}),tree=t.render();
  field(tree,'来源地址或出处').props.change('新来源');field(tree,'版本 / commit').props.change('exact-version');
  field(tree,'生产或导入方式').props.change('manual');field(tree,'生产方式版本').props.change('9');
  await submit(t.render());assert.deepEqual(plain(saved.data.source),{locator:'新来源',version:'exact-version',accessed_at:null,conditions:[]});
  assert.deepEqual(plain(saved.data.production),{method:'manual',version:'9'});assert.deepEqual(plain(saved.data.attachments),[]);
});

test('safe Preview injection receives original text; fallback renders text rather than HTML',()=>{
  function Preview(){}const payload='<img src="https://untrusted.invalid/a" onerror="alert(1)">\n$x^2$';
  for(const preview of [undefined,Preview]){
    const t=fixture('Note',{Preview:preview});field(t.render(),'完整正文').props.change(payload);const tree=t.render();
    const node=nodes(tree).find(node=>node.type===(preview||'pre'));assert.ok(node);
    assert.equal(preview?node.props.text:node.children[0],payload);
    assert.equal(nodes(tree).some(node=>node.props.dangerouslySetInnerHTML||node.type==='img'),false);
  }
});

test('validation summary maps to controls, opens folded ancestors, and never saves partial records',async()=>{
  let saves=0;const t=fixture('Note',{save:()=>saves++});field(t.render(),'笔记标题').props.change('');
  const tree=t.render();let focused=false,scrolled=false;const details={tagName:'DETAILS',open:false,parentElement:null};
  tree.props.ref.current={querySelector:selector=>{assert.equal(selector,'[data-dm-field="title"]');return {parentElement:details,focus(){focused=true;},scrollIntoView(){scrolled=true;}};}};
  await submit(tree);assert.equal(saves,0);assert.equal(focused,true);assert.equal(details.open,true);assert.equal(scrolled,true);
  focused=false;button(t.render(),'请填写笔记标题').props.onClick();assert.equal(focused,true);
  assert.equal(field(t.render(),'笔记标题').props.invalid,true);
});

test('failed-attempt fields and precise citation condition errors remain editable and navigable',async()=>{
  const note=fixture();field(note.render(),'笔记类型').props.change('failed_attempt');
  for(const label of ['失败输入','失败位置','观察结果','否定范围','再次尝试应改变什么'])field(note.render(),label);
  await submit(note.render());assert.ok(button(note.render(),'请填写失败输入'));
  const t=fixture('Evidence');const check=nodes(t.render()).find(node=>node.props['data-dm-field']==='citation');check.props.onChange({target:{checked:true}});
  for(const label of ['文献标题','页码','章节','命题 / 定理 / 开放问题编号'])field(t.render(),label);
  button(t.render(),'添加适用条件').props.onClick();await submit(t.render());
  assert.ok(button(t.render(),'请填写条件 1 的适用条件'));assert.ok(button(t.render(),'请填写条件 1 的条件解释'));
  const tree=t.render();field(tree,'条件 1').props.change('完整条件');field(tree,'条件 1 的解释').props.change('明确未核实');
  assert.equal(field(t.render(),'条件 1').props.value,'完整条件');assert.equal(field(t.render(),'条件 1 的解释').props.value,'明确未核实');
});

test('provided source requires actual excerpt or a pre-existing non-placeholder digest',async()=>{
  let saved=0;const t=fixture('Evidence',{save:()=>saved++});field(t.render(),'来源材料状态').props.change('provided');
  await submit(t.render());assert.equal(saved,0);
  nodes(t.render()).find(node=>node.props['data-dm-field']==='citation').props.onChange({target:{checked:true}});
  await submit(t.render());assert.equal(saved,0,'new citation placeholder is not archived source text');
  field(t.render(),'原文摘录 / 证明全文').props.change('合成可审核摘录');await submit(t.render());assert.equal(saved,1);
});

test('program-checked obligations cannot silently enter the human revision path',async()=>{
  let saved=0;const t=fixture('Obligation',{save:()=>saved++});t.initial.data.check_method='program';
  await submit(t.render());assert.equal(saved,0);assert.ok(button(t.render(),'程序检查事项必须通过其专用入口修订'));
});

test('selected historical reference survives search and does not upgrade revision',()=>{
  const react=hooks(),context={};vm.runInNewContext(source,context);const {Refs}=context.DshMathFormUI(react,F);
  const changes=[];
  const props={label:'引用',objects:[1,2].map(rev=>({id:'N',rev,kind:'Note',data:{title:'合成记录',problem_ref:{id:'P',rev:1}}})),kinds:['Note'],problem:{id:'P',rev:1},value:[{id:'N',rev:1}],change:v=>changes.push(v)};
  const render=()=>{react.reset();return Refs(props);};field(render(),'搜索 引用').props.change('不匹配');
  assert.ok(nodes(render()).some(node=>node.type==='span'&&String(node.children[0]).includes('合成记录 · 版本1')));
  assert.equal(nodes(render()).some(node=>node.type==='span'&&String(node.children[0]).includes('版本2')),false);
  assert.equal(nodes(render()).filter(node=>node.type==='span').some(node=>String(node.children).includes('N@1')),false);
  nodes(render()).find(node=>node.props.type==='checkbox'&&node.props.checked).props.onChange();
  assert.deepEqual(JSON.parse(JSON.stringify(changes)),[[]]);
  props.value=[];field(render(),'搜索 引用').props.change('');
  nodes(render()).filter(node=>node.props.type==='checkbox').at(-1).props.onChange();
  assert.deepEqual(JSON.parse(JSON.stringify(changes.at(-1))),[{id:'N',rev:2}]);
});

for(const flag of ['busy','readOnly'])test(`${flag} blocks stale editing, uploads, submission and preserves data`,async()=>{
  let changed=0,saved=0;const t=fixture('Evidence',{onDraftChange:()=>changed++,save:()=>saved++});const before=t.render();
  t.set({[flag]:true});const tree=t.render();field(before,'来源地址或出处').props.change('不允许');
  await submit(before);await nodes(before).find(node=>node.props.type==='file').props.onChange({target:{files:[{size:1,arrayBuffer(){throw Error('must not read');}}]}});
  assert.equal(changed,0);assert.equal(saved,0);assert.equal(field(tree,'来源地址或出处').props.value,'合成来源');assert.equal(nodes(tree).find(node=>node.props.type==='submit').props.disabled,true);
});

test('attachment read is atomic, busy-protected, byte exact and never auto-saves',async()=>{
  const gate=deferred(),busy=[],changes=[],saved=[];let cancelled=0;
  const t=fixture('Evidence',{onBusyChange:value=>busy.push(value),onDraftChange:draft=>changes.push(draft),save:r=>saved.push(r),cancel:()=>cancelled++});const tree=t.render();
  const upload=nodes(tree).find(node=>node.props.type==='file').props.onChange;
  const reading=upload({target:{files:[{name:'synthetic.bin',size:3,arrayBuffer:()=>gate.promise}]}});
  await submit(tree);button(tree,'取消').props.onClick();field(tree,'来源地址或出处').props.change('blocked');
  assert.equal(cancelled,0);assert.equal(saved.length,0);assert.equal(changes.length,0);assert.deepEqual(busy,[true]);
  gate.resolve(Uint8Array.from([0,255,17]).buffer);await reading;
  assert.deepEqual(busy,[true,false]);assert.deepEqual(plain(changes[0].files),[{logical_name:'synthetic.bin',base64:'AP8R'}]);assert.equal(saved.length,0);
  await upload({target:{files:[{name:'too-large.bin',size:385*1024,arrayBuffer(){throw Error('must not read');}}]}});
  assert.equal(changes.length,1);assert.ok(nodes(t.render()).some(node=>node.children.includes('附件总计最多384 KiB；大文件请先选取相关原文片段')));
});

test('unmount discards unfinished file read without publishing a partial draft',async()=>{
  const gate=deferred(),changes=[],busy=[];const t=fixture('Evidence',{onDraftChange:d=>changes.push(d),onBusyChange:value=>busy.push(value)});
  const reading=nodes(t.render()).find(node=>node.props.type==='file').props.onChange({target:{files:[{name:'pending',size:1,arrayBuffer:()=>gate.promise}]}});
  t.react.unmount();gate.resolve(Uint8Array.from([1]).buffer);await reading;assert.equal(changes.length,0);assert.deepEqual(busy,[true,false]);
});

test('unknown saves retain identical request and operation across retry and remount',async()=>{
  const requests=[],attempts=[],busy=[];const t=fixture('Note',{save:r=>{requests.push(r);throw Error('offline');},onAttemptChange:r=>attempts.push(r),onBusyChange:value=>busy.push(value)});
  await submit(t.render());const after=t.render();assert.ok(button(after,'重试原保存请求'));
  field(after,'笔记标题').props.change('must stay frozen');assert.equal(field(t.render(),'笔记标题').props.value,'标题');
  await submit(after);assert.deepEqual(plain(requests[0]),plain(requests[1]));assert.deepEqual(busy,[true,false,true,false]);assert.equal(attempts.length,1);
  t.react.unmount();const restored=fixture('Note',{initial:t.initial,initialAttempt:plain(attempts[0]),save:r=>requests.push(r)});
  await submit(restored.render());assert.deepEqual(plain(requests[2]),plain(requests[0]));
});

test('known precommit validation rejection unlocks editing without replacing operation id',async()=>{
  const attempts=[],requests=[];let reject=true;const t=fixture('Note',{onAttemptChange:r=>attempts.push(r),save:r=>{requests.push(r);if(reject)throw Object.assign(Error('invalid'),{code:'INVALID_SCHEMA'});}});
  await submit(t.render());assert.equal(attempts.at(-1),null);field(t.render(),'笔记标题').props.change('纠正标题');
  reject=false;await submit(t.render());assert.equal(requests[1].changes[0].data.title,'纠正标题');assert.equal(requests[0].operation_id,requests[1].operation_id);
});

test('precommit version conflict releases attempt but preserves text and exact expected revision',async()=>{
  const attempts=[],requests=[],changes=[];let cancelled=0;
  const t=fixture('Note',{onAttemptChange:r=>attempts.push(r),onDraftChange:(draft,meta)=>changes.push({draft,meta}),cancel:()=>cancelled++,save:r=>{requests.push(r);throw Object.assign(Error('Version conflict'),{code:'VERSION_CONFLICT'});}});
  t.initial.expected_rev=7;field(t.render(),'完整正文').props.change('未保存的精确正文\nΩ');await submit(t.render());
  assert.equal(attempts.at(-1),null);assert.ok(button(t.render(),'保存记录'));assert.equal(field(t.render(),'完整正文').props.value,'未保存的精确正文\nΩ');
  assert.ok(nodes(t.render()).some(node=>node.children.some(value=>typeof value==='string'&&value.includes('基于版本未自动更新'))));
  field(t.render(),'笔记标题').props.change('继续整理');assert.equal(changes.at(-1).draft.expected_rev,7);assert.equal(changes.at(-1).meta.dirty,true);
  await submit(t.render());assert.equal(requests.length,2);assert.equal(requests[1].changes[0].expected_rev,7);assert.equal(requests[1].operation_id,requests[0].operation_id);
  button(t.render(),'取消').props.onClick();assert.equal(cancelled,1,'Parent may now offer explicit retain/discard confirmation');
});

test('operation conflicts remain frozen and retry the original payload',async()=>{
  const attempts=[],requests=[];const t=fixture('Note',{onAttemptChange:r=>attempts.push(r),save:r=>{requests.push(r);throw Object.assign(Error('Operation conflict'),{code:'OPERATION_CONFLICT'});}});
  await submit(t.render());assert.ok(attempts.at(-1));assert.ok(button(t.render(),'重试原保存请求'));
  field(t.render(),'笔记标题').props.change('不能修改未知请求');assert.equal(field(t.render(),'笔记标题').props.value,'标题');
  await submit(t.render());assert.deepEqual(plain(requests[0]),plain(requests[1]));assert.equal(attempts.length,1);
});

test('duplicate submit callbacks dispatch once before React rerenders and latest text is used',async()=>{
  const gate=deferred(),requests=[];const t=fixture('Note',{save:r=>{requests.push(r);return gate.promise;}}),tree=t.render();
  field(tree,'笔记标题').props.change('最后输入');const pending=submit(tree);await submit(tree);assert.equal(requests.length,1);assert.equal(requests[0].changes[0].data.title,'最后输入');
  gate.resolve();await pending;
});

test('legacy Text preserves short fields with CR or LF and factory remains optional-preview compatible',()=>{
  const t=fixture();for(const value of ['', '短行', '一\n二', '一\r\n二', '一\r二']){
    let changed;const tree=t.ui.Text({label:'短字段',value,multi:false,change:value=>{changed=value;}});
    assert.equal(tree.props.className,'dm-field');assert.equal(tree.children[1].type,/[\r\n]/.test(value)?'textarea':'input');
    tree.children[1].props.onChange({target:{value}});assert.equal(changed,value);
  }
});
