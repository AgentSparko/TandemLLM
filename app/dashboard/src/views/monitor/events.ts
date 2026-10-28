// <qse-events>: Events & Alerts (VIS-248, concept §8.5 and D7–D8, the v3 prototype's page). The alerts
// that fire now, each with Acknowledge (who and when once done; it stays until it resolves) and its
// action; the fleet's timeline by day, filtered by Spark and type, newer events arriving while live
// and older ones a page at a time; the alert rules with their editor (on, threshold, hold, scope,
// in the app and as a system notification; a rule without a threshold or about the controller's
// machine says what it cannot change); and this device's notifications. Acknowledging and editing
// need the controller's admin password when it has one: the sign-in card shows first.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { EVENT_TYPES, fleet, type EventType, type FleetEvent, type FleetRule } from '../../api/fleet';
import { forgetRules, permission, setSystemOn, systemOn } from '../../lib/alert-notify';
import { refreshContext, type ContextContent } from '../../lib/context';
import { activeAlerts, refreshFleet } from '../../lib/fleet-state';
import { byDay, draftOf, EVENT_LABEL, mayHaveMore, mergeEvents, newerCursor, olderCursor, rulePatch, scopeEditable, scopeLabel, scopeOptions, thresholdEditable, type RuleDraft } from '../../lib/monitor';
import type { View } from '../../lib/router';
import { nodeChip } from '../../ui/node-chip';
import { MonitorPage } from './base';
import { alertCard, eventRow, signInCard } from './bits';
import { acknowledge, checkSession, needsSignIn, onSession } from './data';

export const PAGE = 50;

const PERM_WORDS: Record<ReturnType<typeof permission>, string> = {
  granted: 'allowed in this browser',
  denied: 'blocked in this browser’s site settings',
  default: 'not asked yet: turning it on asks',
  unsupported: 'not available here: it needs https or the installed app',
};

@customElement('qse-events')
export class QseEvents extends MonitorPage {
  protected readonly view: View = 'events';
  @state() private events: FleetEvent[] = [];
  /** the newest alert events, whatever the filters: who acknowledged what */
  @state() private alertEvents: FleetEvent[] = [];
  @state() private more = false;
  @state() private older = false;
  @state() private node = 'all';
  @state() private type: EventType | 'all' = 'all';
  @state() private rules: FleetRule[] = [];
  @state() private editing: string | null = null;
  @state() private draft: RuleDraft | null = null;
  @state() private ruleMsg: { id: string; text: string; err: boolean } | null = null;
  @state() private saving = false;
  @state() private acking = '';
  @state() private ackError = '';
  @state() private notify = systemOn();
  @state() private perm = permission();
  @state() private hostName = 'controller host';
  /** the filter the list was read with: a change starts it again */
  private listed = '';
  private offSession: (() => void) | null = null;

  connectedCallback(): void {
    const s = this.scope;
    if (s.kind === 'node' && s.id) this.node = s.id;
    super.connectedCallback();
    void checkSession();
    this.offSession = onSession(() => (this.requestUpdate(), refreshContext()));
  }
  disconnectedCallback(): void {
    this.offSession?.();
    super.disconnectedCallback();
  }
  protected scopeChanged(): void {
    if (this.scope.kind === 'node' && this.scope.id) this.setFilter(this.scope.id, this.type);
  }

  private query() {
    return { node: this.node === 'all' ? undefined : this.node, type: this.type === 'all' ? undefined : [this.type] };
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    const key = `${this.node}|${this.type}`;
    const fresh = key !== this.listed || !this.events.length;
    const since = fresh ? undefined : (newerCursor(this.events) ?? undefined);
    const [page, acks, rules, topo] = await Promise.all([
      fleet.events({ ...this.query(), since, limit: fresh ? PAGE : 200 }).catch(() => null),
      fleet.events({ type: ['alert'], limit: 50 }).catch(() => null),
      this.rules.length && !fresh ? Promise.resolve(null) : fleet.rules().catch(() => null),
      this.hostName === 'controller host' ? fleet.topology().catch(() => null) : Promise.resolve(null),
    ]);
    if (!isCurrent() || key !== `${this.node}|${this.type}`) return;
    if (page) {
      this.events = fresh ? page.events : mergeEvents(page.events, this.events);
      if (fresh) this.more = mayHaveMore(page.events, PAGE);
      this.listed = key;
    }
    if (acks) this.alertEvents = acks.events;
    if (rules) this.rules = rules.rules;
    if (topo) this.hostName = topo.host.name;
    refreshContext();
  }

  private setFilter(node: string, type: EventType | 'all'): void {
    if (node === this.node && type === this.type) return;
    this.node = node;
    this.type = type;
    this.events = [];
    this.more = false;
    this.reload();
  }

  private async loadOlder(): Promise<void> {
    const before = olderCursor(this.events);
    if (before == null) return;
    this.older = true;
    const key = this.listed;
    const page = await fleet.events({ ...this.query(), before, limit: PAGE }).catch(() => null);
    this.older = false;
    if (!page || key !== this.listed) return;
    this.events = mergeEvents(this.events, page.events);
    this.more = mayHaveMore(page.events, PAGE);
  }

  // ---- alerts -----------------------------------------------------------------------------------
  private async ack(id: string): Promise<void> {
    this.acking = id;
    this.ackError = '';
    const r = await acknowledge({ id });
    this.acking = '';
    if (r !== 'ok' && r !== 'sign-in') this.ackError = r;
    this.reload();
    refreshContext();
  }

  private alertsSection(compact = false): TemplateResult {
    const f = this.fleetState!;
    const list = [...activeAlerts(f), ...f.alerts.filter((a) => a.acked)];
    const locked = needsSignIn();
    return html`${this.ackError ? html`<p class="small err-ink" role="alert">${this.ackError}</p>` : nothing}
      ${list.length
        ? html`<div class="alert-list ${compact ? 'ctx-alerts' : ''}" data-alerts>${list.map((a) => alertCard(a, { nodes: f.nodes, events: this.alertEvents, hostName: this.hostName, locked, busy: this.acking === a.id, onAck: (x) => void this.ack(x.id), compact }))}</div>`
        : html`<p class="small ok-ink" data-no-alerts>✓ No alert fires now. Every rule below is watched on each sample.</p>`}`;
  }

  // ---- the timeline -----------------------------------------------------------------------------
  private timeline(): TemplateResult {
    const f = this.fleetState!;
    const days = byDay(this.events);
    const nodeOpts: [string, TemplateResult | string][] = [['all', 'All'], ...f.nodes.map((n): [string, TemplateResult] => [n.id, nodeChip(n, { status: false })]), ['host', 'Controller']];
    return html`<section class="panel" data-panel="timeline">
      <header class="panel-head">
        <div class="panel-titles"><h2 class="panel-title">Timeline</h2><p class="panel-sub">${this.events.length} event${this.events.length === 1 ? '' : 's'} shown${this.more ? ' · older ones a page at a time' : ''} · newest first</p></div>
        <div class="panel-tools ev-filters">
          <div class="seg" role="group" aria-label="Spark">${nodeOpts.map(([v, l]) => html`<button type="button" class="seg-btn ${this.node === v ? 'is-on' : ''}" aria-pressed=${this.node === v} data-ev-node=${v} @click=${() => this.setFilter(v, this.type)}>${l}</button>`)}</div>
          <select class="wm-in" aria-label="Type" data-ev-type @change=${(e: Event) => this.setFilter(this.node, (e.target as HTMLSelectElement).value as EventType | 'all')}>
            <option value="all" ?selected=${this.type === 'all'}>All types</option>
            ${EVENT_TYPES.map((t) => html`<option value=${t} ?selected=${this.type === t}>${EVENT_LABEL[t]}</option>`)}
          </select>
        </div>
      </header>
      <div class="ev-list" data-timeline>
        ${days.length
          ? days.map(([day, evs]) => html`<div class="timeline-day">${day}</div>${evs.map((e) => eventRow(e, f.nodes, f.deployments, this.hostName))}`)
          : html`<p class="muted small ev-none">${this.listed ? 'No events match these filters.' : 'Reading the timeline…'}</p>`}
      </div>
      ${this.more ? html`<div class="ev-more"><button type="button" class="btn btn-sm" ?disabled=${this.older} data-ev-older @click=${() => void this.loadOlder()}>${this.older ? 'Reading…' : 'Older events'}</button></div>` : nothing}
    </section>`;
  }

  // ---- the rules --------------------------------------------------------------------------------
  private edit(r: FleetRule | null): void {
    this.editing = r?.id ?? null;
    this.draft = r ? draftOf(r) : null;
    this.ruleMsg = null;
  }

  private async save(r: FleetRule, patch: Parameters<typeof fleet.editRule>[1]): Promise<void> {
    this.saving = true;
    this.ruleMsg = null;
    try {
      const got = await fleet.editRule(r.id, patch);
      this.rules = this.rules.map((x) => (x.id === got.id ? got : x));
      this.ruleMsg = { id: r.id, text: 'Saved. The rule runs on the next sample.', err: false };
      if (this.editing === r.id) this.edit(null);
      forgetRules();
      refreshFleet();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.ruleMsg = { id: r.id, text: d.status === 401 ? 'The controller wants its admin password first.' : d.message, err: true };
    } finally {
      this.saving = false;
    }
  }

  private submit(r: FleetRule): void {
    const d = this.draft!;
    const p = rulePatch(r, d);
    if (p.error) {
      this.ruleMsg = { id: r.id, text: p.error, err: true };
      return;
    }
    if (!p.patch) return this.edit(null);
    void this.save(r, p.patch);
  }

  private editor(r: FleetRule): TemplateResult {
    const f = this.fleetState!;
    const d = this.draft!;
    const set = (patch: Partial<RuleDraft>) => (this.draft = { ...d, ...patch });
    const locked = needsSignIn();
    return html`<form class="rule-form" data-rule-form=${r.id} @submit=${(e: Event) => (e.preventDefault(), this.submit(r))}>
      <label class="rf-field"><span>Threshold${r.unit ? ` (${r.unit})` : ''}</span>
        <input class="wm-in" type="text" inputmode="decimal" name="threshold" .value=${d.threshold} ?disabled=${!thresholdEditable(r)} placeholder=${thresholdEditable(r) ? '' : 'none'} @input=${(e: Event) => set({ threshold: (e.target as HTMLInputElement).value })} />
        ${thresholdEditable(r) ? nothing : html`<span class="xs muted">This rule has no threshold: it fires on what it watches.</span>`}
      </label>
      <label class="rf-field"><span>Holds for (s)</span>
        <input class="wm-in" type="text" inputmode="numeric" name="for_s" .value=${d.for_s} @input=${(e: Event) => set({ for_s: (e.target as HTMLInputElement).value })} />
        <span class="xs muted">how long the condition holds before it fires</span>
      </label>
      <label class="rf-field"><span>Scope</span>
        <select class="wm-in" name="scope" ?disabled=${!scopeEditable(r)} @change=${(e: Event) => set({ scope: (e.target as HTMLSelectElement).value })}>
          ${scopeOptions(r, f.nodes).map((o) => html`<option value=${o.value} ?selected=${o.value === d.scope}>${o.label}</option>`)}
        </select>
        ${scopeEditable(r) ? nothing : html`<span class="xs muted">About the controller’s machine: its scope stays.</span>`}
      </label>
      <fieldset class="rf-field rf-notify"><legend>Notify</legend>
        <label class="check"><input type="checkbox" name="app" .checked=${d.app} @change=${(e: Event) => set({ app: (e.target as HTMLInputElement).checked })} /> in the app</label>
        <label class="check"><input type="checkbox" name="system" .checked=${d.system} @change=${(e: Event) => set({ system: (e.target as HTMLInputElement).checked })} /> system notification</label>
      </fieldset>
      <label class="check rf-on"><input type="checkbox" name="enabled" .checked=${d.enabled} @change=${(e: Event) => set({ enabled: (e.target as HTMLInputElement).checked })} /> on</label>
      <div class="rf-acts">
        <button type="submit" class="btn btn-primary btn-sm" ?disabled=${this.saving || locked}>${this.saving ? 'Saving…' : 'Save'}</button>
        <button type="button" class="btn btn-ghost btn-sm" @click=${() => this.edit(null)}>Cancel</button>
        ${this.ruleMsg?.id === r.id ? html`<span class="small ${this.ruleMsg.err ? 'err-ink' : 'ok-ink'}" role=${this.ruleMsg.err ? 'alert' : 'status'}>${this.ruleMsg.text}</span>` : nothing}
      </div>
    </form>`;
  }

  private rulesPanel(): TemplateResult {
    const f = this.fleetState!;
    const locked = needsSignIn();
    return html`<section class="panel" data-panel="rules">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Alert rules</h2><p class="panel-sub">per metric, for all Sparks or one · run on every sample</p></div></header>
      ${this.rules.length
        ? html`<div class="rules" role="table" aria-label="Alert rules">
            <div class="rule-row is-head" role="row"><span role="columnheader">On</span><span role="columnheader">Rule</span><span role="columnheader">When</span><span role="columnheader">Scope</span><span></span></div>
            ${this.rules.map(
              (r) => html`<div class="rule-row ${r.enabled ? '' : 'is-off'}" role="row" data-rule=${r.id}>
                  <span role="cell"><label class="switch" title=${locked ? 'Sign in first' : r.enabled ? 'Turn off' : 'Turn on'}><input type="checkbox" .checked=${r.enabled} ?disabled=${locked || this.saving} aria-label=${`${r.name} on`} @change=${(e: Event) => void this.save(r, { enabled: (e.target as HTMLInputElement).checked })} /></label></span>
                  <span role="cell"><b class="small">${r.name}</b><span class="xs muted">${r.severity === 'err' ? 'serious' : 'warning'} · ${r.notify.length ? r.notify.map((x) => (x === 'app' ? 'in the app' : 'system notification')).join(' + ') : 'notifies nowhere'}</span></span>
                  <span role="cell" class="num small">${r.condition}</span>
                  <span role="cell" class="small">${r.scope.startsWith('node:') && f.nodes.some((n) => `node:${n.id}` === r.scope) ? nodeChip(f.nodes.find((n) => `node:${n.id}` === r.scope)!, { status: false }) : scopeLabel(r.scope, f.nodes)}</span>
                  <span role="cell"><button type="button" class="btn btn-ghost btn-sm" aria-expanded=${this.editing === r.id} @click=${() => this.edit(this.editing === r.id ? null : r)}>${this.editing === r.id ? 'Close' : 'Edit'}</button></span>
                </div>
                ${this.editing === r.id ? this.editor(r) : this.ruleMsg?.id === r.id ? html`<p class="small ${this.ruleMsg.err ? 'err-ink' : 'ok-ink'} rule-msg" role="status">${this.ruleMsg.text}</p>` : nothing}`,
            )}
          </div>`
        : html`<p class="muted small">Reading the rules…</p>`}
    </section>`;
  }

  // ---- notifications ----------------------------------------------------------------------------
  private async toggleNotify(on: boolean): Promise<void> {
    if (on && this.perm === 'default') this.perm = await Notification.requestPermission().catch(() => 'default' as const);
    this.notify = on && this.perm !== 'denied' && this.perm !== 'unsupported';
    setSystemOn(this.notify);
    refreshContext();
  }

  private notifyPanel(): TemplateResult {
    const sys = this.rules.filter((r) => r.enabled && r.notify.includes('system'));
    const can = this.perm !== 'unsupported' && this.perm !== 'denied';
    return html`<section class="panel" data-panel="notify">
      <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Notifications</h2><p class="panel-sub">on this device</p></div></header>
      <dl class="kv">
        <div><dt>in the app</dt><dd>always: this page, the right sidebar and the top bar’s readout</dd></div>
        <div><dt>system</dt><dd>
          <label class="check"><input type="checkbox" data-notify .checked=${this.notify && this.perm === 'granted'} ?disabled=${!can} @change=${(e: Event) => void this.toggleNotify((e.target as HTMLInputElement).checked)} /> show a system notification when an alert fires</label>
          <span class="xs muted" data-perm=${this.perm}>${PERM_WORDS[this.perm]}</span>
        </dd></div>
        <div><dt>rules that use it</dt><dd class="small">${sys.length ? sys.map((r) => r.name).join(', ') : 'none: turn on “system notification” in a rule'}</dd></div>
      </dl>
      <p class="xs muted">While TandemApp is open in a tab, an alert of these rules shows as a notification. The controller sends nothing out itself.</p>
    </section>`;
  }

  protected context(): ContextContent {
    const f = this.fleetState;
    const sec = (t: string, body: TemplateResult) => html`<section class="csec"><div class="csec-h"><span class="eyebrow">${t}</span></div>${body}</section>`;
    if (!f) return { title: 'Firing now', body: html`<p class="muted small">The alerts show here once TandemApp’s controller answers on this server.</p>` };
    return {
      title: 'Firing now',
      body: html`${sec('Active alerts', this.alertsSection(true))}
        ${sec('Notifications', html`<dl class="kv"><div><dt>in the app</dt><dd>always</dd></div><div><dt>system</dt><dd>${this.notify && this.perm === 'granted' ? 'on' : 'off'} · ${PERM_WORDS[this.perm]}</dd></div></dl>`)}`,
    };
  }

  render() {
    const f = this.fleetState;
    const head = html`<header class="page-head" data-page="events"><div class="eyebrow">Monitor</div><h1>Events &amp; Alerts</h1><p class="page-sub">Alerts show in the app always, and as system notifications on a device that allows them.</p></header>`;
    if (!f) return html`${head}<div class="skeleton" style="height:200px"></div>`;
    const writes = activeAlerts(f).length > 0 || this.rules.length > 0;
    return html`${head}
      ${needsSignIn() && writes ? signInCard('Acknowledging alerts and editing rules', activeAlerts(f).length > 0) : nothing}
      <section class="mon-sec" data-panel="alerts"><div class="mon-sec-h"><span class="eyebrow">Active alerts</span></div>${this.alertsSection()}</section>
      <div class="g-wide">${this.timeline()}<div class="mon-col">${this.rulesPanel()}${this.notifyPanel()}</div></div>`;
  }
}
