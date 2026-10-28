// <qse-setup-signin>: the setup guide's sign-in, shown only when a /v1/setup route answered 401 (a
// box with an admin password, e.g. after the guide's own "generate" option). The rest of the
// dashboard still asks for nothing (AU1): with no password set this never renders. The password
// goes once to POST /v1/dashboard/session, which sets the HttpOnly session cookie the setup routes
// take; the page never stores it. With the server's dashboard login off that route does not exist
// (404), and the card says how to get in instead. The Monitor pages use the same card for
// TandemApp's controller (VIS-248): its sign-in (`signIn`, POST /v1/fleet/session) and its words.

import { html, nothing } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { describeError } from '../api/client';
import { LightElement } from '../ui/base';
import { icon } from '../ui/bits';
import { setupApi } from './api';
import { signInError } from './checks';
import { S } from './strings';

@customElement('qse-setup-signin')
export class QseSetupSignin extends LightElement {
  /** where the password goes: the dashboard's session, or the controller's */
  @property({ attribute: false }) signIn: (password: string) => Promise<unknown> = (p) => setupApi.signIn(p);
  @property({ attribute: false }) texts: { title: string; body: string; kept: string } | null = null;
  @state() private busy = false;
  @state() private error: string | null = null;

  private async submit(e: Event): Promise<void> {
    e.preventDefault();
    const input = this.querySelector<HTMLInputElement>('.signin-input');
    const value = input?.value ?? '';
    if (!value || this.busy) return;
    this.busy = true;
    this.error = null;
    try {
      await this.signIn(value);
      if (input) input.value = '';
      window.dispatchEvent(new CustomEvent('qse-signed-in'));
    } catch (err) {
      const d = describeError(err);
      this.error = signInError(d.status, d.message);
    } finally {
      this.busy = false;
    }
  }

  render() {
    return html`<form class="panel signin-card" data-setup-signin @submit=${(e: Event) => void this.submit(e)}>
      <h2 class="signin-title">${icon('warn')} ${this.texts?.title ?? S.signIn.title}</h2>
      <p class="signin-body">${this.texts?.body ?? S.signIn.body}</p>
      <label class="signin-field"
        ><span>${S.signIn.label}</span>
        <input class="signin-input text-input" type="password" name="password" autocomplete="current-password" required spellcheck="false" autocapitalize="off" />
      </label>
      ${this.error ? html`<p class="login-error" role="alert" data-signin-error>${this.error}</p>` : nothing}
      <div class="signin-foot">
        <button class="btn btn-primary" type="submit" ?disabled=${this.busy}>${this.busy ? S.signIn.checking : S.signIn.go}</button>
        <span class="muted small">${this.texts?.kept ?? S.signIn.kept}</span>
      </div>
    </form>`;
  }
}
