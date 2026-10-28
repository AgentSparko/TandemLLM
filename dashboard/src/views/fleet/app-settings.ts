// <qse-app-settings>: Settings → App (OPS-234, concept §8.13, the v3 prototype's App page): where
// TandemApp's controller runs, its API URL, and moving it. A chunk of its own, loaded when the page
// opens.
//
// Controller host: a card for the machine it runs on (This Mac, with the sleep warning while a Mac
// runs it) and one per Spark it may move to (a rented or tunnelled one says why not); Move… reads the
// controller's plan (/v1/fleet/controller/move: what moves, what stays, the new API URL, the admin
// password a controller on the LAN needs) and runs it with the plan's confirm. The move's steps show
// as the controller does them; once it is done the API URL is the new one and this page follows to
// it (five seconds, or now; Stay here keeps it). A controller with no fleet of its own can take one:
// the URL of the controller that runs it and its admin password (This Mac takes the fleet back).

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, type ControllerView, type MoveJob, type MovePlan } from '../../api/fleet';
import { apiOf, followUrl, hostChoices, MIN_PASSWORD, moveFormError, moveWords, newPassword, planGroups, takeFormError } from '../../lib/controller-host';
import { onFleet, refreshFleet, type FleetState } from '../../lib/fleet-state';
import { poll, type Poller } from '../../lib/poll';
import { LightElement } from '../../ui/base';
import { nodeChip } from '../../ui/node-chip';
import { signInCard } from '../monitor/bits';
import { checkSession } from '../monitor/data';
import '../../styles/sparks.css';

const FOLLOW_S = 5;
const HOW: Record<MovePlan['nodes'][number]['how'], string> = {
  repin: 'trusts the new controller',
  release: 'runs the new controller, read over its loopback',
  own: 'this machine’s node: handed on with its LAN address',
  stays: 'stays',
};
const GLYPH = { done: '✓', running: '●', failed: '✕', todo: '○' } as const;

@customElement('qse-app-settings')
export class QseAppSettings extends LightElement {
  @state() private view: ControllerView | null = null;
  @state() private loadErr = '';
  @state() private fleetState: FleetState | null = null;
  @state() private pick = 'here';
  @state() private plan: MovePlan | null = null;
  @state() private port = '8080';
  @state() private password = '';
  @state() private busy = '';
  @state() private err: Record<string, string> = {};
  @state() private signIn = '';
  @state() private takeUrl = '';
  @state() private takePw = '';
  @state() private count: number | null = null;
  @state() private copied = false;
  @state() private showPw = false;
  private poller: Poller | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private unsub: (() => void) | null = null;
  private followed = '';

  private get dialog(): HTMLDialogElement | null {
    return this.querySelector('dialog');
  }

  connectedCallback(): void {
    super.connectedCallback();
    this.unsub = onFleet((f) => (this.fleetState = f));
    this.poller = poll(() => this.load(), 2000);
    this.poller.start();
    window.addEventListener('qse-signed-in', this.onSignedIn);
  }
  disconnectedCallback(): void {
    this.unsub?.();
    this.poller?.stop();
    this.stopCount();
    window.removeEventListener('qse-signed-in', this.onSignedIn);
    super.disconnectedCallback();
  }
  private onSignedIn = (): void => {
    this.signIn = '';
    if (this.plan) void this.readPlan(this.plan.to);
  };

  private async load(): Promise<void> {
    try {
      const v = await fleet.controller();
      this.view = v;
      this.loadErr = '';
      if (v.job?.state === 'done' && v.job.direction === 'pull') refreshFleet();
      // the move ended here: the page follows to the new controller
      if (v.moved_to && this.followed !== v.moved_to.api_url && v.job?.direction === 'push' && v.job.state === 'done') {
        this.followed = v.moved_to.api_url;
        this.startCount();
      }
    } catch (e) {
      const d = describeError(e);
      this.loadErr = d.status === 404 ? 'This server runs no TandemApp controller.' : d.message;
    }
  }

  // ---- following to the new controller -----------------------------------------------------------
  private startCount(): void {
    this.stopCount();
    this.count = FOLLOW_S;
    this.timer = setInterval(() => {
      this.count = (this.count ?? 1) - 1;
      if (this.count <= 0) {
        this.stopCount();
        this.go();
      }
    }, 1000);
  }
  private stopCount(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
  private go(): void {
    const m = this.view?.moved_to;
    if (m) location.href = followUrl(m, location.hash);
  }

  // ---- the move ----------------------------------------------------------------------------------
  private async readPlan(to: string): Promise<void> {
    this.busy = 'plan';
    this.err = { ...this.err, plan: '' };
    try {
      const n = Number(this.port);
      this.plan = await fleet.movePlan(to, Number.isInteger(n) && n >= 1024 && n < 65536 ? n : undefined);
    } catch (e) {
      this.err = { ...this.err, plan: describeError(e).message };
    } finally {
      this.busy = '';
    }
  }

  private openMove(): void {
    if (this.pick === 'here') return;
    this.plan = null;
    this.password = '';
    this.showPw = false;
    this.err = {};
    void this.updateComplete.then(() => this.dialog?.showModal());
    void this.readPlan(this.pick);
  }

  private async move(): Promise<void> {
    const p = this.plan;
    if (!p?.confirm || this.busy) return;
    const bad = moveFormError(p, this.port, this.password);
    if (bad) {
      this.err = { ...this.err, move: bad };
      return;
    }
    this.busy = 'move';
    try {
      await fleet.move({ to: p.to, confirm: p.confirm, port: Number(this.port), ...(this.password ? { password: this.password } : {}) });
      this.password = '';
      this.dialog?.close();
      void this.poller?.refresh();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) {
        await checkSession(true);
        this.signIn = 'move';
      } else this.err = { ...this.err, move: d.message };
      void this.readPlan(p.to);
    } finally {
      this.busy = '';
    }
  }

  private async take(): Promise<void> {
    const bad = takeFormError(this.takeUrl);
    if (bad) {
      this.err = { ...this.err, take: bad };
      return;
    }
    this.busy = 'take';
    this.err = { ...this.err, take: '' };
    try {
      await fleet.take({ url: this.takeUrl.trim(), ...(this.takePw ? { password: this.takePw } : {}) });
      this.takePw = '';
      void this.poller?.refresh();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) {
        await checkSession(true);
        this.signIn = 'take';
      } else this.err = { ...this.err, take: d.message };
    } finally {
      this.busy = '';
    }
  }

  private async copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.copied = true;
      setTimeout(() => (this.copied = false), 1500);
    } catch {
      /* no clipboard: the URL is on the page to select */
    }
  }

  // ---- the parts -----------------------------------------------------------------------------------
  private chip(id: string, alias: string): TemplateResult {
    const n = this.fleetState?.nodes.find((x) => x.id === id);
    return n ? nodeChip(n, { status: false }) : html`<b>${alias}</b>`;
  }

  private jobView(j: MoveJob): TemplateResult {
    const tone = j.state === 'done' ? 'ok-ink' : j.state === 'failed' ? 'err-ink' : '';
    return html`<div class="app-job" data-move-job=${j.state} data-direction=${j.direction}>
      <ol class="app-steps">${j.steps.map((s) => html`<li class="is-${s.state}" data-move-step=${s.id} data-state=${s.state}><span aria-hidden="true">${GLYPH[s.state]}</span><span>${s.label}${s.detail ? html` <span class="muted xs">· ${s.detail}</span>` : nothing}</span></li>`)}</ol>
      <p class="small ${tone}" data-move-words>${moveWords(j)}</p>
      ${j.nodes.length && j.state !== 'running'
        ? html`<div class="app-nodes" data-move-nodes>${j.nodes.map((x) => html`<div data-moved=${x.id}>${this.chip(x.id, x.alias)}<span class="xs ${x.accepted ? 'ok-ink' : x.how === 'stays' || x.state === 'failed' ? 'warn-ink' : 'muted'}">${x.how === 'stays' ? `stays: ${x.why}` : x.state === 'failed' ? `not handed over: ${x.why}` : x.accepted ? `${HOW[x.how]} · answers it` : HOW[x.how]}</span></div>`)}${j.old_refused ? html`<div class="xs ok-ink">✓ The nodes refuse the old controller.</div>` : nothing}</div>`
        : nothing}
    </div>`;
  }

  private moved(m: NonNullable<ControllerView['moved_to']>): TemplateResult {
    return html`<div class="app-moved" data-moved-to=${m.api_url} role="status">
      <span>This controller moved to <b>${m.name}</b>. The API is <code class="num">${apiOf(m.api_url)}</code>.</span>
      <a class="btn btn-sm btn-primary" href=${followUrl(m, location.hash)} data-act="follow">Open the new controller</a>
      ${this.count != null
        ? html`<span class="xs muted" data-follow-count>Opening it in ${this.count} s</span><button type="button" class="btn btn-ghost btn-sm" data-act="stay" @click=${() => ((this.count = null), this.stopCount())}>Stay here</button>`
        : nothing}
    </div>`;
  }

  private hostPanel(v: ControllerView): TemplateResult {
    const choices = hostChoices(v);
    const sel = choices.find((c) => c.id === this.pick) ?? choices[0];
    const running = v.job?.state === 'running';
    // moved away: the Spark it moved to runs it now, this machine no longer
    const movedNode = v.moved_to && v.job?.direction === 'push' ? (v.job.to ?? null) : null;
    const api = apiOf(v.moved_to?.api_url ?? v.api_url);
    return html`<section class="panel" data-panel="controller-host">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Controller host</h2><p class="panel-sub">The controller holds the registry, the metrics and the one API. It must stay on for the API to stay up.</p></div></header>
      <div class="app-hosts" role="radiogroup" aria-label="Where the controller runs">
        ${choices.map(
          (c) => html`<label class="app-host ${this.pick === c.id ? 'is-on' : ''} ${c.allowed ? '' : 'is-dis'}" data-host=${c.id}>
            <input type="radio" name="ctl-host" .checked=${this.pick === c.id} ?disabled=${!c.allowed || !!v.moved_to} @change=${() => (this.pick = c.id)} />
            <span class="app-host-title">${c.title}${c.current && !v.moved_to ? html`<span class="app-badge is-now">runs the controller</span>` : nothing}${c.current && v.moved_to ? html`<span class="app-badge is-was">moved away</span>` : nothing}${c.id === movedNode ? html`<span class="app-badge is-now">runs the controller now</span>` : nothing}${c.recommended && !c.current && !v.moved_to ? html`<span class="app-badge">recommended</span>` : nothing}</span>
            <span class="small muted">${c.sub}</span>
            ${c.warn ? html`<span class="app-warn" data-sleep-warning>⚠ ${c.warn}</span>` : nothing}
          </label>`,
        )}
      </div>
      <div class="app-api" data-api-url><span class="eyebrow">API</span><code>${api}</code><button type="button" class="btn btn-ghost btn-sm" @click=${() => void this.copy(api)}>${this.copied ? 'Copied' : 'Copy'}</button>${v.moved_to ? html`<span class="xs muted">was ${apiOf(v.api_url)}</span>` : nothing}</div>
      ${v.moved_to
        ? nothing
        : html`<div class="app-acts">
            <button type="button" class="btn btn-primary" data-act="move" ?disabled=${sel.current || !sel.allowed || running} @click=${() => this.openMove()}>${!v.targets.length ? 'No other Spark to move it to' : sel.current ? 'Pick a Spark to move it to' : `Move the controller to ${sel.title.replace(/^Spark /, '')}…`}</button>
            ${v.targets.length ? nothing : html`<a class="btn btn-ghost" href="#/add-spark">+ Add Spark</a>`}
          </div>`}
      <p class="xs faint">Rented Sparks are not offered: their leases end. This page and a phone’s browser stay the controller’s UI wherever it runs.</p>
      ${v.job ? html`<h3 class="eyebrow" style="margin-top:12px">${v.job.direction === 'pull' ? 'The last take' : 'The last move'}</h3>${this.jobView(v.job)}` : nothing}
    </section>`;
  }

  private takePanel(v: ControllerView): TemplateResult | typeof nothing {
    const own = (this.fleetState?.nodes ?? []).some((n) => n.pairing.state !== 'local');
    if (own || v.moved_to) return nothing;
    return html`<section class="panel" data-panel="take-fleet">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Run the controller here instead</h2><p class="panel-sub">No other Spark is paired with ${v.host.kind === 'mac' ? 'this Mac' : 'this controller'}. Take the fleet from the controller that runs it now: its nodes then trust this one, and its API moves here.</p></div></header>
      <form class="app-form" @submit=${(e: Event) => (e.preventDefault(), void this.take())}>
        <label class="field"><span>The controller now</span><input data-in="take-url" placeholder="http://192.0.2.40:8080" .value=${this.takeUrl} @input=${(e: Event) => (this.takeUrl = (e.target as HTMLInputElement).value)} /></label>
        <label class="field"><span>Its admin password</span><input type="password" data-in="take-pw" autocomplete="off" .value=${this.takePw} @input=${(e: Event) => (this.takePw = (e.target as HTMLInputElement).value)} /></label>
        <button type="submit" class="btn" data-act="take" ?disabled=${this.busy === 'take' || v.job?.state === 'running'}>${this.busy === 'take' ? 'Taking…' : 'Take the fleet'}</button>
      </form>
      ${v.sleep_warning ? html`<p class="xs warn-ink">⚠ ${v.sleep_warning}</p>` : nothing}
      ${this.signIn === 'take' ? signInCard('Taking a fleet') : nothing}
      ${this.err.take ? html`<p class="small err-ink" data-take-error>${this.err.take}</p>` : nothing}
    </section>`;
  }

  private dialogBody(): TemplateResult {
    const p = this.plan;
    if (!p) return this.err.plan ? html`<p class="small err-ink">${this.err.plan}</p>` : html`<div class="skeleton" style="height:160px"></div>`;
    const g = planGroups(p);
    return html`${p.blocked ? html`<p class="act-blocked warn-ink" data-move-blocked>⚠ ${p.blocked}</p>` : nothing}
      <p class="small">The API moves to <code class="num" data-new-api>${apiOf(p.api_url)}</code>.</p>
      <section class="act-sec"><h3 class="eyebrow">What happens</h3><ul>${p.impact.map((x) => html`<li>${x}</li>`)}</ul></section>
      <section class="act-sec"><h3 class="eyebrow">The nodes</h3><div class="app-nodes" data-move-plan-nodes>${[...g.moved, ...g.stays].map((x) => html`<div data-how=${x.how}>${this.chip(x.id, x.alias)}<span class="xs ${x.how === 'stays' ? 'warn-ink' : 'muted'}">${x.how === 'stays' ? `stays: ${x.why}` : HOW[x.how]}</span></div>`)}</div></section>
      <section class="act-sec is-keeps"><h3 class="eyebrow">Stays</h3><ul>${p.keeps.map((x) => html`<li>${x}</li>`)}</ul></section>
      <div class="app-form">
        <label class="field"><span>Port on ${p.title.replace('Move the controller to ', '')}</span><input data-in="port" inputmode="numeric" .value=${this.port} @change=${(e: Event) => ((this.port = (e.target as HTMLInputElement).value), void this.readPlan(p.to))} /></label>
        ${p.needs_password
          ? html`<label class="field"><span>Admin password for the new controller</span><input type=${this.showPw ? 'text' : 'password'} class="num" data-in="move-pw" autocomplete="new-password" spellcheck="false" placeholder=${`${MIN_PASSWORD} or more characters`} .value=${this.password} @input=${(e: Event) => ((this.password = (e.target as HTMLInputElement).value), (this.err = { ...this.err, move: '' }))} /></label>
              <button type="button" class="btn btn-sm" data-act="gen-pw" @click=${() => ((this.password = newPassword()), (this.showPw = true), (this.err = { ...this.err, move: '' }))}>Generate</button>`
          : nothing}
      </div>
      ${p.needs_password ? html`<p class="xs muted">It listens on the LAN: changes there need this password, reading does not. Keep it: it is written to the controller’s env file on the Spark (mode 0600), nowhere else, and asked when you sign in there.</p>` : nothing}
      ${this.signIn === 'move' ? signInCard('Moving the controller') : nothing}
      ${this.err.move ? html`<p class="small err-ink" data-move-error>${this.err.move}</p>` : nothing}`;
  }

  render() {
    const v = this.view;
    const head = html`<header class="page-head" data-page="app"><div class="eyebrow">Settings</div><h1>App</h1><p class="page-sub">Where TandemApp’s controller runs, and its API.</p></header>`;
    if (!v) return html`${head}${this.loadErr ? html`<p class="page-soon">${this.loadErr}</p>` : html`<div class="skeleton" style="height:240px"></div>`}`;
    const p = this.plan;
    return html`${head}
      ${v.moved_to ? this.moved(v.moved_to) : nothing}
      ${this.hostPanel(v)}
      ${this.takePanel(v)}
      <dialog class="act-dialog" aria-labelledby="move-title" data-dialog="move" @close=${() => (this.plan = null)}>
        <form method="dialog" class="act-body" @submit=${(e: Event) => (e.preventDefault(), void this.move())}>
          <header class="act-head"><h2 id="move-title">${p?.title ?? 'Move the controller'}</h2></header>
          ${this.dialogBody()}
          <footer class="act-foot">
            <button type="button" class="btn btn-ghost" data-act="move-cancel" @click=${() => this.dialog?.close()}>${p && !p.allowed ? 'Close' : 'Cancel'}</button>
            ${p?.allowed ? html`<button type="submit" class="btn btn-primary" data-act="move-go" ?disabled=${this.busy === 'move'}>${this.busy === 'move' ? 'Starting…' : 'Move the controller'}</button>` : nothing}
          </footer>
        </form>
      </dialog>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-app-settings': QseAppSettings;
  }
}
