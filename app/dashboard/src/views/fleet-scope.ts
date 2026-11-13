// The fleet parts of the pages that are older than the fleet (VIS-254, concept §8.2-8.3), one chunk
// loaded the first time a page shows them; with one Spark none of them shows and the pages look as
// they did. Each follows the scope bar (All, a Spark, a deployment) and its live switch.
//
//   <qse-fleet-live>     Performance → Live: the aggregate Now line (decode now, summed over the
//                        Sparks in scope, with a chart of the last 5 minutes in the mode picked) and
//                        a strip per Spark (what it serves, its decode, its requests in flight), then
//                        the 5-minute tiles with each Spark's share
//   <qse-fleet-history>  Performance → History: decode, TTFT and requests in flight per Spark over the
//                        scope's range, each chart with its mode switch and the fleet's events marked
//   <qse-usage-fleet>    Usage → By Spark: tokens per day stacked by Spark, and per Spark its requests,
//                        tokens, share, decode and TTFT p50, its energy in kWh and tokens per kWh
//
// The rates come from the fleet store (/v1/fleet/metrics): each Spark's engine as its agent reads it,
// every request it served included. Usage's rows come from TandemApp's controller ledger
// (/v1/fleet/ledger/usage): what its gateway relayed, each row with its node; its energy is the
// power samples added up (30 days kept). The views around these parts read this server's own
// ledger; no figure here is added to one of theirs.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { api, describeError } from '../api/client';
import { memberHref, membersWord, fleet, METRICS, type FleetNode, type MetricKey, type MetricSeries } from '../api/fleet';
import type { Usage } from '../api/types';
import type { ChartEvent } from '../charts/fleet-chart';
import '../charts/fleet-chart';
import type { NodeSeries } from '../charts/modes';
import { onFleet, type FleetState } from '../lib/fleet-state';
import { compact, exact, fixed, ms } from '../lib/format';
import { aggregateNow, energyKWh, lastValue, liveValues, shareParts } from '../lib/monitor';
import { poll, type Poller } from '../lib/poll';
import { currentScope, onScope, resolveScope, scopeSource, type Scope } from '../lib/scope';
import { dayStart } from '../lib/time';
import { scopeNodes } from '../lib/where';
import { LightElement } from '../ui/base';
import { nodeChip } from '../ui/node-chip';
import { fmt, sparkline, tile, typeBadge } from './monitor/bits';
import { chartEvents } from './monitor/data';
import '../styles/monitor.css';
import '../styles/fleet-scope.css';

/** what every part shares: the fleet from the shell's store, the scope, a poll that follows both */
abstract class ScopedPart extends LightElement {
  @state() protected fleetState: FleetState | null = null;
  @state() protected scope: Scope = currentScope();
  protected every = 10_000;
  private poller: Poller | null = null;
  private unsub: (() => void)[] = [];

  connectedCallback(): void {
    super.connectedCallback();
    this.unsub.push(
      onFleet((f) => {
        const first = !this.fleetState && f;
        this.fleetState = f;
        if (first) this.reload();
      }),
      onScope((s) => {
        this.scope = s;
        this.restart();
      }),
    );
    this.restart();
  }
  disconnectedCallback(): void {
    this.unsub.forEach((u) => u());
    this.unsub = [];
    this.poller?.stop();
    this.poller = null;
    super.disconnectedCallback();
  }
  protected get resolved(): Scope {
    return this.fleetState ? resolveScope(this.scope, this.fleetState) : this.scope;
  }
  protected get nodes(): FleetNode[] {
    return this.fleetState ? scopeNodes(this.fleetState, this.resolved) : [];
  }
  protected restart(): void {
    this.poller?.stop();
    this.poller = poll((cur) => this.load(cur), this.every);
    if (this.scope.live) this.poller.start();
    else void this.poller.refresh();
  }
  protected reload(): void {
    void this.poller?.refresh();
  }
  protected abstract load(isCurrent: () => boolean): Promise<void>;

  protected series(m: MetricSeries | null): NodeSeries[] {
    const f = this.fleetState;
    return (m?.series ?? []).map((s) => {
      const n = f?.nodes.find((x) => x.id === s.node);
      return { id: s.node, alias: n?.alias ?? s.node, colour: n?.colour ?? 1, values: s.values };
    });
  }
}

const sum = (xs: (number | null | undefined)[]) => xs.reduce<number>((a, v) => a + (v ?? 0), 0);
const mean = (xs: (number | null)[]) => {
  const v = xs.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

// ---- Performance → Live ------------------------------------------------------------------------

@customElement('qse-fleet-live')
export class QseFleetLive extends ScopedPart {
  protected every = 2000;
  @state() private m: Partial<Record<'tps' | 'req' | 'ttft', MetricSeries>> = {};
  @state() private marks: ChartEvent[] = [];
  @state() private error = '';

  protected async load(isCurrent: () => boolean): Promise<void> {
    if (!this.fleetState) return;
    const source = scopeSource(this.resolved);
    try {
      const [tps, req, ttft] = await Promise.all((['tps', 'req', 'ttft'] as const).map((metric) => fleet.metrics({ source, metric, range: '5m' })));
      const marks = await chartEvents(tps.t0).catch(() => []);
      if (!isCurrent()) return;
      this.m = { tps, req, ttft };
      this.marks = marks;
      this.error = '';
    } catch (e) {
      if (isCurrent()) this.error = describeError(e).message;
    }
  }

  /** each Spark's value now; none for one that is off */
  private now(k: 'tps' | 'req' | 'ttft'): Record<string, number | null> {
    const m = this.m[k];
    return liveValues(Object.fromEntries((m?.series ?? []).map((s) => [s.node, lastValue(s.values)])), this.fleetState?.nodes ?? []);
  }
  /** each Spark's mean over the 5 minutes */
  private avg(k: 'tps' | 'req' | 'ttft'): Record<string, number | null> {
    return Object.fromEntries((this.m[k]?.series ?? []).map((s) => [s.node, mean(s.values)]));
  }

  render() {
    const f = this.fleetState;
    if (!f) return nothing;
    const nodes = this.nodes;
    const tps = this.m.tps;
    const nowTps = this.now('tps');
    const nowReq = this.now('req');
    const total = aggregateNow(shareParts(nodes, nowTps), 'sum');
    const inFlight = sum(nodes.map((n) => nowReq[n.id]));
    const decoding = nodes.filter((n) => (nowTps[n.id] ?? 0) > 0.5).length;
    const s = this.resolved;
    const what = s.kind === 'deployment' ? f.deployments.find((d) => d.id === s.id)?.name ?? s.id : null;
    return html`<div class="fl-scope fl-live" data-fleet-live>
      <section class="panel fl-now" data-fleet-now>
        <div class="fl-now-num">
          <div class="eyebrow">Decode now · Σ ${nodes.length} ${membersWord(nodes)}${what ? ` · ${what}` : ''}</div>
          <div class="fl-big num" data-fleet-total>${fmt(total)}<span class="u">tok/s</span></div>
          <p class="small muted fl-now-sub"><b class="num">${fmt(inFlight)}</b> in flight · <b class="num">${decoding}</b> of ${nodes.length} decoding${this.error ? html` · <span class="warn-ink">${this.error}</span>` : nothing}</p>
          <p class="xs faint">The fleet store's samples: every request each engine served, through this endpoint or not. A Split's tokens count once.</p>
        </div>
        <div class="fl-now-chart">
          ${tps ? html`<qse-fleet-chart chart-id="perf-now" .series=${this.series(tps)} agg="sum" unit="tok/s" .t0=${tps.t0} .step=${tps.step_s} .events=${this.marks} mode="total" .height=${170} aria-label="Decode tok/s, last 5 minutes"></qse-fleet-chart>` : html`<div class="skeleton" style="height:170px"></div>`}
        </div>
      </section>
      <section class="panel fl-strips" data-fleet-strips>
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Per Spark</h2><p class="panel-sub">One strip per Spark in scope: what it serves, its decode over the last 5 minutes, its requests in flight</p></div></header>
        ${nodes.map((n) => this.strip(n, nowTps[n.id] ?? null, nowReq[n.id] ?? null))}
      </section>
      ${this.tiles(nodes)}
    </div>`;
  }

  private strip(n: FleetNode, tps: number | null, req: number | null): TemplateResult {
    const deps = (this.fleetState?.deployments ?? []).filter((d) => d.members.includes(n.id));
    const values = this.m.tps?.series.find((x) => x.node === n.id)?.values ?? [];
    const k = Math.max(0, Math.round(req ?? 0));
    return html`<div class="fl-strip ${n.status === 'off' ? 'is-off' : ''}" data-strip=${n.id}>
      <div class="fl-strip-who">
        ${nodeChip(n, { href: memberHref(n) })}
        <div class="fl-strip-deps xs">${deps.length ? deps.map((d) => html`<span>${typeBadge(d.type)} ${d.name}</span>`) : html`<span class="faint">serves nothing</span>`}</div>
      </div>
      <div class="fl-strip-chart">${sparkline(values, n.colour, 40, `${n.alias} decode, last 5 minutes`)}</div>
      <div class="fl-strip-lanes" aria-label=${`${k} in flight on ${n.alias}`}>
        <span class="fl-dots" aria-hidden="true">${Array.from({ length: Math.min(k, 12) }, () => html`<i style="--c:var(--n${((n.colour - 1) % 4) + 1})"></i>`)}${k > 12 ? html`<b class="xs">+${k - 12}</b>` : nothing}</span>
        <span class="xs muted num">${n.status === 'off' ? n.status_text : `${k} in flight`}</span>
      </div>
      <div class="fl-strip-now"><b class="num" data-strip-tps>${fmt(tps)}</b><small>tok/s now</small></div>
    </div>`;
  }

  private tiles(nodes: FleetNode[]): TemplateResult {
    const tp = shareParts(nodes, this.avg('tps'));
    const tt = shareParts(nodes, this.avg('ttft'));
    const rq = shareParts(nodes, this.avg('req'));
    return html`<div class="mtiles is-3" data-fleet-tiles>
      ${tile({ id: 'decode-5m', label: 'Decode, 5-min mean', agg: 'sum', value: fmt(aggregateNow(tp, 'sum')), unit: 'tok/s', parts: tp, sub: tp.map((p) => `${p.alias} ${fmt(p.v)}`).join(' · ') })}
      ${tile({ id: 'ttft-5m', label: 'TTFT p50', agg: 'mean', value: fmt(aggregateNow(tt, 'mean')), unit: 'ms', parts: tt, sub: tt.map((p) => `${p.alias} ${p.v == null ? '—' : ms(p.v)}`).join(' · ') })}
      ${tile({ id: 'req-5m', label: 'In flight, 5-min mean', agg: 'sum', value: fmt(aggregateNow(rq, 'sum'), 1), parts: rq, fmt: (v) => fmt(v, 1), sub: rq.map((p) => `${p.alias} ${fmt(p.v, 1)}`).join(' · ') })}
    </div>`;
  }
}

// ---- Performance → History ---------------------------------------------------------------------

const HISTORY: { metric: MetricKey; title: string; sub: string; mode: 'lines' | 'stacked' | 'total'; decimals: number }[] = [
  { metric: 'tps', title: 'Decode', sub: 'tok/s per Spark, Σ in Total', mode: 'stacked', decimals: 0 },
  { metric: 'ttft', title: 'Time to first token', sub: 'p50 ms per Spark, the mean of them in Total', mode: 'lines', decimals: 0 },
  { metric: 'req', title: 'Requests in flight', sub: 'per Spark, Σ in Total', mode: 'stacked', decimals: 1 },
];

@customElement('qse-fleet-history')
export class QseFleetHistory extends ScopedPart {
  protected every = 30_000;
  @state() private m: Partial<Record<MetricKey, MetricSeries>> = {};
  @state() private marks: ChartEvent[] = [];
  @state() private error = '';

  protected async load(isCurrent: () => boolean): Promise<void> {
    if (!this.fleetState) return;
    const s = this.resolved;
    try {
      const got = await Promise.all(HISTORY.map((h) => fleet.metrics({ source: scopeSource(s), metric: h.metric, range: s.range })));
      const marks = await chartEvents(got[0].t0).catch(() => []);
      if (!isCurrent()) return;
      this.m = Object.fromEntries(got.map((m) => [m.metric, m]));
      this.marks = marks;
      this.error = '';
    } catch (e) {
      if (isCurrent()) this.error = describeError(e).message;
    }
  }

  render() {
    if (!this.fleetState) return nothing;
    const s = this.resolved;
    return html`<section class="fl-scope fl-history" data-fleet-history>
      <div class="history-head"><div class="panel-titles"><h2 class="panel-title">By Spark</h2><p class="panel-sub">the fleet store over the scope's range (${s.range}); each chart in Total, Stacked, Lines, Split or Table · ◆ the fleet's events${this.error ? html` · <span class="warn-ink">${this.error}</span>` : nothing}</p></div></div>
      <div class="grid-2">
        ${HISTORY.map((h) => {
          const m = this.m[h.metric];
          return html`<section class="panel" data-fleet-chart=${h.metric}>
            <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">${h.title}</h2><p class="panel-sub">${h.sub}</p></div></header>
            ${m ? html`<qse-fleet-chart chart-id=${`perf-hist-${h.metric}`} .series=${this.series(m)} agg=${METRICS[h.metric].agg} unit=${METRICS[h.metric].unit} .t0=${m.t0} .step=${m.step_s} .decimals=${h.decimals} .events=${this.marks} mode=${h.mode} .height=${200} aria-label=${`${h.title} per Spark over ${m.range}`}></qse-fleet-chart>` : html`<div class="skeleton" style="height:200px"></div>`}
          </section>`;
        })}
      </div>
    </section>`;
  }
}

// ---- Usage → By Spark ----------------------------------------------------------------------------

export interface UsageWindow {
  from: string;
  to: string;
  tz: string;
  model: string | null;
  client: string | null;
  user: string | null;
  internal: boolean;
}

interface NodeRow {
  node: FleetNode;
  usage: Usage;
  /** kWh over the days the power samples cover in the window, and those days' tokens */
  kwh: number | null;
  covered: number;
  tokensInEnergy: number;
  watts: number | null;
}

@customElement('qse-usage-fleet')
export class QseUsageFleet extends ScopedPart {
  protected every = 60_000;
  @property({ attribute: false }) window: UsageWindow | null = null;
  @state() private rows: NodeRow[] | null = null;
  @state() private whole: Usage | null = null;
  @state() private energyFrom: number | null = null;
  @state() private error = '';
  private lastKey = '';

  updated(changed: Map<string, unknown>): void {
    if (changed.has('window') && this.window) {
      const key = JSON.stringify(this.window);
      if (key !== this.lastKey) {
        this.lastKey = key;
        this.rows = null;
        this.reload();
      }
    }
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    const w = this.window;
    const f = this.fleetState;
    if (!w || !f) return;
    const s = this.resolved;
    const nodes = this.nodes;
    const q = { from: w.from, to: w.to, bucket: 'day' as const, tz: w.tz, model: w.model, client: w.client, user: w.user, internal: w.internal, ledger: 'fleet' as const, deployment: s.kind === 'deployment' ? s.id : null };
    try {
      const [whole, per, power] = await Promise.all([
        api.usage(q),
        Promise.all(nodes.map((n) => api.usage({ ...q, node: n.id }))),
        fleet.metrics({ source: s.kind === 'deployment' ? scopeSource(s) : 'all', metric: 'power', range: '30d' }).catch(() => null),
      ]);
      if (!isCurrent()) return;
      const from = dayStart(w.from, w.tz) / 1000;
      const to = Math.min(Date.now() / 1000, dayStart(w.to, w.tz) / 1000 + 86400);
      const eFrom = power ? Math.max(from, power.t0) : null;
      this.energyFrom = eFrom;
      this.whole = whole;
      this.rows = nodes.map((node, i) => {
        const u = per[i];
        const p = power?.series.find((x) => x.node === node.id);
        const e = p && eFrom != null ? energyKWh(p.values, power!.t0, power!.step_s, eFrom, to) : { kwh: null, covered: 0 };
        const tokensInEnergy = eFrom == null ? 0 : sum(u.buckets.filter((b) => Date.parse(b.start) / 1000 + 86400 > eFrom && Date.parse(b.start) / 1000 < to).map((b) => b.total_tokens));
        return { node, usage: u, kwh: e.kwh, covered: e.covered, tokensInEnergy, watts: e.kwh != null && to > eFrom! ? (e.kwh * 3.6e6) / ((to - eFrom!) * e.covered || 1) : null };
      });
      this.error = '';
    } catch (e) {
      if (isCurrent()) this.error = describeError(e).message;
    }
  }

  render() {
    const f = this.fleetState;
    if (!f) return nothing;
    const rows = this.rows;
    const head = html`<div class="history-head"><div class="panel-titles"><h2 class="panel-title">By Spark</h2><p class="panel-sub">TandemApp's controller ledger: the requests its gateway relayed, each with the Spark that served it; energy is each Spark's power samples added up${this.error ? html` · <span class="warn-ink">${this.error}</span>` : nothing}</p></div></div>`;
    if (!rows || !this.whole) return html`<section class="fl-scope fl-usage" data-usage-fleet>${head}<div class="skeleton" style="height:220px"></div></section>`;
    const tokens = sum(rows.map((r) => r.usage.totals.total_tokens));
    const reqs = sum(rows.map((r) => r.usage.totals.requests));
    const kwhParts = rows.map((r) => ({ id: r.node.id, alias: r.node.alias, colour: r.node.colour, v: r.kwh }));
    const kwh = rows.some((r) => r.kwh != null) ? sum(rows.map((r) => r.kwh)) : null;
    const tokE = sum(rows.map((r) => (r.kwh != null ? r.tokensInEnergy : 0)));
    const other = Math.max(0, this.whole.totals.requests - reqs);
    const days = this.whole.buckets.map((b) => Date.parse(b.start) / 1000);
    const series: NodeSeries[] = rows.map((r) => ({ id: r.node.id, alias: r.node.alias, colour: r.node.colour, values: r.usage.buckets.map((b) => b.total_tokens) }));
    const eDays = this.energyFrom == null ? null : Math.max(0, Math.round((Date.now() / 1000 - this.energyFrom) / 86400));
    const eWords = this.energyFrom == null ? 'no power samples from the controller' : `kWh over the last ${eDays} day${eDays === 1 ? '' : 's'} of the period (the controller keeps 30 days of power)`;
    return html`<section class="fl-scope fl-usage" data-usage-fleet>
      ${head}
      <div class="mtiles is-4" data-usage-tiles>
        ${tile({ id: 'u-req', label: 'Requests', agg: 'sum', value: exact(reqs), parts: rows.map((r) => ({ id: r.node.id, alias: r.node.alias, colour: r.node.colour, v: r.usage.totals.requests })), fmt: (v) => exact(v), sub: other ? `${exact(other)} more on no Spark (a cloud backend)` : 'every one on a Spark' })}
        ${tile({ id: 'u-tok', label: 'Tokens', agg: 'sum', value: compact(tokens), parts: rows.map((r) => ({ id: r.node.id, alias: r.node.alias, colour: r.node.colour, v: r.usage.totals.total_tokens })), fmt: (v) => compact(v), sub: 'in and out' })}
        ${tile({ id: 'u-kwh', label: 'Energy', agg: 'sum', value: kwh == null ? '—' : fixed(kwh, kwh < 10 ? 2 : 1), unit: 'kWh', parts: kwhParts, fmt: (v) => fixed(v, 2), sub: eWords })}
        ${tile({ id: 'u-tpk', label: 'Tokens per kWh', agg: null, value: kwh ? compact(tokE / kwh) : '—', sub: 'Σ tokens / Σ kWh over the same days; higher is better' })}
      </div>
      <section class="panel" data-usage-stack>
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Tokens per day by Spark</h2><p class="panel-sub">stacked, from the controller's ledger; the mode switch shows them as lines, small multiples or a table</p></div></header>
        <qse-fleet-chart chart-id="usage-by-spark" .series=${series} agg="sum" unit="tok" .t0=${days[0] ?? 0} .step=${86400} mode="stacked" .height=${220} aria-label="Tokens per day, stacked by Spark"></qse-fleet-chart>
      </section>
      <section class="panel" data-usage-by-node>
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Per Spark</h2><p class="panel-sub">${eWords}; tokens per kWh counts the tokens of those days only</p></div></header>
        <div class="table-scroll"><table class="table fl-node-table">
          <thead><tr><th>Spark</th><th>Serves</th><th class="num-col">Requests</th><th class="num-col">Tokens</th><th class="num-col">Share</th><th class="num-col">Decode p50</th><th class="num-col">TTFT p50</th><th class="num-col">Energy kWh</th><th class="num-col">Avg power W</th><th class="num-col">Tokens per kWh</th></tr></thead>
          <tbody>
            ${rows.map(
              (r) => html`<tr data-node=${r.node.id}>
                <td>${nodeChip(r.node, { href: memberHref(r.node) })}</td>
                <td class="cell-wrap small">${f.deployments.filter((d) => d.members.includes(r.node.id)).map((d) => d.name).join(', ') || html`<span class="muted">nothing</span>`}</td>
                <td class="num-col num">${exact(r.usage.totals.requests)}</td>
                <td class="num-col num" title=${exact(r.usage.totals.total_tokens)}>${compact(r.usage.totals.total_tokens)}</td>
                <td class="num-col num">${tokens ? `${Math.round((100 * r.usage.totals.total_tokens) / tokens)} %` : '—'}</td>
                <td class="num-col num">${r.usage.totals.decode_tps_p50 == null ? '—' : `${fixed(r.usage.totals.decode_tps_p50, 1)} tok/s`}</td>
                <td class="num-col num">${ms(r.usage.totals.ttft_ms_p50)}</td>
                <td class="num-col num" data-kwh>${r.kwh == null ? '—' : fixed(r.kwh, 2)}${r.kwh != null && r.covered < 0.95 ? html` <span class="faint xs" title="the share of the days the power samples cover">${Math.round(r.covered * 100)} %</span>` : nothing}</td>
                <td class="num-col num">${r.watts == null ? '—' : fmt(r.watts)}</td>
                <td class="num-col num" data-tpk>${r.kwh ? compact(r.tokensInEnergy / r.kwh) : '—'}</td>
              </tr>`,
            )}
          </tbody>
          <tfoot><tr><td>Σ ${rows.length} ${membersWord(rows.map((r) => r.node))}</td><td></td><td class="num-col num">${exact(reqs)}</td><td class="num-col num">${compact(tokens)}</td><td class="num-col num">100 %</td><td></td><td></td><td class="num-col num">${kwh == null ? '—' : fixed(kwh, 2)}</td><td></td><td class="num-col num">${kwh ? compact(tokE / kwh) : '—'}</td></tr></tfoot>
        </table></div>
      </section>
    </section>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-fleet-live': QseFleetLive;
    'qse-fleet-history': QseFleetHistory;
    'qse-usage-fleet': QseUsageFleet;
  }
}
