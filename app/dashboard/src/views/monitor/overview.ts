// <qse-overview>: the fleet's home (VIS-248, concept §8.1, the v3 prototype's Overview). Its health in
// one line, the totals of the scope with each Spark's share (decode, active requests, memory, power,
// the energy since midnight computed here from the power samples, requests today from the gateway's
// ledger), decode by Spark with the fleet's events marked, the latest events, the topology (a Spark,
// the host or a link opens its details in the right sidebar), a card per Spark, the deployments. The
// right sidebar: the alerts with Acknowledge, the latest events and the Sparks now.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { fleet, isSpark, memberHref, membersWord, METRICS, NO_FIGURES, type FleetEvent, type FleetLink, type FleetTopology, type MetricKey, type MetricSeries, type NodeMemory } from '../../api/fleet';
import type { Summary } from '../../api/types';
import type { ChartEvent } from '../../charts/fleet-chart';
import '../../charts/fleet-chart';
import type { NodeSeries } from '../../charts/modes';
import { openSelection, refreshContext, type ContextContent } from '../../lib/context';
import { activeAlerts } from '../../lib/fleet-state';
import { aggregateNow, energyKWh, lastValue, liveValues, midnight, shareParts } from '../../lib/monitor';
import type { View } from '../../lib/router';
import { scopeSource } from '../../lib/scope';
import type { Loadable } from '../../ui/base';
import { nodeChip } from '../../ui/node-chip';
import { MonitorPage } from './base';
import { alertCard, clock, eventRow, fmt, inspector, memBar, members, serves, signInCard, span, sparkline, tile, typeBadge } from './bits';
import { acknowledge, chartEvents, checkSession, fromFleet, needsSignIn, onSession } from './data';
import './topology';
import type { TopoNow, TopoPick } from './topology';

const NOW_KEYS: MetricKey[] = ['tps', 'req', 'power', 'temp', 'mem', 'gpu', 'lan', 'fabric'];

@customElement('qse-overview')
export class QseOverview extends MonitorPage {
  protected readonly view: View = 'overview';
  @property({ attribute: false }) summary: Loadable<Summary> | null = null;
  @state() private topo: FleetTopology | null = null;
  @state() private now: Partial<Record<MetricKey, Record<string, number | null>>> = {};
  @state() private spark: Record<string, (number | null)[]> = {};
  @state() private chart: MetricSeries | null = null;
  @state() private marks: ChartEvent[] = [];
  @state() private memory: NodeMemory[] = [];
  @state() private depRate: Record<string, number | null> = {};
  @state() private depScoped: { tps: number | null; req: number | null } | null = null;
  @state() private energy: { kwh: Record<string, number | null>; covered: number } | null = null;
  @state() private events: FleetEvent[] = [];
  @state() private alertEvents: FleetEvent[] = [];
  @state() private absent = false;
  @state() private acking = '';
  @state() private ackError = '';
  private offSession: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    void checkSession();
    this.offSession = onSession(() => (this.requestUpdate(), refreshContext()));
  }
  disconnectedCallback(): void {
    this.offSession?.();
    super.disconnectedCallback();
  }
  protected scopeChanged(): void {
    this.chart = null;
    this.depScoped = null;
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    const s = this.resolved;
    const series = (metric: MetricKey, range = s.range, source = 'all') => fleet.metrics({ source, metric, range });
    const topo = await fromFleet(() => fleet.topology()).catch(() => null);
    if (topo === 'absent') {
      if (isCurrent()) this.absent = true;
      return;
    }
    const nowAns = await Promise.all(NOW_KEYS.map((k) => series(k, '5m').catch(() => null)));
    const [chart, power, mem, evs, acks, deps] = await Promise.all([
      series('tps', s.range, scopeSource(s)).catch(() => null),
      series('power', '24h').catch(() => null),
      fleet.memory().catch(() => null),
      fleet.events({ limit: 6 }).catch(() => null),
      fleet.events({ type: ['alert'], limit: 30 }).catch(() => null),
      Promise.all((this.fleetState?.deployments ?? []).map((d) => series('tps', '5m', `deployment:${d.id}`).then((m) => [d.id, m.series.reduce((a, x) => a + (lastValue(x.values) ?? 0), 0)] as const).catch(() => [d.id, null] as const))),
    ]);
    let scoped: { tps: number | null; req: number | null } | null = null;
    if (s.kind === 'deployment' && s.id) {
      const [t, r] = await Promise.all([series('tps', '5m', `deployment:${s.id}`).catch(() => null), series('req', '5m', `deployment:${s.id}`).catch(() => null)]);
      const sum = (m: MetricSeries | null) => (m ? aggregateNow(m.series.map((x) => ({ v: lastValue(x.values) })), 'sum') : null);
      scoped = { tps: sum(t), req: sum(r) };
    }
    const marks = chart ? await chartEvents(chart.t0).catch(() => []) : [];
    if (!isCurrent()) return;
    this.absent = false;
    if (topo) this.topo = topo;
    const now: Partial<Record<MetricKey, Record<string, number | null>>> = {};
    NOW_KEYS.forEach((k, i) => {
      const m = nowAns[i];
      if (m) now[k] = Object.fromEntries(m.series.map((x) => [x.node, lastValue(x.values)]));
      if (k === 'tps' && m) this.spark = Object.fromEntries(m.series.map((x) => [x.node, x.values.slice(-90)]));
    });
    this.now = now;
    if (chart) this.chart = chart;
    this.marks = marks;
    if (mem) this.memory = mem.nodes;
    if (evs) this.events = evs.events;
    if (acks) this.alertEvents = acks.events;
    this.depRate = Object.fromEntries(deps);
    this.depScoped = scoped;
    if (power) {
      const from = midnight(Date.now() / 1000);
      const to = power.t0 + power.step_s * (Math.max(0, ...power.series.map((x) => x.values.length)) - 1);
      const per = power.series.map((x) => [x.node, energyKWh(x.values, power.t0, power.step_s, from, Math.max(from, to))] as const);
      this.energy = { kwh: Object.fromEntries(per.map(([n, e]) => [n, e.kwh])), covered: per.length ? Math.max(...per.map(([, e]) => e.covered)) : 0 };
    }
    refreshContext();
  }

  // ---- the sidebar ------------------------------------------------------------------------------
  private async ack(id: string): Promise<void> {
    this.acking = id;
    this.ackError = '';
    const r = await acknowledge({ id });
    this.acking = '';
    if (r !== 'ok' && r !== 'sign-in') this.ackError = r;
    refreshContext();
  }

  protected context(): ContextContent {
    const f = this.fleetState;
    const sec = (t: string, body: TemplateResult, tools?: TemplateResult) => html`<section class="csec"><div class="csec-h"><span class="eyebrow">${t}</span>${tools ?? nothing}</div>${body}</section>`;
    if (!f) return { title: 'Fleet now', body: html`<p class="muted small">The fleet shows here once TandemApp’s controller answers on this server.</p>` };
    const alerts = activeAlerts(f).concat(f.alerts.filter((a) => a.acked));
    const locked = needsSignIn();
    return {
      title: 'Fleet now',
      body: html`${sec(
        'Alerts',
        html`${locked && alerts.some((a) => !a.acked) ? signInCard('Acknowledging alerts') : nothing}${this.ackError ? html`<p class="small err-ink">${this.ackError}</p>` : nothing}
          ${alerts.length ? html`<div class="ctx-alerts">${alerts.map((a) => alertCard(a, { nodes: f.nodes, events: this.alertEvents, hostName: this.topo?.host.name, locked, busy: this.acking === a.id, onAck: (x) => void this.ack(x.id), compact: true }))}</div>` : html`<p class="small ok-ink">✓ No alerts fire now.</p>`}`,
        html`<a class="small" href="#/events">All</a>`,
      )}
        ${sec('Latest events', this.events.length ? html`<div class="ev-list is-compact">${this.events.slice(0, 5).map((e) => eventRow(e, f.nodes, f.deployments, this.topo?.host.name))}</div>` : html`<p class="muted small">No events yet.</p>`)}
        ${sec(
          'Sparks',
          html`<div class="ctx-nodes">${f.nodes.map(
            (n) => html`<a class="mini-now" href=${memberHref(n)} data-node=${n.id}><span>${nodeChip(n)}<span class="mini-dep">${f.deployments.filter((d) => d.members.includes(n.id)).map((d) => d.name).join(' · ')}</span></span><span class="num small">${fmt(this.now.tps?.[n.id] ?? f.tps[n.id])} <span class="muted">tok/s</span></span></a>`,
          )}</div>`,
        )}`,
    };
  }

  // ---- picks on the topology ----------------------------------------------------------------------
  private onPick(p: TopoPick): void {
    const f = this.fleetState;
    const t = this.topo;
    if (!f || !t) return;
    if (p.kind === 'node') {
      const n = f.nodes.find((x) => x.id === p.id);
      if (!n) return;
      const now = Object.fromEntries(NOW_KEYS.map((k) => [k, this.now[k]?.[n.id] ?? null]));
      openSelection(n.alias, inspector({ node: n, deps: f.deployments, now, memory: this.memory.find((m) => m.node === n.id) ?? null }));
    } else if (p.kind === 'host') {
      openSelection(t.host.name, html`<dl class="kv">
          <div><dt>runs</dt><dd>TandemApp’s controller and the API</dd></div>
          <div><dt>address</dt><dd class="num">${t.host.address}</dd></div>
          <div><dt>API</dt><dd class="num">${t.host.api_url}/v1</dd></div>
          <div><dt>sleep</dt><dd class=${t.host.may_sleep ? 'warn-ink' : ''}>${t.host.may_sleep ? 'this machine may sleep: the API and the metrics stop while it does' : 'does not sleep'}</dd></div>
        </dl>
        <a class="btn btn-sm" href="#/hardware?focus=host">Host metrics</a>`);
    } else if (p.kind === 'link') {
      const l = t.links.find((x) => x.id === p.id);
      if (l) openSelection('Link', linkDetails(l, t, f.nodes));
    }
  }

  // ---- the page -----------------------------------------------------------------------------------
  private health(): TemplateResult {
    const f = this.fleetState!;
    const nodes = this.inScope;
    const up = f.nodes.filter((n) => n.status !== 'off').length;
    const off = f.nodes.filter((n) => n.status === 'off');
    const hot = f.nodes.filter((n) => n.status === 'alert');
    const serving = f.deployments.filter((d) => d.status === 'serving').length;
    const weak = f.deployments.filter((d) => d.status === 'degraded' || d.status === 'down');
    const alerts = activeAlerts(f);
    // OPS-234: the nodes run more than one release (the controller's version-mismatch rule)
    const mixed = f.alerts.find((a) => a.id === 'version-mismatch:all') ?? null;
    const lease = f.nodes.map((n) => n.lease_end).filter((v): v is number => v != null);
    const sc = this.resolved;
    const scopeName = sc.kind === 'node' ? (f.nodes.find((n) => n.id === sc.id)?.alias ?? sc.id) : sc.kind === 'deployment' ? (f.deployments.find((d) => d.id === sc.id)?.name ?? sc.id) : `All ${membersWord(f.nodes, 2)}`;
    return html`<div class="panel health-strip fleet-health" data-fleet-health>
      <span class="ok-ink"><span aria-hidden="true">●</span> ${up} ${membersWord(f.nodes, up)} up</span>
      ${off.map((n) => html`<span class="err-ink"><span aria-hidden="true">○</span> ${n.alias} ${n.status_text}</span>`)}
      ${hot.map((n) => html`<span class="warn-ink small"><span aria-hidden="true">⚠</span> ${n.alias}: ${n.status_text}</span>`)}
      <span class="faint" aria-hidden="true">·</span>
      <span>${serving} deployment${serving === 1 ? '' : 's'} serving${weak.length ? html`, <span class="warn-ink">${weak.map((d) => `${d.name} ${d.status}`).join(', ')}</span>` : nothing}</span>
      <span class="faint" aria-hidden="true">·</span>
      ${mixed ? html`<a class="warn-ink" href=${mixed.action?.route ?? '#/sparks?update=1'} data-mixed-versions><span aria-hidden="true">⚠</span> ${mixed.title} · update the fleet</a><span class="faint" aria-hidden="true">·</span>` : nothing}
      ${alerts.length ? html`<a class="warn-ink" href="#/events"><span aria-hidden="true">⚠</span> ${alerts.length} alert${alerts.length === 1 ? '' : 's'}: ${(alerts.find((a) => a !== mixed) ?? alerts[0]).title}</a>` : html`<span class="ok-ink">no alerts</span>`}
      ${lease.length ? html`<span class="faint" aria-hidden="true">·</span><span class="muted">leases end in ${span(Math.min(...lease) - Date.now() / 1000)}</span>` : nothing}
      <span class="grow"></span>
      <span class="muted small">scope: ${scopeName} · ${nodes.length} ${membersWord(nodes)}</span>
    </div>`;
  }

  /** a Spark's value now; none for one that is off (its last sample is history, not now) */
  private nowOf(k: MetricKey): Record<string, number | null> {
    return liveValues(this.now[k] ?? {}, this.fleetState?.nodes ?? []);
  }

  private tiles(): TemplateResult {
    const nodes = this.inScope;
    const parts = (k: MetricKey) => shareParts(nodes, this.nowOf(k));
    const total = (k: MetricKey) => aggregateNow(parts(k), METRICS[k].agg);
    const tps = this.depScoped?.tps ?? total('tps');
    const req = this.depScoped?.req ?? total('req');
    const mem = parts('mem');
    const cap = this.memory.filter((m) => nodes.some((n) => n.id === m.node)).reduce((a, m) => a + m.usable, 0) || null;
    const used = total('mem');
    const tight = [...mem].filter((p) => p.v != null).sort((a, b) => (b.v ?? 0) - (a.v ?? 0))[0];
    const pw = total('power');
    const kwhParts = shareParts(nodes, this.energy?.kwh ?? {});
    const kwh = aggregateNow(kwhParts, 'sum');
    const today = this.summary?.state === 'ready' ? this.summary.data.windows.today : null;
    const reqParts = parts('req');
    const decoding = reqParts.reduce((a, p) => a + (p.v ?? 0), 0);
    return html`<div class="mtiles" data-tiles>
      ${tile({ id: 'tps', label: 'Decode', agg: 'sum', value: fmt(tps), unit: 'tok/s', parts: this.depScoped ? [] : parts('tps'), sub: decoding ? `${fmt(decoding)} decoding · ${fmt((tps ?? 0) / decoding)} tok/s each` : 'nothing decoding now' })}
      ${tile({ id: 'req', label: 'Active requests', agg: 'sum', value: fmt(req), parts: this.depScoped ? [] : reqParts, sub: 'in flight on the engines now' })}
      ${tile({ id: 'mem', label: 'Memory used', agg: 'sum', value: fmt(used, 1), unit: cap ? `/ ${fmt(cap, 1)} GiB` : 'GiB', parts: mem, cap, fmt: (v) => fmt(v, 1), sub: cap && used != null ? `free ${fmt(cap - used, 1)} GiB${nodes.length > 1 && tight ? ` · tightest ${tight.alias}` : ''}` : 'unified memory' })}
      ${tile({ id: 'power', label: 'Power', agg: 'sum', value: fmt(pw), unit: 'W', parts: parts('power'), sub: pw != null ? `≈ ${fmt((pw * 24) / 1000, 1)} kWh a day at this draw` : 'no power reading' })}
      ${tile({
        id: 'kwh',
        label: 'Energy today',
        agg: 'sum',
        value: fmt(kwh, 2),
        unit: 'kWh',
        parts: kwhParts,
        fmt: (v) => fmt(v, 2),
        sub: html`<span title="TandemApp adds up the power samples since local midnight (24 h tier, 10 min steps); the controller keeps no energy counter of its own">since 00:00 · computed from power samples${this.energy && this.energy.covered < 0.95 ? ` · ${Math.round(this.energy.covered * 100)} % of today sampled` : ''}</span>`,
      })}
      ${tile({ id: 'reqday', label: 'Requests today', agg: null, value: today ? fmt(today.requests) : '—', sub: today ? `the gateway’s ledger · ${fmt(today.completion_tokens)} tokens out` : 'the gateway’s ledger answers with the page' })}
    </div>`;
  }

  private decodeChart(): TemplateResult {
    const m = this.chart;
    const f = this.fleetState!;
    const series: NodeSeries[] = (m?.series ?? []).map((s) => {
      const n = f.nodes.find((x) => x.id === s.node);
      return { id: s.node, alias: n?.alias ?? s.node, colour: n?.colour ?? 1, values: s.values };
    });
    return html`<section class="panel" data-panel="decode">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Decode by Spark</h2><p class="panel-sub">${m ? `last ${m.range} · tok/s · ◆ events` : 'tok/s'}</p></div></header>
      ${m ? html`<qse-fleet-chart chart-id="overview-tps" .series=${series} agg=${m.agg} unit=${m.unit} .t0=${m.t0} .step=${m.step_s} .decimals=${0} .events=${this.marks} mode="stacked" .height=${230} aria-label=${`Decode tok/s by Spark over ${m.range}`}></qse-fleet-chart>` : html`<div class="skeleton" style="height:230px"></div>`}
    </section>`;
  }

  private latest(): TemplateResult {
    const f = this.fleetState!;
    const n = activeAlerts(f).length;
    return html`<section class="panel" data-panel="events">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Latest events</h2><p class="panel-sub">${n} active alert${n === 1 ? '' : 's'}</p></div><div class="panel-tools"><a class="btn btn-ghost btn-sm" href="#/events">All events</a></div></header>
      ${this.events.length ? html`<div class="ev-list">${this.events.slice(0, 5).map((e) => eventRow(e, f.nodes, f.deployments, this.topo?.host.name))}</div>` : html`<p class="muted small">No events yet. Pairing, deployments, alerts and restarts show here.</p>`}
    </section>`;
  }

  private topology(): TemplateResult {
    const t = this.topo;
    if (!t) return html`<section class="panel"><div class="skeleton" style="height:240px"></div></section>`;
    const lan = t.nodes.filter((n) => n.transport === 'lan').length;
    const tun = t.nodes.length - lan;
    const fab = t.links.filter((l) => l.kind === 'fabric').length;
    const onNode = !t.infra.some((i) => i.kind === 'host');
    const sub = [
      onNode ? `the controller runs on ${t.host.name}` : `${t.host.name} runs the controller`,
      lan ? `${lan} Spark${lan === 1 ? '' : 's'} on the LAN` : '',
      tun ? `${tun} rented through an SSH tunnel` : '',
      fab ? `${fab} fabric link${fab === 1 ? '' : 's'}` : t.nodes.length > 1 ? 'no fabric link' : 'no fabric: this box has no ConnectX cable',
    ]
      .filter(Boolean)
      .join(' · ');
    const now: TopoNow = { tps: this.nowOf('tps'), temp: this.nowOf('temp'), power: this.nowOf('power'), lan: this.nowOf('lan'), fabric: this.nowOf('fabric') };
    return html`<section class="panel" data-panel="topology">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Topology</h2><p class="panel-sub">${sub[0].toUpperCase()}${sub.slice(1)}. Click a Spark or a link.</p></div><div class="panel-tools"><a class="btn btn-sm" href="#/sparks/topology">Open topology</a></div></header>
      <qse-topology compact .topo=${t} .deployments=${this.fleetState?.deployments ?? []} .now=${now} @qse-topo-pick=${(e: CustomEvent<TopoPick>) => this.onPick(e.detail)}></qse-topology>
    </section>`;
  }

  private cards(): TemplateResult {
    const f = this.fleetState!;
    const nodes = this.inScope;
    return html`<section class="mon-sec" data-panel="cards">
      <div class="mon-sec-h"><span class="eyebrow">${membersWord(nodes, 2)} in scope</span><a class="small" href="#/sparks">Sparks table</a></div>
      <div class="ncards ${nodes.length === 1 ? 'is-one' : ''}">
        ${nodes.map((n) => {
          const v = (k: MetricKey) => this.nowOf(k)[n.id] ?? null;
          const alert = n.status === 'alert';
          const be = !isSpark(n);
          return html`<a class="ncard ${alert ? 'is-alert' : ''} ${n.status === 'off' ? 'is-off' : ''}" href=${memberHref(n)} style=${`--nc:var(--n${((n.colour - 1) % 4) + 1})`} data-node=${n.id}>
            <div class="ncard-h">${nodeChip(n, { lg: true })}<span class="small ${alert ? 'warn-ink' : n.status === 'off' ? 'err-ink' : 'muted'}">${n.status === 'up' ? 'up' : n.status_text}</span></div>
            <div class="ncard-serves small">${serves(n, f.deployments)}</div>
            ${be ? html`<p class="xs muted" data-no-figures>${NO_FIGURES}</p>` : memBar(this.memory.find((m) => m.node === n.id), true)}
            <div class="ncard-nums"><span>tok/s<b class="num" data-num="tps">${fmt(v('tps'))}</b></span><span>GPU °C<b class="num ${(v('temp') ?? 0) >= 85 ? 'warn-ink' : ''}" data-num="temp">${fmt(v('temp'))}</b></span><span>W<b class="num" data-num="power">${fmt(v('power'))}</b></span></div>
            ${sparkline(this.spark[n.id] ?? [], n.colour, 30, `${n.alias} decode, last 3 minutes`)}
            <div class="ncard-foot xs muted"><span>${be ? `${n.hw} · ${n.address}` : n.owned ? `owned · ${n.address}` : `rented${n.lease_end ? ` · lease ${span(n.lease_end - Date.now() / 1000)}` : ''}`}</span><span class="num">${be ? '' : `v${n.version || '—'}`}</span></div>
            ${alert ? html`<div class="alert-line small"><span aria-hidden="true">⚠</span><span>${n.status_text}</span></div>` : nothing}
          </a>`;
        })}
      </div>
    </section>`;
  }

  private deployments(): TemplateResult {
    const f = this.fleetState!;
    const ids = new Set(this.inScope.map((n) => n.id));
    const deps = f.deployments.filter((d) => d.members.some((m) => ids.has(m)));
    const glyph = { serving: '●', starting: '◐', degraded: '◐', down: '○', stopped: '○' } as const;
    return html`<section class="panel" data-panel="deployments">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Deployments</h2><p class="panel-sub">One API; each deployment is routed to its Sparks</p></div><div class="panel-tools"><a class="btn btn-ghost btn-sm" href="#/models">Models</a></div></header>
      ${deps.length
        ? deps.map(
            (d) => html`<div class="dep-row" data-dep=${d.id}>
              <div class="dep-id"><b>${d.name}</b><div class="xs muted num">${[d.pack, d.api_id, d.role_of ? `role for ${f.deployments.find((x) => x.id === d.role_of)?.name ?? d.role_of}` : ''].filter(Boolean).join(' · ')}</div></div>
              ${typeBadge(d.type)}
              ${members(d, f.nodes)}
              <span class="small ${d.status === 'serving' ? 'ok-ink' : d.status === 'starting' ? 'muted' : 'warn-ink'}" title=${d.status_text}><span aria-hidden="true">${glyph[d.status]}</span> ${d.status === 'serving' ? `${fmt(this.depRate[d.id])} tok/s` : d.status_text}</span>
              <a class="btn btn-ghost btn-sm" href="#/models">Open</a>
            </div>`,
          )
        : html`<p class="muted small">Nothing deployed in this scope.</p>`}
    </section>`;
  }

  render() {
    const f = this.fleetState;
    const head = html`<header class="page-head" data-page="overview"><div class="eyebrow">Monitor</div><h1>Overview</h1></header>`;
    if (!f) {
      return html`${head}${this.absent ? html`<p class="page-soon">The fleet’s figures come from TandemApp’s controller, which this server does not run yet.</p>` : html`<div class="skeleton" style="height:200px"></div>`}`;
    }
    return html`${head}${this.health()}${this.tiles()}
      <div class="g-wide">${this.decodeChart()}${this.latest()}</div>
      ${this.topology()}${this.cards()}${this.deployments()}
      <p class="xs faint mon-foot">read ${clock(Date.now() / 1000)} · every 10 s while live</p>`;
  }
}

/** A link's details in the sidebar: what it is, its state, speed, latency and MTU; a fabric link's
 *  port, RoCE, logical interfaces and last test. */
export function linkDetails(l: FleetLink, t: FleetTopology, nodes: { id: string; alias: string; colour: number }[]): TemplateResult {
  const end = (id: string) => {
    const n = nodes.find((x) => x.id === id);
    if (n) return nodeChip(n);
    if (id === 'host') return html`<span class="small">${t.host.name}</span>`;
    return html`<span class="small">${t.infra.find((i) => i.id === id)?.label ?? id}</span>`;
  };
  return html`<div class="row small">${end(l.a)}<span class="faint" aria-hidden="true">⟷</span>${end(l.b)}</div>
    <dl class="kv">
      <div><dt>kind</dt><dd>${l.kind === 'fabric' ? 'fabric (ConnectX)' : l.kind === 'wan' ? 'internet / SSH tunnel' : 'LAN'}</dd></div>
      <div><dt>speed</dt><dd>${l.speed}</dd></div>
      <div><dt>state</dt><dd class=${l.state === 'up' ? 'ok-ink' : l.state === 'degraded' ? 'warn-ink' : 'err-ink'}>${l.state === 'up' ? '●' : l.state === 'degraded' ? '◐' : '○'} ${l.state}</dd></div>
      <div><dt>RTT</dt><dd class="num">${l.rtt_ms == null ? 'not timed (the controller runs here)' : `${l.rtt_ms} ms`}</dd></div>
      <div><dt>MTU</dt><dd class="num">${l.mtu ?? '—'}</dd></div>
      ${l.port ? html`<div><dt>port</dt><dd class="num">${l.port}</dd></div>` : nothing}
      ${l.roce ? html`<div><dt>RoCE</dt><dd>${l.roce}</dd></div>` : nothing}
      ${l.interfaces ? html`<div><dt>interfaces</dt><dd>${l.interfaces.up} of ${l.interfaces.total} up${l.interfaces.up < l.interfaces.total ? ' · half bandwidth' : ''}</dd></div>` : nothing}
      ${l.test ? html`<div><dt>last test</dt><dd class="num">${l.test.gbps} Gb/s · ${clock(l.test.at)}</dd></div>` : nothing}
      ${l.note ? html`<div><dt>note</dt><dd>${l.note}</dd></div>` : nothing}
    </dl>`;
}
