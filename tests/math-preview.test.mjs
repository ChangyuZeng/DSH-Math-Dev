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
