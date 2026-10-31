globalThis.DshMathTheme = `
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
.dm-import { display: grid; grid-template-columns: 36px minmax(0, 1fr); align-items: center; gap: 12px; max-width: 460px; margin: 20px auto 4px; padding: 16px; border: 1px solid var(--dm-border); border-radius: 12px; background: var(--dm-panel); }
.dm-import-mark { display: flex; align-items: center; justify-content: center; width: 36px; height: 36px; border: 1px solid var(--dm-border); border-radius: 10px; background: var(--dm-bg); color: var(--dm-muted); }
.dm-import-mark .dm-icon { width: 20px; height: 20px; }
.dm-import-copy { min-width: 0; }
.dm-import-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.dm-import-heading h3 { margin: 0; font-size: 13px; font-weight: 650; line-height: 1.5; }
.dm-import-copy p { margin: 5px 0 0; color: var(--dm-muted); font-size: 11px; line-height: 1.7; overflow-wrap: anywhere; }
.dm-import-action { grid-column: 2 / -1; }
.dm-import-action .dm-button { min-height: 32px; padding: 5px 12px; background: var(--dm-bg); }
.dm-import-action .dm-button:hover:not(:disabled) { background: var(--dm-soft); }
.dm-import-hint { grid-column: 1 / -1; margin: 0; padding-top: 11px; border-top: 1px solid var(--dm-border); color: var(--dm-muted); font-size: 11px; line-height: 1.6; text-align: center; }
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
@container dsh-math (max-width: 520px) {
  .dm-form-grid { grid-template-columns: 1fr; }
  .dm-tab { font-size: 12px; }
}
@container dsh-math (max-width: 380px) {
  .dm-tabs { gap: 2px; }
  .dm-tab { font-size: 11px; }
  .dm-panel, .dm-goal { padding: 10px; }
  .dm-panel-header { flex-wrap: wrap; }
  .dm-target-cards { grid-template-columns: 1fr; }
  .dm-import { padding: 14px; gap: 10px; }
  .dm-import-action { grid-column: 1 / -1; }
  .dm-import-action .dm-button { width: 100%; }
  .dm-graph-board { padding: 10px 5px; }
  .dm-graph-children { padding-left: 10px; margin-left: 5px; }
  .dm-graph-children > .dm-graph-branch::before { left: -11px; width: 10px; }
  .dm-start-steps { grid-template-columns: 1fr; gap: 12px; max-width: 230px; margin-left: auto; margin-right: auto; }
  .dm-start-steps li > div { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; }
  .dm-start-steps li div > span { margin-top: 0; }
  .dm-stat { flex-direction: column; align-items: center; }
  .dm-welcome { padding-top: 16px; }
}
@media (max-width: 680px) {
  .dsh-math-view { padding: 12px; }
  .dm-dialog { inset: 8px; }
}
`;
