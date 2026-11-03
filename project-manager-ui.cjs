(function (publish) {
  'use strict';
  const retained = new Map();
  if (typeof window !== 'undefined') window.addEventListener?.('beforeunload', event => {
    if ([...retained.values()].some(scope => scope.state.attempt)) { event.preventDefault(); event.returnValue = ''; }
  });
  function createProjectManagerController({request, sessionId, notify = () => {}, onChange = () => {}, uuid = () => globalThis.crypto.randomUUID()}) {
    let scope = retained.get(sessionId);
    if (!scope) {
      scope = {state: {items: [], selected: null, error: '', busy: false, attempt: null, conflict: false}, listeners: new Set(), changes: new Set(), listing: 0};
      retained.set(sessionId, scope);
    }
    let active = true;
    scope.listeners.add(notify); scope.changes.add(onChange);
    const emit = patch => { scope.state = {...scope.state, ...patch}; for (const listener of scope.listeners) listener(scope.state); };
    const base = project => ({session_id: sessionId, project_id: project.id, entry: {kind: 'project', id: project.id}, rev: null, offset: 0, limit: 12000});
    async function load() {
      if (!active) return false;
      const serial = ++scope.listing;
      try {
        const rows = await request('list', {include_removed: true});
        if (serial === scope.listing) emit({items: rows});
        return true;
      } catch (error) { if (serial === scope.listing) emit({error: error.message || String(error)}); return false; }
    }
    async function choose(project) {
      if (!active || scope.state.busy || scope.state.attempt || scope.state.conflict) return false;
      emit({busy: true, error: ''});
      try {
        const detail = await request('library-read', base(project));
        emit({selected: {project, item: detail.item}, conflict: false}); return true;
      } catch (error) { emit({error: error.message || String(error)}); return false; }
      finally { emit({busy: false}); }
    }
    async function reread() {
      if (!active || scope.state.busy || scope.state.attempt || !scope.state.conflict || !scope.state.selected) return false;
      const project = scope.state.selected.project;
      emit({busy: true, error: ''});
      try {
        const detail = await request('library-read', base(project));
        emit({selected: {project, item: detail.item}, conflict: false}); return true;
      } catch (error) { emit({error: error.message || String(error)}); return false; }
      finally { emit({busy: false}); }
    }
    async function save() {
      const state = scope.state;
      if (!active || state.busy || !state.selected || state.conflict) return false;
      const attempt = state.attempt || Object.freeze({action: state.selected.item.removed ? 'library-restore' : 'library-remove', args: Object.freeze({session_id: sessionId, project_id: state.selected.project.id, entry: Object.freeze({...state.selected.item.entry}), expected_rev: state.selected.item.metadata_rev, operation_id: uuid()})});
      // Update shared state before dispatch so double clicks and remounts share one lock.
      emit({busy: true, error: '', attempt});
      try {
        await request(attempt.action, attempt.args);
        emit({attempt: null, selected: null, conflict: false});
        const serial = ++scope.listing;
        try { const items = await request('list', {include_removed: true}); if (serial === scope.listing) emit({items}); }
        catch (error) { emit({error: '操作已保存；刷新列表失败：' + (error.message || String(error))}); }
        for (const change of scope.changes) { try { change(); } catch (error) { emit({error: '操作已保存；刷新界面失败：' + (error.message || String(error))}); } }
        return true;
      } catch (error) {
        emit(error.code === 'VERSION_CONFLICT' ? {attempt: null, conflict: true, error: '项目已有新版本；请明确读取当前状态，核对后再确认操作。'} : {error: (error.message || String(error)) + '；结果尚未确认，请重试原操作。'});
        return false;
      } finally { emit({busy: false}); }
    }
    return {get: () => scope.state, load, choose, save, reread,
      cancel() { if (active && !scope.state.busy && !scope.state.attempt) emit({selected: null, conflict: false, error: ''}); },
      dispose() { active = false; scope.listeners.delete(notify); scope.changes.delete(onChange); }};
  }
  function factory(React) {
    const {createElement: h, useState, useEffect, useRef} = React;
    function ProjectManager({request, sessionId, onChange}) {
      const [state, setState] = useState(null), controller = useRef(null), changed = useRef(onChange);
      changed.current = onChange;
      useEffect(() => {
        const control = createProjectManagerController({request, sessionId, notify: setState, onChange: () => changed.current?.()});
        controller.current = control; setState(control.get()); control.load();
        return () => { control.dispose(); if (controller.current === control) controller.current = null; };
      }, [request, sessionId]);
      if (!state) return h('p', {role: 'status'}, '读取项目…');
      const {items, selected, error, busy, attempt, conflict} = state, control = controller.current;
      return h('section', {className: 'dm-panel', 'aria-label': '项目管理'}, h('h3', {className: 'dm-panel-title'}, '项目与回收站'),
        error && h('p', {className: 'dm-error', role: 'alert'}, error),
        h('ul', {className: 'dm-library-list'}, ...items.map(project => h('li', {key: project.id, className: 'dm-library-row'}, h('span', null, project.title || '未命名项目', project.library?.removed && ' · 已删除'), h('button', {type: 'button', className: 'dm-button', disabled: busy || !!attempt || conflict || project.schema_version !== 4, onClick: () => control.choose(project)}, project.library?.removed ? '恢复' : '删除')))),
        selected && h('section', {className: 'dm-library-confirm'}, h('p', null, selected.item.removed ? `恢复“${selected.project.title}”？` : `将“${selected.project.title}”移入回收站？`),
          !selected.item.removed && h('p', {className: 'dm-local-hint'}, '从活动项目中移除，研究历史保留，可随时恢复。'),
          h('div', {className: 'dm-actions'}, conflict && h('button', {type: 'button', className: 'dm-button', disabled: busy, onClick: () => control.reread()}, '读取当前状态'), h('button', {type: 'button', className: 'dm-button', disabled: busy || conflict, onClick: () => control.save()}, attempt ? '重试原操作' : selected.item.removed ? '确认恢复' : '确认删除'), h('button', {type: 'button', className: 'dm-button', disabled: busy || !!attempt, onClick: () => control.cancel()}, '取消'))));
    }
    return {ProjectManager};
  }
  factory.createProjectManagerController = createProjectManagerController;
  publish(factory);
})(value => { if (typeof module !== 'undefined') module.exports = value; else globalThis.DshMathProjectManagerUI = value; });
