import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url), preview = require('../math-preview.cjs');
const React = {Fragment: 'fragment', createElement(type, props, ...children) {return {type, props: props || {}, children};}};
const {Preview} = preview(React);
function nodes(tree) {return tree && typeof tree === 'object' ? [tree, ...(tree.children || []).flat(Infinity).flatMap(nodes)] : [];}
function text(tree) {return tree && typeof tree === 'object' ? (tree.children || []).flat(Infinity).map(text).join('') : String(tree ?? '');}

test('Markdown headings, lists, fenced code and tables render without raw HTML injection', () => {
  const tree = Preview({text: '# 研究\n\n**大胆猜想** 与 `x < y`\n\n- [x] 观察\n- 未核实\n\n| 变量 | 值 |\n| --- | --- |\n| n | 1 |\n\n```python\nprint("$x$")\n```'});
  const rendered = nodes(tree);
  for (const tag of ['h1', 'strong', 'code', 'ul', 'input', 'table', 'pre']) assert.ok(rendered.some(node => node.type === tag), tag);
  assert.equal(rendered.some(node => node.props.dangerouslySetInnerHTML), false);
  assert.match(text(tree), /print\("\$x\$"\)/);
  assert.equal(rendered.find(node => node.type === 'input').props.disabled, true);
});

test('inline and display math use safe locally rendered KaTeX with accessible MathML', () => {
  const tree = Preview({text: '设 $n \\in \\mathbb{N}$。\n\n$$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$$\n\n\\[x^2\\]\n\n也可 \\(a+b\\)。'});
  const rendered = nodes(tree).filter(node => node.props.dangerouslySetInnerHTML);
  assert.equal(rendered.length, 4);
  for (const node of rendered) {
    assert.match(node.props.dangerouslySetInnerHTML.__html, /class="katex/);
    assert.match(node.props.dangerouslySetInnerHTML.__html, /<math/);
    assert.doesNotMatch(node.props.dangerouslySetInnerHTML.__html, /<script|<iframe|<img|<object|onerror=/);
  }
  assert.equal(preview.mathToken('$5 and $20', false), undefined);
});

test('HTML and image input stays inert and links allow only explicit safe schemes', () => {
  const tree = Preview({text: '<script>alert(1)</script>\n\n<img src="https://bad.invalid/a" onerror="x">\n\n![remote](https://bad.invalid/track)\n\n[x](javascript:alert) [file](file:///etc/passwd) [ok](https://example.org/a)'});
  assert.equal(nodes(tree).some(node => ['script', 'img', 'iframe', 'object'].includes(node.type)), false);
  assert.equal(nodes(tree).some(node => node.props.dangerouslySetInnerHTML), false);
  const links = nodes(tree).filter(node => node.type === 'a');
  assert.equal(links.length, 1);
  assert.equal(links[0].props.href, 'https://example.org/a');
  assert.equal(links[0].props.rel, 'noopener noreferrer');
  assert.match(text(tree), /<script>alert\(1\)<\/script>/);
  for (const href of ['javascript:1', 'data:text/html,<b>x</b>', 'file:///x', 'https:\n//example.org', '//example.org', '/dsh-math/api']) assert.equal(preview.safeHref(href), null);
});

test('KaTeX trust is false, macro state is isolated and resource limits keep source recoverable', () => {
  for (const source of ['\\href{javascript:alert(1)}{x}', '\\includegraphics{https://bad.invalid/x}', '\\htmlClass{evil}{x}']) {
    const html = preview.mathHtml(source, false);
    assert.doesNotMatch(html, /<a\b|<img\b|class="evil"/);
  }
  preview.mathHtml('\\gdef\\secretmacro{x}\\secretmacro', false);
  assert.match(preview.mathHtml('\\secretmacro', false), /katex-error|color:#cc0000/);
  assert.equal(preview.mathHtml('x'.repeat(16385), false), null);
  const over = Preview({text: 'x'.repeat(256 * 1024 + 1)});
  assert.match(text(over), /超过预览上限/);
  const many = Preview({text: Array(300).fill('$x$').join(' ')});
  assert.equal(nodes(many).filter(node => node.props.dangerouslySetInnerHTML).length, 256);
  assert.match(text(many), /\$x\$/);
});

test('optional native renderer is used only for safe non-embedded Markdown', () => {
  const Native = () => null, {Preview: withNative} = preview(React, Native);
  assert.ok(nodes(withNative({text: '**普通文本**'})).some(node => node.type === Native));
  for (const source of ['![x](https://bad.invalid)', '$x$', '<b>x</b>', '[x](https://example.org)']) assert.equal(nodes(withNative({text: source})).some(node => node.type === Native), false);
});

function withoutStyles(tree) {
  if (tree == null || tree === false || tree?.type === 'style') return null;
  if (typeof tree !== 'object') return tree;
  return {...tree, children: tree.children.flat(Infinity).map(withoutStyles).filter(value => value != null)};
}

test('shared styles return the complete supplied font CSS once while default previews remain self-contained', t => {
  const previous = globalThis.DshMathKatexCSS;
  t.after(() => { if (previous === undefined) delete globalThis.DshMathKatexCSS; else globalThis.DshMathKatexCSS = previous; });
  delete globalThis.DshMathKatexCSS;
  const fallback = preview.styles();
  assert.match(fallback, /\.dm-preview\{/); assert.match(fallback, /\.dm-preview \.katex\{/);
  // Synthetic bundled data: exercise the full 500KiB payload, not a shortened
  // font URL. Packaging/font production is unchanged by this rendering mode.
  const bundled = '@font-face{font-family:KaTeX_Main;src:url(data:font/woff2;base64,' + 'A'.repeat(500 * 1024) + ')}';
  globalThis.DshMathKatexCSS = bundled;
  assert.equal(preview.styles(), bundled + fallback);
  const defaultPreview = preview(React).Preview, explicitDefault = preview(React, undefined, {sharedStyles: false}).Preview;
  for (const render of [defaultPreview, explicitDefault]) {
    const styleNodes = nodes(render({text: '$x$'})).filter(node => node.type === 'style');
    assert.equal(styleNodes.length, 1); assert.equal(styleNodes[0].children[0], bundled + fallback);
  }
  const Shared = preview(React, undefined, {sharedStyles: true}).Preview;
  const documents = ['# 标题\n\n$x^2$ 与 **正文**', '$$\\sum_{i=1}^n i$$', '普通正文'];
  const trees = documents.map(source => Shared({text: source}));
  assert.equal(trees.flatMap(nodes).filter(node => node.type === 'style').length, 0);
  assert.equal(trees.some(tree => text(tree).includes(bundled)), false, 'Large CSS never becomes repeated body text');
  for (let index = 0; index < documents.length; index++)
    assert.deepEqual(withoutStyles(trees[index]), withoutStyles(defaultPreview({text: documents[index]})));
  assert.doesNotMatch(preview.styles(), /url\(["']?https?:|@import/);
  globalThis.DshMathKatexCSS = bundled + '/*updated host bundle*/';
  assert.equal(preview.styles(), globalThis.DshMathKatexCSS + fallback, 'Host registration reads current complete CSS, not a stale cached payload');
});

test('shared local and native previews preserve rendering safety and existing resource limits', () => {
  const Native = () => null;
  for (const renderer of [undefined, Native]) {
    const ordinary = preview(React, renderer).Preview, shared = preview(React, renderer, {sharedStyles: true}).Preview;
    for (const source of ['**普通文本**', '# h\n\n- [x] task\n\n```js\nx < y\n```',
      '<script>alert(1)</script>\n\n![x](https://bad.invalid/x)\n\n[bad](javascript:alert) [ok](https://example.org)',
      '$\\includegraphics{https://bad.invalid/x}$ 与 $x$', 'x'.repeat(256 * 1024 + 1), Array(260).fill('$x$').join(' ')]) {
      const tree = shared({text: source});
      assert.equal(nodes(tree).some(node => node.type === 'style'), false);
      assert.deepEqual(withoutStyles(tree), withoutStyles(ordinary({text: source})));
      assert.equal(nodes(tree).some(node => ['script', 'img', 'iframe', 'object'].includes(node.type)), false);
      for (const node of nodes(tree).filter(node => node.props.dangerouslySetInnerHTML))
        assert.doesNotMatch(node.props.dangerouslySetInnerHTML.__html, /<script|<iframe|<img|<object|onerror=/);
    }
    assert.equal(nodes(shared({text: '**普通文本**'})).some(node => node.type === Native), Boolean(renderer));
  }
});
