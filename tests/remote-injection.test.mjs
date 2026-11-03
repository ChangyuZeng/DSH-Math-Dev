import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {Context} from '@deepseek-ai/cordis';

test('material send bridge declares Remote in a real Cordis scope', async () => {
  let registration;
  const React = {createElement() {}, useState() {}, useEffect() {}, useRef() {}};
  const globals = {window: {__ModuleLoader__: {load(value) { registration = value; }}},
    DshMathForms: {}, DshMathPreview: () => ({}), DshMathFormUI: () => ({}),
    DshMathRecordUI: () => ({}), DshMathProjectForm: () => ({}), DshMathWorkbenchUI: () => ({}),
    DshMathGraphUI: () => ({}), DshMathResearchGraphUI: () => ({}), DshMathProjectAccessUI: () => ({}),
    DshMathLibraryUI: () => ({}), DshMathExportUI: () => ({}), DshMathProjectManagerUI: () => ({})};
  vm.createContext(globals);
  vm.runInContext(readFileSync(new URL('../workflow-ui.cjs', import.meta.url), 'utf8'), globals);
  vm.runInContext(readFileSync(new URL('../client-main.cjs', import.meta.url), 'utf8'), globals);
  const plugin = registration.factory(() => React);
  const root = new Context(), sends = [], descriptors = [];
  root.provide('slots', {inject(_name, callback) { callback(); }, register(descriptor) { descriptors.push(descriptor); }});
  root.provide('uiConversation', {views: {register() {}}});
  root.provide('uiWorkspace', {openSession() {}});
  root.plugin({name: 'remote-fixture-provider', apply(ctx) {
    ctx.provide('remote', {session: {prompt: async request => { sends.push(request); return {ok: true, value: {accepted: true}}; }}});
  }});
  root.plugin(plugin);
  await new Promise(resolve => setImmediate(resolve));
  const bridge = descriptors.find(row => row.id === 'math')?.inject().organizeMaterials;
  assert.equal(typeof bridge, 'function');
  const result = await bridge('fixture-session', 'fixture-material', 'stable-request');
  assert.equal(result.accepted, true);
  assert.equal(sends.length, 1);
  assert.equal(sends[0].requestId, 'stable-request');
});
