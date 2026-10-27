// <qse-fleet-page>: TandemApp's new pages before they are built (VIS-242): Overview, Hardware, Events &
// Alerts, Sparks, Models, API, Add Spark and App, each with its head and what it will hold, and the
// step of the program that builds it (concept §8). Overview and Hardware already draw the fleet's
// metrics in the global scope with <qse-fleet-chart>. Their own chunk, loaded when one of them
// opens, with the fleet chart and its modes.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { describeError } from '../api/client';
import { fleet, METRICS, type MetricKey, type MetricSeries } from '../api/fleet';
import '../charts/fleet-chart';
import type { NodeSeries } from '../charts/modes';
import { onFleet, activeAlerts, type FleetState } from '../lib/fleet-state';
import { poll, type Poller } from '../lib/poll';
import type { View } from '../lib/router';
import { currentScope, onScope, resolveScope, scopeSource, type Scope } from '../lib/scope';
import { LightElement, type Loadable } from '../ui/base';
import { icon, panel, whenReady } from '../ui/bits';

interface PageDef {
  eyebrow: string;
  title: string;
  text: string;
  step: number;
  /** the fleet charts the page draws now */
  charts?: MetricKey[];
  /** today's page that already does part of it */
  link?: { href: string; label: string };
}

const PAGES: Partial<Record<View, PageDef>> = {
  overview: {
    eyebrow: 'Monitor',
    title: 'Overview',
    text: 'The fleet’s home: its health in one line, totals with each Spark’s share, the topology, a card per Spark, the deployments and the latest events.',
    step: 4,
    charts: ['tps'],
  },
  hardware: {
    eyebrow: 'Monitor',
    title: 'Hardware',
    text: 'Every metric of every Spark: aggregated for the fleet, one Spark’s full sheet, and the host that runs TandemApp.',
    step: 4,
    charts: ['temp', 'power'],
    link: { href: '#/hardware/system', label: 'This box’s System page' },
  },
  events: { eyebrow: 'Monitor', title: 'Events & Alerts', text: 'The fleet’s timeline, the alerts that fire now with their acknowledge, and the alert rules.', step: 4 },
  sparks: { eyebrow: 'Fleet', title: 'Sparks', text: 'Every Spark in a table: alias, status, what it serves, memory, temperature, power, version, link and lease.', step: 4 },
  models: {
    eyebrow: 'Fleet',
    title: 'Deployments',
    text: 'Deployments as the unit: Single, Replicas, Split or Role, their members, status and rate, with stop, move and scale.',
    step: 6,
    link: { href: '#/models/serve', label: 'Serve a model' },
  },
  api: {
    eyebrow: 'Fleet',
    title: 'Endpoint and keys',
    text: 'The one endpoint and its keys, the clients that use it, and the routes to deployments.',
    step: 6,
    link: { href: '#/api/backends', label: 'Backends' },
  },
  'add-spark': { eyebrow: 'Settings', title: 'Add Spark', text: 'Find, Connect, Check, Install, Link, Name, Done: a resumable wizard that asks for every permission where it needs it.', step: 5 },
  app: { eyebrow: 'Settings', title: 'App', text: 'Where the controller runs, the permissions, the admin password, updates, and the fleet’s config to export and import.', step: 5 },
};

/** a sub-page of Sparks or Models: its own title and text */
function subPage(view: View, path: string[], f: FleetState | null): PageDef | null {
  const p = path[0];
  if (view === 'sparks' && p === 'topology')
    return { eyebrow: 'Fleet', title: 'Topology', text: 'The controller, the LAN, tunnels and fabric links with their speed, latency and state; deployments drawn over their members.', step: 4 };
  if (view === 'sparks' && p) {
    const n = f?.nodes.find((x) => x.id === p);
    return { eyebrow: 'Sparks', title: n?.alias ?? p, text: 'The Spark’s inventory, what it serves, quick charts and its actions: drain, restart, reboot, update, link test, logs, release.', step: 4 };
  }
  if (view === 'models' && p === 'weights') return { eyebrow: 'Models', title: 'Weights', text: 'Every model pack on every Spark: present, its size, copying; copy to a Spark, delete with a confirm.', step: 6 };
  return null;
}

@customElement('qse-fleet-page')
export class QseFleetPage extends LightElement {
  @property({ type: String }) view: View = 'overview';
  @property({ attribute: false }) path: string[] = [];
  @state() private fleetState: FleetState | null = null;
  @state() private scope: Scope = currentScope();
  @state() private charts: Partial<Record<MetricKey, Loadable<MetricSeries>>> = {};
  private poller: Poller | null = null;
  /** the page the poller runs for (the element stays when the shell moves between these pages) */
  private polling: View | null = null;
  private unsub: (() => void)[] = [];

  connectedCallback(): void {
    super.connectedCallback();
    this.unsub.push(
      onFleet((f) => {
        const first = !this.fleetState && f;
        this.fleetState = f;
        if (first) void this.poller?.refresh();
      }),
      onScope((s) => {
        this.scope = s;
        this.charts = {};
        this.restart();
      }),
    );
    this.requestUpdate();
  }
  disconnectedCallback(): void {
    this.unsub.forEach((u) => u());
    this.unsub = [];
    this.poller?.stop();
    this.polling = null;
    super.disconnectedCallback();
  }
  updated(): void {
    if (this.view === this.polling) return;
    this.charts = {};
    this.restart();
  }

  private get def(): PageDef | undefined {
    return subPage(this.view, this.path, this.fleetState) ?? PAGES[this.view];
  }

  /** the charts follow the scope: every 10 s while live, once when paused */
  private restart(): void {
    this.poller?.stop();
    this.poller = null;
    this.polling = this.view;
    if (!this.def?.charts) return;
    this.poller = poll((isCurrent) => this.load(isCurrent), 10_000);
    if (this.scope.live) this.poller.start();
    else void this.poller.refresh();
  }

  private async load(isCurrent: () => boolean): Promise<void> {
    const f = this.fleetState;
    const s = f ? resolveScope(this.scope, f) : this.scope;
    for (const metric of this.def?.charts ?? []) {
      try {
        const m = await fleet.metrics({ source: scopeSource(s), metric, range: s.range });
        if (isCurrent()) this.charts = { ...this.charts, [metric]: { state: 'ready', data: m } };
      } catch (e) {
        const d = describeError(e);
        if (isCurrent()) this.charts = { ...this.charts, [metric]: d.status === 404 ? { state: 'empty', note: 'The fleet’s metrics come from TandemApp’s controller, which this server does not run yet.' } : { state: 'error', endpoint: d.endpoint, message: d.message } };
      }
    }
  }

  private chart(metric: MetricKey): TemplateResult {
    const info = METRICS[metric];
    const l = this.charts[metric] ?? { state: 'loading' };
    const nodes = this.fleetState?.nodes ?? [];
    return panel(
      info.label,
      whenReady(
        l,
        (m) => {
          const series: NodeSeries[] = m.series.map((s) => {
            const n = nodes.find((x) => x.id === s.node);
            return { id: s.node, alias: n?.alias ?? s.node, colour: n?.colour ?? 1, values: s.values };
          });
          return html`<qse-fleet-chart chart-id=${`${this.view}-${metric}`} .series=${series} agg=${m.agg} unit=${m.unit} .t0=${m.t0} .step=${m.step_s} .decimals=${info.decimals} .cap=${m.cap} .max=${info.max ?? null} mode=${m.agg === 'sum' ? 'stacked' : 'lines'} aria-label=${`${info.label} over ${m.range}`}></qse-fleet-chart>`;
        },
        { lines: 4, emptyTitle: 'No fleet yet' },
      ),
      { sub: info.unit, cls: 'fleet-panel', id: `fleet-${metric}` },
    );
  }

  private health(f: FleetState): TemplateResult {
    const serving = f.deployments.filter((d) => d.status === 'serving').length;
    const alerts = activeAlerts(f).length;
    return html`<p class="fleet-health num" data-fleet-health>
      <span>${f.nodes.length} Spark${f.nodes.length === 1 ? '' : 's'}</span><span aria-hidden="true">·</span><span>${serving} serving</span><span aria-hidden="true">·</span>
      <a class=${alerts ? 'warn-ink' : 'muted'} href="#/events">${alerts ? `${alerts} alert${alerts === 1 ? '' : 's'}` : 'no alerts'}</a>
    </p>`;
  }

  render() {
    const d = this.def;
    if (!d) return nothing;
    const f = this.fleetState;
    return html`<header class="page-head" data-page=${this.view}>
        <div class="eyebrow">${d.eyebrow}</div>
        <h1>${d.title}</h1>
        <p class="page-sub">${d.text}</p>
        ${this.view === 'overview' && f ? this.health(f) : nothing}
      </header>
      <p class="page-soon">${icon('info')} <span>This page is built in step ${d.step} of TandemApp.${d.link ? html` Today: <a href=${d.link.href}>${d.link.label}</a>.` : nothing}</span></p>
      ${d.charts ? html`<div class="fleet-charts">${d.charts.map((m) => this.chart(m))}</div>` : nothing}`;
  }
}
