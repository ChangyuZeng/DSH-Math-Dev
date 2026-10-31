import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const F=createRequire(import.meta.url)('../forms.cjs');
test('reference choices preserve revisions and isolate target and kind',()=>{
 const p={id:'P',rev:1};const objects=[{id:'N',rev:1,kind:'Note',data:{problem_ref:p}},{id:'N',rev:2,kind:'Note',data:{problem_ref:p}},{id:'X',rev:1,kind:'Note',data:{problem_ref:{id:'P',rev:2}}},{id:'E',rev:1,kind:'Evidence',data:{problem_ref:p}}];
 assert.deepEqual(F.choices(objects,['Note'],p).map(F.refKey),['N@1','N@2']);
 assert.deepEqual(F.choices(objects,['Note'],p,'N'),[]);
});
test('draft saves exact text, immutable revision and stable operation identity',()=>{
 let n=0;const d=F.draft('Note',{id:'P',rev:1},()=>`id-${++n}`);d.data.statement='det >= 17776121037665193653653203126';d.data.evidence=[{id:'E',rev:1}];
 const a=F.request(d),b=F.request(d);assert.deepEqual(a,b);assert.equal(a.changes[0].data.statement,d.data.statement);assert.equal(a.changes[0].data.evidence[0].rev,1);
 const revised=F.draft('Note',{id:'P',rev:1},()=>`id-${++n}`,{id:'N',rev:3,data:d.data});assert.equal(F.request(revised).changes[0].expected_rev,3);
});
test('evidence request includes explicit file bytes and excerpt without hidden authority',()=>{
 const d=F.draft('Evidence',{id:'P',rev:1},()=> 'fixture');d.files=[{logical_name:'test.bin',base64:'AP8='}];d.excerpt='原文';const r=F.request(d);
 assert.equal(r.files[0].base64,'AP8=');assert.equal(r.excerpt,'原文');assert.equal(r.data.source_status,'unverified_source');assert.ok(!('actor' in r));
});
