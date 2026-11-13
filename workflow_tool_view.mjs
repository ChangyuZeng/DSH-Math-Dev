/** Common model-only view. Call after accounting, binding and durable archival.
 * Never use this view as a receipt, an accounting source, or a stop decision.
 */
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value) && !JSON.isRawJSON?.(value);

// Copy JSON trees without re-parsing exact numeric tokens (JSON.rawJSON).
function copy(value) {
  if (JSON.isRawJSON?.(value)) return JSON.rawJSON(value.rawJSON);
  if (Array.isArray(value)) return value.map(copy);
  if (record(value)) return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)]));
  return value;
}

export function projectToolResult(name, fullResult, evidenceRef = null) {
  const view = copy(fullResult);
  // Unknown formats and a future field with our metadata name remain untouched.
  if (!record(view) || !record(view.metrics) || view.metrics.resource_scope !== 'observed_tool_cost' ||
      Object.hasOwn(view, 'tool_result_projection')) return view;
  const metrics = view.metrics;
  let changed = false;
  const omit = key => {
    if (Object.hasOwn(metrics, key)) { delete metrics[key]; changed = true; }
  };
  // wall_seconds retains elapsed time; timestamps are duplicate host observations.
  const wall = JSON.isRawJSON?.(metrics.wall_seconds) ? Number(metrics.wall_seconds.rawJSON) : metrics.wall_seconds;
  if (typeof wall === 'number' && Number.isFinite(wall) && wall >= 0) {
    omit('started_at'); omit('finished_at');
  }
  // Retain nulls and completeness flags: unknown resource cost never means zero.
  // Keep all resource scope/source/mode, totals and known lower bounds.
  if (Object.hasOwn(metrics, 'cpu_seconds') && Object.hasOwn(metrics, 'known_cpu_seconds') &&
      Object.hasOwn(metrics, 'cpu_accounting_complete')) {
    omit('parent_cpu_seconds'); omit('child_cpu_seconds'); omit('known_child_cpu_seconds');
  }
  if (changed) view.tool_result_projection = {
    metrics_hash_scope: 'original_tool_reply_before_metrics_and_host_enrichment',
    // A tracing identifier, never a replacement for model-readable material.
    host_audit_ref: copy(evidenceRef),
  };
  // A conservative view must not add context bytes. Metadata can outweigh the
  // redundant observations on short receipts or when an audit ref is long.
  if (changed && Buffer.byteLength(JSON.stringify(view), 'utf8') >=
      Buffer.byteLength(JSON.stringify(fullResult), 'utf8')) return copy(fullResult);
  return view;
}
