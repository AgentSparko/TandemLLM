// The fleet's windows (VIS-256), one chunk, loaded the first time one of them shows:
//  Logs       the nodes' logs, live (every 2 s), by Spark, level and text, paused at will; a server
//             without TandemApp's controller shows the box's own log and says so, and a Spark there
//             is named as not reachable, never filled in.
//  Chart      any fleet metric in <qse-fleet-chart> with its modes, following the scope bar or held on
//             all Sparks or one.
//  Inspector  one Spark: status, its figures now, memory, what it serves, how it is reached, its
//             hardware, version and lease, and Terminal, Logs and Chart windows on it.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { api, describeError } from '../api/client';
import { fleet, METRICS, METRIC_KEYS, type FleetLogLine, type FleetMemory, type FleetNode, type MetricKey, type MetricSeries } from '../api/fleet';
import '../charts/fleet-chart';
import type { NodeSeries } from '../charts/modes';
import { onFleet, type FleetState } from '../lib/fleet-state';
import { currentScope, onScope, resolveScope, scopeSource, type Scope } from '../lib/scope';
import { icon } from '../ui/bits';
import { nodeChip, statusWords } from '../ui/node-chip';
import { Feed, WindowBody } from './base';

const NO_CONTROLLER = 'TandemApp’s controller does not run on this server yet (SRV-723), so the fleet API does not answer here.';
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

/** a fleet answer, or `absent` for a server without the fleet API */
async function fromFleet<T>(f: () => Promise<T>): Promise<T | 'absent'> {
  try {
    return await f();
  } catch (e) {
    if (describeError(e).status === 404) return 'absent';
    throw e;
  }
}

/** windows that list the Sparks */
class FleetBody extends WindowBody {
  @state() protected fleetState: FleetState | null = null;
  private offFleet: (() => void) | null = null;
  connectedCallback(): void {
    super.connectedCallback();
    this.offFleet = onFleet((f) => (this.fleetState = f));
  }
  disconnectedCallback(): void {
    this.offFleet?.();
    this.offFleet = null;
    super.disconnectedCallback();
  }
  protected get nodes(): FleetNode[] {
    return this.fleetState?.nodes ?? [];
  }
  protected node(id: unknown): FleetNode | undefined {
    return this.nodes.find((n) => n.id === id);
  }
}

// ---- Logs ----------------------------------------------------------------------------------------

// `every` adds the HTTP access lines; a window saved with the old `all` opens at info
type Level = 'every' | 'debug' | 'info' | 'warn' | 'error';
const LEVELS: [Level, string][] = [
  ['every', 'Every line'],
  ['debug', 'Debug and up'],
  ['info', 'Info and up'],
  ['warn', 'Warn and error'],
  ['error', 'Errors'],
];

const levelOf = (v: unknown): Level => (LEVELS.some(([k]) => k === v) ? (v as Level) : 'info');

@customElement('qse-win-logs')
export class QseWinLogs extends FleetBody {
  @state() private lines: FleetLogLine[] = [];
  /** the server has no fleet API: the box's own log stands in for "all" */
  @state() private boxOnly = false;
  @state() private error: string | null = null;
  @state() private loaded = false;
  private feed = new Feed((cur) => this.load(cur), 2_000);
  private asked = '';

  connectedCallback(): void {
    super.connectedCallback();
    if (!this.ref.paused) this.feed.start();
  }
  disconnectedCallback(): void {
    this.feed.stop();
    super.disconnectedCallback();
  }
  updated(): void {
    const r = this.ref;
    if (r.paused) this.feed.stop();
    else if (this.isConnected) this.feed.start();
    const asked = `${r.node}|${r.level}|${r.q}`;
    if (asked !== this.asked) {
      this.asked = asked;
      this.loaded = false;
      this.feed.refresh();
    }
    const out = this.querySelector<HTMLElement>('.wm-log');
    if (out && out.dataset.follow !== '0') out.scrollTop = out.scrollHeight;
  }

  private async load(isCurrent: () => boolean): Promise<void> {
    const r = this.ref;
    const node = typeof r.node === 'string' ? r.node : 'all';
    const level = levelOf(r.level);
    const q = typeof r.q === 'string' ? r.q : '';
    try {
      const a = this.boxOnly ? 'absent' : await fromFleet(() => fleet.logs({ node, level: level === 'every' ? 'debug' : level, access: level === 'every', q, limit: 300 }));
      if (a !== 'absent') {
        if (isCurrent()) [this.lines, this.error, this.loaded] = [a.lines, null, true];
        return;
      }
      this.boxOnly = true;
      if (node !== 'all') {
        if (isCurrent()) [this.lines, this.loaded] = [[], true];
        return;
      }
      // the box's own log: the engine and the proxy that serve this page
      const own = await api.logsJson({ level: level === 'every' ? 'debug' : level === 'warn' ? 'warning' : level, grep: q || null, backlog: 300 });
      if (isCurrent())
        [this.lines, this.error, this.loaded] = [own.lines.map((l) => ({ t: Date.parse(l.ts) / 1000, node: '', level: l.level === 'warning' ? 'warn' : l.level, source: l.source, msg: l.msg })), null, true];
    } catch (e) {
      if (isCurrent()) [this.error, this.loaded] = [describeError(e).message, true];
    }
  }

  render() {
    const r = this.ref;
    const node = typeof r.node === 'string' ? r.node : 'all';
    const paused = !!r.paused;
    const remote = this.boxOnly && node !== 'all' ? this.node(node)?.alias ?? node : null;
    return html`<div class="wm-pane">
      <div class="wm-bar">
        <select class="wm-in" aria-label="Spark" @change=${(e: Event) => this.setRef({ node: (e.target as HTMLSelectElement).value })}>
          <option value="all" ?selected=${node === 'all'}>${this.boxOnly ? 'This box' : 'All Sparks'}</option>
          ${this.nodes.map((n) => html`<option value=${n.id} ?selected=${node === n.id}>${n.alias}</option>`)}
        </select>
        <select class="wm-in" aria-label="Level" @change=${(e: Event) => this.setRef({ level: (e.target as HTMLSelectElement).value })}>
          ${LEVELS.map(([v, l]) => html`<option value=${v} ?selected=${levelOf(r.level) === v}>${l}</option>`)}
        </select>
        <input class="wm-in wm-filter" type="search" placeholder="Filter" aria-label="Filter" .value=${typeof r.q === 'string' ? r.q : ''} @change=${(e: Event) => this.setRef({ q: (e.target as HTMLInputElement).value })} />
        <span class="grow"></span>
        <button type="button" class="btn btn-ghost btn-sm" aria-pressed=${paused} @click=${() => this.setRef({ paused: !paused })}>${icon(paused ? 'play' : 'pause')} ${paused ? 'Follow' : 'Pause'}</button>
      </div>
      ${this.boxOnly ? html`<p class="wm-note muted small">${remote ? `${remote}’s log comes through its TandemNode agent and TandemApp’s controller; neither answers on this server yet.` : `The box’s own log. ${NO_CONTROLLER}`}</p>` : nothing}
      ${this.error ? html`<p class="wm-note err-ink small">${this.error}</p>` : nothing}
      <div class="wm-log num" role="log" aria-live="off" data-follow=${paused ? '0' : '1'}>
        ${!this.loaded ? html`<p class="muted small wm-empty">loading</p>` : !this.lines.length && !remote ? html`<p class="muted small wm-empty">No lines match.</p>` : nothing}
        ${this.lines.map((l) => {
          const n = this.node(l.node);
          return html`<div class="wm-log-l is-${l.level}"><span class="t">${clock(l.t)}</span><span class="n">${n ? nodeChip(n, { status: false }) : html`<span class="muted">${l.node || 'box'}</span>`}</span><span class="lv">${l.level}</span><span class="m" title=${l.msg}>${l.msg}</span></div>`;
        })}
      </div>
    </div>`;
  }
}

// ---- Chart ---------------------------------------------------------------------------------------

@customElement('qse-win-chart')
export class QseWinChart extends FleetBody {
  @state() private data: MetricSeries | 'absent' | null = null;
  @state() private error: string | null = null;
  @state() private scope: Scope = currentScope();
  private feed = new Feed((cur) => this.load(cur), 10_000);
  private offScope: (() => void) | null = null;
  private asked = '';

  connectedCallback(): void {
    super.connectedCallback();
    this.offScope = onScope((s) => {
      this.scope = s;
      if (this.ref.scope === 'page' || !this.ref.scope) this.feed.refresh();
    });
    this.feed.start();
  }
  disconnectedCallback(): void {
    this.offScope?.();
    this.feed.stop();
    super.disconnectedCallback();
  }
  updated(): void {
    const asked = `${this.metric}|${this.ref.scope}`;
    if (asked !== this.asked) {
      this.asked = asked;
      this.data = null;
      this.feed.refresh();
    }
  }
  private get metric(): MetricKey {
    return METRIC_KEYS.includes(this.ref.metric as MetricKey) ? (this.ref.metric as MetricKey) : 'tps';
  }
  private source(): string {
    const s = typeof this.ref.scope === 'string' ? this.ref.scope : 'page';
    if (s !== 'page') return s;
    return scopeSource(this.fleetState ? resolveScope(this.scope, this.fleetState) : this.scope);
  }

  private async load(isCurrent: () => boolean): Promise<void> {
    try {
      const m = await fromFleet(() => fleet.metrics({ source: this.source(), metric: this.metric, range: this.scope.range }));
      if (isCurrent()) [this.data, this.error] = [m, null];
    } catch (e) {
      if (isCurrent()) this.error = describeError(e).message;
    }
  }

  render() {
    const info = METRICS[this.metric];
    const sc = typeof this.ref.scope === 'string' ? this.ref.scope : 'page';
    const d = this.data;
    let body: TemplateResult;
    if (d === 'absent') body = html`<p class="wm-note muted small">${NO_CONTROLLER}</p>`;
    else if (this.error) body = html`<p class="wm-note err-ink small">${this.error}</p>`;
    else if (!d) body = html`<p class="muted small wm-empty">loading</p>`;
    else {
      const series: NodeSeries[] = d.series.map((s) => {
        const n = this.node(s.node);
        return { id: s.node, alias: n?.alias ?? s.node, colour: n?.colour ?? 1, values: s.values };
      });
      body = html`<qse-fleet-chart chart-id=${`win-${this.win?.id}-${this.metric}`} .series=${series} agg=${d.agg} unit=${d.unit} .t0=${d.t0} .step=${d.step_s} .decimals=${info.decimals} .cap=${d.cap} .max=${info.max ?? null} mode=${d.agg === 'sum' ? 'stacked' : 'lines'} aria-label=${`${info.label} over ${d.range}`}></qse-fleet-chart>`;
    }
    return html`<div class="wm-pane">
      <div class="wm-bar">
        <select class="wm-in" aria-label="Metric" @change=${(e: Event) => this.setRef({ metric: (e.target as HTMLSelectElement).value })}>
          ${METRIC_KEYS.map((k) => html`<option value=${k} ?selected=${k === this.metric}>${METRICS[k].label} (${METRICS[k].unit})</option>`)}
        </select>
        <select class="wm-in" aria-label="Scope" @change=${(e: Event) => this.setRef({ scope: (e.target as HTMLSelectElement).value })}>
          <option value="page" ?selected=${sc === 'page'}>Follow the scope bar</option>
          <option value="all" ?selected=${sc === 'all'}>All Sparks</option>
          ${this.nodes.map((n) => html`<option value=${`node:${n.id}`} ?selected=${sc === `node:${n.id}`}>${n.alias}</option>`)}
        </select>
      </div>
      <div class="wm-chart">${body}</div>
    </div>`;
  }
}

// ---- Node inspector ------------------------------------------------------------------------------

const FIGS: [MetricKey, string][] = [
  ['tps', 'tok/s'],
  ['temp', '°C'],
  ['power', 'W'],
  ['gpu', '% GPU'],
];

@customElement('qse-win-inspector')
export class QseWinInspector extends FleetBody {
  @state() private now: Partial<Record<MetricKey, number | null>> = {};
  @state() private memory: FleetMemory['nodes'][number] | null = null;
  @state() private absent = false;
  private feed = new Feed((cur) => this.load(cur), 10_000);
  private asked = '';

  connectedCallback(): void {
    super.connectedCallback();
    this.feed.start();
  }
  disconnectedCallback(): void {
    this.feed.stop();
    super.disconnectedCallback();
  }
  updated(): void {
    const id = this.nodeId;
    if (id && id !== this.asked) {
      this.asked = id;
      this.now = {};
      this.memory = null;
      this.feed.refresh();
    }
  }
  /** the Spark it shows: its own, else the first */
  private get nodeId(): string | null {
    return typeof this.ref.node === 'string' ? this.ref.node : (this.nodes[0]?.id ?? null);
  }

  private async load(isCurrent: () => boolean): Promise<void> {
    const id = this.nodeId;
    if (!id) return;
    const now: Partial<Record<MetricKey, number | null>> = {};
    for (const [k] of FIGS) {
      const m = await fromFleet(() => fleet.metrics({ source: `node:${id}`, metric: k, range: '5m' }));
      if (m === 'absent') {
        if (isCurrent()) this.absent = true;
        return;
      }
      now[k] = [...(m.series[0]?.values ?? [])].reverse().find((v) => v != null) ?? null;
    }
    const mem = await fromFleet(() => fleet.memory());
    if (!isCurrent() || id !== this.nodeId) return;
    this.now = now;
    this.memory = mem === 'absent' ? null : (mem.nodes.find((x) => x.node === id) ?? null);
  }

  render() {
    const id = this.nodeId;
    const n = this.node(id);
    const bar = html`<div class="wm-bar">
      <select class="wm-in" aria-label="Spark" @change=${(e: Event) => this.setRef({ node: (e.target as HTMLSelectElement).value })}>
        ${this.nodes.map((x) => html`<option value=${x.id} ?selected=${x.id === id}>${x.alias}</option>`)}
      </select>
      <span class="grow"></span>
      ${id ? html`<a class="btn btn-ghost btn-sm" href=${`#/sparks/${encodeURIComponent(id)}`}>Open page</a>` : nothing}
    </div>`;
    if (this.absent || !this.fleetState) return html`<div class="wm-pane">${bar}<p class="wm-note muted small">${this.fleetState ? NO_CONTROLLER : `The node inspector shows a Spark of the fleet. ${NO_CONTROLLER}`}</p></div>`;
    if (!n) return html`<div class="wm-pane">${bar}<p class="wm-note muted small">No Spark ${id ?? ''} in this fleet.</p></div>`;
    const deps = this.fleetState.deployments.filter((d) => d.members.includes(n.id));
    const m = this.memory;
    const used = m ? m.weights + m.kv + m.cache + m.other : null;
    const lease = n.lease_end ? `${Math.max(0, Math.round((n.lease_end - Date.now() / 1000) / 86400))} days left` : n.owned ? 'owned' : 'none';
    return html`<div class="wm-pane">
      ${bar}
      <div class="wm-insp">
        <div class="wm-insp-h">${nodeChip(n, { lg: true })}<span class="small muted">${statusWords(n)}</span></div>
        <div class="wm-insp-figs">
          ${FIGS.map(([k, u]) => html`<div><span class="muted small">${METRICS[k].label}</span><b class="num">${this.now[k] == null ? '—' : Math.round(this.now[k]!)}</b><span class="muted small">${u}</span></div>`)}
        </div>
        ${m && used != null
          ? html`<div class="wm-insp-mem"><div class="small muted">Unified memory <span class="num">${used.toFixed(1)} of ${m.usable} GiB</span></div><div class="meter"><span class="meter-fill" style=${`width:${Math.min(100, (used / m.usable) * 100)}%`}></span></div></div>`
          : nothing}
        <dl class="kv">
          <dt>serves</dt><dd>${deps.length ? deps.map((d) => d.name).join(', ') : 'nothing'}</dd>
          <dt>reach</dt><dd>${n.transport === 'tunnel' ? 'SSH tunnel' : 'LAN'} · <span class="num">${n.address}</span></dd>
          <dt>hardware</dt><dd>${n.hw}</dd>
          <dt>version</dt><dd>${n.version}</dd>
          <dt>lease</dt><dd>${lease}</dd>
        </dl>
        ${n.status === 'alert' ? html`<p class="wm-note warn-ink small">⚠ ${n.status_text}</p>` : nothing}
        <div class="wm-insp-acts">
          <button type="button" class="btn btn-sm" @click=${() => this.openWindow('terminal', { node: n.id })}>${icon('terminal')} Terminal</button>
          <button type="button" class="btn btn-sm" @click=${() => this.openWindow('logs', { node: n.id })}>${icon('doc')} Logs</button>
          <button type="button" class="btn btn-ghost btn-sm" @click=${() => this.openWindow('chart', { metric: 'tps', scope: `node:${n.id}` })}>${icon('performance')} Chart</button>
        </div>
      </div>
    </div>`;
  }
}
