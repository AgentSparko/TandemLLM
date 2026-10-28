// <qse-node-action>: a node action's confirmation (VIS-250). It reads the controller's plan of the
// action first and shows it as the controller says it: what the action stops and where new requests
// go, what it removes for good and what stays, or why it may not run here. The action runs only with
// the plan's own confirm (Release lease asks for the alias typed as well); a job the node's agent
// runs (an engine's restart, a reboot, an update) is followed until it ends. A write the controller
// wants its admin password for shows the sign-in in place.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, type ActionPlan, type ActionResult, type FleetNode, type NodeAction } from '../../api/fleet';
import { refreshFleet } from '../../lib/fleet-state';
import { confirmButton, jobWords } from '../../lib/sparks';
import { LightElement } from '../../ui/base';
import { nodeChip } from '../../ui/node-chip';
import { clock, signInCard } from '../monitor/bits';
import { checkSession } from '../monitor/data';

const FOLLOW_MS = 1500;

@customElement('qse-node-action')
export class QseNodeAction extends LightElement {
  @state() private node: FleetNode | null = null;
  @state() private action: NodeAction | null = null;
  @state() private plan: ActionPlan | null = null;
  @state() private engine = '';
  @state() private typed = '';
  @state() private busy = false;
  @state() private error = '';
  @state() private signIn = false;
  @state() private result: ActionResult | null = null;
  private get dialog(): HTMLDialogElement | null {
    return this.querySelector('dialog');
  }
  private follow: ReturnType<typeof setTimeout> | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    window.addEventListener('qse-signed-in', this.onSignedIn);
  }
  disconnectedCallback(): void {
    window.removeEventListener('qse-signed-in', this.onSignedIn);
    this.stopFollow();
    super.disconnectedCallback();
  }
  private onSignedIn = (): void => {
    if (this.signIn) {
      this.signIn = false;
      void this.read();
    }
  };

  /** Opens the dialog on a node's action and reads its plan. */
  open(node: FleetNode, action: NodeAction): void {
    this.stopFollow();
    Object.assign(this, { node, action, plan: null, engine: '', typed: '', busy: false, error: '', signIn: false, result: null });
    void this.updateComplete.then(() => {
      if (this.dialog && !this.dialog.open) this.dialog.showModal();
    });
    void this.read();
  }

  private close(): void {
    this.stopFollow();
    this.dialog?.close();
    this.action = null;
  }

  private stopFollow(): void {
    if (this.follow) clearTimeout(this.follow);
    this.follow = null;
  }

  private async read(): Promise<void> {
    const n = this.node;
    const a = this.action;
    if (!n || !a) return;
    try {
      const p = await fleet.plan(n.id, a, this.engine || undefined);
      if (this.action !== a) return;
      this.plan = p;
      this.error = '';
    } catch (e) {
      this.error = describeError(e).message;
    }
  }

  private async run(): Promise<void> {
    const n = this.node;
    const p = this.plan;
    if (!n || !p || this.busy) return;
    this.busy = true;
    this.error = '';
    try {
      this.result = await fleet.act(n.id, p.action, { ...(p.confirm ? { confirm: p.confirm } : {}), ...(this.engine ? { engine: this.engine } : {}) });
      refreshFleet();
      this.dispatchEvent(new CustomEvent('qse-action-done', { detail: this.result, bubbles: true }));
      if (this.result.state === 'running') this.followJob();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) {
        await checkSession(true);
        this.signIn = true;
      } else {
        this.error = d.message;
        // a confirm is single use: a refusal needs a new plan
        void this.read();
      }
    } finally {
      this.busy = false;
    }
  }

  /** the agent's job, read again until it ends */
  private followJob(): void {
    this.stopFollow();
    this.follow = setTimeout(async () => {
      await this.read();
      refreshFleet();
      if (this.plan?.job?.state === 'running' && this.dialog?.open) this.followJob();
    }, FOLLOW_MS);
  }

  private list(title: string, items: string[], cls: string): TemplateResult | typeof nothing {
    if (!items.length) return nothing;
    return html`<section class="act-sec ${cls}"><h3 class="eyebrow">${title}</h3><ul>${items.map((x) => html`<li>${x}</li>`)}</ul></section>`;
  }

  private body(): TemplateResult {
    const p = this.plan;
    const n = this.node!;
    if (this.result) {
      const job = jobWords(this.plan?.job ?? this.result.job, (t) => clock(t));
      return html`<p class="act-done ok-ink" data-act-result>✓ ${this.result.text}</p>${job ? html`<p class="small muted" data-act-job>${job}</p>` : nothing}`;
    }
    if (!p) return html`${this.error ? html`<p class="small err-ink">${this.error}</p>` : html`<div class="skeleton" style="height:120px"></div>`}`;
    const { typeAlias } = confirmButton(p);
    return html`${p.blocked ? html`<p class="act-blocked warn-ink" data-act-blocked><span aria-hidden="true">⚠</span> ${p.blocked}</p>` : nothing}
      ${p.engines.length > 1
        ? html`<label class="field act-engine"><span>Engine</span><select data-act-engine @change=${(e: Event) => ((this.engine = (e.target as HTMLSelectElement).value), void this.read())}>
            ${p.engines.map((e) => html`<option value=${e.id} ?selected=${e.id === (this.engine || p.engines[0].id)}>${e.id} · port ${e.port ?? '—'} · ${e.managed ? 'managed by TandemNode' : 'not managed by TandemNode'}</option>`)}
          </select></label>`
        : nothing}
      ${this.list('What happens', p.impact, 'is-impact')}
      ${this.list('Removed for good', p.removes, 'is-removes')}
      ${this.list('Stays', p.keeps, 'is-keeps')}
      ${p.job ? html`<p class="small muted" data-act-job>Last job: ${jobWords(p.job, (t) => clock(t))}</p>` : nothing}
      ${typeAlias && p.allowed
        ? html`<label class="field act-type"><span>Type <b>${n.alias}</b> to confirm</span><input data-act-type autocomplete="off" spellcheck="false" .value=${this.typed} @input=${(e: Event) => (this.typed = (e.target as HTMLInputElement).value)} /></label>`
        : nothing}
      ${this.signIn ? signInCard('Node actions') : nothing}
      ${this.error ? html`<p class="small err-ink" data-act-error>${this.error}</p>` : nothing}`;
  }

  render() {
    const p = this.plan;
    const n = this.node;
    const btn = p ? confirmButton(p) : null;
    const ready = !!p && p.allowed && !this.result && (!btn?.typeAlias || this.typed.trim() === n?.alias);
    const danger = !!p?.destructive && p.allowed;
    return html`<dialog class="act-dialog ${danger ? 'is-danger' : ''}" aria-labelledby="act-title" data-act=${this.action ?? nothing} @close=${() => this.close()} @cancel=${() => this.close()}>
      ${n && this.action
        ? html`<form method="dialog" class="act-body" @submit=${(e: Event) => (e.preventDefault(), ready && void this.run())}>
            <header class="act-head">
              <h2 id="act-title">${p?.title ?? '…'}</h2>
              ${nodeChip(n, { lg: true })}
            </header>
            ${this.body()}
            <footer class="act-foot">
              <button type="button" class="btn btn-ghost" data-act-cancel @click=${() => this.close()}>${this.result || (p && !p.allowed) ? 'Close' : 'Cancel'}</button>
              ${p && p.allowed && !this.result ? html`<button type="submit" class="btn ${danger ? 'btn-danger-solid' : 'btn-primary'}" data-act-go ?disabled=${!ready || this.busy}>${this.busy ? 'Working…' : btn!.label}</button>` : nothing}
            </footer>
          </form>`
        : nothing}
    </dialog>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-node-action': QseNodeAction;
  }
}
