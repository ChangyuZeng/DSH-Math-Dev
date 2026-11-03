import assert from 'node:assert/strict';
import {readFileSync, statSync} from 'node:fs';
import test from 'node:test';

const root = new URL('../', import.meta.url);
const json = name => JSON.parse(readFileSync(new URL(name, root), 'utf8'));

test('rc5 preview declares its runtime target and includes the admission module', () => {
  const manifest = json('package.json');
  assert.equal(manifest.version, '0.11.0');
  for (const file of ['workflow_store.py', 'workflow_context.py', 'workflow_service.py', 'workflow_strategy.py', 'workflow_runner.mjs', 'workflow-ui.cjs', 'contracts/workflow-v1.md', 'contracts/workflow-evaluation-v1.md']) {
    assert.ok(manifest.files.includes(file), `${file} must be packaged`);
    assert.ok(statSync(new URL(file, root)).isFile());
  }
  for (const file of ['library_store.py', 'library-ui.cjs', 'project-manager-ui.cjs', 'export-ui.cjs', 'selection_export.py', 'verify_selection.py']) {
    assert.ok(manifest.files.includes(file), `${file} must be packaged`);
    assert.ok(statSync(new URL(file, root)).isFile());
  }
  assert.ok(manifest.dsh.client.inject.includes('@deepseek-ai/dsh-api-remotes'));
  assert.equal(manifest.dependencies.mammoth, '1.13.0');
  assert.equal(manifest.dependencies['pdfjs-dist'], '6.4.299');
  for (const file of ['materials-ui.cjs', 'material_store.py', 'material_extract.mjs', 'chat_research.py', 'project_access.py', 'project-access-ui.cjs', 'research-graph.cjs', 'research-graph-ui.cjs', 'research-graph-theme.cjs']) {
    assert.ok(manifest.files.includes(file), `${file} must be packaged`);
    assert.ok(statSync(new URL(file, root)).isFile());
  }
  for (const file of ['record-ui.cjs', 'comparison-ui.cjs', 'chat-tools.mjs', 'preparation_tools.py', 'workbench_store.py', 'project-fields.json', 'project-form.cjs', 'workbench-ui.cjs', 'math-preview.cjs', 'THIRD-PARTY-NOTICES.txt']) {
    assert.ok(manifest.files.includes(file), `${file} must be packaged`);
    assert.ok(statSync(new URL(file, root)).isFile());
  }
  assert.equal(manifest.devDependencies.katex, '0.19.1');
  assert.equal(manifest.devDependencies.marked, '18.1.2');
  assert.ok(manifest.files.includes('process-graph.cjs'));
  assert.ok(manifest.files.includes('graph-ui.cjs'));
  assert.equal(manifest.engines.dsh, '0.2.1-rc.5');
  assert.equal(manifest.peerDependencies['@deepseek-ai/dsh-client-connection'], '0.2.1-rc.5');
  assert.equal(manifest.peerDependencies['@deepseek-ai/dsh-host-webserver'], '0.2.1-rc.5');
  assert.ok(manifest.files.includes('human-api.mjs'));
  assert.ok(readFileSync(new URL('host.mjs', root), 'utf8').includes("from './human-api.mjs'"));
});

test('display resources are packaged and bilingual, without remote icon resources', () => {
  const manifest = json('package.json');
  assert.equal(manifest.exports['./locale/*.json'], './locale/*.json');
  assert.equal(manifest.exports['./icon'], './icon.svg');
  assert.ok(manifest.files.includes('locale/*.json'));
  assert.ok(manifest.files.includes('icon.svg'));
  for (const language of ['en', 'zh']) {
    const locale = json(`locale/${language}.json`);
    assert.ok(locale.meta.title.length > 2);
    assert.ok(locale.meta.description.length > 20);
  }
  const icon = readFileSync(new URL('icon.svg', root), 'utf8');
  assert.ok(statSync(new URL('icon.svg', root)).size < 256 * 1024);
  assert.match(icon, /<svg[^>]*viewBox="0 0 64 64"/);
  assert.doesNotMatch(icon, /<script|<foreignObject|\bhref=|\bon\w+=/i);
  // Structural checks only. The installed icon still needs real visual inspection.
});
