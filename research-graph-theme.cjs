/* Appended by the owning client; inherits the existing dsh-math theme. */
(function (publish) {
  'use strict';
  publish(`
.dm-research-graph { min-width: 0; color: var(--dm-text); }
.dm-rg-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 0; }
.dm-rg-toolbar > strong { margin-right: auto; font-size: 14px; }
.dm-rg-toolbar label { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--dm-muted); }
.dm-rg-toolbar output { font: 11px ui-monospace, monospace; color: var(--dm-muted); min-width: 38px; text-align: center; }
.dm-rg-outline { margin: 0 0 12px; border: 1px solid var(--dm-border); border-radius: 8px; overflow: hidden; }
.dm-rg-outline > summary { padding: 9px 12px; cursor: pointer; color: var(--dm-muted); font-size: 12px; }
.dm-rg-outline > nav { max-height: 240px; overflow: auto; padding: 0 6px 6px; }
.dm-rg-outline-item { display: block; width: 100%; min-height: 34px; border: 0; border-radius: 5px; padding: 7px; background: transparent; color: var(--dm-text); text-align: left; overflow-wrap: anywhere; font: inherit; font-size: 12px; line-height: 1.6; cursor: pointer; }
.dm-rg-outline-item:hover, .dm-rg-outline-item[aria-current="true"] { background: var(--dm-soft); color: var(--dm-accent); }
.dm-rg-outline-item:focus-visible { outline: 2px solid var(--dm-accent); outline-offset: -2px; }
.dm-rg-legend { margin: 0 0 10px; color: var(--dm-muted); font-size: 11px; line-height: 1.7; }
.dm-rg-viewport { display: block; width: 100%; height: clamp(420px, 65vh, 760px); border: 1px solid var(--dm-border); border-radius: 12px; background: var(--dm-bg); touch-action: none; cursor: grab; user-select: none; }
.dm-rg-viewport:active { cursor: grabbing; }
.dm-rg-viewport:focus-visible { outline: 2px solid var(--dm-accent); outline-offset: 2px; }
.dm-rg-branch path { stroke: var(--dm-border); stroke-width: 1.5; }
.dm-rg-relation path { stroke: var(--dm-accent); stroke-width: 1; stroke-dasharray: 5 5; opacity: .7; }
.dm-rg-relation text { fill: var(--dm-accent); font: 10px sans-serif; paint-order: stroke; stroke: var(--dm-bg); stroke-width: 4px; }
.dm-rg-object { overflow: visible; }
.dm-rg-card { position: relative; box-sizing: border-box; height: 192px; border: 1px solid var(--dm-border); border-radius: 10px; background: var(--dm-panel); color: var(--dm-text); overflow: hidden; box-shadow: 0 3px 10px color-mix(in srgb, var(--dm-text) 4%, transparent); }
.dm-rg-card[data-selected="true"] { border-color: var(--dm-accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--dm-accent) 18%, transparent); }
.dm-rg-card[data-kind="Problem"] { border-top: 3px solid var(--dm-accent); }
.dm-rg-card[data-kind="route"] { background: var(--dm-soft); }
.dm-rg-card[data-status="abandoned"] { border-style: dashed; }
.dm-rg-card-main { box-sizing: border-box; display: flex; flex-direction: column; gap: 6px; width: 100%; height: 100%; border: 0; border-radius: 9px; padding: 12px 14px 30px; text-align: left; color: inherit; background: transparent; cursor: pointer; font: inherit; overflow: hidden; }
.dm-rg-card-main:focus-visible { outline: 2px solid var(--dm-accent); outline-offset: -3px; }
.dm-rg-kicker { display: block; color: var(--dm-muted); font-size: 10px; }
.dm-rg-title { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.dm-rg-ref { color: var(--dm-muted); font: 10px ui-monospace, monospace; }
.dm-rg-summary { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; color: var(--dm-muted); font-size: 11px; line-height: 1.55; overflow-wrap: anywhere; }
.dm-rg-status { margin-top: auto; color: var(--dm-muted); font-size: 10px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; max-width: 100%; }
.dm-rg-collapse { position: absolute; right: 10px; bottom: 6px; border: 0; background: transparent; color: var(--dm-muted); padding: 3px 5px; cursor: pointer; font: 10px sans-serif; }
.dm-rg-collapse:hover { color: var(--dm-accent); }
.dm-rg-detail { margin-top: 16px; min-width: 0; }
@media (max-width: 640px) { .dm-rg-viewport { height: 480px; } .dm-rg-toolbar { gap: 5px; } .dm-rg-toolbar > strong { flex-basis: 100%; } }
`);
})(css => { if (typeof module !== 'undefined' && module.exports) module.exports = css; else globalThis.DshMathResearchGraphTheme = css; });
