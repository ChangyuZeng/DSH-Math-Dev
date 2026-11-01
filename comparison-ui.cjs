(function (publish) {
  const text = value => value == null ? '' : Array.isArray(value) ? value.join('\n') : String(value);
  // Preserve line endings and whitespace: comparison must never normalize mathematical text.
  const lines = value => text(value).match(/[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$/g) || [];
  function diffLines(local, remote) {
    const a = lines(local), b = lines(remote), rows = [];
    let i = 0, j = 0;
    const add = (kind, left, right) => rows.push({kind, local: left, remote: right,
      localLine: left == null ? null : ++i, remoteLine: right == null ? null : ++j});
    // Bound memory/work for long pasted documents. Positional fallback still shows every byte.
    if (a.length + b.length > 5000 || a.length * b.length > 250000) {
      while (i < a.length || j < b.length) {
        const left = a[i] ?? null, right = b[j] ?? null;
        add(left === right ? 'same' : left == null ? 'remote' : right == null ? 'local' : 'changed', left, right);
      }
      return rows;
    }
    const table = Array.from({length: a.length + 1}, () => new Uint32Array(b.length + 1));
    for (let x = a.length - 1; x >= 0; x--) for (let y = b.length - 1; y >= 0; y--)
      table[x][y] = a[x] === b[y] ? table[x + 1][y + 1] + 1 : Math.max(table[x + 1][y], table[x][y + 1]);
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) add('same', a[i], b[j]);
      else if (i < a.length && (j === b.length || table[i + 1][j] >= table[i][j + 1])) add('local', a[i], null);
      else add('remote', null, b[j]);
    }
    return rows;
  }
  function compareFields(fields, local = {}, remote = {}) {
    return fields.map(field => ({key: field.key, label: field.label || field.key, local: text(local[field.key]), remote: text(remote[field.key]),
      changed: JSON.stringify(local[field.key] ?? '') !== JSON.stringify(remote[field.key] ?? '')}));
  }
  function factory(React) {
    const h = React.createElement;
    const value = content => h('pre', {className: 'dm-comparison-value'}, content === '' ? '（空）' : content);
    const ending = content => content.endsWith('\r\n') ? 'CRLF 换行' : content.endsWith('\n') ? 'LF 换行' : content.endsWith('\r') ? 'CR 换行' : '无末尾换行';
    const lineLabel = (label, number, content, kind) => number == null ? `${label} · 此侧无对应行` : `${label} · 第 ${number} 行 · ${kind === 'same' ? '相同' : '不同'} · ${ending(content)}`;
    function TextComparison({local = '', remote = '', localLabel = '我的内容', remoteLabel = '服务器内容', localVersion = '', remoteVersion = ''}) {
      const rows = diffLines(local, remote), changed = rows.filter(row => row.kind !== 'same').length;
      const [page, setPage] = React.useState({local, remote, localVersion, remoteVersion, limit: 200});
      const sameInput = page.local === local && page.remote === remote && page.localVersion === localVersion && page.remoteVersion === remoteVersion;
      const limit = sameInput ? page.limit : 200, shown = Math.min(limit, rows.length);
      // Reset immediately when a different comparison arrives, without first
      // mounting the old expanded row count. Only presentation state changes.
      if (!sameInput) setPage({local, remote, localVersion, remoteVersion, limit: 200});
      return h('section', {className: 'dm-comparison', 'aria-label': '正文逐行比较'},
        h('p', {className: 'dm-comparison-summary'}, changed ? '以下标明双方不同的行；不会自动合并。' : '正文逐字相同。'),
        h('div', {className: 'dm-comparison-head'}, h('strong', null, `${localLabel}${localVersion ? ` · ${localVersion}` : ''}`), h('strong', null, `${remoteLabel}${remoteVersion ? ` · ${remoteVersion}` : ''}`)),
        h('p', {className: 'dm-comparison-count', 'aria-live': 'polite'}, `已展示 ${shown} / ${rows.length} 行比较。原文保持完整，未展示的行可按需继续展开。`),
        ...rows.slice(0, limit).map((row, index) => h('div', {key: index, className: `dm-comparison-row dm-diff-${row.kind}`},
          h('div', null, h('span', {className: 'dm-comparison-line'}, lineLabel(localLabel, row.localLine, row.local, row.kind)), row.local != null && value(row.local)),
          h('div', null, h('span', {className: 'dm-comparison-line'}, lineLabel(remoteLabel, row.remoteLine, row.remote, row.kind)), row.remote != null && value(row.remote)))),
        shown < rows.length && h('button', {type: 'button', className: 'dm-button dm-small dm-comparison-more',
          onClick: () => setPage(previous => ({...previous, limit: previous.limit + 200}))}, `显示更多行（再显示 ${Math.min(200, rows.length - shown)} 行）`),
        !rows.length && h('p', null, '双方正文均为空。'));
    }
    function FieldComparison({fields = [], local = {}, remote = {}, localLabel = '我的内容', remoteLabel = '服务器内容', localVersion = '', remoteVersion = ''}) {
      return h('section', {className: 'dm-comparison-fields', 'aria-label': '逐字段比较'},
        ...compareFields(fields, local, remote).map(field => h('details', {key: field.key, className: 'dm-comparison-field', open: field.changed},
          h('summary', null, `${field.label} · ${field.changed ? '内容不同' : '相同'}`),
          h(TextComparison, {local: field.local, remote: field.remote, localLabel, remoteLabel, localVersion, remoteVersion}))));
    }
    return {TextComparison, FieldComparison};
  }
  factory.diffLines = diffLines;
  factory.compareFields = compareFields;
  publish(factory);
})(value => { if (typeof module !== 'undefined') module.exports = value; globalThis.DshMathComparisonUI = value; });
