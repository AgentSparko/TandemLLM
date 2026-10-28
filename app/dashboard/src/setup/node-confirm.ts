// <qse-setup-confirm>: a Setup Guide write on another Spark, said before it goes (VIS-252). The guide of
// a node runs through TandemApp's controller, which answers what the write does there (its engine
// restarts, which deployments stop answering on it and where their requests go, what is removed) and
// a single-use confirm; this dialog shows that plan as the controller says it, and its yes is what
// lets setup/api.ts send the write with the confirm. A plan that may not run says why and only closes.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import type { SetupConfirm } from '../api/fleet';
import { LightElement } from '../ui/base';

@customElement('qse-setup-confirm')
export class QseSetupConfirm extends LightElement {
  /** the node's alias, for the head */
  @property() alias = '';
  @state() private plan: SetupConfirm | null = null;
  private answer: ((yes: boolean) => void) | null = null;
  private get dialog(): HTMLDialogElement | null {
    return this.querySelector('dialog');
  }

  /** Shows the plan; resolves true when the user confirms, false on Cancel, Escape or Close. */
  ask(plan: SetupConfirm): Promise<boolean> {
    this.answer?.(false);
    this.plan = plan;
    void this.updateComplete.then(() => {
      if (this.dialog && !this.dialog.open) this.dialog.showModal();
    });
    return new Promise((res) => (this.answer = res));
  }

  private done(yes: boolean): void {
    const a = this.answer;
    this.answer = null;
    if (this.dialog?.open) this.dialog.close();
    a?.(yes);
  }

  private list(title: string, items: string[], cls: string): TemplateResult | typeof nothing {
    if (!items.length) return nothing;
    return html`<section class="act-sec ${cls}"><h3 class="eyebrow">${title}</h3><ul>${items.map((x) => html`<li>${x}</li>`)}</ul></section>`;
  }

  render() {
    const p = this.plan;
    return html`<dialog class="act-dialog ${p?.allowed ? 'is-danger' : ''}" aria-labelledby="sc-title" data-setup-confirm=${p?.route ?? nothing} @close=${() => this.done(false)} @cancel=${() => this.done(false)}>
      ${p
        ? html`<form method="dialog" class="act-body" @submit=${(e: Event) => (e.preventDefault(), p.allowed && this.done(true))}>
            <header class="act-head"><h2 id="sc-title">${p.title}</h2><span class="small muted">through TandemApp’s controller</span></header>
            ${p.blocked ? html`<p class="act-blocked warn-ink" data-act-blocked><span aria-hidden="true">⚠</span> ${p.blocked}</p>` : nothing}
            ${this.list('What happens', p.impact, 'is-impact')}
            ${this.list('Stays', p.keeps, 'is-keeps')}
            <footer class="act-foot">
              <button type="button" class="btn btn-ghost" data-act-cancel @click=${() => this.done(false)}>${p.allowed ? 'Cancel' : 'Close'}</button>
              ${p.allowed ? html`<button type="submit" class="btn btn-danger-solid" data-act-go>Go ahead on ${this.alias}</button>` : nothing}
            </footer>
          </form>`
        : nothing}
    </dialog>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-setup-confirm': QseSetupConfirm;
  }
}
