// <qse-fleet-page>: TandemApp's new pages (VIS-242, concept §8), one chunk loaded when one of them
// opens. Overview, Hardware and Events & Alerts (VIS-248, views/monitor/) and Sparks with its
// topology and each Spark's page (VIS-250, views/fleet/) are built, and Add Spark (VIS-246,
// views/fleet/wizard.ts, a chunk of its own that loads when it opens) and App (OPS-234, the controller's
// host and its move: views/fleet/app-settings.ts, a chunk of its own too); Models and API still show
// their head, what they will hold and the step of the program that builds them.

import { html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';
import type { Summary } from '../api/types';
import type { View } from '../lib/router';
import { LightElement, type Loadable } from '../ui/base';
import { icon } from '../ui/bits';
import '../styles/monitor.css';
import '../styles/sparks.css';
import './monitor/overview';
import './monitor/hardware';
import './monitor/events';
import './fleet/sparks';
import './fleet/node';

interface PageDef {
  eyebrow: string;
  title: string;
  text: string;
  step: number;
  /** today's page that already does part of it */
  link?: { href: string; label: string };
}

const PAGES: Partial<Record<View, PageDef>> = {
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
};

/** a sub-page of Models: its own title and text */
function subPage(view: View, path: string[]): PageDef | null {
  const p = path[0];
  if (view === 'models' && p === 'weights') return { eyebrow: 'Models', title: 'Weights', text: 'Every model pack on every Spark: present, its size, copying; copy to a Spark, delete with a confirm.', step: 6 };
  return null;
}

/** The Add Spark wizard: its own chunk; the element upgrades in place once it has loaded. */
let wizardChunk: Promise<unknown> | null = null;
const loadWizard = () => (wizardChunk ??= import('./fleet/wizard'));
let appChunk: Promise<unknown> | null = null;
const loadApp = () => (appChunk ??= import('./fleet/app-settings'));

@customElement('qse-fleet-page')
export class QseFleetPage extends LightElement {
  @property({ type: String }) view: View = 'overview';
  @property({ attribute: false }) path: string[] = [];
  /** the shell's summary: Overview's requests today */
  @property({ attribute: false }) summary: Loadable<Summary> | null = null;
  render() {
    if (this.view === 'overview') return html`<qse-overview .summary=${this.summary}></qse-overview>`;
    if (this.view === 'hardware') return html`<qse-hardware></qse-hardware>`;
    if (this.view === 'events') return html`<qse-events></qse-events>`;
    if (this.view === 'add-spark') {
      void loadWizard();
      return html`<qse-add-spark></qse-add-spark>`;
    }
    if (this.view === 'app') {
      void loadApp();
      return html`<qse-app-settings></qse-app-settings>`;
    }
    if (this.view === 'sparks') {
      const p = this.path[0];
      return p && p !== 'topology' ? html`<qse-spark-node .nodeId=${p}></qse-spark-node>` : html`<qse-sparks .tab=${p === 'topology' ? 'topology' : 'list'}></qse-sparks>`;
    }
    const d = subPage(this.view, this.path) ?? PAGES[this.view];
    if (!d) return nothing;
    return html`<header class="page-head" data-page=${this.view}>
        <div class="eyebrow">${d.eyebrow}</div>
        <h1>${d.title}</h1>
        <p class="page-sub">${d.text}</p>
      </header>
      <p class="page-soon">${icon('info')} <span>This page is built in step ${d.step} of TandemApp.${d.link ? html` Today: <a href=${d.link.href}>${d.link.label}</a>.` : nothing}</span></p>`;
  }
}
