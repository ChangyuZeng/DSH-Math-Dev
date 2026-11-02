// Fresh in-memory engineering fixtures. No projects, runs, checkers or research calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const require = createRequire(import.meta.url);
const Process = require('../process-graph.cjs');
const Graph = require('../research-graph.cjs');
const ref = (id, rev = 1) => ({id, rev});
const obj = (kind, id, rev = 1, data = {}) => ({schema_version: 4, id, rev, kind, data: {title: '合成 ' + id, statement: 'fixture only', parent: null, problem_ref: ref('P'), route_id: null, uses: [], candidate_for: [], candidates: [], evidence: [], dependencies: [], ...data}});
function fixture(records) {
  const objects = Object.fromEntries(Object.entries(Object.groupBy(structuredClone(records), o => o.id))), hash = 'a'.repeat(64);
  const view = {project: {id:'fixture', campaign_id:'campaign', title:'合成研究树', schema_version:4}, snapshot:{usable:true,state:{seq:1,head_hash:hash,campaign_id:'campaign',objects,research:{claims:[],routes:{}}}},objects:records,groups:{},links:[]};
  const event = {schema_version:4,campaign_id:'campaign',seq:1,event_id:'fixture-event',kind:'commit',changes:structuredClone(records),event_hash:hash,previous_hash:'0'.repeat(64),actor:{kind:'human'}};
  const replay = {format:'dsh-math-replay-v1',research_head:hash,sources:[],events:[{source:'research/events.jsonl',seq:1,hash,kind:'research.commit',data:event}]};
  return {view,replay};
}
const build = f => Graph.build(f.view, Process.build(f.view,f.replay));
const node = (g,id,rev=1) => g.nodes.find(n => n.ref?.id === id && n.ref.rev === rev);

test('exact route controls, historical Problems and canonical shared obligations', () => {
  const f = fixture([obj('Problem','P'),obj('Problem','P',2),obj('Problem','Child',1,{parent:ref('P')}),obj('Note','A',1,{route_id:'R1',candidate_for:[ref('O')]}),obj('Note','B',1,{route_id:'R2',candidate_for:[ref('O')]}),obj('Note','New',1,{problem_ref:ref('P',2),route_id:'R1'}),obj('Note','Null'),obj('Obligation','O'),obj('Note','F',1,{route_id:'R2',note_type:'failed_attempt',failure:{negated_scope:'local_claim',observed:'synthetic failure'},gaps:['gap']})]);
  f.view.snapshot.state.research.routes = {R1:{route_id:'R1',problem_ref:ref('P'),status:'paused',reason:'fixture reason',next_action:'fixture next',rev:2},R2:{route_id:'R2',problem_ref:ref('P'),status:'abandoned',rev:1}};
  f.view.routes = structuredClone(f.view.snapshot.state.research.routes);
  const before=structuredClone(f), g=build(f);
  const routes = g.nodes.filter(n=>n.kind==='route');
  assert.equal(routes.find(n=>n.route_id==='R1'&&n.problem_ref.rev===1).status,'paused');
  assert.equal(routes.find(n=>n.route_id==='R1'&&n.problem_ref.rev===2).status,'unregistered');
  assert.match(routes.find(n=>n.route_id===null).statusLabel,/未指定路线/);
  assert.equal(node(g,'Child').parentId,node(g,'P').id);
  assert.equal(node(g,'P').historical,true);
  assert.equal(g.nodes.filter(n=>n.ref?.id==='O').length,1);
  assert.equal(node(g,'O').parentId,node(g,'P').id);
  assert.equal(g.relations.filter(e=>e.relation==='candidate_for').length,2);
  assert.ok(g.hierarchy.every(e=>e.relation==='display_parent'));
  assert.equal(node(g,'F').failure.negated_scope,'local_claim');
  assert.deepEqual(f,before);
});

test('assessments never leak acceptance to another revision or target', () => {
  const old=obj('Note','N'), latest=obj('Note','N',2), other=obj('Note','Other');
  const f=fixture([obj('Problem','P'),old,latest,other]);
  f.view.groups={accepted:[{object:latest,assessment:{ref:ref('N',2),state:'accepted',reasons:['local_claim'],review_id:'R1'}}],notes:[{object:other,assessment:{ref:ref('N',2),state:'accepted'}}]};
  const g=build(f);
  assert.equal(node(g,'N').status,'unassessed'); assert.equal(node(g,'N',2).status,'accepted'); assert.equal(node(g,'Other').status,'unassessed');
  assert.match(g.notice,/R1\/R2/); assert.match(g.notice,/local_claim/);
});

test('integrity gate rejects stale head, different project, missing revisions and route drift', () => {
  const f=fixture([obj('Problem','P'),obj('Note','N')]), gate=Process.build(f.view,f.replay);
  for(const patch of [{research_head:'b'.repeat(64)},{project_id:'foreign'},{research_seq:2},{format:'unknown'}]) assert.throws(()=>Graph.build(f.view,{...gate,...patch}),/研究树/);
  f.view.objects[1].data.title='changed'; assert.throws(()=>Graph.build(f.view,gate),/概览与快照/);
  const fresh=fixture([obj('Problem','P')]);fresh.view.routes={bad:{route_id:'bad',problem_ref:ref('P'),status:'active'}};
  assert.throws(()=>build(fresh),/路线概览与快照不一致/);
});

test('unresolved exact ref stays independent and no timestamp/title joins are invented', () => {
  const f=fixture([obj('Problem','P'),obj('Note','Lost',1,{problem_ref:ref('P',99),route_id:'R1'}),obj('Evidence','E'),obj('Review','R',1,{target:ref('Lost'),candidate:null,scope:'local_claim',method:'human',verdict:'pass'})]);
  const g=build(f); assert.equal(node(g,'Lost').parentId,null); assert.equal(node(g,'Lost').unresolved,true);
  assert.equal(node(g,'R').parentId,node(g,'Lost').id);assert.equal(node(g,'Lost').status,'unassessed');
  assert.equal(g.relations.some(e=>e.relation==='causes'),false);
});

test('CJS and browser publication agree and process v1 is preserved', () => {
  const sandbox={};vm.runInNewContext(readFileSync(new URL('../research-graph.cjs',import.meta.url),'utf8'),sandbox);
  assert.equal(typeof sandbox.DshMathResearchGraph.build,'function');
  const f=fixture([obj('Problem','P')]),gate=Process.build(f.view,f.replay),before=Process.serialize(gate);
  assert.equal(JSON.stringify(sandbox.DshMathResearchGraph.build(f.view,gate)),JSON.stringify(Graph.build(f.view,gate)));
  assert.equal(Process.serialize(gate),before);assert.equal(gate.format,'dsh-math-process-graph-v1');
});
