(function (publish) {
  const CHUNK = 384 * 1024;
  async function downloadSelection({request, sessionId, projectId, receipt, isCurrent = () => true}) {
    let offset = 0; const parts = [];
    do {
      if (!isCurrent()) throw Error('项目已切换，请重新导出。');
      const part = await request('selection-bytes', {session_id: sessionId, project_id: projectId, export_id: receipt.export_id, offset, limit: CHUNK});
      if (!isCurrent()) throw Error('项目已切换，请重新导出。');
      if (part.offset !== offset || part.sha256 !== receipt.sha256 || part.bytes !== receipt.bytes || part.name !== receipt.name || part.bytes > 200 * 1024 * 1024) throw Error('导出文件版本不一致。');
      const raw = Uint8Array.from(atob(part.base64), c => c.charCodeAt(0)), end = offset + raw.length;
      if (raw.length > CHUNK || end > part.bytes || (part.next_offset === null ? end !== part.bytes : part.next_offset !== end || end <= offset || end >= part.bytes)) throw Error('导出文件分段不完整。');
      parts.push(raw); offset = part.next_offset;
    } while (offset !== null);
    const blob = new Blob(parts, {type: 'application/zip'});
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())), x => x.toString(16).padStart(2, '0')).join('');
    if (hash !== receipt.sha256 || !isCurrent()) throw Error('导出文件校验失败或项目已切换。');
    return blob;
  }
  function factory(React) {
    const {createElement: h, useState, useEffect, useRef} = React;
    function ExportPanel({request, sessionId, projectId, view, onAuditExport}) {
      const [library, setLibrary] = useState(null), [selected, setSelected] = useState([]), [busy, setBusy] = useState(false), [error, setError] = useState(''), [receipt, setReceipt] = useState(null), [category, setCategory] = useState('all');
      const [options, setOptions] = useState({include_material_originals: false, include_dependencies: false, include_process: false, include_summary: true});
      const current = useRef({sessionId, projectId, generation: 0});
      if (current.current.sessionId !== sessionId || current.current.projectId !== projectId) current.current = {sessionId, projectId, generation: current.current.generation + 1};
      const generation = current.current.generation;
      const same = () => current.current.generation === generation;
      const refresh = async () => {
        setError(''); setReceipt(null); setSelected([]);
        try {
          let offset = 0, result = null; const items = [];
          do {
            const page = await request('library-list', {session_id: sessionId, project_id: projectId, include_removed: false, limit: 100, offset});
            if (!same()) return;
            if (result && JSON.stringify(page.version) !== JSON.stringify(result.version)) throw Error('目录有变化，请刷新后重试。');
            result = page; items.push(...page.items); offset = page.next_offset;
          } while (offset !== null && items.length < 500);
          if (same()) setLibrary({...result, items});
        }
        catch (e) { if (same()) setError(e.message); }
      };
      useEffect(() => { setLibrary(null); setSelected([]); setReceipt(null); setBusy(false); setError(''); if (projectId) refresh(); }, [sessionId, projectId, view?.snapshot?.state?.seq]);
      async function submit() {
        if (busy || !library) return;
        setBusy(true); setError(''); setReceipt(null);
        try {
          const rows = library.items.filter((_, i) => selected.includes(i));
          const result = await request('selection-export', {session_id: sessionId, project_id: projectId,
            expected_seq: view.snapshot.state.seq, expected_library_version: library.version,
            selected_refs: rows.filter(row => row.kind !== 'material').map(row => row.ref),
            material_refs: rows.filter(row => row.kind === 'material').map(row => row.ref), ...options});
          if (!same()) return;
          setReceipt(result);
          const blob = await downloadSelection({request, sessionId, projectId, receipt: result, isCurrent: same});
          const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = result.name; a.click();
          setTimeout(() => URL.revokeObjectURL(url), 30000);
        } catch (e) { if (same()) setError(e.message); }
        finally { if (same()) setBusy(false); }
      }
      const rows = (library?.items || []).filter(row => row.ref && !row.removed && !['scratchpad', 'draft', 'project'].includes(row.kind) && (category === 'all' || row.kind === category));
      return h('section', {className: 'dm-card', 'aria-label': '导出研究内容'},
        h('h3', null, '导出研究内容'), h('p', {className: 'dm-local-hint'}, '选择要分享的版本。默认只列出未移除条目；依赖仅保留名称，私有草稿不会导出。'),
        error && h('p', {role: 'alert', className: 'dm-error'}, error),
        h('button', {className: 'dm-button', disabled: busy, onClick: refresh}, '刷新目录'),
        h('label', {className: 'dm-field'}, '内容类别', h('select', {value: category, disabled: busy, onChange: e => setCategory(e.target.value)}, ...Object.entries({all: '全部', Problem: '研究问题', Note: '笔记与路线', Obligation: '待完成条件', Evidence: '证据', material: '导入材料'}).map(([value, label]) => h('option', {key: value, value}, label)))),
        h('button', {className: 'dm-button', disabled: busy || !rows.length, onClick: () => setSelected(old => [...new Set([...old, ...rows.map(row => library.items.indexOf(row))])])}, '选择当前类别'),
        h('button', {className: 'dm-button', disabled: busy || !selected.length, onClick: () => setSelected([])}, '清空选择'),
        library?.next_offset !== null && library?.items?.length >= 500 && h('p', {className: 'dm-local-hint'}, '此处最多显示500条；单次导出最多500个版本。'),
        h('div', {className: 'dm-library-export-list'}, ...rows.map(row => { const index = library.items.indexOf(row); return h('label', {key: row.ref.id + '@' + row.ref.rev, className: 'dm-field'},
          h('input', {type: 'checkbox', disabled: busy, checked: selected.includes(index), onChange: e => setSelected(old => e.target.checked ? [...old, index] : old.filter(i => i !== index))}), ` ${row.title || row.kind} · 第 ${row.ref.rev} 版`); })),
        library && !rows.length && h('p', null, '没有可导出的已保存条目。'),
        ...Object.entries({include_material_originals: '包含所选材料原件', include_dependencies: '同时包含未移除的依赖正文', include_process: '包含所选内容过程图和提纲', include_summary: '包含项目摘要'}).map(([key, text]) => h('label', {key, className: 'dm-field'}, h('input', {type: 'checkbox', disabled: busy, checked: options[key], onChange: e => setOptions(old => ({...old, [key]: e.target.checked}))}), ' ' + text)),
        h('button', {className: 'dm-button dm-primary', disabled: busy || !selected.length || !view?.snapshot, onClick: submit}, busy ? '生成并下载…' : `导出所选 ZIP（${selected.length}）`),
        receipt && h('p', {role: 'status'}, '已生成可校验 ZIP；解压后可阅读，验证命令见说明。'),
        onAuditExport && h('button', {className: 'dm-button', disabled: busy, onClick: onAuditExport}, '完整审核包（保留全部审核证据）'));
    }
    return {ExportPanel};
  }
  factory.downloadSelection = downloadSelection; publish(factory);
})(value => { if (typeof module !== 'undefined') module.exports = value; else globalThis.DshMathExportUI = value; });
