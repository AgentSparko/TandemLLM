// Conductor — how TandemLLM decides, and every knob of it (VIS-42, SRV-129). A header with the
// active profile's three-way switch (raw, shadow, on; "on" asks first) and the orange lines that
// matter everywhere (a config file that failed to load; VIS-68: never the speed guard), then nine
// screens: Overview (tiles, the 24 h ribbons, the live decision feed, the monitor's last actions),
// Routing (task classes and their policy, the classifier stages, effort levels, the note wrapper,
// a test box), Monitor (the session monitor's journal and its rules: the tool checks, the
// reasoning arms, the empty-turn nudges, caps and texts), Profiles (tiles and the full editor),
// Packages (every package and skill the router may inject, their texts, tokens, classes and use,
// and the library's retrieval per profile), Deciders (the decision models the engine serves
// itself: load and unload through the planner, the model per question, a test box), Sessions
// (timelines and the export, router and monitor in one), Memory (the session transcripts on the
// box, VIS-48) and Compare (precision and promotion, the eval tables, the classifier report).
// Loaded on demand, like the Playground.
//
// VIS-254: on a fleet (two Sparks or more) the header's Deployment picks whose conductor this is
// (`?dep=`). The conductor runs where a deployment's requests are served: this page reads the one of
// the server it talks to, so the deployments on its Spark are the ones it shows (a Replicas member on
// another Spark runs its own copy of the profiles, said beside it); a deployment only other Sparks
// serve is listed with why it is not here. On a controller with no Spark of its own the conductor is
// its gateway's, for every deployment whose backend has its harness on.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { describeError } from '../api/client';
import { conductor, type ConfigInfo, type Meta, type ProfileMode, type ProfileTile, type RouterDoc } from '../api/conductor';
import { timeShort } from '../lib/format';
import { CONDUCTOR_SCREENS, type ConductorScreen } from '../lib/menu';
import { poll, type Poller } from '../lib/poll';
import { setParams } from '../lib/router';
import { LightElement } from '../ui/base';
import { icon } from '../ui/bits';
import type { FleetDeployment } from '../api/fleet';
import { onFleet, type FleetState } from '../lib/fleet-state';
import { deploymentOf, hereNode, isFleet } from '../lib/where';
import { placeLine } from '../ui/place';
import { confirmDialog, modeSeg } from './conductor/shared';
import './conductor/overview';
import './conductor/routing';
import './conductor/monitor';
import './conductor/profiles';
import './conductor/packages';
import './conductor/deciders';
import './conductor/sessions';
import './conductor/memory';
import './conductor/compare';

export type Screen = ConductorScreen;
/** the nine screens: the menu's list (lib/menu.ts), so the left panel's sub-pages are the same */
const SCREENS = CONDUCTOR_SCREENS;

@customElement('qse-conductor')
export class QseConductor extends LightElement {
  @property({ attribute: false }) params: URLSearchParams = new URLSearchParams();
  @state() private tiles: ProfileTile[] = [];
  @state() private defaultProfile = 'chat';
  @state() private meta: Meta | null = null;
  @state() private config: ConfigInfo | null = null;
  @state() private ids: string[] = [];
  @state() private error: string | null = null;
  @state() private switching = false;
  @state() private reloaded: string | null = null;
  @state() private switchError: string | null = null;
  @state() private fleet: FleetState | null = null;
  private poller: Poller | null = null;
  private offFleet: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    this.poller = poll(() => this.load(), 15_000);
    this.poller.start();
    this.offFleet = onFleet((f) => (this.fleet = f));
  }
  disconnectedCallback(): void {
    this.poller?.stop();
    this.offFleet?.();
    super.disconnectedCallback();
  }

  // ---- VIS-254: whose conductor ------------------------------------------------------------------
  /** a deployment whose requests this server's conductor sees: one on its Spark, or any when this
   *  server is a gateway with no Spark of its own */
  private mine(d: FleetDeployment): boolean {
    const here = hereNode(this.fleet);
    return !here || d.members.includes(here.id);
  }
  /** the picked deployment (`?dep=`), else the one this server's own model runs as */
  private get deployment(): FleetDeployment | null {
    const f = this.fleet;
    if (!isFleet(f)) return null;
    const picked = f.deployments.find((d) => d.id === this.params.get('dep'));
    return picked ?? deploymentOf(f, this.config?.server.composite ?? 'TandemLLM', true) ?? f.deployments.find((d) => this.mine(d)) ?? null;
  }

  private renderDeployment(): TemplateResult | typeof nothing {
    const f = this.fleet;
    if (!isFleet(f)) return nothing;
    const here = hereNode(f);
    const d = this.deployment;
    const others = d ? d.members.filter((m) => m !== here?.id).map((m) => f.nodes.find((n) => n.id === m)?.alias ?? m) : [];
    const whose = !here ? "TandemApp's gateway (this server): it acts on a deployment's requests when its backend's harness is on (API → Backends)" : `${here.alias}'s engine, the server this page talks to`;
    return html`<div class="cond-dep" data-cond-dep>
      <label class="field"><span>deployment</span>
        <select aria-label="whose conductor" @change=${(e: Event) => setParams({ dep: (e.target as HTMLSelectElement).value || null })}>
          ${f.deployments.map((x) => html`<option value=${x.id} ?selected=${x.id === d?.id} ?disabled=${!this.mine(x)} title=${this.mine(x) ? x.name : `${x.name}: its Sparks' own engines run its conductor`}>${x.name}${this.mine(x) ? '' : ' · on other Sparks'}</option>`)}
        </select></label>
      ${d ? placeLine(d, f.nodes) : nothing}
      <span class="muted small">the conductor of ${whose}${d?.type === 'replicas' && others.length && here ? `; ${others.join(', ')} run${others.length === 1 ? 's' : ''} their own copy of the profiles` : ''}</span>
    </div>`;
  }

  private get screen(): Screen {
    const s = this.params.get('screen') as Screen | null;
    return s && SCREENS.some((x) => x.id === s) ? s : 'overview';
  }
  private get active(): string {
    return this.params.get('profile') ?? this.defaultProfile;
  }

  private async load(): Promise<void> {
    try {
      const [p, meta, config, router] = await Promise.all([
        conductor.profiles(),
        this.meta ? Promise.resolve(this.meta) : conductor.meta(),
        conductor.config(),
        conductor.doc<RouterDoc>('router'),
      ]);
      this.tiles = p.profiles;
      this.defaultProfile = p.default_profile;
      this.meta = meta;
      this.config = config;
      this.ids = router.doc.classes.map((c) => c.id);
      this.error = null;
    } catch (e) {
      const d = describeError(e);
      this.error = d.status === 404 ? 'The conductor is off on this server (start it with --conductor-config).' : `${d.endpoint}: ${d.message}`;
    }
  }

  private async setMode(mode: ProfileMode): Promise<void> {
    const tile = this.tiles.find((t) => t.name === this.active);
    if (!tile || tile.locked) return;
    if (mode === 'on') {
      const ok = await confirmDialog(`Turn the ${tile.name} profile on?`, [
        'Every decision type set to on in this profile will change what the model sees on its next request: effort, hidden skills and packages, rules.',
        'Shadow decisions stay in shadow. Requests sent with X-Tandem: raw (benchmarks) are not affected.',
      ], 'Turn on');
      if (!ok) return;
    }
    this.switching = true;
    this.switchError = null;
    try {
      const r = await conductor.setMode(tile.name, mode, `mode to ${mode}`, tile.version);
      this.reloaded = `v${r.version} · reloaded ${r.loaded_at ? timeShort(r.loaded_at) : 'now'}`;
      await this.load();
    } catch (e) {
      this.switchError = describeError(e).message;
    } finally {
      this.switching = false;
    }
  }

  render() {
    const tile = this.tiles.find((t) => t.name === this.active);
    const loadErrors = this.config ? Object.entries(this.config.store.documents).filter(([, d]) => d.error) : [];
    return html`<div class="view view-conductor">
      <div class="cond-head">
        <div class="cond-head-row">
          <div class="cond-title">${icon('conductor')} Conductor <span class="muted small num" title=${`the one model; ${this.config?.server.raw_model ?? 'the raw engine name'} is a hidden alias, and X-Tandem: raw serves the raw path`}>TandemLLM, the one model · raw by X-Tandem: raw</span></div>
          <div class="mode-switch" data-mode-switch>
            <label class="field"><span>profile</span>
              <select aria-label="active profile" @change=${(e: Event) => setParams({ profile: (e.target as HTMLSelectElement).value })}>
                ${this.tiles.map((t) => html`<option value=${t.name} ?selected=${t.name === this.active}>${t.name}${t.locked ? ' (locked)' : ''}</option>`)}
              </select>
            </label>
            ${tile ? modeSeg(['raw', 'shadow', 'on'] as const, tile.mode, (m) => void this.setMode(m), { disabled: !!tile.locked || this.switching, label: `mode of ${tile.name}` }) : nothing}
            ${this.reloaded ? html`<span class="reload" role="status" data-reloaded>${this.reloaded}</span>` : nothing}
          </div>
        </div>
        ${this.renderDeployment()}
        ${this.switchError ? html`<div class="guard-line" role="alert">${icon('warn')} ${this.switchError}</div>` : nothing}
        ${this.error ? html`<div class="guard-line" role="alert">${icon('warn')} ${this.error}</div>` : nothing}
        ${loadErrors.map(([k, d]) => html`<div class="guard-line" role="alert">${icon('warn')} ${k}: ${d.error?.message} (kept version ${d.version})</div>`)}
        ${tile?.locked ? html`<div class="note" data-locked>${icon('warn')} ${tile.name} is locked: ${tile.locked}.</div>` : nothing}
        <nav class="cond-tabs" aria-label="conductor screens">
          ${SCREENS.map((s) => html`<a class="cond-tab ${s.id === this.screen ? 'is-on' : ''}" href=${`#/conductor?screen=${s.id}${this.params.get('profile') ? `&profile=${encodeURIComponent(this.active)}` : ''}`} aria-current=${s.id === this.screen ? 'page' : nothing}>${s.label}</a>`)}
        </nav>
      </div>
      ${this.renderScreen()}
    </div>`;
  }

  private renderScreen(): TemplateResult {
    const common = { ids: this.ids, meta: this.meta, tiles: this.tiles };
    switch (this.screen) {
      case 'overview':
        return html`<qse-cond-overview .ids=${common.ids} .tiles=${common.tiles} .active=${this.active}></qse-cond-overview>`;
      case 'routing':
        return html`<qse-cond-routing .ids=${common.ids} .meta=${common.meta} .active=${this.active} .onSaved=${() => this.load()}></qse-cond-routing>`;
      case 'monitor':
        return html`<qse-cond-monitor .meta=${common.meta} .onSaved=${() => this.load()}></qse-cond-monitor>`;
      case 'profiles':
        return html`<qse-cond-profiles .ids=${common.ids} .meta=${common.meta} .tiles=${common.tiles} .selected=${this.params.get('edit')} .onSaved=${() => this.load()}></qse-cond-profiles>`;
      case 'packages':
        return html`<qse-cond-packages .ids=${common.ids} .tiles=${common.tiles} .selected=${this.params.get('pkg')} .tab=${this.params.get('ptab')} .pane=${this.params.get('pview')}></qse-cond-packages>`;
      case 'deciders':
        return html`<qse-cond-deciders></qse-cond-deciders>`;
      case 'sessions':
        return html`<qse-cond-sessions .ids=${common.ids} .selected=${this.params.get('session')}></qse-cond-sessions>`;
      case 'memory':
        return html`<qse-cond-memory .ids=${common.ids} .selected=${this.params.get('memory')} .page=${Number(this.params.get('mpage')) || null} .tab=${this.params.get('mtab')} .note=${this.params.get('note')} .nnode=${this.params.get('nnode')}></qse-cond-memory>`;
      case 'compare':
        return html`<qse-cond-compare .ids=${common.ids} .tiles=${common.tiles} .onSaved=${() => this.load()}></qse-cond-compare>`;
    }
  }
}
