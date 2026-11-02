// Hook/geometry checks only; Lead owns installed-browser visual verification.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const require=createRequire(import.meta.url), UI=require('../research-graph-ui.cjs');
const n=(id,kind,parentId=null,extra={})=>({id,kind,parentId,ref:kind==='route'?null:{id,rev:1},title:id,summary:'fixture',status:'unassessed',statusLabel:'无此版本审核评估',historical:false,...extra});
const p=n('P','Problem',null,{historical:true}),p2=n('P2','Problem'),r=n('r','route','P',{problem_ref:{id:'P',rev:1},route_id:'R1'}),a=n('A','Note','r'),o=n('O','Obligation','P'),old=n('Old','Note','r',{historical:true}),e=n('E','Evidence','P');
const graph={project_id:'fixture',nodes:[p,p2,r,a,o,old,e],hierarchy:[{from:'P',to:'r'},{from:'r',to:'A'},{from:'P',to:'O'},{from:'r',to:'Old'},{from:'P',to:'E'}],relations:[{from:'A',to:'O',relation:'candidate_for',label:'候选用于'}],warnings:[],notice:'只读 local_claim R1/R2'};
function harness({width=900,height=560}={}){
 const slots=[];let index=0;const effects=[],cleanups=[],listeners=new Map();
 const element={clientWidth:width,clientHeight:height,getBoundingClientRect:()=>({left:0,top:0}),addEventListener:(name,fn,options)=>listeners.set(name,{fn,options}),removeEventListener:(name,fn)=>{if(listeners.get(name)?.fn===fn)listeners.delete(name);}};
 const React={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity).filter(v=>v!==false&&v!=null)}),useState(initial){const i=index++;if(!slots[i])slots[i]={value:typeof initial==='function'?initial():initial};return[slots[i].value,value=>{slots[i].value=typeof value==='function'?value(slots[i].value):value;}];},useRef(initial){const i=index++;return slots[i]||=( {current:initial});},useEffect(effect,deps){const i=index++,prev=slots[i];if(!prev||deps.some((v,j)=>!Object.is(v,prev.deps[j]))){slots[i]={deps};effects.push(effect);}}};
 function Reader(){} const Component=UI(React,Reader);
 return {Reader,listeners,unmount(){for(const cleanup of cleanups)cleanup();},render(props){index=0;const tree=Component(props);for(const node of nodes(tree,n=>n.type==='svg'))node.props.ref.current=element;while(effects.length){const cleanup=effects.shift()();if(typeof cleanup==='function')cleanups.push(cleanup);}return tree;}};
}
const nodes=(tree,predicate)=>{const out=[];function visit(v){if(!v||typeof v!=='object')return;if(predicate(v))out.push(v);for(const child of v.children||[])visit(child);}visit(tree);return out;};
const text=tree=>tree==null?'':typeof tree==='object'?(tree.children||[]).map(text).join(' '):String(tree);
const button=(tree,label)=>{const value=nodes(tree,n=>n.type==='button'&&text(n)===label)[0];assert.ok(value,'button '+label);return value;};
const svg=tree=>nodes(tree,n=>n.type==='svg')[0];
const transform=tree=>nodes(tree,n=>n.type==='g'&&n.props.transform)[0].props.transform;
const props={graph,view:{objects:[{id:'A',rev:1,kind:'Note',data:{body:'exact fixture body'}}]}};

test('layout preserves historical target needed by current records and shared card uniqueness',()=>{
 const scene=UI.layout(graph);assert.ok(scene.positions.some(p=>p.node.id==='P'));assert.equal(scene.positions.some(p=>p.node.id==='Old'),false);assert.equal(scene.positions.some(p=>p.node.id==='E'),false);
 assert.equal(scene.positions.filter(p=>p.node.id==='O').length,1);
 assert.equal(UI.layout(graph,{showHistory:true,showSupporting:true}).positions.length,graph.nodes.length);
 const collapsed=UI.layout(graph,{collapsed:{r:true}});assert.equal(collapsed.positions.some(p=>p.node.id==='A'),false);assert.equal(collapsed.relations.length,0);
 for(const a of scene.positions)for(const b of scene.positions)if(a!==b&&a.y===b.y)assert.ok(Math.abs(a.x-b.x)>=a.width);
 const byId=new Map(scene.positions.map(p=>[p.node.id,p]));
 assert.equal(byId.get('r').y,byId.get('O').y);assert.ok(byId.get('r').y>=byId.get('P').y+byId.get('P').height);
 assert.equal(byId.get('P').x,(byId.get('r').x+byId.get('O').x)/2);
 assert.ok(byId.get('A').y>byId.get('r').y);
 const h=harness(),tree=h.render(props),branch=nodes(tree,n=>n.props.className==='dm-rg-branch')[0];
 const from=byId.get('P'),to=byId.get('r'),x1=from.x+from.width/2,y1=from.y+from.height,x2=to.x+to.width/2,y2=to.y,mid=(y1+y2)/2;
 assert.equal(nodes(branch,n=>n.type==='path')[0].props.d,`M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`);
});

test('card click opens exact inline reader; only explicit full-record action calls inspect',()=>{
 const h=harness(),calls=[];const input={...props,inspect:o=>calls.push(o)};let tree=h.render(input);tree=h.render(input);
 const card=nodes(tree,n=>n.type==='button'&&n.props.className==='dm-rg-card-main'&&text(n).includes('A@1'))[0];assert.ok(card);card.props.onClick({currentTarget:{focus(){}}});
 tree=h.render(input);const reader=nodes(tree,n=>n.type===h.Reader)[0];assert.ok(reader);assert.equal(reader.props.object,props.view.objects[0]);assert.equal(calls.length,0);
 assert.equal(reader.props.inspect instanceof Function,true);button(tree,'打开完整记录').props.onClick();assert.equal(calls[0].id,'A');
 button(tree,'关闭详情').props.onClick();tree=h.render(input);assert.equal(nodes(tree,n=>n.type===h.Reader).length,0);
});

test('zoom, keyboard pan, fit, collapse and relation toggle are native view operations',()=>{
 const h=harness();let tree=h.render(props);tree=h.render(props);const before=transform(tree);
 button(tree,'放大').props.onClick();tree=h.render(props);assert.notEqual(transform(tree),before);
 const zoomed=transform(tree),s=svg(tree);s.props.onKeyDown({target:s,currentTarget:s,key:'ArrowRight',preventDefault(){}});tree=h.render(props);assert.notEqual(transform(tree),zoomed);
 button(tree,'适应画布').props.onClick();tree=h.render(props);assert.match(transform(tree),/scale/);
 const relation=nodes(tree,n=>n.type==='input')[0];relation.props.onChange({target:{checked:true}});tree=h.render(props);assert.equal(nodes(tree,n=>n.props.className==='dm-rg-relation').length,1);
 button(tree,'折叠 1 项').props.onClick();tree=h.render(props);assert.equal(nodes(tree,n=>n.props.className==='dm-rg-relation').length,0);
 button(tree,'展开全部').props.onClick();tree=h.render(props);assert.equal(nodes(tree,n=>n.props.className==='dm-rg-relation').length,1);
});

test('narrow outline lists visible exact records and locates them at readable scale with inline detail',()=>{
 const h=harness({width:340,height:480});let tree=h.render(props);tree=h.render(props);
 const outline=nodes(tree,n=>n.type==='details'&&n.props.className==='dm-rg-outline')[0];assert.ok(outline);
 assert.equal(nodes(outline,n=>n.type==='button').length,UI.layout(graph).positions.length);assert.equal(text(outline).includes('Old@1'),false);assert.equal(text(outline).includes('E@1'),false);
 const shrink=button(tree,'缩小');for(let i=0;i<10;i++)shrink.props.onClick();tree=h.render(props);
 let restored=false;button(tree,'A · A@1').props.onClick({currentTarget:{focus(){restored=true;}}});tree=h.render(props);
 const values=transform(tree).match(/translate\(([-\d.e]+) ([-\d.e]+)\) scale\(([-\d.e]+)\)/).slice(1).map(Number),[x,y,scale]=values;
 const position=UI.layout(graph).positions.find(p=>p.node.id==='A');assert.ok(scale>=0.65);assert.ok(Math.abs(x+(position.x+position.width/2)*scale-170)<1e-9);assert.ok(Math.abs(y+(position.y+position.height/2)*scale-240)<1e-9);
 const reader=nodes(tree,n=>n.type===h.Reader)[0];assert.equal(reader.props.object,props.view.objects[0]);assert.equal(button(tree,'A · A@1').props['aria-current'],'true');
 button(tree,'关闭详情').props.onClick();assert.equal(restored,true);
 button(tree,'折叠 1 项').props.onClick();tree=h.render(props);const collapsed=nodes(tree,n=>n.props.className==='dm-rg-outline')[0];assert.equal(text(collapsed).includes('A@1'),false);
});

test('wheel zoom prevents page scrolling and cleans up its native listener',()=>{
 const h=harness();let tree=h.render(props);tree=h.render(props);const before=transform(tree),wheel=h.listeners.get('wheel');assert.equal(wheel.options.passive,false);
 let prevented=false;wheel.fn({deltaY:-1,clientX:200,clientY:200,preventDefault(){prevented=true;}});tree=h.render(props);assert.equal(prevented,true);assert.notEqual(transform(tree),before);
 h.unmount();assert.equal(h.listeners.size,0);
});

test('CJS/UMD factories and theme string load without side effects',()=>{
 const sandbox={};for(const name of ['research-graph-ui.cjs','research-graph-theme.cjs'])vm.runInNewContext(readFileSync(new URL('../'+name,import.meta.url),'utf8'),sandbox);
 assert.equal(typeof sandbox.DshMathResearchGraphUI,'function');assert.equal(typeof sandbox.DshMathResearchGraphTheme,'string');
 assert.equal(sandbox.DshMathResearchGraphTheme,require('../research-graph-theme.cjs'));
 assert.match(sandbox.DshMathResearchGraphTheme,/var\(--dm-/);assert.doesNotMatch(sandbox.DshMathResearchGraphTheme,/#(?:[a-fA-F0-9]{3}){1,2}\b/);
});
