import {readFileSync, writeFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {dirname, join} from 'node:path';
import {build} from 'esbuild';

const require = createRequire(import.meta.url);
const sources = ['forms.cjs', 'form-ui.cjs', 'comparison-ui.cjs', 'project-form.cjs', 'materials-ui.cjs', 'workbench-ui.cjs', 'record-ui.cjs', 'process-graph.cjs', 'graph-ui.cjs', 'client-theme.cjs', 'client-main.cjs'];
for (const source of [...sources, 'math-preview.cjs', 'host.mjs', 'human-api.mjs', 'chat-tools.mjs']) {
  const result = spawnSync(process.execPath, ['--check', source], {stdio: 'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
const descriptor = JSON.parse(readFileSync('project-fields.json', 'utf8'));
if (descriptor.format !== 'dsh-math-project-fields-v1' || descriptor.fields.length !== 9) throw Error('Invalid shared project field descriptor');
for (const language of ['en', 'zh']) {
  const locale = JSON.parse(readFileSync(`locale/${language}.json`, 'utf8'));
  if (!locale.meta?.title || !locale.meta?.description) throw Error(`Missing ${language} plugin metadata`);
}

// Keep KaTeX selectors scoped and font families private. Inline woff2 bytes avoid both
// authenticated asset-route coupling and external requests; no CDN or remote font fallback.
const katexDist = dirname(require.resolve('katex'));
let katexCSS = readFileSync(join(katexDist, 'katex.css'), 'utf8');
let fontCount = 0;
katexCSS = katexCSS.replace(/src:\s*url\(fonts\/([A-Za-z0-9_-]+\.woff2)\)[^;]*;/g, (_, file) => {
  fontCount++;
  return `src:url(data:font/woff2;base64,${readFileSync(join(katexDist, 'fonts', file)).toString('base64')}) format("woff2");`;
}).replace(/KaTeX_/g, 'DshMathKaTeX_');
if (fontCount !== 20 || /url\((?!data:)/.test(katexCSS)) throw Error('Unexpected KaTeX font assets; review dependency update');
katexCSS = `@scope (.dm-preview) {${katexCSS}}`;
const preview = await build({entryPoints: ['math-preview.cjs'], bundle: true, write: false, format: 'iife', platform: 'browser', target: 'es2022', minify: true, legalComments: 'inline'});
const prelude = `globalThis.DshMathProjectFields=${JSON.stringify(descriptor)};\nglobalThis.DshMathKatexCSS=${JSON.stringify(katexCSS)};\n`;
writeFileSync('client.js', prelude + preview.outputFiles[0].text + '\n' + sources.map(source => readFileSync(source, 'utf8')).join('\n'));
const notices = [
  'Third-party code bundled into client.js (this does not change the dsh-math source license).',
  'KaTeX 0.19.1 — https://katex.org/', readFileSync(join(katexDist, '..', 'LICENSE'), 'utf8'),
  'Marked 18.1.2 — https://marked.js.org/', readFileSync(join(dirname(require.resolve('marked')), '..', 'LICENSE'), 'utf8'),
].join('\n\n');
writeFileSync('THIRD-PARTY-NOTICES.txt', notices.trimEnd() + '\n');
console.log(`Built dsh-math client with local Markdown/KaTeX and ${fontCount} embedded font faces.`);
