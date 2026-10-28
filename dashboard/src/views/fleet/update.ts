// Sparks → Update fleet (OPS-234, concept A12): the plan/confirm dialog and the update's progress.
//
// <qse-fleet-update> reads the controller's plan (/v1/fleet/update/plan) and shows it as the controller
// says it: the release every Spark goes to, each Spark with a box to leave it out, the order (idle
// first, Replicas members one at a time, Single, a Split's members together), what each step stops,
// the Splits refused and the Sparks skipped and why. A selection that takes one member of a Split
// and not another is the controller's to refuse; the dialog shows its words. Start runs only with
// the plan's own confirm (428 without, 409 spent).
//
// <qse-update-progress> follows the job (/v1/fleet/update): the share done, the step that runs, each
// Spark's phase (draining, updating, rejoining, updated) with the controller's detail, the log on
// demand and Stop (after the step that runs). A job that ended stays until it is dismissed.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, type FleetNode, type UpdateJob, type UpdatePlan, type UpdateStepKind } from '../../api/fleet';
import { refreshFleet } from '../../lib/fleet-state';
import { jobProgress, PHASE_WORDS } from '../../lib/fleet-update';
import { poll, type Poller } from '../../lib/poll';
import { LightElement } from '../../ui/base';
import { nodeChip } from '../../ui/node-chip';
import { clock, signInCard } from '../monitor/bits';
import { checkSession } from '../monitor/data';

const KIND: Record<UpdateStepKind, string> = { idle: 'Idle', replica: 'Replica', single: 'Single', split: 'Split · together' };
const DISMISSED = 'qse.update.dismissed';

/** a Spark by id as the fleet knows it, else its alias from the plan */
function chipOf(id: string, nodes: FleetNode[], alias: string): TemplateResult {
  const n = nodes.find((x) => x.id === id);
  return n ? nodeChip(n, { status: false }) : html`<b class="small">${alias}</b>`;
}

@customElement('qse-fleet-update')
export class QseFleetUpdate extends LightElement {
  /** the fleet's Sparks, for their chips */
  @property({ attribute: false }) nodes: FleetNode[] = [];
  @state() private plan: UpdatePlan | null = null;
  /** the Sparks left out (unticked); none: the whole fleet */
  @state() private out = new Set<string>();
  @state() private busy = false;
  @state() private error = '';
  @state() private signIn = false;
  private get dialog(): HTMLDialogElement | null {
    return this.querySelector('dialog');
  }

  connectedCallback(): void {
    super.connectedCallback();
    window.addEventListener('qse-signed-in', this.onSignedIn);
  }
  disconnectedCallback(): void {
    window.removeEventListener('qse-signed-in', this.onSignedIn);
    super.disconnectedCallback();
  }
  private onSignedIn = (): void => {
    if (this.signIn) {
      this.signIn = false;
      void this.read();
    }
  };

  open(): void {
    Object.assign(this, { plan: null, out: new Set<string>(), busy: false, error: '', signIn: false });
    void this.updateComplete.then(() => {
      if (this.dialog && !this.dialog.open) this.dialog.showModal();
    });
    void this.read();
  }

  private close(): void {
    this.dialog?.close();
  }

  private selected(): string[] | undefined {
    if (!this.out.size || !this.plan) return undefined;
    return this.plan.nodes.filter((n) => !this.out.has(n.id)).map((n) => n.id);
  }

  private async read(): Promise<void> {
    try {
      this.plan = await fleet.updatePlan(this.selected());
      this.error = '';
    } catch (e) {
      this.error = describeError(e).message;
    }
  }

  private toggle(id: string, on: boolean): void {
    const out = new Set(this.out);
    if (on) out.delete(id);
    else out.add(id);
    this.out = out;
    void this.read();
  }

  private async start(): Promise<void> {
    const p = this.plan;
    if (!p?.confirm || this.busy) return;
    this.busy = true;
    this.error = '';
    try {
      const sel = this.selected();
      await fleet.startUpdate({ confirm: p.confirm, ...(sel ? { nodes: sel } : {}) });
      refreshFleet();
      this.dispatchEvent(new CustomEvent('qse-update-started', { bubbles: true }));
      this.close();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) {
        await checkSession(true);
        this.signIn = true;
      } else this.error = d.message;
      void this.read();
    } finally {
      this.busy = false;
    }
  }

  private body(): TemplateResult {
    const p = this.plan;
    if (!p) return this.error ? html`<p class="small err-ink">${this.error}</p>` : html`<div class="skeleton" style="height:180px"></div>`;
    const alias = (id: string) => p.nodes.find((n) => n.id === id)?.alias ?? id;
    return html`<p class="small upd-target">Every Spark goes to TandemLLM <b class="num" data-upd-target>${p.target}</b>, the release this controller carries: TandemNode and the engine’s runtime. Weights, records and pairings stay.</p>
      <fieldset class="upd-pick" data-upd-pick>
        <legend class="eyebrow">Sparks</legend>
        ${p.nodes.map((n) => {
          const cur = !!p.current.includes(n.alias);
          return html`<label class="upd-pick-row ${n.updatable ? '' : 'is-dis'}" data-pick=${n.id}>
            <input type="checkbox" .checked=${!this.out.has(n.id)} ?disabled=${cur || !n.updatable} @change=${(e: Event) => this.toggle(n.id, (e.target as HTMLInputElement).checked)} />
            ${chipOf(n.id, this.nodes, n.alias)}
            <span class="num small">${n.version ?? '—'}</span>
            <span class="xs ${cur ? 'ok-ink' : n.updatable ? 'muted' : 'warn-ink'}">${cur ? `runs ${p.target}` : n.updatable ? `→ ${p.target}` : n.why}</span>
          </label>`;
        })}
      </fieldset>
      ${p.blocked ? html`<p class="act-blocked warn-ink" data-upd-blocked><span aria-hidden="true">⚠</span> ${p.blocked}</p>` : nothing}
      ${p.steps.length
        ? html`<section class="act-sec"><h3 class="eyebrow">The order</h3><ol class="upd-order" data-upd-order>${p.steps.map(
            (s) => html`<li class="upd-step is-${s.kind}" data-step=${s.n} data-kind=${s.kind}>
              <span class="upd-n num">${s.n}</span>
              <div class="upd-step-main">
                <div class="upd-step-head"><span class="upd-kind">${KIND[s.kind]}</span>${s.nodes.map((id) => chipOf(id, this.nodes, alias(id)))}</div>
                <div class="small">${s.words}</div>
                <div class="xs ${s.downtime ? 'warn-ink' : 'ok-ink'}" data-downtime>${s.downtime ?? (s.kind === 'idle' ? 'nothing is served there' : 'no downtime: the other members serve')}</div>
              </div>
            </li>`,
          )}</ol></section>`
        : nothing}
      ${p.refused.length
        ? html`<section class="act-sec is-removes" data-upd-refused><h3 class="eyebrow">Refused</h3><ul>${p.refused.map((r) => html`<li data-refused=${r.deployment}>${r.why}</li>`)}</ul></section>`
        : nothing}
      ${p.skipped.length ? html`<section class="act-sec" data-upd-skipped><h3 class="eyebrow">Skipped</h3><ul>${p.skipped.map((s) => html`<li><b>${alias(s.node)}</b>: ${s.why}</li>`)}</ul></section>` : nothing}
      ${this.signIn ? signInCard('The fleet update') : nothing}
      ${this.error ? html`<p class="small err-ink" data-upd-error>${this.error}</p>` : nothing}`;
  }

  render() {
    const p = this.plan;
    return html`<dialog class="act-dialog upd-dialog" aria-labelledby="upd-title" @close=${() => this.close()} @cancel=${() => this.close()}>
      <form method="dialog" class="act-body" @submit=${(e: Event) => (e.preventDefault(), void this.start())}>
        <header class="act-head"><h2 id="upd-title">Update the fleet${p ? html` to <span class="num">${p.target}</span>` : nothing}</h2></header>
        ${this.body()}
        <footer class="act-foot">
          <button type="button" class="btn btn-ghost" data-upd-cancel @click=${() => this.close()}>${p && !p.allowed ? 'Close' : 'Cancel'}</button>
          ${p?.allowed ? html`<button type="submit" class="btn btn-primary" data-upd-go ?disabled=${this.busy}>${this.busy ? 'Starting…' : `Start rolling update (${p.steps.length} step${p.steps.length === 1 ? '' : 's'})`}</button>` : nothing}
        </footer>
      </form>
    </dialog>`;
  }
}

@customElement('qse-update-progress')
export class QseUpdateProgress extends LightElement {
  @property({ attribute: false }) nodes: FleetNode[] = [];
  @state() private job: UpdateJob | null = null;
  @state() private log = false;
  @state() private busy = false;
  @state() private error = '';
  @state() private dismissed = sessionStorage.getItem(DISMISSED) ?? '';
  private poller: Poller | null = null;
  private every = 0;

  connectedCallback(): void {
    super.connectedCallback();
    this.follow(1500);
  }
  disconnectedCallback(): void {
    this.poller?.stop();
    this.poller = null;
    super.disconnectedCallback();
  }

  /** read the job now and every `ms` */
  follow(ms = 1500): void {
    if (this.poller && this.every === ms) {
      void this.poller.refresh();
      return;
    }
    this.poller?.stop();
    this.every = ms;
    this.poller = poll(() => this.load(), ms);
    this.poller.start();
  }

  private async load(): Promise<void> {
    try {
      const was = this.job?.state;
      this.job = (await fleet.update()).job;
      if (was === 'running' && this.job?.state !== 'running') refreshFleet();
      if (this.job?.state === 'running') refreshFleet();
      this.follow(this.job?.state === 'running' ? 1500 : 10_000);
    } catch {
      /* an older controller: no update route, nothing to show */
    }
  }

  private async stop(): Promise<void> {
    this.busy = true;
    try {
      this.job = (await fleet.stopUpdate()).job;
    } catch (e) {
      this.error = describeError(e).message;
    } finally {
      this.busy = false;
    }
  }

  render() {
    const j = this.job;
    if (!j || (j.state !== 'running' && this.dismissed === j.id)) return nothing;
    const pr = jobProgress(j);
    const title = j.state === 'running' ? `Updating the fleet to ${j.target}` : j.state === 'done' ? `Fleet updated to ${j.target}` : j.state === 'stopped' ? `Fleet update to ${j.target} stopped` : `Fleet update to ${j.target} failed`;
    return html`<section class="panel upd-prog is-${j.state}" data-panel="update-progress" data-state=${j.state} aria-live="polite">
      <header class="panel-head">
        <div class="panel-titles"><h2 class="panel-title">${title}</h2>
          <p class="panel-sub"><span class="num" data-upd-count>${pr.done} of ${pr.total}</span> Spark${pr.total === 1 ? '' : 's'} updated · started ${clock(j.started_at)} by ${j.by}${pr.running ? html` · step ${pr.running.n}: ${pr.running.words}` : nothing}</p></div>
        ${j.state === 'running'
          ? html`<button type="button" class="btn btn-sm" data-upd-stop ?disabled=${this.busy || j.stop} @click=${() => void this.stop()}>${j.stop ? 'Stops after this step' : 'Stop after this step'}</button>`
          : html`<button type="button" class="btn btn-ghost btn-sm" data-upd-dismiss @click=${() => (sessionStorage.setItem(DISMISSED, j.id), (this.dismissed = j.id))}>Dismiss</button>`}
      </header>
      <div class="upd-bar ${j.state === 'done' ? 'is-done' : j.state === 'failed' ? 'is-err' : ''}" role="progressbar" aria-valuenow=${pr.pct} aria-valuemin="0" aria-valuemax="100" aria-label="Fleet update"><i style="width:${pr.pct}%"></i></div>
      ${j.error ? html`<p class="small err-ink" data-upd-failed>${j.error.message}</p>` : nothing}
      <ol class="upd-steps">${j.steps.map(
        (s) => html`<li class="upd-run is-${s.state}" data-step=${s.n} data-state=${s.state}>
          <div class="upd-run-head small"><span class="upd-n num">${s.n}</span><span class="upd-kind">${KIND[s.kind]}</span>${s.downtime && s.state !== 'done' ? html`<span class="xs warn-ink">${s.downtime}</span>` : nothing}</div>
          ${s.nodes.map((id) => {
            const n = j.nodes[id];
            return html`<div class="upd-node" data-node-phase=${id} data-phase=${n.phase}>
              ${chipOf(id, this.nodes, n.alias)}
              <span class="num small upd-ver">${n.from ?? '—'} → ${n.to}</span>
              <span class="upd-phase is-${n.phase}">${n.phase === 'done' ? '✓ ' : n.phase === 'failed' ? '✕ ' : ''}${PHASE_WORDS[n.phase]}</span>
              <span class="xs muted upd-detail">${n.detail ?? ''}</span>
            </div>`;
          })}
        </li>`,
      )}</ol>
      ${j.refused.length ? html`<p class="xs warn-ink">${j.refused.map((r) => r.why).join(' ')}</p>` : nothing}
      <button type="button" class="btn btn-ghost btn-sm" data-upd-log @click=${() => (this.log = !this.log)}>${this.log ? 'Hide log' : 'Show log'}</button>
      ${this.log ? html`<div class="upd-log" data-log>${j.log.map((l) => html`<div class="upd-log-l"><span class="num faint">${clock(l.t)}</span><span>${l.text}</span></div>`)}</div>` : nothing}
      ${this.error ? html`<p class="small err-ink">${this.error}</p>` : nothing}
    </section>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-fleet-update': QseFleetUpdate;
    'qse-update-progress': QseUpdateProgress;
  }
}
