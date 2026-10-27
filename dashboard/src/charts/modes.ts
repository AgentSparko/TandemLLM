// The five forms of a multi-node chart (VIS-242), computed from the per-node series the fleet API
// sends: Total (the aggregate, its operation stated: Σ, mean, max, or max with the mean dashed),
// Stacked (each node's share; only for values that add up), Lines (one per node), Split (small
// multiples on a shared y axis) and Table (now, min, mean, max per node and for the aggregate).
// Pure functions: <qse-fleet-chart> draws what chartModel() says. A null value is no sample: the
// aggregates skip it, they never count it as zero.

import type { Agg } from '../api/fleet';
import { niceMax } from './svg';

export type ChartMode = 'total' | 'stacked' | 'lines' | 'split' | 'table';
export const CHART_MODES: { mode: ChartMode; label: string }[] = [
  { mode: 'total', label: 'Total' },
  { mode: 'stacked', label: 'Stacked' },
  { mode: 'lines', label: 'Lines' },
  { mode: 'split', label: 'Split' },
  { mode: 'table', label: 'Table' },
];
const MODES = CHART_MODES.map((m) => m.mode);
export const isChartMode = (m: unknown): m is ChartMode => (MODES as unknown[]).includes(m);

export interface NodeSeries {
  id: string;
  alias: string;
  colour: number;
  values: (number | null)[];
}

/** the aggregate's operation as the chart states it */
export const AGG_OP: Record<Agg, string> = { sum: 'Σ', mean: 'mean', max: 'max', maxmean: 'max · mean' };
const AGG_WORD: Record<Agg, string> = { sum: 'sum over', mean: 'mean of', max: 'max of', maxmean: 'max and mean of' };

/** Why a mode is off for this aggregation, or null. */
export function modeRefusal(mode: ChartMode, agg: Agg): string | null {
  if (mode === 'stacked' && agg !== 'sum') return `Only values that add up can stack; this one is a ${agg === 'maxmean' ? 'max and a mean' : agg}`;
  return null;
}

/** The mode the chart draws: one node has no aggregate to show, and a refused mode falls back to Lines. */
export function effectiveMode(mode: ChartMode, agg: Agg, nodes: number): ChartMode {
  if (nodes < 2) return mode === 'table' ? 'table' : 'lines';
  return modeRefusal(mode, agg) ? 'lines' : mode;
}

function present(series: (number | null)[][], i: number): number[] {
  const out: number[] = [];
  for (const s of series) {
    const v = s[i];
    if (v != null && Number.isFinite(v)) out.push(v);
  }
  return out;
}

/** The aggregate per sample (null where no node has a sample); `mean` is the dashed line of maxmean. */
export function aggregate(series: (number | null)[][], agg: Agg): { total: (number | null)[]; mean: (number | null)[] | null } {
  const n = Math.max(0, ...series.map((s) => s.length));
  const total: (number | null)[] = [];
  const mean: (number | null)[] = [];
  for (let i = 0; i < n; i++) {
    const a = present(series, i);
    const sum = a.reduce((x, y) => x + y, 0);
    mean.push(a.length ? sum / a.length : null);
    total.push(!a.length ? null : agg === 'sum' ? sum : agg === 'mean' ? sum / a.length : Math.max(...a));
  }
  return { total, mean: agg === 'maxmean' ? mean : null };
}

/** Stacked bands bottom to top in series order; a node without a sample has no band there (null). */
export function stackLayers(series: NodeSeries[]): { id: string; lo: (number | null)[]; hi: (number | null)[] }[] {
  const n = Math.max(0, ...series.map((s) => s.values.length));
  const acc = new Array<number>(n).fill(0);
  return series.map((s) => {
    const lo: (number | null)[] = [];
    const hi: (number | null)[] = [];
    for (let i = 0; i < n; i++) {
      const v = s.values[i];
      if (v == null || !Number.isFinite(v)) {
        lo.push(null);
        hi.push(null);
        continue;
      }
      lo.push(acc[i]);
      acc[i] += v;
      hi.push(acc[i]);
    }
    return { id: s.id, lo, hi };
  });
}

export interface Stats {
  now: number | null;
  min: number | null;
  mean: number | null;
  max: number | null;
}

export function stats(values: (number | null)[]): Stats {
  let now: number | null = null;
  let min = Infinity;
  let max = -Infinity;
  let sum = 0;
  let k = 0;
  for (const v of values) {
    if (v == null || !Number.isFinite(v)) continue;
    now = v;
    min = Math.min(min, v);
    max = Math.max(max, v);
    sum += v;
    k++;
  }
  return k ? { now, min, mean: sum / k, max } : { now: null, min: null, mean: null, max: null };
}

/** The Table: a row per node and the aggregate's row (Σ for sums, else its operation). */
export function tableRows(series: NodeSeries[], agg: Agg): { rows: (NodeSeries & { stats: Stats })[]; total: Stats & { label: string } } {
  const { total } = aggregate(series.map((s) => s.values), agg);
  return { rows: series.map((s) => ({ ...s, stats: stats(s.values) })), total: { ...stats(total), label: AGG_OP[agg === 'maxmean' ? 'max' : agg] } };
}

/** The capacity line: the fleet's for Total and Stacked of a sum, one node's for Lines and Split. */
export function capacity(mode: ChartMode, agg: Agg, cap: number | null | undefined, nodes: number): { v: number; perNode: boolean } | null {
  if (cap == null) return null;
  if (mode === 'stacked' || (mode === 'total' && agg === 'sum')) return { v: cap * nodes, perNode: false };
  if (mode === 'lines' || mode === 'split') return { v: cap, perNode: true };
  return null;
}

export interface ChartModel {
  mode: ChartMode;
  count: number;
  total: (number | null)[];
  mean: (number | null)[] | null;
  layers: { id: string; lo: (number | null)[]; hi: (number | null)[] }[];
  /** the y axis top, shared by every small multiple in Split */
  top: number;
  cap: { v: number; perNode: boolean } | null;
}

/** Everything <qse-fleet-chart> draws for one mode. `max` is a fixed axis top (percentages). */
export function chartModel(i: { series: NodeSeries[]; agg: Agg; mode: ChartMode; cap?: number | null; max?: number | null }): ChartModel {
  const mode = effectiveMode(i.mode, i.agg, i.series.length);
  const vals = i.series.map((s) => s.values);
  const { total, mean } = aggregate(vals, i.agg);
  const layers = mode === 'stacked' ? stackLayers(i.series) : [];
  const cap = capacity(mode, i.agg, i.cap, i.series.length);
  const finite = (a: (number | null)[]) => a.filter((v): v is number => v != null && Number.isFinite(v));
  let peak: number;
  if (mode === 'total') peak = Math.max(0, ...finite(total));
  else if (mode === 'stacked') peak = Math.max(0, ...layers.flatMap((l) => finite(l.hi)));
  else peak = Math.max(0, ...vals.flatMap(finite));
  if (cap) peak = Math.max(peak, cap.v * 1.04);
  // a fixed top holds wherever the drawn values are per node, or not a sum
  const fixed = i.max != null && (mode === 'lines' || mode === 'split' || mode === 'table' || i.agg !== 'sum');
  return { mode, count: Math.max(0, ...vals.map((v) => v.length)), total, mean, layers, top: fixed ? (i.max as number) : niceMax(Math.max(peak * 1.06, 1e-9)), cap };
}

/** The line under the legend that says what the chart shows. */
export function modeNote(mode: ChartMode, agg: Agg, nodes: number): string {
  if (nodes < 2) return 'one Spark in scope';
  if (mode === 'total') return `${AGG_WORD[agg]} ${nodes} Sparks`;
  if (mode === 'stacked') return 'stacked: each band is one Spark';
  if (mode === 'split') return 'one chart per Spark, shared y axis';
  if (mode === 'table') return `now, min, mean and max per Spark`;
  return 'one line per Spark';
}

/** An axis or tooltip time: seconds for a 5-minute window, the weekday over a week. */
export function timeLabel(t: number, span: number): string {
  const d = new Date(t * 1000);
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: span <= 600 ? '2-digit' : undefined, hour12: false });
  return span > 2 * 86400 ? `${d.toLocaleDateString([], { weekday: 'short', day: 'numeric' })} ${hm}` : hm;
}

// ---- the chosen mode per chart, kept for the session and on the device ------------------------
const KEY = 'qse.chart-modes';
const chosen = new Map<string, ChartMode>();

export function savedMode(chartId: string): ChartMode | null {
  if (chosen.has(chartId)) return chosen.get(chartId)!;
  try {
    const m = JSON.parse(localStorage.getItem(KEY) ?? '{}')[chartId];
    if (isChartMode(m)) {
      chosen.set(chartId, m);
      return m;
    }
  } catch {
    /* private mode, no storage, a broken entry */
  }
  return null;
}

export function saveMode(chartId: string, mode: ChartMode): void {
  chosen.set(chartId, mode);
  let all: Record<string, unknown> = {};
  try {
    const o = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    if (o && typeof o === 'object') all = o;
  } catch {
    /* a broken entry is replaced */
  }
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...all, [chartId]: mode }));
  } catch {
    /* private mode, no storage */
  }
}
