globalThis.DshMathTheme = `
/* One enablement control for this single-component package. Keep native runtime
   status visible; the package switch owns mounting and unmounting the bundle. */
[data-plugin-detail="dsh-math"] [data-plugin-row] [role="switch"] {
  display: none;
}
.dsh-math-view, .dm-dialog, .dm-button {
  --dm-bg: var(--dsw-alias-bg-base);
  --dm-panel: var(--dsw-alias-bg-layer-1);
  --dm-text: var(--dsw-alias-label-primary);
  --dm-muted: var(--dsw-alias-label-secondary);
  --dm-border: var(--dsw-alias-border-l1);
  --dm-soft: var(--dsw-alias-bg-layer-2);
  --dm-accent: var(--dsw-alias-brand-primary);
  --dm-green: var(--dsw-alias-state-success-primary);
  --dm-red: var(--dsw-alias-state-error-primary);
}
.dsh-math-view {
  container: dsh-math / inline-size;
  width: 100%; min-width: 0; height: 100%; overflow: auto; box-sizing: border-box; padding: 14px;
  background: var(--dm-bg); color: var(--dm-text);
  font: 13px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.dsh-math-view *, .dsh-math-view *::before, .dsh-math-view *::after { box-sizing: border-box; }
.dsh-math-view.dm-sidebar { padding: 10px; }
.dsh-math-view button, .dsh-math-view input, .dsh-math-view textarea, .dsh-math-view select { font: inherit; }
.dsh-math-view button { cursor: pointer; }
.dsh-math-view button:disabled, .dsh-math-view select:disabled { cursor: not-allowed; opacity: .48; }
.dsh-math-view button:focus-visible, .dsh-math-view input:focus-visible,
.dsh-math-view textarea:focus-visible, .dsh-math-view select:focus-visible, .dsh-math-view summary:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--dm-accent) 38%, transparent); outline-offset: 2px;
}
.dm-header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; padding-bottom: 12px; margin-bottom: 12px; border-bottom: 1px solid var(--dm-border); }
.dm-header > * { min-width: 0; }
.dm-title { display: flex; align-items: center; gap: 7px; margin: 0; font-size: 14px; font-weight: 650; line-height: 1.4; }
.dm-title > .dm-icon { color: var(--dm-muted); }
.dm-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.dm-header .dm-actions { gap: 3px; }
.dm-record-actions { align-items: flex-start; }
.dm-record-more { min-width: 0; }
.dm-record-more > summary { padding: 5px 8px; min-height: 30px; color: var(--dm-muted); cursor: pointer; border-radius: 6px; }
.dm-record-more > summary:hover { background: var(--dm-soft); color: var(--dm-text); }
.dm-record-secondary { margin-top: 7px; }
.dm-button {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px;
  max-width: 100%; min-height: 30px; padding: 5px 10px; border: 1px solid var(--dm-border);
  border-radius: 6px; background: var(--dm-panel); color: var(--dm-text); overflow-wrap: anywhere;
  transition: border-color .15s, background .15s, color .15s;
}
.dm-button:hover:not(:disabled) { border-color: color-mix(in srgb, var(--dm-accent) 40%, var(--dm-border)); background: var(--dm-soft); }
.dm-button.dm-primary { border-color: color-mix(in srgb, var(--dm-accent) 40%, var(--dm-border)); background: color-mix(in srgb, var(--dm-accent) 12%, var(--dm-panel)); color: var(--dm-accent); }
.dm-button.dm-primary:hover:not(:disabled) { background: color-mix(in srgb, var(--dm-accent) 20%, var(--dm-panel)); }
.dm-button.dm-ghost { border-color: transparent; background: transparent; color: var(--dm-muted); }
.dm-button.dm-small { min-height: 28px; padding: 3px 7px; font-size: 12px; }
.dm-icon { width: 14px; height: 14px; flex: none; }
.dm-panel { min-width: 0; margin-bottom: 12px; padding: 12px; border: 1px solid var(--dm-border); border-radius: 8px; background: var(--dm-panel); }
.dm-panel-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.dm-panel-title { min-width: 0; margin: 0; font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
.dm-panel-meta { color: var(--dm-muted); font-size: 11px; }
.dm-project-picker { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; margin-bottom: 12px; }
.dm-project-picker > .dm-panel-meta { white-space: nowrap; }
.dm-input, .dsh-math-view input, .dsh-math-view textarea, .dsh-math-view select {
  width: 100%; min-width: 0; max-width: 100%; border: 1px solid var(--dm-border); border-radius: 6px;
  background: var(--dm-bg); color: var(--dm-text); padding: 7px 9px;
}
.dsh-math-view input[type="checkbox"], .dsh-math-view input[type="radio"] { width: auto; accent-color: var(--dm-accent); }
.dsh-math-view textarea { resize: vertical; line-height: 1.5; }
.dm-field { display: block; min-width: 0; margin: 10px 0; color: var(--dm-muted); font-size: 12px; overflow-wrap: anywhere; }
.dm-field > input, .dm-field > textarea, .dm-field > select { display: block; margin-top: 5px; color: var(--dm-text); font-size: 13px; }
.dm-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 10px; border: 1px solid var(--dm-border); border-radius: 7px; background: var(--dm-panel); }
.dm-stat { display: flex; align-items: baseline; justify-content: center; flex-wrap: wrap; gap: 3px 6px; min-width: 0; padding: 7px 5px; }
.dm-stat + .dm-stat { border-left: 1px solid var(--dm-border); }
.dm-stat-value { font-size: 14px; font-weight: 650; line-height: 1.3; }
.dm-stat-label { color: var(--dm-muted); font-size: 11px; }
.dm-tabs { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 4px; margin: 0 0 10px; border-bottom: 1px solid var(--dm-border); }
.dm-tab { position: relative; min-width: 0; padding: 6px 0 8px; border: 0; background: transparent; color: var(--dm-muted); white-space: nowrap; }
.dm-tab[data-active="true"] { color: var(--dm-accent); font-weight: 600; }
.dm-tab[data-active="true"]::after { content: ""; position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; border-radius: 2px; background: var(--dm-accent); }
.dm-record { padding: 10px 0; border-top: 1px solid var(--dm-border); }
.dm-record:first-of-type { border-top: 0; }
.dsh-math-view .dm-record-title { display: block; width: 100%; min-width: 0; padding: 0; border: 0; background: transparent; color: var(--dm-text); text-align: left; font-weight: 600; overflow-wrap: anywhere; }
.dm-record-title:hover { color: var(--dm-accent); }
.dm-record-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 5px; }
.dm-record-text { margin: 5px 0 0; color: var(--dm-muted); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-ref { display: inline-flex; min-width: 0; max-width: 100%; padding: 1px 5px; border: 0; border-radius: 4px; background: color-mix(in srgb, var(--dm-accent) 6%, transparent); color: var(--dm-muted); font: 10px/1.6 ui-monospace, monospace; text-align: left; white-space: normal; overflow-wrap: anywhere; }
.dm-ref:hover { color: var(--dm-accent); }
.dm-badge { display: inline-flex; align-items: center; padding: 1px 6px; border-radius: 999px; background: var(--dm-soft); color: var(--dm-muted); font-size: 10px; white-space: nowrap; }
.dm-badge.dm-success { background: color-mix(in srgb, var(--dm-green) 11%, transparent); color: var(--dm-green); }
.dm-empty { margin: 7px 0 2px; color: var(--dm-muted); font-size: 12px; }
.dm-error { margin: 0 0 12px; padding: 9px 11px; border: 1px solid color-mix(in srgb, var(--dm-red) 28%, var(--dm-border)); border-radius: 8px; background: color-mix(in srgb, var(--dm-red) 7%, var(--dm-panel)); color: var(--dm-red); overflow-wrap: anywhere; }
.dm-operation-status { margin: 0 0 12px; color: var(--dm-muted); font-size: 12px; }
.dm-entry-error { max-width: 260px; color: var(--dsw-alias-state-error-primary); font-size: 12px; overflow-wrap: anywhere; }
.dm-code { max-height: 460px; overflow: auto; padding: 10px; border: 1px solid var(--dm-border); border-radius: 7px; background: var(--dm-bg); font: 11px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-details { margin: 10px 0; }
.dm-details > summary { cursor: pointer; color: var(--dm-muted); font-size: 12px; }
.dm-import { display: flex; align-items: center; justify-content: space-between; gap: 20px; max-width: 700px; margin: 22px auto 0; padding: 20px 0 0; border-top: 1px solid var(--dm-border); }
.dm-import-copy { min-width: 0; }
.dm-import-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; }
.dm-import-heading h3 { margin: 0; font-size: 13px; font-weight: 600; line-height: 1.5; }
.dm-import-copy p { margin: 4px 0 0; color: var(--dm-muted); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.dm-import-action { flex: none; }
.dm-import-action .dm-button { background: transparent; }
.dm-import-action .dm-button::after { content: '→'; margin-left: 5px; font-size: 14px; line-height: 1; font-weight: 400; }
.dm-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 10px; }
.dm-form-grid .dm-wide { grid-column: 1 / -1; }
.dm-sidebar .dm-form-grid { grid-template-columns: 1fr; }
.dm-editor { min-width: 0; scroll-margin-top: 12px; }
.dm-editor-title { margin: 0 0 6px; font-size: 14px; }
.dm-editor .dm-actions { margin-top: 14px; }
.dm-fieldset { min-inline-size: 0; margin: 12px 0; padding: 10px; border: 1px solid var(--dm-border); border-radius: 8px; }
.dm-fieldset > legend { max-width: 100%; padding: 0 4px; font-size: 12px; overflow-wrap: anywhere; }
.dm-ref-options { max-height: 220px; overflow: auto; margin-top: 8px; }
.dm-ref-option { display: flex; align-items: flex-start; gap: 8px; padding: 7px 0; }
.dm-ref-option > label { flex: 1; }
.dm-check-label { display: flex; min-width: 0; align-items: flex-start; gap: 6px; font-size: 12px; }
.dm-check-label > input { flex: none; margin-top: 4px; }
.dm-check-label > span { min-width: 0; overflow-wrap: anywhere; }
.dm-welcome { max-width: 460px; margin: 0 auto; padding: 24px 8px 12px; text-align: center; }
.dm-welcome-mark { display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px; border: 1px solid var(--dm-border); border-radius: 12px; background: var(--dm-panel); color: var(--dm-accent); }
.dm-welcome-mark .dm-icon { width: 24px; height: 24px; }
.dm-welcome h3 { margin: 14px 0 8px; font-size: 18px; font-weight: 650; letter-spacing: -.02em; }
.dm-welcome-text { max-width: 36em; margin: 0 auto 18px; font-size: 12px; line-height: 1.8; color: var(--dm-muted); }
.dm-start-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; list-style: none; padding: 0; margin: 26px 0 18px; text-align: left; }
.dm-start-steps li { display: flex; align-items: flex-start; gap: 7px; min-width: 0; }
.dm-step-number { flex: none; color: var(--dm-muted); font-size: 11px; line-height: 20px; }
.dm-start-steps strong, .dm-start-steps li div > span { display: block; }
.dm-start-steps strong { font-size: 12px; font-weight: 500; }
.dm-start-steps li div > span { margin-top: 3px; font-size: 11px; color: var(--dm-muted); }
.dm-local-hint { margin: 12px 0 0; color: var(--dm-muted); font-size: 11px; line-height: 1.6; }
.dm-goal { min-width: 0; margin-bottom: 16px; padding: 14px; border: 1px solid var(--dm-border); border-radius: 8px; background: var(--dm-panel); }
.dm-section-label { display: flex; justify-content: space-between; gap: 8px; color: var(--dm-muted); font-size: 11px; }
.dm-goal-title { margin: 9px 0 6px; font-size: 16px; line-height: 1.5; overflow-wrap: anywhere; }
.dm-goal-statement { margin: 0 0 9px; font-size: 13px; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-next-actions { margin-top: 16px; }
.dm-dialog { position: fixed; inset: 0; width: min(1100px, calc(100vw - 32px)); height: calc(100dvh - 32px); max-width: none; max-height: none; margin: auto; padding: 0; box-sizing: border-box; overflow: hidden; color: var(--dm-text); background: var(--dm-bg); border: 1px solid var(--dm-border); border-radius: 14px; box-shadow: 0 18px 60px color-mix(in srgb, var(--dm-text) 20%, transparent); }
.dm-dialog::backdrop { background: color-mix(in srgb, var(--dm-text) 18%, transparent); }
.dm-dialog > .dsh-math-view { height: 100%; }
.dm-sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
.dm-graph-tools { margin-bottom: 8px; }
.dm-process-graph { margin-top: 14px; min-width: 0; }
.dm-graph-caption { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.dm-graph-caption .dm-button { text-decoration: none; }
.dm-graph-notice { margin: 10px 0; }
.dm-graph-warnings { padding: 8px 10px; border: 1px solid var(--dm-border); border-radius: 6px; color: var(--dm-muted); font-size: 11px; }
.dm-graph-warnings ul { padding-left: 18px; overflow-wrap: anywhere; }
.dm-graph-board { max-height: 70vh; min-height: 160px; margin: 14px 0; padding: 14px 10px; overflow: auto; border: 1px solid var(--dm-border); border-radius: 8px; background: var(--dm-bg); }
.dm-graph-roots, .dm-graph-sources, .dm-graph-children { list-style: none; padding: 0; margin: 0; }
.dm-graph-branch { position: relative; min-width: 0; }
.dm-graph-node-wrap { min-width: 0; position: relative; }
.dm-graph-node { display: block; width: 100%; padding: 0; text-align: left; color: var(--dm-text); background: var(--dm-panel); border: 1px solid var(--dm-border); border-radius: 7px; overflow: hidden; }
.dm-graph-node:hover { border-color: color-mix(in srgb, var(--dm-accent) 50%, var(--dm-border)); }
.dm-graph-node[data-selected="true"] { border-color: var(--dm-accent); box-shadow: 0 0 0 1px color-mix(in srgb, var(--dm-accent) 35%, transparent); }
.dm-graph-project > .dm-graph-node-wrap { max-width: 340px; margin: 0 auto; }
.dm-graph-project > .dm-graph-node-wrap > .dm-graph-node { background: color-mix(in srgb, var(--dm-accent) 7%, var(--dm-panel)); }
.dm-graph-kicker { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px; padding: 9px 11px 0; color: var(--dm-muted); font-size: 10px; }
.dm-graph-kicker > span { overflow-wrap: anywhere; }
.dm-graph-label { display: block; padding: 5px 11px 8px; font-size: 12px; line-height: 1.5; font-weight: 550; overflow-wrap: anywhere; }
.dm-graph-summary { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; padding: 0 11px; margin-bottom: 9px; color: var(--dm-muted); font-size: 11px; overflow-wrap: anywhere; }
.dm-graph-status { display: flex; align-items: center; gap: 6px; padding: 5px 11px; border-top: 1px solid var(--dm-border); background: var(--dm-bg); color: var(--dm-muted); font-size: 10px; }
.dm-graph-dot { width: 5px; height: 5px; flex: none; border-radius: 50%; background: var(--dm-muted); }
.dm-graph-node[data-status="error"] .dm-graph-dot { background: var(--dm-red); }
.dm-graph-node[data-status="unknown"] .dm-graph-dot { background: var(--dsw-alias-state-warn-primary); }
.dm-graph-toggle { margin-top: 3px; font-size: 10px; }
.dm-graph-sources { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 250px), 1fr)); gap: 24px; margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--dsw-alias-border-l2); position: relative; }
.dm-graph-sources::before { content: ""; position: absolute; left: 50%; top: -24px; height: 24px; border-left: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-source::before { content: ""; position: absolute; top: -20px; left: 50%; height: 20px; border-left: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-children { padding: 2px 0 0 15px; margin: 0 0 0 10px; border-left: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-children > .dm-graph-branch { margin-top: 12px; }
.dm-graph-children > .dm-graph-branch::before { content: ""; position: absolute; left: -16px; top: 24px; width: 15px; border-top: 1px solid var(--dsw-alias-border-l2); }
.dm-graph-record .dm-graph-label { font-size: 11px; }
.dm-graph-more { margin-top: 10px; }
.dm-graph-detail { padding: 12px; margin: 12px 0; border: 1px solid color-mix(in srgb, var(--dm-accent) 40%, var(--dm-border)); border-radius: 8px; background: var(--dm-panel); }
.dm-graph-detail h3:focus { outline: none; }
.dm-graph-facts { margin: 10px 0; font-size: 11px; }
.dm-graph-facts > div { display: grid; grid-template-columns: minmax(65px, 1fr) minmax(0, 3fr); gap: 8px; padding: 4px 0; }
.dm-graph-facts dt { color: var(--dm-muted); }
.dm-graph-facts dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-graph-relations { border-top: 1px solid var(--dm-border); margin-top: 10px; padding-top: 6px; }
.dm-editor-fields { min-width: 0; margin: 0; padding: 0; border: 0; }
.dm-project-form { min-width: 0; }
.dm-project-form h3 { margin: 0 0 6px; font-size: 14px; }
.dm-project-field { min-width: 0; margin: 12px 0; }
.dm-project-field .dm-field { margin-bottom: 3px; }
.dm-field-label { color: var(--dm-muted); font-size: 12px; }
.dm-required { margin-left: 6px; color: var(--dm-accent); font-size: 10px; }
.dm-field-help { margin: 4px 0 0; color: var(--dm-muted); font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }
.dm-field-error { margin: 4px 0 0; color: var(--dm-red); font-size: 11px; }
.dm-input[aria-invalid="true"] { border-color: var(--dm-red); }
.dm-target-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); gap: 6px; margin-top: 6px; }
.dm-target-card { display: flex; align-items: flex-start; gap: 7px; min-width: 0; padding: 9px; border: 1px solid var(--dm-border); border-radius: 7px; background: var(--dm-bg); cursor: pointer; }
.dm-target-card[data-active="true"] { border-color: var(--dm-accent); background: color-mix(in srgb, var(--dm-accent) 6%, var(--dm-panel)); }
.dm-target-card input { flex: none; margin: 3px 0 0; }
.dm-target-card > span { min-width: 0; overflow-wrap: anywhere; }
.dm-target-card strong, .dm-target-card span span, .dm-target-card small { display: block; }
.dm-target-card strong { color: var(--dm-text); font-size: 12px; }
.dm-target-card span span { margin-top: 3px; font-size: 11px; }
.dm-target-card small { margin-top: 4px; color: var(--dm-muted); font-size: 10px; }
.dm-conflict { padding: 9px; margin: 10px 0; border: 1px solid var(--dsw-alias-state-warn-primary); border-radius: 7px; overflow-wrap: anywhere; }
.dm-conflict p { margin: 0 0 7px; }
.dm-conflict .dm-button { margin: 3px 5px 0 0; }
.dm-readonly { padding: 7px 9px; border: 1px dashed var(--dm-border); border-radius: 6px; color: var(--dm-muted); font-size: 11px; }
.dm-scratchpad { min-width: 0; padding: 11px; margin-bottom: 12px; border: 1px solid var(--dm-border); border-radius: 8px; background: var(--dm-panel); }
.dm-scratchpad[hidden] { display: none; }
.dm-scratch-body { min-height: 200px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace !important; font-size: 12px !important; }
.dm-history-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px; margin: 6px 0; }
.dm-history-row > span { flex: 1; min-width: 0; }
.dm-promotion { padding-top: 10px; margin-top: 10px; border-top: 1px solid var(--dm-border); }
.dm-next-action-list { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--dm-border); }
.dm-next-action-list h4 { margin: 0 0 6px; color: var(--dm-muted); font-size: 11px; font-weight: 500; }
.dm-next-action { display: flex; align-items: flex-start; gap: 8px; margin: 5px 0; font-size: 12px; }
.dm-next-action > span { flex: 1; min-width: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.dm-modal-backdrop { width: min(520px, calc(100vw - 28px)); max-width: none; max-height: 90vh; margin: auto; padding: 0; border: 0; border-radius: 10px; background: var(--dm-panel); color: var(--dm-text); }
.dm-modal-backdrop::backdrop { background: color-mix(in srgb, var(--dm-text) 18%, transparent); }
.dm-modal { min-width: 0; max-height: 90vh; overflow: auto; padding: 16px; border: 1px solid var(--dm-border); border-radius: 10px; background: var(--dm-panel); box-shadow: 0 14px 45px color-mix(in srgb, var(--dm-text) 15%, transparent); }
.dm-modal h3 { margin: 0 0 8px; font-size: 14px; }
.dm-import-facts { margin: 12px 0; font-size: 12px; }
.dm-import-facts > div { display: grid; grid-template-columns: 80px minmax(0, 1fr); gap: 7px; margin: 6px 0; }
.dm-import-facts dt { color: var(--dm-muted); }
.dm-import-facts dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
/* Research desk: readable documents first, metadata and diagnostics second. */
.dsh-math-view { font-size: 14px; line-height: 1.65; }
.dsh-math-view [hidden] { display: none !important; }
.dm-title { font-size: 16px; }
.dm-panel-title, .dm-editor-title { font-size: 14px; }
.dm-panel-meta, .dm-local-hint, .dm-field-help, .dm-readonly, .dm-badge { font-size: 12px; }
.dm-ref { font-size: 12px; line-height: 1.6; }
.dm-button { min-height: 34px; }
.dm-goal-title { font-size: 18px; }
.dm-goal-statement { font-size: 14px; line-height: 1.75; }
.dm-goal-statement > .dm-preview { padding: 0; border: 0; }
.dm-draft-summary { color: var(--dm-muted); font-size: 12px; margin: 10px 0 0; overflow-wrap: anywhere; }
.dm-tabs { position: sticky; top: -1px; z-index: 2; background: var(--dm-bg); padding-top: 5px; }
.dm-tab { min-height: 36px; }
.dm-muted { color: var(--dm-muted); font-size: 12px; }
.dm-editor-section { margin: 16px 0 0; padding-top: 12px; border-top: 1px solid var(--dm-border); min-width: 0; }
.dm-editor-section-heading { margin: 0 0 8px; font-size: 14px; font-weight: 600; }
summary.dm-editor-section-heading { cursor: pointer; }
.dm-editor-preview { margin: 8px 0 16px; }
.dm-editor-status { font-size: 12px; color: var(--dm-muted); }
.dm-editor-errors ul { margin: 6px 0; padding-left: 18px; }
.dm-editor .dm-actions { position: sticky; bottom: 0; padding: 10px 0; background: var(--dm-panel); z-index: 1; }
.dm-record-layout, .dm-record-list, .dm-record-detail, .dm-record-reader, .dm-record-browser { min-width: 0; }
.dm-record-layout[data-detail="true"] > .dm-record-list { display: none; }
.dm-record-reader { padding: 16px; background: var(--dm-panel); border: 1px solid var(--dm-border); border-radius: 12px; overflow-wrap: anywhere; }
.dm-record-reader > .dm-record-section { max-width: 80ch; }
.dm-record-reader .dm-preview { padding: 0; border: 0; background: transparent; font-size: 14px; line-height: 1.75; }
.dm-record-reader .dm-preview > :first-child { margin-top: 0; }
.dm-record-heading { margin: 16px 0 4px; font-size: 18px; font-weight: 650; line-height: 1.5; overflow-wrap: anywhere; scroll-margin-top: 52px; }
.dm-reader-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }
.dm-reader-actions > h3 { margin: 0; font-size: 16px; }
.dm-record-section { margin: 18px 0; min-width: 0; }
.dm-record-section > h4 { margin: 0 0 8px; font-size: 13px; font-weight: 600; color: var(--dm-muted); }
.dm-record-section > ul { margin: 0; padding-left: 20px; }
.dm-record-section li { margin: 5px 0; overflow-wrap: anywhere; }
.dm-record-meta { font-size: 12px; color: var(--dm-muted); overflow-wrap: anywhere; }
.dm-record-reference { display: inline; overflow-wrap: anywhere; }
.dm-record-refs, .dm-record-raw { border-top: 1px solid var(--dm-border); padding-top: 14px; }
.dm-record-raw { margin-top: 18px; color: var(--dm-muted); font-size: 12px; }
.dm-record-raw summary { cursor: pointer; }
.dm-copy { margin: 6px 0; }
.dm-copy > p { font-size: 12px; color: var(--dm-muted); }
.dm-copy-fallback { min-height: 100px; }
.dm-record-browser { padding: 12px; border: 1px solid var(--dm-border); border-radius: 10px; background: var(--dm-panel); }
.dm-record-filters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.dm-record-filters > label { min-width: 0; color: var(--dm-muted); font-size: 12px; }
.dm-record-filters > label:first-child { grid-column: 1 / -1; }
.dm-record-filters input, .dm-record-filters select { display: block; margin-top: 4px; }
.dm-record-browser > p, .dm-record-selected { font-size: 12px; color: var(--dm-muted); }
.dm-record-selected { margin-top: 10px; padding: 8px; border-left: 2px solid var(--dm-accent); background: var(--dm-soft); }
.dm-record-selected p { margin: 0 0 6px; }
.dm-record-results { list-style: none; padding: 0; margin: 10px 0; }
.dm-record-item { padding: 12px 0; border-top: 1px solid var(--dm-border); }
.dm-record-item > .dm-button { display: block; padding: 0; min-height: 30px; border: 0; width: 100%; text-align: left; font-size: 14px; font-weight: 550; background: transparent; }
.dm-record-item > .dm-button:hover { color: var(--dm-accent); background: transparent; }
.dm-record-item > p { margin: 4px 0 0; }
.dm-pinned-record { margin-top: 16px; min-width: 0; }
.dm-pinned-record > h3 { margin: 0 0 10px; }
.dm-pinned-record .dm-record-reader { border-style: dashed; }
.dm-review-summary { min-width: 0; overflow-wrap: anywhere; }
.dm-review-summary > h3 { font-size: 15px; }
.dm-review-summary .dm-record-section { margin: 12px 0; }
.dm-export-scope, .dm-export-receipt, .dm-context-pack, .dm-candidate { margin: 16px 0; padding: 14px; border: 1px solid var(--dm-border); border-radius: 10px; background: var(--dm-panel); min-width: 0; overflow-wrap: anywhere; }
.dm-export-scope > h4, .dm-export-receipt > h3 { margin: 0 0 8px; }
.dm-export-scope p { margin: 6px 0; }
.dm-export-receipt .dm-record-section { margin: 12px 0; }
.dm-context-options { margin-top: 12px; }
.dm-context-options > ul { max-height: 240px; overflow: auto; list-style: none; padding: 0; }
.dm-context-options li { margin: 6px 0; padding: 6px 0; border-bottom: 1px solid var(--dm-border); }
.dm-context-options li label { display: flex; align-items: flex-start; gap: 8px; }
.dm-context-options li input { flex: none; margin-top: 5px; }
.dm-context-risks { font-size: 12px; color: var(--dm-muted); padding-left: 18px; }
.dm-context-preview { max-height: 400px; }
.dm-comparison { min-width: 0; margin: 12px 0; }
.dm-comparison-summary { font-size: 12px; color: var(--dm-muted); }
.dm-comparison-head, .dm-comparison-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.dm-comparison-head { margin-bottom: 8px; font-size: 12px; }
.dm-comparison-row { border-top: 1px solid var(--dm-border); padding: 7px 0; }
.dm-comparison-row > div { min-width: 0; padding: 5px 7px; }
.dm-comparison-value { margin: 4px 0 0; font: 12px/1.65 ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.dm-comparison-line { display: block; font-size: 11px; color: var(--dm-muted); }
.dm-diff-local > :first-child, .dm-diff-remote > :last-child, .dm-diff-changed > div { background: color-mix(in srgb, var(--dm-accent) 6%, var(--dm-bg)); border-left: 2px solid var(--dm-accent); }
.dm-comparison-field { margin: 8px 0; }
.dm-comparison-field > summary { cursor: pointer; font-size: 12px; }
details.dm-candidate:not([open]) { margin: 10px 0; padding: 0; border: 0; background: transparent; }
.dm-record-advanced > .dm-record-filters { margin-top: 10px; }
.dm-candidate-input { min-height: 130px; }
.dm-candidate-preview { margin-top: 14px; }
.dm-graph-mode-switch { display: flex; gap: 6px; margin: 12px 0; }
.dm-graph-mode-button[data-active="true"] { color: var(--dm-accent); border-color: var(--dm-accent); background: var(--dm-panel); }
.dm-graph-filters { display: grid; grid-template-columns: minmax(0, 2fr) repeat(2, minmax(0, 1fr)); gap: 10px; }
.dm-graph-filters > label { color: var(--dm-muted); font-size: 12px; min-width: 0; }
.dm-graph-filters input, .dm-graph-filters select { margin-top: 5px; }
.dm-graph-source-group { margin: 16px 0; min-width: 0; }
.dm-graph-source-header { border-bottom: 1px solid var(--dm-border); padding-bottom: 8px; overflow-wrap: anywhere; }
.dm-graph-source-header h4 { margin: 0; font-size: 14px; }
.dm-graph-source-header p, .dm-graph-list-count, .dm-graph-row-meta { color: var(--dm-muted); font-size: 12px; }
.dm-graph-rows { list-style: none; margin: 10px 0; padding: 0; }
.dm-graph-row { margin-bottom: 10px; }
.dm-graph-row-meta { display: block; margin-bottom: 4px; }
.dm-import-compact { display: flex; justify-content: flex-end; margin-top: 16px; padding-top: 8px; border-top: 1px solid var(--dm-border); }
/* Research desk / quiet editorial system. The host owns the palette; the content owns the hierarchy. */
.dsh-math-view, .dm-dialog, .dm-button {
  --dm-hairline: color-mix(in srgb, var(--dm-text) 12%, transparent);
  --dm-raised: color-mix(in srgb, var(--dm-text) 4%, var(--dm-bg));
  --dm-hover: color-mix(in srgb, var(--dm-text) 8%, var(--dm-bg));
}
.dsh-math-view {
  padding: 28px clamp(16px, 4%, 48px) 48px;
  font-size: 13px; line-height: 1.6;
  letter-spacing: -.005em;
}
.dsh-math-view.dm-sidebar { padding: 24px clamp(14px, 3%, 42px) 48px; }
.dsh-math-view > .dm-header, .dsh-math-view > .dm-project-picker,
.dsh-math-view > .dm-stats, .dsh-math-view > .dm-tabs,
.dsh-math-view > [role="tabpanel"], .dsh-math-view > .dm-import-compact,
.dsh-math-view > .dm-error, .dsh-math-view > .dm-operation-status,
.dsh-math-view > .dm-local-hint, .dsh-math-view > .dm-readonly,
.dsh-math-view > .dm-conflict { max-width: 960px; margin-left: auto; margin-right: auto; }
.dm-header { min-height: 58px; margin-bottom: 24px; padding: 0 0 18px; border-color: var(--dm-hairline); }
.dm-heading { display: flex; min-width: 0; align-items: center; gap: 11px; }
.dm-header-mark { display: grid; width: 36px; height: 36px; flex: none; place-items: center; border: 1px solid var(--dm-hairline); border-radius: 10px; background: var(--dm-raised); color: var(--dm-text); }
.dm-header-mark .dm-icon { width: 18px; height: 18px; }
.dm-heading-copy { display: flex; flex-direction: column; min-width: 0; gap: 1px; }
.dm-eyebrow { color: var(--dm-muted); font-size: 9px; font-weight: 600; line-height: 1.4; letter-spacing: .16em; }
.dm-title { font-size: 15px; font-weight: 600; line-height: 1.35; letter-spacing: -.025em; }
.dm-header .dm-actions { gap: 2px; }
.dm-button { min-height: 34px; padding: 6px 12px; border-color: var(--dm-hairline); border-radius: 8px; background: var(--dm-raised); font-size: 12px; font-weight: 500; transition: background .18s ease, border-color .18s ease, color .18s ease, transform .18s ease; }
.dm-button:hover:not(:disabled) { border-color: color-mix(in srgb, var(--dm-text) 26%, transparent); background: var(--dm-hover); }
.dm-button.dm-primary { border-color: var(--dm-text); background: var(--dm-text); color: var(--dm-bg); }
.dm-button.dm-primary:hover:not(:disabled) { border-color: color-mix(in srgb, var(--dm-text) 82%, var(--dm-bg)); background: color-mix(in srgb, var(--dm-text) 82%, var(--dm-bg)); color: var(--dm-bg); transform: translateY(-1px); }
.dm-button.dm-ghost { color: var(--dm-muted); }
.dm-button.dm-ghost:hover:not(:disabled) { border-color: transparent; background: var(--dm-hover); color: var(--dm-text); }
.dm-button.dm-small { font-size: 11px; }
.dm-button-arrow { margin-left: 17px; font-size: 16px; line-height: .8; font-weight: 400; }
.dm-welcome { width: 100%; max-width: 700px; margin: clamp(70px, 12vh, 150px) auto 0; padding: 0 24px 36px; text-align: left; }
.dm-welcome-overline { display: flex; align-items: center; gap: 10px; color: var(--dm-muted); font-size: 11px; font-weight: 500; letter-spacing: .035em; }
.dm-welcome-line { width: 22px; height: 1px; flex: none; background: var(--dm-text); }
.dm-welcome h3 { max-width: 610px; margin: 24px 0 15px; color: var(--dm-text); font-size: clamp(32px, 4.5vw, 48px); font-weight: 600; line-height: 1.2; letter-spacing: -.055em; white-space: pre-line; }
.dm-welcome-text { max-width: 32em; margin: 0 0 27px; color: var(--dm-muted); font-size: 14px; line-height: 1.8; }
.dm-welcome-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.dm-welcome-actions .dm-button { min-height: 42px; padding: 9px 15px; font-size: 13px; }
.dm-draft-summary { margin-top: 14px; font-size: 11px; }
.dm-start-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 22px; margin: clamp(50px, 9vh, 108px) 0 0; padding: 18px 0 0; border-top: 1px solid var(--dm-hairline); }
.dm-start-steps li { gap: 10px; }
.dm-step-number { display: grid; width: 23px; height: 23px; flex: none; place-items: center; border: 1px solid var(--dm-hairline); border-radius: 50%; color: var(--dm-muted); font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace; }
.dm-start-steps strong { color: var(--dm-text); font-size: 12px; font-weight: 550; }
.dm-start-steps li div > span { margin-top: 4px; font-size: 11px; line-height: 1.5; }
.dm-welcome > .dm-local-hint { margin-top: 23px; font-size: 11px; }
.dm-import { max-width: 652px; border-color: var(--dm-hairline); }
.dm-import-action .dm-button { border-color: var(--dm-hairline); }
@container dsh-math (max-width: 520px) {
  .dm-import { align-items: flex-start; flex-wrap: wrap; gap: 14px; }
  .dm-import-action { flex-basis: 100%; }
}
.dm-project-picker { margin-bottom: 23px; padding: 0 0 18px; border-bottom: 1px solid var(--dm-hairline); }
.dm-project-picker select { max-width: 520px; }
.dm-stats { gap: 0; margin: 8px auto 25px !important; border: 0; border-radius: 0; background: transparent; }
.dm-stat { display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 2px; padding: 8px 16px; }
.dm-stat:first-child { padding-left: 0; }
.dm-stat + .dm-stat { border-left: 1px solid var(--dm-hairline); }
.dm-stat-value { font-size: 23px; font-weight: 550; line-height: 1.3; letter-spacing: -.04em; font-variant-numeric: tabular-nums; }
.dm-stat-label { font-size: 11px; }
.dm-tabs { display: flex; gap: 26px; margin-bottom: 20px; padding-top: 5px; border-color: var(--dm-hairline); background: var(--dm-bg); }
.dm-tab { width: auto; min-height: 39px; padding: 7px 1px 12px; font-size: 12px; font-weight: 500; }
.dm-tab[data-active="true"] { color: var(--dm-text); font-weight: 600; }
.dm-tab[data-active="true"]::after { right: 0; left: 0; height: 1px; background: var(--dm-text); }
.dm-goal { margin: 8px 0 28px; padding: clamp(20px, 3vw, 32px); border-color: var(--dm-hairline); border-radius: 12px; background: var(--dm-raised); }
.dm-section-label { color: var(--dm-muted); font-size: 11px; letter-spacing: .03em; }
.dm-goal-title { margin: 18px 0 10px; font-size: clamp(21px, 2.8vw, 29px); font-weight: 600; letter-spacing: -.035em; line-height: 1.3; }
.dm-goal-statement { max-width: 68ch; line-height: 1.8; }
.dm-panel { margin-bottom: 8px; padding: 18px 0 20px; border: 0; border-top: 1px solid var(--dm-hairline); border-radius: 0; background: transparent; }
.dm-panel-header { margin-bottom: 8px; }
.dm-panel-title { font-size: 13px; font-weight: 600; }
.dm-record { padding: 13px 0; border-color: var(--dm-hairline); }
.dm-record-title { font-size: 13px; }
.dm-record-text { line-height: 1.75; }
.dm-badge { border-radius: 5px; background: var(--dm-raised); }
.dm-ref { background: var(--dm-raised); }
.dm-scratchpad, .dm-record-browser, .dm-record-reader, .dm-export-scope, .dm-export-receipt, .dm-context-pack, .dm-candidate, .dm-graph-board { border-color: var(--dm-hairline); background: var(--dm-raised); }
.dm-input, .dsh-math-view input, .dsh-math-view textarea, .dsh-math-view select { border-color: var(--dm-hairline); border-radius: 8px; background: var(--dm-bg); }
.dsh-math-view input:focus, .dsh-math-view textarea:focus, .dsh-math-view select:focus { border-color: color-mix(in srgb, var(--dm-text) 48%, transparent); }
.dm-target-card { border-color: var(--dm-hairline); border-radius: 8px; background: var(--dm-raised); }
.dm-target-card[data-active="true"] { border-color: var(--dm-accent); }
.dm-dialog { border-color: var(--dm-hairline); }
@container dsh-math (max-width: 640px) {
  .dm-header { gap: 8px; margin-bottom: 20px; }
  .dm-welcome { margin-top: clamp(40px, 9vh, 90px); padding: 0 3px 28px; }
  .dm-welcome h3 { font-size: clamp(30px, 7vw, 42px); }
  .dm-start-steps { gap: 12px; }
  .dm-tabs { gap: 18px; overflow-x: auto; scrollbar-width: none; }
  .dm-tab { flex: none; }
}
@container dsh-math (max-width: 420px) {
  .dm-header .dm-actions { width: 100%; justify-content: flex-start; }
  .dm-header .dm-actions .dm-button:first-child { padding-left: 0; }
  .dm-start-steps { grid-template-columns: 1fr; gap: 16px; margin-top: 50px; }
  .dm-start-steps li > div { display: block; }
  .dm-stat { padding: 7px 8px; }
  .dm-stat-value { font-size: 19px; }
  .dm-stat-label { font-size: 10px; }
}
@container dsh-math (min-width: 900px) {
  .dm-record-layout[data-detail="true"] { display: grid; grid-template-columns: minmax(220px, .7fr) minmax(0, 1.6fr); gap: 16px; align-items: start; }
  .dm-record-layout[data-detail="true"] > .dm-record-list { display: block; max-height: 75vh; overflow: auto; }
  .dm-records:has(.dm-pinned-record) { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 16px; align-items: start; }
  .dm-records:has(.dm-pinned-record) > .dm-panel, .dm-records > .dm-context-pack { grid-column: 1 / -1; }
  .dm-records:has(.dm-pinned-record) > .dm-record-layout { display: block; grid-column: 1; }
  .dm-records:has(.dm-pinned-record) .dm-record-layout[data-detail="true"] > .dm-record-list { display: none; }
  .dm-records > .dm-pinned-record { grid-column: 2; margin-top: 0; }
}
@media (prefers-reduced-motion: reduce) { .dsh-math-view *, .dm-button { transition: none !important; scroll-behavior: auto !important; } }
@container dsh-math (max-width: 520px) {
  .dm-comparison-head, .dm-comparison-row { grid-template-columns: 1fr; }
  .dm-record-reader { padding: 12px; }
  .dm-graph-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dm-graph-search { grid-column: 1 / -1; }
  .dm-form-grid { grid-template-columns: 1fr; }
  .dm-tab { font-size: 12px; }
}
@container dsh-math (max-width: 380px) {
  .dm-tabs { gap: 14px; }
  .dm-tab { font-size: 11px; }
  .dm-panel { padding: 16px 0; }
  .dm-goal { padding: 18px; }
  .dm-panel-header { flex-wrap: wrap; }
  .dm-target-cards { grid-template-columns: 1fr; }
  .dm-import { padding-top: 18px; gap: 12px; }
  .dm-import-action .dm-button { width: 100%; }
  .dm-graph-board { padding: 10px 5px; }
  .dm-graph-children { padding-left: 10px; margin-left: 5px; }
  .dm-graph-children > .dm-graph-branch::before { left: -11px; width: 10px; }
  .dm-start-steps { grid-template-columns: 1fr; gap: 14px; max-width: none; margin-left: 0; margin-right: 0; }
  .dm-start-steps li > div { display: block; }
  .dm-start-steps li div > span { margin-top: 4px; }
  .dm-stat { flex-direction: column; align-items: flex-start; }
  .dm-welcome { padding-top: 0; }
}
@media (max-width: 680px) {
  .dsh-math-view { padding: 12px; }
  .dm-dialog { inset: 8px; }
}
`;
