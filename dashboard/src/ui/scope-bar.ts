// <qse-scope-bar>: the global scope above every Monitor page (VIS-242, concept §4, D-4): one control,
// never a page per Spark. A dropdown (All Sparks with their swatches, each Spark as its chip, each
// deployment with its type), the time range, live or paused and a note; all of it in the URL
// (lib/scope.ts), so a reload and a shared link keep it. A fleet of one has nothing to pick: the
// dropdown steps aside and the bar is today's box. On a phone the bar is one chip that opens a sheet.
// Usage keeps its own period in days (the range has no say there).

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { RANGES, RANGE_KEYS, type FleetDeployment, type FleetNode, type Range } from '../api/fleet';
import { currentScope, onScope, resolveScope, scopeChoices, setScope, type Scope } from '../lib/scope';
import { LightElement } from './base';
import { nodeChip, nodeColour, isHollow, statusWords } from './node-chip';

const TYPE_WORD: Record<FleetDeployment['type'], string> = { single: 'Single', replicas: 'Replicas', split: 'Split', role: 'Role' };

/** a deployment's type, as a small badge before its name */
export const typeBadge = (d: Pick<FleetDeployment, 'type'>) => html`<span class="dep-type is-${d.type}">${TYPE_WORD[d.type]}</span>`;

/** the swatches of every Spark, as the All option and the fleet readout show them */
export const swatches = (nodes: Pick<FleetNode, 'id' | 'colour'>[]) =>
  html`<span class="fdots" aria-hidden="true">${nodes.map((n) => html`<i class="fdot ${isHollow(n.colour) ? 'is-hollow' : ''}" style="--c:${nodeColour(n.colour)}"></i>`)}</span>`;

const clock = (t: number) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

@customElement('qse-scope-bar')
export class QseScopeBar extends LightElement {
  @property({ attribute: false }) nodes: FleetNode[] = [];
  @property({ attribute: false }) deployments: FleetDeployment[] = [];
  /** the phone: a chip and a sheet */
  @property({ type: Boolean }) phone = false;
  /** Usage: the period is the page's own */
  @property({ type: Boolean }) usage = false;
  @state() private scope: Scope = currentScope();
  @state() private open = false;
  @state() private sheet = false;
  /** when the charts were paused here (a reload keeps the pause, not the moment) */
  @state() private pausedAt: number | null = null;
  private off: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    this.off = onScope((s) => (this.scope = s));
    document.addEventListener('pointerdown', this.onOutside);
    document.addEventListener('keydown', this.onKey);
  }
  disconnectedCallback(): void {
    this.off?.();
    document.removeEventListener('pointerdown', this.onOutside);
    document.removeEventListener('keydown', this.onKey);
    super.disconnectedCallback();
  }

  private onOutside = (e: Event) => {
    if (this.open && !(e.target as Element).closest?.('.scope-dd')) this.open = false;
  };
  private onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || (!this.open && !this.sheet)) return;
    e.stopPropagation();
    const was = this.sheet ? '.scope-chip' : '.scope-dd-btn';
    this.open = this.sheet = false;
    this.querySelector<HTMLElement>(was)?.focus();
  };

  private get shown(): Scope {
    return resolveScope(this.scope, { nodes: this.nodes, deployments: this.deployments });
  }

  private pick(s: Partial<Scope>): void {
    setScope(s);
    this.scope = currentScope();
    this.open = false;
  }
  private toggleLive(): void {
    const live = !this.shown.live;
    this.pausedAt = live ? null : Date.now();
    this.pick({ live });
  }
  private openMenu(): void {
    this.open = !this.open;
    if (this.open) this.updateComplete.then(() => this.querySelector<HTMLElement>('.scope-opt.is-on, .scope-opt')?.focus());
  }
  private openSheet(): void {
    this.sheet = true;
    this.updateComplete.then(() => this.querySelector<HTMLElement>('.scope-sheet .scope-opt.is-on, .scope-sheet .scope-opt')?.focus());
  }

  /** what the scope shows as: All with every swatch, a Spark's chip, a deployment's type and name */
  private label(s: Scope): TemplateResult {
    if (s.kind === 'node') {
      const n = this.nodes.find((x) => x.id === s.id);
      if (n) return nodeChip(n, { status: false });
    }
    if (s.kind === 'deployment') {
      const d = this.deployments.find((x) => x.id === s.id);
      if (d) return html`${typeBadge(d)} <span class="scope-dep">${d.name}</span>`;
    }
    return html`${swatches(this.nodes)}<span>All Sparks</span>${this.nodes.length ? html` <span class="scope-n num">${this.nodes.length}</span>` : nothing}`;
  }

  private options(): TemplateResult {
    let group: string | null = null;
    return html`${scopeChoices(this, this.scope).map((c) => {
      const n = c.group === 'Sparks' ? this.nodes.find((x) => x.id === c.id) : undefined;
      const d = c.group === 'Deployments' ? this.deployments.find((x) => x.id === c.id) : undefined;
      const head = c.group !== group ? html`<div class="scope-sec eyebrow">${c.group}</div>` : nothing;
      group = c.group;
      const body = n ? nodeChip(n, { status: false }) : d ? html`${typeBadge(d)} <span class="scope-dep">${d.name}</span>` : html`<b>All Sparks</b>`;
      const meta = n ? statusWords(n) : d ? d.members.map((m) => this.nodes.find((x) => x.id === m)?.alias ?? m).join(' + ') : `${this.nodes.length} nodes`;
      return html`${head}<button type="button" class="scope-opt ${c.on ? 'is-on' : ''}" role="option" aria-selected=${c.on} data-scope=${c.id ?? 'all'} @click=${() => {
          this.sheet = false;
          this.pick(c.scope);
        }}><span class="scope-check" aria-hidden="true">${c.on ? '✓' : ''}</span>${body}<span class="scope-meta">${meta}</span></button>`;
    })}`;
  }

  private ranges(): TemplateResult {
    return html`<div class="seg scope-range" role="group" aria-label="Time range">
      ${RANGE_KEYS.map((k: Range) => html`<button type="button" class="seg-btn ${this.shown.range === k ? 'is-on' : ''}" aria-pressed=${this.shown.range === k} @click=${() => this.pick({ range: k })}>${k}</button>`)}
    </div>`;
  }

  private liveBtn(): TemplateResult {
    const live = this.shown.live;
    return html`<button type="button" class="live-btn ${live ? '' : 'is-paused'}" aria-pressed=${live} title=${live ? 'Pause every chart on this page' : 'Back to live'} @click=${() => this.toggleLive()}><span class="live-dot" aria-hidden="true"></span>${live ? 'Live' : 'Paused'}</button>`;
  }

  render() {
    const s = this.shown;
    const many = this.nodes.length > 1;
    const note = `${s.live ? 'live' : this.pausedAt ? `paused at ${clock(this.pausedAt)}` : 'paused'} · ${this.usage ? 'the period is set on the page' : 'every chart on this page follows this scope'}`;
    if (this.phone) {
      return html`<button type="button" class="scope-chip" aria-haspopup="dialog" aria-expanded=${this.sheet} @click=${() => this.openSheet()}>
          ${many ? this.label(s) : nothing}${many ? html`<span class="scope-sep" aria-hidden="true">·</span>` : nothing}${this.usage ? nothing : html`<span class="num">${RANGES[s.range].label}</span><span class="scope-sep" aria-hidden="true">·</span>`}<span class=${s.live ? 'scope-live' : 'scope-paused'}>${s.live ? '● live' : 'paused'}</span><span class="scope-caret" aria-hidden="true">▾</span>
        </button>
        ${this.sheet
          ? html`<div class="scrim scope-scrim" @click=${() => (this.sheet = false)}></div>
              <section class="sheet scope-sheet" role="dialog" aria-modal="true" aria-label="Scope">
                <div class="sheet-grab" aria-hidden="true"></div>
                ${many ? html`<div class="eyebrow">Scope</div><div role="listbox" aria-label="Scope">${this.options()}</div>` : nothing}
                ${this.usage ? nothing : html`<div class="eyebrow">Time range</div>${this.ranges()}`}
                <div class="scope-sheet-live">${this.liveBtn()}</div>
                <p class="scope-note">${note}</p>
              </section>`
          : nothing}`;
    }
    return html`<span class="scope-label">Scope</span>
      ${many
        ? html`<div class="scope-dd">
            <button type="button" class="scope-dd-btn" aria-haspopup="listbox" aria-expanded=${this.open} @click=${() => this.openMenu()}>${this.label(s)}<span class="scope-caret" aria-hidden="true">▾</span></button>
            ${this.open ? html`<div class="scope-menu" role="listbox" aria-label="Scope">${this.options()}</div>` : nothing}
          </div>`
        : nothing}
      ${this.usage ? nothing : this.ranges()} ${this.liveBtn()}
      <span class="scope-note">${note}</span>`;
  }
}
