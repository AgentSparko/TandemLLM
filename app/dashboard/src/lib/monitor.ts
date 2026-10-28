// The Monitor pages' own rules (VIS-248, concept §5 and §8): Overview, Hardware and Events & Alerts.
// Pure, so they are tested without a page: a series' newest value, each Spark's share of a total
// with the right operation per metric, the energy since midnight integrated from power samples, the
// timeline's pages, an alert rule's edit, and the words for what a board does not expose.

import type { Agg, EventType, FleetAlert, FleetEvent, FleetNode, FleetRule, MetricKey, MetricSeries, NodeSample, RulePatch } from '../api/fleet';
import type { ChartMode } from '../charts/modes';

/** what a page says for a field the board does not expose (SRV-725: absent, never 0) */
export const NOT_EXPOSED = 'not exposed by this board';

/** The newest value of a series that has one; null when every sample is null. */
export function lastValue(values: (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i--) {
    const v = values[i];
    if (v != null && Number.isFinite(v)) return v;
  }
  return null;
}

/** Each node's newest value in a metric answer. */
export function nowByNode(m: Pick<MetricSeries, 'series'>): Record<string, number | null> {
  return Object.fromEntries(m.series.map((s) => [s.node, lastValue(s.values)]));
}

/** The values now of the Sparks that answer: an off Spark's last sample is history, not now. */
export function liveValues(values: Record<string, number | null>, nodes: Pick<FleetNode, 'id' | 'status'>[]): Record<string, number | null> {
  const off = new Set(nodes.filter((n) => n.status === 'off').map((n) => n.id));
  return Object.fromEntries(Object.entries(values).map(([id, v]) => [id, off.has(id) ? null : v]));
}

/** Whether a series has no sample at all: the board does not give it (memory clock on a GB10, a
 *  fabric port where there is none), or the history has not started. */
export const noSamples = (values: (number | null)[]) => values.every((v) => v == null);

export interface SharePart {
  id: string;
  alias: string;
  colour: number;
  v: number | null;
}

/** The nodes in scope with their value, in the fleet's order. */
export function shareParts(nodes: Pick<FleetNode, 'id' | 'alias' | 'colour'>[], values: Record<string, number | null | undefined>): SharePart[] {
  return nodes.map((n) => ({ id: n.id, alias: n.alias, colour: n.colour, v: values[n.id] ?? null }));
}

/** The aggregate of the parts with the metric's operation; a node without a value is left out, never
 *  counted as zero; null when none has one. `maxmean` is the max (its mean is said beside it). */
export function aggregateNow(parts: { v: number | null }[], agg: Agg): number | null {
  const vs = parts.map((p) => p.v).filter((v): v is number => v != null && Number.isFinite(v));
  if (!vs.length) return null;
  const sum = vs.reduce((a, b) => a + b, 0);
  if (agg === 'sum') return sum;
  if (agg === 'mean') return sum / vs.length;
  return Math.max(...vs);
}

/** The share bar's segments: each node's fraction of the sum (a sum metric only); the nodes without a
 *  value get none. `cap` (memory) leaves the free part as its own segment. */
export function shareSegments(parts: SharePart[], cap: number | null = null): { id: string | null; colour: number | null; f: number }[] {
  const vs = parts.map((p) => Math.max(0, p.v ?? 0));
  const sum = vs.reduce((a, b) => a + b, 0);
  const whole = cap != null && cap > sum ? cap : sum;
  if (whole <= 0) return [];
  const out: { id: string | null; colour: number | null; f: number }[] = parts.filter((_, i) => vs[i] > 0).map((p) => ({ id: p.id, colour: p.colour, f: Math.max(0, p.v ?? 0) / whole }));
  if (cap != null && cap > sum) out.push({ id: null, colour: null, f: (cap - sum) / whole });
  return out;
}

/** The share bar's tooltip: one line per node with its value and share, then the total. */
export function shareTip(parts: SharePart[], unit: string, fmt: (v: number) => string, total: number | null): string {
  const sum = parts.reduce((a, p) => a + Math.max(0, p.v ?? 0), 0);
  const rows = parts.map((p) => (p.v == null ? `${p.alias}: no sample` : `${p.alias}: ${fmt(p.v)} ${unit}${sum > 0 ? ` (${Math.round((100 * Math.max(0, p.v)) / sum)} %)` : ''}`.trim()));
  return [...rows, total == null ? '' : `Σ ${fmt(total)} ${unit}`.trim()].filter(Boolean).join('\n');
}

/** Local midnight of the day `t` (Unix seconds) falls in. */
export function midnight(t: number): number {
  const d = new Date(t * 1000);
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
}

/** Energy in kWh from power samples in W between `from` and `to` (Unix seconds): the trapezoid
 *  between two samples with values; a null breaks it (no sample, no energy guessed). `covered` is the
 *  share of the span the samples cover, so a day the history began in the middle of says so. */
export function energyKWh(values: (number | null)[], t0: number, step: number, from: number, to: number): { kwh: number | null; covered: number } {
  if (to <= from || step <= 0) return { kwh: null, covered: 0 };
  let ws = 0;
  let span = 0;
  for (let i = 1; i < values.length; i++) {
    const a = values[i - 1];
    const b = values[i];
    if (a == null || b == null) continue;
    const ta = t0 + (i - 1) * step;
    const tb = ta + step;
    const lo = Math.max(ta, from);
    const hi = Math.min(tb, to);
    if (hi <= lo) continue;
    // the value at lo and hi on the line from (ta, a) to (tb, b)
    const at = (t: number) => a + ((b - a) * (t - ta)) / step;
    ws += ((at(lo) + at(hi)) / 2) * (hi - lo);
    span += hi - lo;
  }
  return { kwh: span > 0 ? ws / 3.6e6 : null, covered: Math.min(1, span / (to - from)) };
}

// ---- Hardware ----------------------------------------------------------------------------------

export interface HwGroup {
  id: string;
  metric: MetricKey;
  title: string;
  sub: string;
  mode: ChartMode;
  /** what the samples add under the chart */
  extra?: 'gpu' | 'mem' | 'cores' | 'disk' | 'lan' | 'fabric';
}

/** the metric groups of concept §5, in the order a reader looks: the GPU, memory, CPU, disk,
 *  network, then the engines on top of them */
export const HW_GROUPS: HwGroup[] = [
  { id: 'gpu', metric: 'gpu', title: 'GPU utilisation', sub: '% · mean of the scope', mode: 'lines', extra: 'gpu' },
  { id: 'power', metric: 'power', title: 'Power draw', sub: 'W · Σ of the scope', mode: 'stacked' },
  { id: 'temp', metric: 'temp', title: 'GPU temperature', sub: '°C · max and mean · the rule fires at 85', mode: 'lines' },
  { id: 'sm', metric: 'sm', title: 'SM clock', sub: 'MHz · mean · the clock drops when it throttles', mode: 'lines' },
  { id: 'memclk', metric: 'memclk', title: 'Memory clock', sub: 'MHz · mean', mode: 'lines' },
  { id: 'mem', metric: 'mem', title: 'Unified memory', sub: 'GiB used · Σ, with the usable capacity', mode: 'stacked', extra: 'mem' },
  { id: 'cpu', metric: 'cpu', title: 'CPU', sub: '% · mean · per core under it', mode: 'lines', extra: 'cores' },
  { id: 'disk', metric: 'disk', title: 'Disk', sub: 'MB/s read + write · Σ', mode: 'lines', extra: 'disk' },
  { id: 'lan', metric: 'lan', title: 'LAN', sub: 'MB/s rx + tx · Σ · management and the API', mode: 'stacked', extra: 'lan' },
  { id: 'fabric', metric: 'fabric', title: 'Fabric', sub: 'Gb/s rx + tx · Σ over the ConnectX ports', mode: 'lines', extra: 'fabric' },
  { id: 'tps', metric: 'tps', title: 'Engines: decode', sub: 'tok/s · Σ', mode: 'stacked' },
  { id: 'req', metric: 'req', title: 'Engines: active requests', sub: 'Σ', mode: 'stacked' },
  { id: 'ttft', metric: 'ttft', title: 'Engines: time to first token', sub: 'ms · p50 per window · mean of the scope', mode: 'lines' },
];

/** whether the board gives a metric at all, from its newest sample: null when the sample cannot say */
export function exposes(metric: MetricKey, s: NodeSample | null | undefined): boolean | null {
  if (!s) return null;
  const g = s.gpus ?? [];
  switch (metric) {
    case 'gpu':
      return g.some((x) => x.util != null);
    case 'power':
      return g.some((x) => x.power_w != null);
    case 'temp':
      return g.some((x) => x.temp_c != null);
    case 'sm':
      return g.some((x) => x.sm_mhz != null);
    case 'memclk':
      return g.some((x) => x.mem_mhz != null);
    case 'mem':
      return s.memory?.used != null;
    case 'cpu':
      return s.cpu?.util != null;
    case 'disk':
      return !!s.disks?.devices?.length;
    case 'lan':
      return !!s.net?.lan?.length;
    case 'fabric':
      return !!s.net?.fabric?.some((p) => p.state === 'up');
    default:
      return null;
  }
}

/** Why a node's series has no line: the board does not expose it, the controller runs on the node
 *  (no RTT to time), no ConnectX port, or no sample in the range yet. Without a sample to ask, a
 *  metric the node gave nothing of while its other figures came (`others`) is one it does not give. */
export function absentWhy(metric: MetricKey, n: FleetNode, s: NodeSample | null | undefined, hostNode: string | null, others = false): string {
  if (metric === 'rtt' && hostNode === n.id) return 'the controller runs on this Spark: no network between them to time';
  if (metric === 'fabric' && !s?.net?.fabric?.length && n.inventory.cx7 == null) return `no ConnectX port: ${NOT_EXPOSED}`;
  if (metric === 'fabric' && s?.net?.fabric?.length && !s.net.fabric.some((p) => p.state === 'up')) return 'ConnectX ports without a cable';
  if (exposes(metric, s) === false) return NOT_EXPOSED;
  if (!s && others && !['tps', 'req', 'ttft', 'rtt'].includes(metric)) return NOT_EXPOSED;
  return 'no samples in this range yet';
}

// ---- events ------------------------------------------------------------------------------------

export const EVENT_LABEL: Record<EventType, string> = {
  alert: 'Alerts',
  failover: 'Failovers',
  deploy: 'Deployments',
  link: 'Link tests',
  weights: 'Weights',
  node: 'Sparks joined, off, up',
  update: 'Updates',
  engine: 'Engines',
  controller: 'Controller',
};

const GLYPH: Record<EventType, string> = { alert: '⚠', failover: '⇄', deploy: '▶', link: '⟷', weights: '⇣', node: '◆', update: '↻', engine: '⏻', controller: '⌂' };

/** The event's glyph: an alert resolved is a tick, an acknowledgement a dot. */
export function eventGlyph(e: Pick<FleetEvent, 'type' | 'severity'>): string {
  if (e.type === 'alert') return e.severity === 'resolved' ? '✓' : e.severity == null ? '·' : '⚠';
  return GLYPH[e.type] ?? '•';
}

/** The event's tone: an alert firing or a node off is a warning or an error, a resolution ok. */
export function eventTone(e: Pick<FleetEvent, 'type' | 'severity'>): 'ok' | 'warn' | 'err' | '' {
  if (e.severity === 'resolved') return 'ok';
  if (e.severity === 'err') return 'err';
  if (e.severity === 'warn' || e.type === 'failover') return 'warn';
  return '';
}

/** Two pages of the timeline as one: each event once, newest first. */
export function mergeEvents(a: FleetEvent[], b: FleetEvent[]): FleetEvent[] {
  const byId = new Map<string, FleetEvent>();
  for (const e of [...a, ...b]) byId.set(e.id, e);
  return [...byId.values()].sort((x, y) => y.at - x.at || (x.id < y.id ? 1 : -1));
}

/** The `before` of the next older page: the oldest event's time; null for an empty list. */
export function olderCursor(list: FleetEvent[]): number | null {
  return list.length ? Math.min(...list.map((e) => e.at)) : null;
}

/** The `since` of a poll for newer events: a little before the newest shown, because another event
 *  of that same second (an acknowledgement right after its alert fired) may not be written yet;
 *  mergeEvents drops the ones already shown. */
export function newerCursor(list: FleetEvent[], overlap = 5): number | null {
  return list.length ? Math.max(...list.map((e) => e.at)) - overlap : null;
}

/** A page that came back full may have more behind it. */
export const mayHaveMore = (page: FleetEvent[], limit: number) => page.length >= limit;

/** The events grouped by local day, newest first: [day label, events]. */
export function byDay(list: FleetEvent[], now = Date.now() / 1000): [string, FleetEvent[]][] {
  const out: [string, FleetEvent[]][] = [];
  const today = midnight(now);
  for (const e of list) {
    const m = midnight(e.at);
    const days = Math.round((today - m) / 86400);
    const label = days === 0 ? 'Today' : days === 1 ? 'Yesterday' : new Date(e.at * 1000).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    const last = out[out.length - 1];
    if (last && last[0] === label) last[1].push(e);
    else out.push([label, [e]]);
  }
  return out;
}

/** Who acknowledged an alert and when: the time from the alert, who from its "Acknowledged: …"
 *  event (the controller writes "by <who>"); null when it is not acknowledged. */
export function ackInfo(a: Pick<FleetAlert, 'acked' | 'acked_at' | 'title'>, events: FleetEvent[]): { at: number | null; who: string | null } | null {
  if (!a.acked) return null;
  const ev = events.find((e) => e.type === 'alert' && e.text === `Acknowledged: ${a.title}`);
  const who = ev ? /^by (.+)$/.exec(ev.detail)?.[1] ?? null : null;
  return { at: a.acked_at, who };
}

/** The rule an alert belongs to: its id is `<rule>:<subject>`. */
export const alertRule = (a: Pick<FleetAlert, 'id'>) => a.id.split(':')[0];

// ---- alert rules -------------------------------------------------------------------------------

export interface RuleDraft {
  enabled: boolean;
  /** the fields as typed */
  threshold: string;
  for_s: string;
  scope: string;
  app: boolean;
  system: boolean;
}

export function draftOf(r: FleetRule): RuleDraft {
  return { enabled: r.enabled, threshold: r.threshold == null ? '' : String(r.threshold), for_s: String(r.for_s), scope: r.scope, app: r.notify.includes('app'), system: r.notify.includes('system') };
}

/** Whether the rule's threshold can be edited: a rule without one (a node that does not answer)
 *  has nothing to set. */
export const thresholdEditable = (r: Pick<FleetRule, 'threshold'>) => r.threshold != null;

/** Whether the rule's scope can change: a rule about the controller's machine keeps its own, and a
 *  group only the mock knows is shown, not offered. */
export const scopeEditable = (r: Pick<FleetRule, 'scope'>) => r.scope !== 'host';

/** What the scope select offers: all Sparks and each one; the rule's own scope stays listed when
 *  it is something else (a group), so the select shows it. */
export function scopeOptions(r: Pick<FleetRule, 'scope'>, nodes: Pick<FleetNode, 'id' | 'alias'>[]): { value: string; label: string }[] {
  const opts = [{ value: 'all', label: 'All Sparks' }, ...nodes.map((n) => ({ value: `node:${n.id}`, label: n.alias }))];
  if (!opts.some((o) => o.value === r.scope)) opts.unshift({ value: r.scope, label: scopeLabel(r.scope, nodes) });
  return opts;
}

/** A rule's scope in words. */
export function scopeLabel(scope: string, nodes: Pick<FleetNode, 'id' | 'alias'>[]): string {
  if (scope === 'all') return 'All Sparks';
  if (scope === 'host') return 'The controller’s machine';
  if (scope.startsWith('node:')) return nodes.find((n) => `node:${n.id}` === scope)?.alias ?? scope.slice(5);
  return scope[0].toUpperCase() + scope.slice(1);
}

/** The PATCH for a draft: only what changed, or what is wrong with it (the controller's own
 *  refusals, said before the request). Nothing changed is `{patch: null}`. */
export function rulePatch(r: FleetRule, d: RuleDraft): { patch: RulePatch | null; error?: string } {
  const p: RulePatch = {};
  if (d.enabled !== r.enabled) p.enabled = d.enabled;
  const notify = [...(d.app ? ['app' as const] : []), ...(d.system ? ['system' as const] : [])];
  if (notify.join() !== [...r.notify].sort().join()) p.notify = notify;
  if (thresholdEditable(r)) {
    const t = Number(d.threshold.trim().replace(',', '.'));
    if (!d.threshold.trim() || !Number.isFinite(t)) return { patch: null, error: 'The threshold is a number.' };
    if (t !== r.threshold) p.threshold = t;
  }
  const f = Number(d.for_s.trim());
  if (!d.for_s.trim() || !Number.isFinite(f) || f < 0 || f > 86400) return { patch: null, error: 'Hold is a number of seconds from 0 to 86400.' };
  if (f !== r.for_s) p.for_s = f;
  if (d.scope !== r.scope) {
    if (!scopeEditable(r)) return { patch: null, error: 'This rule is about the controller’s machine; its scope stays.' };
    if (d.scope !== 'all' && !d.scope.startsWith('node:')) return { patch: null, error: 'The scope is all Sparks or one.' };
    p.scope = d.scope;
  }
  return { patch: Object.keys(p).length ? p : null };
}

/** A duration in seconds as the rules say it: 300 → "5 min", 45 → "45 s". */
export function holdWords(s: number): string {
  return s >= 120 && s % 60 === 0 ? `${s / 60} min` : `${s} s`;
}
