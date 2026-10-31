/* Safe local Markdown/LaTeX preview. Bundled dependencies and fonts never use a CDN. */
(() => {
  const katex = require('katex');
  const {Marked} = require('marked');
  function mathToken(src, block) {
    const delimiters = block ? [['$$', '$$'], ['\\[', '\\]']] : [['$', '$'], ['\\(', '\\)']];
    for (const [open, close] of delimiters) {
      if (!src.startsWith(open) || !block && open === '$' && (src.startsWith('$$') || /\s/.test(src[1] || ''))) continue;
      let end = src.indexOf(close, open.length);
      while (end >= 0 && src[end - 1] === '\\' && close === '$') end = src.indexOf(close, end + close.length);
      if (end < 0) continue;
      const text = src.slice(open.length, end);
      if (!text || !block && (text.includes('\n') || /\s$/.test(text))) continue;
      const raw = src.slice(0, end + close.length);
      return {type: block ? 'mathBlock' : 'mathInline', raw, text, display: block};
    }
  }
  const parser = new Marked({gfm: true, breaks: false, async: false, extensions: [
    {name: 'mathBlock', level: 'block', start: src => {const n = src.search(/\$\$|\\\[/); return n < 0 ? undefined : n;}, tokenizer: src => mathToken(src, true)},
    {name: 'mathInline', level: 'inline', start: src => {const n = src.search(/\$|\\\(/); return n < 0 ? undefined : n;}, tokenizer: src => mathToken(src, false)},
  ]});
  function safeHref(href) {
    if (typeof href !== 'string' || /[\u0000-\u0020\u007f]/.test(href)) return null;
    if (/^#[\w.-]+$/.test(href)) return href;
    try { const url = new URL(href); return ['https:', 'http:', 'mailto:'].includes(url.protocol) ? href : null; } catch { return null; }
  }
  function mathHtml(text, display) {
    if (typeof text !== 'string' || text.length > 16384) return null;
    try {
      return katex.renderToString(text, {displayMode: display, throwOnError: false, trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 20, output: 'htmlAndMathml', macros: Object.create(null), globalGroup: false});
    } catch { return null; }
  }
  function safeForNative(tokens) {
    let safe = true;
    parser.walkTokens(tokens, token => {if (['html', 'image', 'link', 'code', 'mathBlock', 'mathInline'].includes(token.type)) safe = false;});
    return safe;
  }
  const fallbackStyle = '.dm-preview{font-size:13px;line-height:1.7;overflow-wrap:anywhere;min-width:0}.dm-preview p{margin:0 0 .75em}.dm-preview pre{overflow:auto;padding:10px;border:1px solid var(--dsw-alias-border-l1,#ddd);border-radius:8px;white-space:pre}.dm-preview code{font-size:.92em}.dm-preview blockquote{margin:8px 0;padding:0 12px;border-left:3px solid var(--dsw-alias-border-l1,#aaa)}.dm-preview table{border-collapse:collapse;display:block;overflow:auto}.dm-preview th,.dm-preview td{border:1px solid var(--dsw-alias-border-l1,#ddd);padding:5px 8px}.dm-preview .dm-math-block{overflow:auto;padding:5px 0}.dm-preview .dm-literal-resource{opacity:.75}.dm-preview .katex{font-size:1.1em}';
  function factory(React, MarkdownText) {
    const h = React.createElement;
    function Preview({text = ''}) {
      const source = String(text);
      const style = (globalThis.DshMathKatexCSS || '') + fallbackStyle;
      if (new TextEncoder().encode(source).byteLength > 256 * 1024) return h('div', {className: 'dm-preview'}, h('p', {role: 'status'}, '内容超过预览上限，请在编辑区查看原文。'));
      let tokens;
      try { tokens = parser.lexer(source); } catch { return h('div', {className: 'dm-preview'}, h('pre', {}, source)); }
      // Use a confirmed public host renderer only for safe non-embedded Markdown.
      // Current hosts without an injected MarkdownText use the complete local renderer below.
      if (typeof MarkdownText === 'function' && safeForNative(tokens)) return h('div', {className: 'dm-preview'}, h('style', {}, style), h(MarkdownText, {text: source, labels: {code: {copyLabel: '复制', copiedLabel: '已复制', toolbarLabels: {codeLabel: '代码', wrapLabel: '自动换行', unwrapLabel: '取消换行'}}, footnotes: ''}}));
      const budget = {math: 256, nodes: 12000};
      function inline(list, key) { return render(list || [], key); }
      function render(list, prefix = 'p') {
        return list.map((token, index) => {
          const key = `${prefix}-${index}`;
          if (--budget.nodes < 0) return token.raw || token.text || '';
          const children = () => inline(token.tokens, key);
          switch (token.type) {
            case 'space': return null;
            case 'heading': return h(`h${Math.max(1, Math.min(6, token.depth))}`, {key}, ...children());
            case 'paragraph': return h('p', {key}, ...children());
            case 'text': return token.tokens ? h(React.Fragment, {key}, ...children()) : token.text;
            case 'escape': return token.text;
            case 'strong': return h('strong', {key}, ...children());
            case 'em': return h('em', {key}, ...children());
            case 'del': return h('del', {key}, ...children());
            case 'br': return h('br', {key});
            case 'hr': return h('hr', {key});
            case 'codespan': return h('code', {key}, token.text);
            case 'code': return h('pre', {key}, h('code', {}, token.text));
            case 'html': return h('span', {key, className: 'dm-literal-resource'}, token.raw || token.text);
            case 'image': return h('span', {key, className: 'dm-literal-resource', title: '远程图片不自动加载'}, `[图片：${token.text || ''}]`);
            case 'link': { const href = safeHref(token.href); return href ? h('a', {key, href, target: href.startsWith('#') ? undefined : '_blank', rel: 'noopener noreferrer', referrerPolicy: 'no-referrer'}, ...children()) : h('span', {key}, ...children()); }
            case 'blockquote': return h('blockquote', {key}, ...children());
            case 'list': return h(token.ordered ? 'ol' : 'ul', {key, ...(token.ordered ? {start: token.start} : {})}, ...token.items.map((item, n) => h('li', {key: `${key}-${n}`}, ...(item.task ? [h('input', {key: 'check', type: 'checkbox', checked: Boolean(item.checked), disabled: true, 'aria-label': '草稿中的任务标记'})] : []), ...render(item.tokens || [], `${key}-${n}`))));
            case 'table': return h('table', {key}, h('thead', {}, h('tr', {}, ...token.header.map((cell, n) => h('th', {key: n}, ...render(cell.tokens || [], `${key}-h${n}`))))), h('tbody', {}, ...token.rows.map((row, n) => h('tr', {key: n}, ...row.map((cell, c) => h('td', {key: c}, ...render(cell.tokens || [], `${key}-r${n}-${c}`)))))));
            case 'mathBlock': case 'mathInline': { const html = budget.math-- > 0 ? mathHtml(token.text, token.display) : null; return html === null ? h(token.display ? 'pre' : 'code', {key}, token.raw) : h(token.display ? 'div' : 'span', {key, className: token.display ? 'dm-math-block' : 'dm-math-inline', dangerouslySetInnerHTML: {__html: html}}); }
            default: return token.tokens ? h(React.Fragment, {key}, ...children()) : token.raw || token.text || '';
          }
        });
      }
      return h('div', {className: 'dm-preview'}, h('style', {}, style), ...render(tokens));
    }
    return {Preview};
  }
  factory.mathToken = mathToken;
  factory.mathHtml = mathHtml;
  factory.safeHref = safeHref;
  factory.lex = text => parser.lexer(text);
  globalThis.DshMathPreview = factory;
  if (typeof module === 'object' && module.exports) module.exports = factory;
})();
