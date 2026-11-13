// <qse-start>: Settings → Get started (SRV-749), and the page a fleet with no member opens on. The
// three ways in (lib/start.ts): connect a server by its URL, add a Spark, install the TandemLLM
// engine. Connect is a form on this page: the URL, a key when the server wants one, and under
// Advanced a name and the kind. Test asks the server for its model list; Save is offered after a
// test of exactly what the form holds, and after a failed test only once the user says so (a
// refused key, no answer). The key goes to the controller once and is never read back.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError, ApiError } from '../../api/client';
import { config, type BackendConfig, type BackendKind, type TestResult } from '../../api/config';
import { sparksOf } from '../../api/fleet';
import { onFleet, refreshFleet, type FleetState } from '../../lib/fleet-state';
import { advancedOpen, canSave, formKey, PATHS, testWords, urlError } from '../../lib/start';
import { LightElement } from '../../ui/base';
import { nodeChip } from '../../ui/node-chip';

const GLYPH = { ok: '✓', warn: '⚠', err: '✕' } as const;

@customElement('qse-start')
export class QseStart extends LightElement {
  @state() private fleet: FleetState | null = null;
  @state() private open = false;
  @state() private url = '';
  @state() private key = '';
  @state() private label = '';
  @state() private kind: BackendKind = 'generic';
  @state() private busy: 'test' | 'save' | null = null;
  @state() private result: TestResult | null = null;
  /** the form as it was tested (lib/start.ts formKey) */
  private tested: string | null = null;
  @state() private confirmed = false;
  @state() private error: string | null = null;
  @state() private saved: BackendConfig | null = null;
  private off: (() => void) | null = null;

  connectedCallback(): void {
    super.connectedCallback();
    this.off = onFleet((f) => (this.fleet = f));
  }
  disconnectedCallback(): void {
    this.off?.();
    super.disconnectedCallback();
  }

  private get now(): string {
    return formKey(this.url, this.key, this.kind);
  }

  private show(): void {
    this.open = true;
    this.saved = null;
    void this.updateComplete.then(() => this.querySelector<HTMLInputElement>('[name=url]')?.focus());
  }

  private async test(e?: Event): Promise<void> {
    e?.preventDefault();
    this.error = urlError(this.url);
    if (this.error || this.busy) return;
    this.busy = 'test';
    this.result = null;
    this.confirmed = false;
    const key = this.now;
    try {
      this.result = await config.test({ kind: this.kind, url: this.url.trim(), key: this.key || undefined });
      this.tested = key;
    } catch (err) {
      this.error = describeError(err).message;
    } finally {
      this.busy = null;
    }
  }

  private async save(): Promise<void> {
    if (this.busy || !canSave(this.tested, this.now, this.result, this.confirmed)) return;
    this.busy = 'save';
    this.error = null;
    try {
      this.saved = await config.connect({ url: this.url.trim(), key: this.key || undefined, label: this.label.trim() || undefined, kind: this.kind, confirm: this.confirmed || undefined });
      // the key is the controller's now: this page forgets it
      this.url = this.key = this.label = '';
      this.result = null;
      this.tested = null;
      this.open = false;
      refreshFleet();
    } catch (err) {
      // the server tests again before it saves: what it found since the test shows here
      const t = err instanceof ApiError ? (err.body as { error?: { test?: TestResult } } | undefined)?.error?.test : undefined;
      if (t) {
        this.result = t;
        this.confirmed = false;
      } else this.error = describeError(err).message;
    } finally {
      this.busy = null;
    }
  }

  private paths(): TemplateResult {
    return html`<div class="ncards start-paths">
      ${PATHS.map((p, i) => {
        const body = html`<span class="eyebrow">${i + 1}</span><b>${p.title}</b><span class="small muted">${p.text}</span>`;
        const style = `--nc:var(--n${i + 1})`;
        return p.href
          ? html`<a class="ncard" style=${style} href=${p.href} data-path=${p.id}>${body}</a>`
          : html`<button type="button" class="ncard ${this.open ? 'is-on' : ''}" style=${style} data-path=${p.id} aria-expanded=${this.open} aria-controls="start-connect" @click=${() => this.show()}>${body}</button>`;
      })}
    </div>`;
  }

  private outcome(): TemplateResult | typeof nothing {
    if (this.busy === 'test') return html`<p class="small muted" role="status">Asking the server for its models…</p>`;
    const t = this.result;
    if (!t) return nothing;
    if (this.tested !== this.now) return html`<p class="small muted" role="status" data-test="stale">The form changed since the test. Test again.</p>`;
    const w = testWords(t);
    return html`<div class="note ${w.tone === 'ok' ? 'note-info' : ''}" role="status" data-test=${t.ok ? 'ok' : (t.reason ?? 'failed')}>
      <span class="${w.tone}-ink" aria-hidden="true">${GLYPH[w.tone]}</span> ${w.text}
      ${w.confirm ? html`<label class="start-confirm"><input type="checkbox" name="confirm" .checked=${this.confirmed} @change=${(e: Event) => (this.confirmed = (e.target as HTMLInputElement).checked)} /> ${w.confirm}</label>` : nothing}
    </div>`;
  }

  private form(): TemplateResult {
    const input = (set: (v: string) => void) => (e: Event) => set((e.target as HTMLInputElement).value);
    const ready = canSave(this.tested, this.now, this.result, this.confirmed);
    return html`<section class="panel" id="start-connect" data-connect>
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Connect a server</h2><p class="panel-sub">TandemApp asks the server for its model list. Nothing is saved until you press Save.</p></div></header>
      <form class="start-form" novalidate @submit=${(e: Event) => this.test(e)}>
        <label class="field field-block"><span>Server URL</span><input name="url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="http://192.0.2.20:8000/v1" .value=${this.url} @input=${input((v) => (this.url = v))} /></label>
        <label class="field field-block"><span>API key <span class="muted">(only if the server asks for one)</span></span><input name="key" type="password" autocomplete="off" .value=${this.key} @input=${input((v) => (this.key = v))} /><span class="xs muted">Kept encrypted on the controller. It is not shown again.</span></label>
        <details class="start-adv" ?open=${advancedOpen()}>
          <summary class="small">Advanced</summary>
          <label class="field field-block"><span>Name</span><input name="label" maxlength="64" autocomplete="off" placeholder="the server’s host" .value=${this.label} @input=${input((v) => (this.label = v))} /></label>
          <label class="field field-block"><span>Kind</span><select name="kind" @change=${(e: Event) => (this.kind = (e.target as HTMLSelectElement).value as BackendKind)}>
            <option value="generic" ?selected=${this.kind === 'generic'}>OpenAI-compatible server</option>
            <option value="tandemllm" ?selected=${this.kind === 'tandemllm'}>TandemLLM engine</option>
          </select></label>
          <p class="xs muted">Other kinds, timeouts, limits, prices and routing: <a href="#/api/backends">API → Backends</a>.</p>
        </details>
        ${this.error ? html`<p class="small err-ink" role="alert" data-error>${this.error}</p>` : nothing}
        ${this.outcome()}
        <div class="start-actions">
          <button type="submit" class="btn ${ready ? '' : 'btn-primary'}" ?disabled=${!!this.busy} data-act="test">${this.busy === 'test' ? 'Testing…' : 'Test connection'}</button>
          <button type="button" class="btn ${ready ? 'btn-primary' : ''}" ?disabled=${!ready || !!this.busy} data-act="save" @click=${() => this.save()}>${this.busy === 'save' ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </section>`;
  }

  private done(b: BackendConfig): TemplateResult {
    const n = (b.model_rows ?? []).filter((m) => m.present).length;
    return html`<section class="panel" data-saved=${b.id} role="status">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title"><span class="ok-ink" aria-hidden="true">✓</span> ${b.label} is in your fleet</h2><p class="panel-sub">${b.health.state === 'up' ? `${n} model${n === 1 ? '' : 's'} ready behind TandemApp’s endpoint.` : `Saved. It does not answer yet: ${b.health.last_error ?? 'not checked'}.`}</p></div></header>
      <p class="start-actions"><a class="btn btn-primary" href="#/models">See its models</a><a class="btn" href="#/playground">Try it in Playground</a><button type="button" class="btn btn-ghost" @click=${() => this.show()}>Connect another</button></p>
    </section>`;
  }

  render() {
    const f = this.fleet;
    const empty = !f?.nodes.length;
    const ns = f?.nodes ?? [];
    return html`<header class="page-head" data-page="start" data-first-run=${empty}>
        <div class="eyebrow">Settings</div>
        <h1>${empty ? 'Get started' : 'Add to your fleet'}</h1>
        <p class="page-sub">${empty ? 'Nothing is connected yet. TandemApp works with any OpenAI-compatible server and with Sparks; pick a way in.' : html`In your fleet now: ${sparksOf(ns).length} Spark${sparksOf(ns).length === 1 ? '' : 's'}, ${ns.length - sparksOf(ns).length} backend${ns.length - sparksOf(ns).length === 1 ? '' : 's'}. ${ns.map((n) => nodeChip(n))}`}</p>
      </header>
      ${this.paths()}${this.saved ? this.done(this.saved) : nothing}${this.open ? this.form() : nothing}`;
  }
}
