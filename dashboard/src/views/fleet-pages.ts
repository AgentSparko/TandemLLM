// <qse-fleet-page>: TandemApp's new pages (VIS-242, concept §8), one chunk loaded when one of them
// opens. Overview, Hardware and Events & Alerts are built (VIS-248, views/monitor/); Sparks, Models,
// API, Add Spark and App still show their head, what they will hold and the step of the program that
// builds them.

import { html, nothing } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import type { Summary } from '../api/types';
import { onFleet, type FleetState } from '../lib/fleet-state';
import type { View } from '../lib/router';
import { LightElement, type Loadable } from '../ui/base';
import { icon } from '../ui/bits';
import '../styles/monitor.css';
import './monitor/overview';
import './monitor/hardware';
import './monitor/events';

interface PageDef {
  eyebrow: string;
  title: string;
  text: string;
  step: number;
  /** today's page that already does part of it */
  link?: { href: string; label: string };
}

const PAGES: Partial<Record<View, PageDef>> = {
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
  /** the shell's summary: Overview's requests today */
  @property({ attribute: false }) summary: Loadable<Summary> | null = null;
  @state() private fleetState: FleetState | null = null;
  private off: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    this.off = onFleet((f) => (this.fleetState = f));
  }
  disconnectedCallback(): void {
    this.off?.();
    this.off = null;
    super.disconnectedCallback();
  }

  render() {
    if (this.view === 'overview') return html`<qse-overview .summary=${this.summary}></qse-overview>`;
    if (this.view === 'hardware') return html`<qse-hardware></qse-hardware>`;
    if (this.view === 'events') return html`<qse-events></qse-events>`;
    const d = subPage(this.view, this.path, this.fleetState) ?? PAGES[this.view];
    if (!d) return nothing;
    return html`<header class="page-head" data-page=${this.view}>
        <div class="eyebrow">${d.eyebrow}</div>
        <h1>${d.title}</h1>
        <p class="page-sub">${d.text}</p>
      </header>
      <p class="page-soon">${icon('info')} <span>This page is built in step ${d.step} of TandemApp.${d.link ? html` Today: <a href=${d.link.href}>${d.link.label}</a>.` : nothing}</span></p>`;
  }
}
