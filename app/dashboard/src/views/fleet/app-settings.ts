// <qse-app-settings>: Settings → App (OPS-234, concept §8.13, the v3 prototype's App page): where
// TandemApp's controller runs, its API URL, and moving it; how it runs and its release; the admin
// password; the machine's permissions; the fleet's configuration as a file. A chunk of its own,
// loaded when the page opens.
//
// Controller host: a card for the machine it runs on (This Mac, with the sleep warning while a Mac
// runs it) and one per Spark it may move to (a rented or tunnelled one says why not); Move… reads the
// controller's plan (/v1/fleet/controller/move: what moves, what stays, the new API URL, the admin
// password a controller on the LAN needs) and runs it with the plan's confirm. The move's steps show
// as the controller does them; once it is done the API URL is the new one and this page follows to
// it (five seconds, or now; Stay here keeps it). A controller with no fleet of its own can take one:
// the URL of the controller that runs it and its admin password (This Mac takes the fleet back).
//
// 6.3b. This controller: the service it runs as and the release, with Update fleet (the Sparks page's
// dialog). Admin password: set (the first one from the controller's own machine), change and remove,
// each with the current password or the admin token; a change signs out every other browser. The
// permissions (A14) as the controller reads them, `unknown` where it cannot, each with why, its
// fallback and its button (Check now asks macOS once; notifications are this browser's). Fleet
// configuration: Export downloads the file (secrets left out, the page lists which); Import reads a
// file, shows the controller's plan item by item (merge or replace) and applies it with the plan's
// confirm.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, type AppView, type ControllerView, type ImportMode, type ImportPlan, type MoveJob, type MovePlan } from '../../api/fleet';
import { applyLabel, browserNotify, exportName, importGroups, kindWord, passwordBlocked, passwordError, passwordStatus, permRows, planSummary, readConfig, resultWords, updateLine, type BrowserNotify, type PermRow } from '../../lib/app-settings';
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
  // 6.3b
  @state() private app: AppView | null = null;
  @state() private notify: BrowserNotify = browserNotify();
  @state() private pw = { current: '', next: '', again: '' };
  @state() private pwShow = false;
  @state() private pwDrop = false;
  @state() private msg: Record<string, string> = {};
  @state() private file: { name: string; doc: Record<string, unknown> } | null = null;
  @state() private mode: ImportMode = 'merge';
  @state() private iplan: ImportPlan | null = null;
  private poller: Poller | null = null;
  private appPoller: Poller | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private unsub: (() => void) | null = null;
  private followed = '';

  private get dialog(): HTMLDialogElement | null {
    return this.querySelector('dialog[data-dialog="move"]');
  }
  private get importDialog(): HTMLDialogElement | null {
    return this.querySelector('dialog[data-dialog="import"]');
  }

  connectedCallback(): void {
    super.connectedCallback();
    this.unsub = onFleet((f) => (this.fleetState = f));
    this.poller = poll(() => this.load(), 2000);
    this.poller.start();
    this.appPoller = poll(() => this.loadApp(), 15000);
    this.appPoller.start();
    window.addEventListener('qse-signed-in', this.onSignedIn);
  }
  disconnectedCallback(): void {
    this.unsub?.();
    this.poller?.stop();
    this.appPoller?.stop();
    this.stopCount();
    window.removeEventListener('qse-signed-in', this.onSignedIn);
    super.disconnectedCallback();
  }
  private onSignedIn = (): void => {
    const was = this.signIn;
    this.signIn = '';
    if (this.plan) void this.readPlan(this.plan.to);
    if (was === 'import' && this.file) void this.readImport();
    void this.appPoller?.refresh();
  };

  private async loadApp(): Promise<void> {
    try {
      this.app = await fleet.app();
      this.notify = browserNotify();
    } catch (e) {
      // an older controller has no App settings: the sections that need it stay away
      if (describeError(e).status !== 404) this.msg = { ...this.msg, app: describeError(e).message };
    }
  }

  /** a write refused 401: the session check, then the sign-in card under `where` */
  private async refused(e: unknown, where: string): Promise<boolean> {
    if (describeError(e).status !== 401) return false;
    await checkSession(true);
    this.signIn = where;
    return true;
  }

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
          ? html`<label class="field"><span>Admin password for the new controller</span><span class="app-pw"><input type=${this.showPw ? 'text' : 'password'} class="num" data-in="move-pw" autocomplete="new-password" spellcheck="false" placeholder=${`${MIN_PASSWORD} or more characters`} .value=${this.password} @input=${(e: Event) => ((this.password = (e.target as HTMLInputElement).value), (this.err = { ...this.err, move: '' }))} /><button type="button" class="btn btn-sm" data-act="gen-pw" @click=${() => ((this.password = newPassword()), (this.showPw = true), (this.err = { ...this.err, move: '' }))}>Generate</button></span></label>`
          : nothing}
      </div>
      ${p.needs_password ? html`<p class="xs muted">It listens on the LAN: changes there need this password, reading does not. Keep it: it is written to the controller’s env file on the Spark (mode 0600), nowhere else, and asked when you sign in there.</p>` : nothing}
      ${this.signIn === 'move' ? signInCard('Moving the controller') : nothing}
      ${this.err.move ? html`<p class="small err-ink" data-move-error>${this.err.move}</p>` : nothing}`;
  }

  // ---- 6.3b: this controller, the password, the permissions, the configuration file -------------
  private servicePanel(a: AppView): TemplateResult {
    const nodes = this.fleetState?.nodes ?? [];
    const up = updateLine(a.release, nodes);
    const s = a.service;
    return html`<section class="panel" data-panel="service">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">This controller</h2><p class="panel-sub">How it runs on its machine, and the release it gives the Sparks.</p></div></header>
      <dl class="app-dl">
        <dt>Service</dt><dd data-service=${s.manager}>${s.text}${s.manager === 'none' ? html` <code class="app-cmd">tandem-app service install</code>` : nothing}</dd>
        <dt>Updates</dt><dd data-updates=${up.behind}><span class=${up.mixed ? 'warn-ink' : ''}>${up.text}</span></dd>
      </dl>
      ${nodes.length ? html`<div class="app-acts"><a class="btn" href="#/sparks?update=1" data-act="update-fleet">Update fleet…</a></div>` : nothing}
    </section>`;
  }

  private async savePassword(): Promise<void> {
    const p = this.app?.password;
    if (!p || this.busy) return;
    const bad = passwordError(p, this.pw);
    if (bad) {
      this.msg = { ...this.msg, pw: '', pwErr: bad };
      return;
    }
    this.busy = 'pw';
    this.msg = { ...this.msg, pw: '', pwErr: '' };
    try {
      const was = p.password;
      await fleet.setPassword({ ...(p.set ? { current: this.pw.current } : {}), password: this.pw.next });
      this.pw = { current: '', next: '', again: '' };
      this.pwShow = false;
      this.msg = { ...this.msg, pw: was ? 'Changed. Every other browser is signed out; this one stays signed in.' : 'Set. Changes from other devices need it from now on; this browser is signed in.' };
      window.dispatchEvent(new Event('qse-signed-in'));
    } catch (e) {
      if (!(await this.refused(e, 'pw'))) this.msg = { ...this.msg, pwErr: describeError(e).message };
    } finally {
      this.busy = '';
      void this.appPoller?.refresh();
    }
  }

  private async dropPassword(): Promise<void> {
    if (this.busy) return;
    if (!this.pw.current) {
      this.msg = { ...this.msg, pwErr: 'Your current password, or the admin token, first.' };
      return;
    }
    this.busy = 'pw-drop';
    this.msg = { ...this.msg, pw: '', pwErr: '' };
    try {
      const after = await fleet.removePassword(this.pw.current);
      this.pw = { current: '', next: '', again: '' };
      this.pwDrop = false;
      this.msg = { ...this.msg, pw: after.set ? 'Removed. The admin token still protects the fleet; every other browser is signed out.' : 'Removed. Anyone who opens this page can change the fleet again.' };
      window.dispatchEvent(new Event('qse-signed-in'));
    } catch (e) {
      if (!(await this.refused(e, 'pw'))) this.msg = { ...this.msg, pwErr: describeError(e).message };
    } finally {
      this.busy = '';
      void this.appPoller?.refresh();
    }
  }

  private pwInput(key: 'current' | 'next' | 'again', label: string, auto: string, extra: TemplateResult | typeof nothing = nothing): TemplateResult {
    return html`<label class="field"><span>${label}</span><span class="app-pw"><input type=${this.pwShow && key !== 'current' ? 'text' : 'password'} data-in=${`pw-${key}`} autocomplete=${auto} spellcheck="false" .value=${this.pw[key]} @input=${(e: Event) => ((this.pw = { ...this.pw, [key]: (e.target as HTMLInputElement).value }), (this.msg = { ...this.msg, pwErr: '' }))} />${extra}</span></label>`;
  }

  private passwordPanel(a: AppView): TemplateResult {
    const p = a.password;
    const st = passwordStatus(p);
    const blocked = passwordBlocked(p);
    const gen = html`<button type="button" class="btn btn-sm" data-act="pw-gen" @click=${() => {
      const v = newPassword();
      this.pw = { ...this.pw, next: v, again: v };
      this.pwShow = true;
    }}>Generate</button>`;
    return html`<section class="panel" data-panel="password">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Admin password</h2><p class="panel-sub">Changes from another device need it: node actions, deployments, Setup Guide writes, weights, imports and these settings. Reading needs none.</p></div></header>
      <div class="app-pw-state" data-password=${p.set ? (p.password ? 'set' : 'token') : 'none'}><span class="app-state is-${st.tone}">${p.set ? '● Protected' : '○ Open'}</span><b>${st.title}</b><span class="small muted">${st.sub}</span></div>
      ${blocked
        ? html`<p class="small warn-ink" data-password-blocked>${blocked}</p>`
        : html`<form class="app-form app-pw-form" @submit=${(e: Event) => (e.preventDefault(), this.pwDrop ? void this.dropPassword() : void this.savePassword())}>
            ${p.set ? this.pwInput('current', 'Current password or admin token', 'current-password') : nothing}
            ${this.pwDrop ? nothing : this.pwInput('next', p.password ? 'New password' : 'Password', 'new-password', gen)}
            ${this.pwDrop ? nothing : this.pwInput('again', 'Again', 'new-password', html`<button type="button" class="btn btn-sm btn-ghost" data-act="pw-show" aria-pressed=${this.pwShow} @click=${() => (this.pwShow = !this.pwShow)}>${this.pwShow ? 'Hide' : 'Show'}</button>`)}
            ${this.pwDrop
              ? html`<button type="submit" class="btn app-danger" data-act="pw-remove-go" ?disabled=${this.busy === 'pw-drop'}>${this.busy === 'pw-drop' ? 'Removing…' : 'Remove the password'}</button>`
              : html`<button type="submit" class="btn btn-primary" data-act="pw-save" ?disabled=${this.busy === 'pw'}>${this.busy === 'pw' ? 'Saving…' : p.password ? 'Change password' : 'Set password'}</button>`}
          </form>
          ${this.pwDrop ? html`<p class="small warn-ink" data-remove-warning>${p.token ? 'The admin token in the env file keeps protecting the fleet.' : 'With no password, anyone who opens this page can change the fleet.'}</p>` : nothing}
          <div class="app-acts">
            ${p.password ? html`<button type="button" class="btn btn-ghost btn-sm" data-act="pw-remove" @click=${() => ((this.pwDrop = !this.pwDrop), (this.msg = { ...this.msg, pwErr: '' }))}>${this.pwDrop ? 'Keep the password' : 'Remove the password…'}</button>` : nothing}
          </div>`}
      <p class="xs faint">${p.min_length} to ${p.max_length} characters, kept as a salted hash on the controller (never the password). Setting or changing it signs out every other browser. Lost it? Run <code class="app-cmd">tandem-app password set</code> on the controller’s machine.</p>
      ${this.signIn === 'pw' ? signInCard('Password changes') : nothing}
      ${this.msg.pw ? html`<p class="small ok-ink" role="status" data-password-done>${this.msg.pw}</p>` : nothing}
      ${this.msg.pwErr ? html`<p class="small err-ink" role="alert" data-password-error>${this.msg.pwErr}</p>` : nothing}
    </section>`;
  }

  private async permAction(r: PermRow): Promise<void> {
    if (r.action?.kind === 'browser') {
      if (typeof Notification !== 'undefined') await Notification.requestPermission();
      this.notify = browserNotify();
      return;
    }
    this.busy = `perm-${r.id}`;
    try {
      this.app = await fleet.checkLan();
    } catch (e) {
      if (!(await this.refused(e, 'perm'))) this.msg = { ...this.msg, perm: describeError(e).message };
    } finally {
      this.busy = '';
    }
  }

  private permPanel(a: AppView): TemplateResult {
    const rows = permRows(a.permissions, this.notify);
    return html`<section class="panel" data-panel="permissions">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Permissions</h2><p class="panel-sub">${a.platform === 'mac' ? 'What this Mac lets the controller do. Each is asked when it is first needed; a denial has a way around it.' : 'The controller runs on Linux: most of these do not apply here.'}</p></div></header>
      <ul class="app-perms">
        ${rows.map(
          (r) => html`<li class="app-perm ${r.applies ? '' : 'is-na'}" data-perm=${r.id} data-state=${r.badge}>
            <div class="app-perm-head"><b>${r.title}</b><span class="app-state is-${r.tone}">${r.badge}</span></div>
            <p class="small">${r.why}</p>
            <p class="small muted">${r.detail}</p>
            ${r.files.length ? html`<ul class="app-files">${r.files.map((f) => html`<li class="xs" data-file-state=${f.state}><span class=${f.state === 'ok' ? 'ok-ink' : 'err-ink'} aria-hidden="true">${f.state === 'ok' ? '✓' : '⚠'}</span> ${f.what} <code>${f.path}</code> ${f.mode ?? ''}</li>`)}</ul>` : nothing}
            ${r.fallback ? html`<p class="small app-fallback" data-fallback>${r.fallback.startsWith('chmod ') ? html`Fix: <code class="app-cmd">${r.fallback}</code>` : html`Way around: ${r.fallback}`}</p>` : nothing}
            ${r.settings && r.tone !== 'ok' ? html`<p class="xs muted">Turn it on in ${r.settings}.</p>` : nothing}
            ${r.action ? html`<div><button type="button" class="btn btn-sm" data-act=${`perm-${r.id}`} ?disabled=${this.busy === `perm-${r.id}`} @click=${() => void this.permAction(r)}>${this.busy === `perm-${r.id}` ? 'Checking…' : r.action.label}</button></div>` : nothing}
          </li>`,
        )}
      </ul>
      ${this.signIn === 'perm' ? signInCard('Checking a permission') : nothing}
      ${this.msg.perm ? html`<p class="small err-ink">${this.msg.perm}</p>` : nothing}
    </section>`;
  }

  private async exportFile(): Promise<void> {
    this.busy = 'export';
    this.msg = { ...this.msg, cfg: '', cfgErr: '' };
    try {
      const doc = await fleet.exportConfig();
      const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = exportName(new Date());
      this.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      this.msg = { ...this.msg, cfg: `Exported ${doc.nodes.length} Spark${doc.nodes.length === 1 ? '' : 's'}, ${doc.deployments.length} deployment${doc.deployments.length === 1 ? '' : 's'}, ${doc.rules.length} rules, ${doc.layouts.length} layout${doc.layouts.length === 1 ? '' : 's'}.` };
    } catch (e) {
      if (!(await this.refused(e, 'cfg'))) this.msg = { ...this.msg, cfgErr: describeError(e).message };
    } finally {
      this.busy = '';
    }
  }

  private async pickFile(e: Event): Promise<void> {
    const input = e.target as HTMLInputElement;
    const f = input.files?.[0];
    input.value = '';
    if (!f) return;
    this.msg = { ...this.msg, cfg: '', cfgErr: '' };
    const got = readConfig(f.size > 4 * 1024 * 1024 ? '' : await f.text(), f.size);
    if ('error' in got) {
      this.msg = { ...this.msg, cfgErr: got.error };
      return;
    }
    this.file = { name: f.name, doc: got.doc };
    this.mode = 'merge';
    this.iplan = null;
    void this.updateComplete.then(() => this.importDialog?.showModal());
    void this.readImport();
  }

  private async readImport(): Promise<void> {
    if (!this.file) return;
    this.busy = 'iplan';
    this.msg = { ...this.msg, imp: '' };
    try {
      this.iplan = await fleet.importPlan(this.file.doc, this.mode);
    } catch (e) {
      if (!(await this.refused(e, 'import'))) this.msg = { ...this.msg, imp: describeError(e).message };
    } finally {
      this.busy = '';
    }
  }

  private async applyImport(): Promise<void> {
    const p = this.iplan;
    if (!this.file || !p?.confirm || this.busy) return;
    this.busy = 'iapply';
    try {
      const r = await fleet.importApply(this.file.doc, this.mode, p.confirm);
      this.msg = { ...this.msg, cfg: resultWords(r), cfgErr: '' };
      this.importDialog?.close();
      refreshFleet();
    } catch (e) {
      if (!(await this.refused(e, 'import'))) this.msg = { ...this.msg, imp: describeError(e).message };
      void this.readImport();
    } finally {
      this.busy = '';
    }
  }

  private configPanel(a: AppView): TemplateResult {
    return html`<section class="panel" data-panel="config">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Fleet configuration</h2><p class="panel-sub">The registry as a file: the Sparks with their names and colours, the deployments, the alert rules, the layouts. Keep it, or bring it into this controller again.</p></div></header>
      <details class="app-excluded"><summary class="small">Secrets stay out of the file</summary><ul class="small muted">${a.export.excluded.map((x) => html`<li>${x}</li>`)}</ul><p class="xs muted">To move the controller with its keys, use Move or Take the fleet above: that export is sealed to the other controller.</p></details>
      <div class="app-acts">
        <button type="button" class="btn" data-act="export" ?disabled=${this.busy === 'export'} @click=${() => void this.exportFile()}>${this.busy === 'export' ? 'Exporting…' : 'Export file'}</button>
        <label class="btn" data-act="import">Import file…<input type="file" accept="application/json,.json" data-in="import-file" class="app-file" @change=${(e: Event) => void this.pickFile(e)} /></label>
      </div>
      <p class="xs faint">An import shows what it adds, changes and removes first. It never brings keys or trust: a Spark it adds waits to be paired, a deployment it adds waits stopped.</p>
      ${this.signIn === 'cfg' ? signInCard('Exporting the fleet') : nothing}
      ${this.msg.cfg ? html`<p class="small ok-ink" role="status" data-config-done>${this.msg.cfg}</p>` : nothing}
      ${this.msg.cfgErr ? html`<p class="small err-ink" role="alert" data-config-error>${this.msg.cfgErr}</p>` : nothing}
    </section>`;
  }

  private importBody(): TemplateResult {
    const p = this.iplan;
    const from = p?.from;
    const modes: [ImportMode, string, string][] = [
      ['merge', 'Merge', 'adds and changes; removes nothing'],
      ['replace', 'Replace', 'also removes what the file lacks, where that is safe'],
    ];
    return html`<fieldset class="app-modes" role="radiogroup" aria-label="How to import">
        ${modes.map(([m, t, sub]) => html`<label class="app-mode ${this.mode === m ? 'is-on' : ''}"><input type="radio" name="imp-mode" data-mode=${m} .checked=${this.mode === m} @change=${() => ((this.mode = m), void this.readImport())} /><b>${t}</b><span class="xs muted">${sub}</span></label>`)}
      </fieldset>
      ${!p
        ? this.msg.imp ? nothing : html`<div class="skeleton" style="height:120px"></div>`
        : html`<p class="small" data-import-from>${from?.same_controller ? 'This controller’s own export' : `Exported from ${from?.name ?? 'another controller'}`}${from?.exported_at ? ` on ${new Date(from.exported_at * 1000).toLocaleString()}` : ''}${from?.release ? ` (TandemLLM ${from.release})` : ''}.</p>
          <p class="small"><b data-import-summary>${planSummary(p.counts)}</b></p>
          <p class="xs muted" data-import-identity>Stays: ${p.identity}.</p>
          ${p.blocked ? html`<p class="act-blocked warn-ink" data-import-blocked>⚠ ${p.blocked}</p>` : nothing}
          ${importGroups(p).map((g) => {
            const list = html`<ul class="app-imp">${g.items.map((i) => html`<li data-import=${`${i.kind}:${i.id}`} data-op=${i.op}><span class="xs muted">${kindWord(i.kind)}</span> <b>${i.label}</b>${i.fields.length ? html` <span class="xs muted">(${i.fields.join(', ')})</span>` : nothing}${i.detail ? html`<span class="xs ${i.op === 'conflict' ? 'warn-ink' : 'muted'}"> · ${i.detail}</span>` : nothing}</li>`)}</ul>`;
            return g.op === 'keep'
              ? html`<details class="act-sec"><summary class="eyebrow">${g.title} (${g.items.length})</summary>${list}</details>`
              : html`<section class="act-sec" data-group=${g.op}><h3 class="eyebrow">${g.title} (${g.items.length})</h3>${list}</section>`;
          })}`}
      ${this.signIn === 'import' ? signInCard('Importing') : nothing}
      ${this.msg.imp ? html`<p class="small err-ink" role="alert" data-import-error>${this.msg.imp}</p>` : nothing}`;
  }

  render() {
    const v = this.view;
    const head = html`<header class="page-head" data-page="app"><div class="eyebrow">Settings</div><h1>App</h1><p class="page-sub">Where TandemApp’s controller runs, its API, who may change the fleet, and its configuration.</p></header>`;
    if (!v) return html`${head}${this.loadErr ? html`<p class="page-soon">${this.loadErr}</p>` : html`<div class="skeleton" style="height:240px"></div>`}`;
    const p = this.plan;
    const a = this.app;
    const ip = this.iplan;
    return html`${head}
      ${v.moved_to ? this.moved(v.moved_to) : nothing}
      ${this.hostPanel(v)}
      ${this.takePanel(v)}
      ${a ? html`${this.servicePanel(a)}${this.passwordPanel(a)}${this.permPanel(a)}${this.configPanel(a)}` : this.msg.app ? html`<p class="small err-ink">${this.msg.app}</p>` : nothing}
      <dialog class="act-dialog" aria-labelledby="import-title" data-dialog="import" @close=${() => ((this.iplan = null), (this.file = null), (this.msg = { ...this.msg, imp: '' }))}>
        <form method="dialog" class="act-body" @submit=${(e: Event) => (e.preventDefault(), void this.applyImport())}>
          <header class="act-head"><h2 id="import-title">Import ${this.file?.name ?? 'a file'}</h2></header>
          ${this.importBody()}
          <footer class="act-foot">
            <button type="button" class="btn btn-ghost" data-act="import-cancel" @click=${() => this.importDialog?.close()}>${ip && !ip.allowed ? 'Close' : 'Cancel'}</button>
            ${ip?.allowed ? html`<button type="submit" class="btn btn-primary" data-act="import-go" ?disabled=${this.busy === 'iapply' || this.busy === 'iplan'}>${this.busy === 'iapply' ? 'Importing…' : applyLabel(ip.counts)}</button>` : nothing}
          </footer>
        </form>
      </dialog>
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
