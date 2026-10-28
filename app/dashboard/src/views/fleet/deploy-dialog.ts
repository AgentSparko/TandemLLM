// <qse-deploy-action>: a deployment's start, stop or move (VIS-252) as the controller plans it, and its
// edit, record and delete. Start, stop and move read the plan first (GET .../actions/{action}: what it
// does on each Spark, what stays, or why not here); a move picks the Spark it goes to (and from, for a
// Replicas member) and reads the plan again for it. The action runs only with the plan's own confirm
// (stop and move; 428 without, 409 when it is spent or stale, then the plan is read again), and its
// job is followed until it ends: each Spark's step and the log. Edit changes the record (its name, API
// id, context, a Replicas' members, its conversation key); Record makes a record of a derived Single so
// it can be named, scaled or moved; Delete removes the record and says that its engines keep running.
// A write the controller wants its admin password for shows the sign-in in place.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, type DeploymentAction, type DeploymentJob, type DeploymentPatch, type DeploymentPlan, type FleetDeployment, type FleetNode } from '../../api/fleet';
import { refreshFleet } from '../../lib/fleet-state';
import { TYPE_WORD } from '../../lib/models';
import { LightElement } from '../../ui/base';
import { nodeChip } from '../../ui/node-chip';
import { clock, signInCard } from '../monitor/bits';
import { checkSession } from '../monitor/data';

const FOLLOW_MS = 1500;
type Kind = DeploymentAction | 'edit' | 'record' | 'delete';

const PHASE_WORD: Record<DeploymentJob['steps'][number]['phase'], string> = {
  start: 'starting its engine',
  warm: 'warming up: one token',
  serving: 'serving',
  stop: 'stopping its engine',
  stopped: 'stopped',
  kept: 'kept running (not managed by TandemNode)',
  failed: 'failed',
};

interface Draft {
  name: string;
  api_id: string;
  ctx: string;
  members: string[];
  affinity: 'root' | 'own';
}

@customElement('qse-deploy-action')
export class QseDeployAction extends LightElement {
  @state() private dep: FleetDeployment | null = null;
  @state() private nodes: FleetNode[] = [];
  @state() private kind: Kind | null = null;
  @state() private plan: DeploymentPlan | null = null;
  @state() private to = '';
  @state() private from = '';
  @state() private draft: Draft | null = null;
  @state() private busy = false;
  @state() private error = '';
  @state() private signIn = false;
  @state() private job: DeploymentJob | null = null;
  @state() private done = '';
  private follow: ReturnType<typeof setTimeout> | null = null;
  private get dialog(): HTMLDialogElement | null {
    return this.querySelector('dialog');
  }

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
    if (!this.signIn) return;
    this.signIn = false;
    if (this.isPlanned()) void this.read();
  };

  private isPlanned(): boolean {
    return this.kind === 'start' || this.kind === 'stop' || this.kind === 'move';
  }

  /** Opens the dialog on one deployment and what to do with it. */
  open(dep: FleetDeployment, kind: Kind, nodes: FleetNode[]): void {
    this.stopFollow();
    const first = dep.member_states?.[0]?.node ?? dep.members[0] ?? '';
    Object.assign(this, { dep, kind, nodes, plan: null, to: '', from: first, busy: false, error: '', signIn: false, job: null, done: '' });
    this.draft =
      kind === 'edit' || kind === 'record'
        ? { name: dep.name, api_id: dep.api_id, ctx: dep.ctx ? String(dep.ctx) : '', members: [...dep.members], affinity: dep.affinity ?? 'root' }
        : null;
    void this.updateComplete.then(() => {
      if (this.dialog && !this.dialog.open) this.dialog.showModal();
    });
    if (kind === 'start' || kind === 'stop') void this.read();
  }

  private close(): void {
    this.stopFollow();
    if (this.dialog?.open) this.dialog.close();
    this.kind = null;
  }
  private stopFollow(): void {
    if (this.follow) clearTimeout(this.follow);
    this.follow = null;
  }

  private async read(): Promise<void> {
    const d = this.dep;
    const k = this.kind;
    if (!d || !k || !this.isPlanned()) return;
    if (k === 'move' && !this.to) {
      this.plan = null;
      return;
    }
    try {
      const p = await fleet.deploymentPlan(d.id, k as DeploymentAction, k === 'move' ? { from: this.from, to: this.to } : {});
      if (this.kind !== k || this.dep !== d) return;
      this.plan = p;
      this.error = '';
    } catch (e) {
      this.error = describeError(e).message;
    }
  }

  private async failed(e: unknown): Promise<void> {
    const d = describeError(e);
    if (d.status === 401) {
      await checkSession(true);
      this.signIn = true;
    } else this.error = d.message;
  }

  private async run(): Promise<void> {
    const d = this.dep;
    const p = this.plan;
    if (!d || !p || this.busy) return;
    this.busy = true;
    this.error = '';
    try {
      const r = await fleet.deploymentAct(d.id, p.action, { ...(p.confirm ? { confirm: p.confirm } : {}), ...(p.from ? { from: p.from } : {}), ...(p.to ? { to: p.to } : {}), ...(p.engine ? { engine: p.engine } : {}) });
      this.job = r.job;
      refreshFleet();
      this.dispatchEvent(new CustomEvent('qse-deploy-changed', { bubbles: true }));
      this.followJob();
    } catch (e) {
      await this.failed(e);
      // a confirm is single use: a refusal needs a new plan
      if (!this.signIn) void this.read();
    } finally {
      this.busy = false;
    }
  }

  private followJob(): void {
    this.stopFollow();
    const j = this.job;
    if (!j || j.state !== 'running') return;
    this.follow = setTimeout(async () => {
      try {
        const v = await fleet.deploymentJob(j.id);
        this.job = v.job;
      } catch {
        /* read again at the next tick */
      }
      refreshFleet();
      this.dispatchEvent(new CustomEvent('qse-deploy-changed', { bubbles: true }));
      if (this.dialog?.open) this.followJob();
    }, FOLLOW_MS);
  }

  /** what the edit form changes: only the fields that differ from the record */
  private patch(): DeploymentPatch | { error: string } {
    const d = this.dep!;
    const x = this.draft!;
    const out: DeploymentPatch = {};
    if (x.name.trim() && x.name.trim() !== d.name) out.name = x.name.trim();
    if (x.api_id.trim() && x.api_id.trim() !== d.api_id) out.api_id = x.api_id.trim();
    if (x.ctx.trim()) {
      const n = Number(x.ctx.replace(/[\s,_]/g, ''));
      if (!Number.isInteger(n) || n < 1) return { error: 'The context is a whole number of tokens.' };
      if (n !== d.ctx) out.ctx = n;
    }
    if (d.type === 'replicas') {
      if (x.members.length < 2) return { error: 'Replicas need two or more Sparks.' };
      if (x.members.join() !== d.members.join()) out.members = x.members;
      if (x.affinity !== (d.affinity ?? 'root')) out.affinity = x.affinity;
    }
    return out;
  }

  private async save(): Promise<void> {
    const d = this.dep;
    if (!d || !this.draft || this.busy) return;
    this.error = '';
    this.busy = true;
    try {
      if (this.kind === 'record') {
        const x = this.draft;
        const r = await fleet.createDeployment({ model: d.model, type: 'single', members: d.members, ...(x.name.trim() ? { name: x.name.trim() } : {}), ...(x.api_id.trim() ? { api_id: x.api_id.trim() } : {}), ...(d.pack ? { pack: d.pack } : {}), ...(Number(x.ctx) > 0 ? { ctx: Number(x.ctx) } : {}) });
        this.done = `${r.name} is a record now: TandemApp routes ${r.api_id} to it.`;
      } else if (this.kind === 'delete') {
        await fleet.deleteDeployment(d.id);
        this.done = `${d.name}'s record is gone; its engines keep running.`;
      } else {
        const p = this.patch();
        if ('error' in p) {
          this.error = p.error;
          return;
        }
        if (!Object.keys(p).length) {
          this.close();
          return;
        }
        const r = await fleet.patchDeployment(d.id, p);
        this.done = `${r.name} saved.`;
      }
      refreshFleet();
      this.dispatchEvent(new CustomEvent('qse-deploy-changed', { bubbles: true }));
    } catch (e) {
      await this.failed(e);
    } finally {
      this.busy = false;
    }
  }

  private list(title: string, items: string[], cls: string): TemplateResult | typeof nothing {
    if (!items.length) return nothing;
    return html`<section class="act-sec ${cls}"><h3 class="eyebrow">${title}</h3><ul>${items.map((x) => html`<li>${x}</li>`)}</ul></section>`;
  }

  private chipOf(id: string): TemplateResult {
    const n = this.nodes.find((x) => x.id === id);
    return n ? nodeChip(n) : html`<span class="small">${id}</span>`;
  }

  private jobView(j: DeploymentJob): TemplateResult {
    const tone = j.state === 'done' ? 'ok-ink' : j.state === 'failed' ? 'err-ink' : 'muted';
    return html`<section class="dj" data-dep-job=${j.state}>
      <p class="small ${tone}">${j.state === 'running' ? `${j.action} runs…` : j.state === 'done' ? `✓ ${j.action} done` : `✕ ${j.action} failed: ${j.error?.message ?? ''}`}</p>
      <ul class="dj-steps">${j.steps.map((s) => html`<li data-phase=${s.phase}>${this.chipOf(s.node)} <span class="small ${s.phase === 'failed' ? 'err-ink' : s.phase === 'serving' || s.phase === 'stopped' ? 'ok-ink' : 'muted'}">${PHASE_WORD[s.phase]}</span>${s.text ? html`<span class="xs faint">${s.text}</span>` : nothing}</li>`)}</ul>
      ${j.log.length ? html`<details class="dj-log"><summary class="xs muted">Log (${j.log.length})</summary><pre class="xs">${j.log.map((l) => `${clock(l.t)} ${l.text}`).join('\n')}</pre></details>` : nothing}
    </section>`;
  }

  private movePick(): TemplateResult {
    const d = this.dep!;
    const others = this.nodes.filter((n) => !d.members.includes(n.id));
    const set = (k: 'to' | 'from', v: string) => {
      this[k] = v;
      this.plan = null;
      void this.read();
    };
    return html`<div class="dd-pick">
      ${d.members.length > 1
        ? html`<label class="field"><span>From</span><select data-move-from @change=${(e: Event) => set('from', (e.target as HTMLSelectElement).value)}>${d.members.map((m) => html`<option value=${m} ?selected=${m === this.from}>${this.nodes.find((n) => n.id === m)?.alias ?? m}</option>`)}</select></label>`
        : nothing}
      <label class="field"><span>To</span><select data-move-to @change=${(e: Event) => set('to', (e.target as HTMLSelectElement).value)}>
        <option value="" ?selected=${!this.to}>Pick a Spark…</option>
        ${others.map((n) => html`<option value=${n.id} ?selected=${n.id === this.to} ?disabled=${n.status === 'off'}>${n.alias}${n.status === 'off' ? ' (off)' : ''}</option>`)}
      </select></label>
      ${others.length ? nothing : html`<p class="small muted">Every Spark serves ${d.name} already: no Spark to move it to.</p>`}
    </div>`;
  }

  private form(): TemplateResult {
    const d = this.dep!;
    const x = this.draft!;
    const set = (k: keyof Draft, v: string) => (this.draft = { ...x, [k]: v });
    const toggle = (id: string) => (this.draft = { ...x, members: x.members.includes(id) ? x.members.filter((m) => m !== id) : [...x.members, id] });
    return html`<div class="dd-form">
      <label class="field"><span>Name</span><input data-dd-name .value=${x.name} @input=${(e: Event) => set('name', (e.target as HTMLInputElement).value)} /></label>
      <label class="field"><span>Model id in the API</span><input data-dd-api class="num" .value=${x.api_id} spellcheck="false" @input=${(e: Event) => set('api_id', (e.target as HTMLInputElement).value)} /><span class="xs muted">what clients send as <code>model</code>; /v1/models lists it once</span></label>
      <label class="field"><span>Context, tokens</span><input data-dd-ctx class="num" inputmode="numeric" .value=${x.ctx} @input=${(e: Event) => set('ctx', (e.target as HTMLInputElement).value)} /></label>
      ${d.type === 'replicas'
        ? html`<fieldset class="field dd-members"><legend>Members</legend>${this.nodes.map(
              (n) => html`<label class="dd-member"><input type="checkbox" data-dd-member=${n.id} .checked=${x.members.includes(n.id)} @change=${() => toggle(n.id)} />${nodeChip(n)}</label>`,
            )}<span class="xs muted">a new member joins once its engine serves and is warm; one taken out finishes what it runs</span></fieldset>
            <label class="field"><span>Conversation key</span><select data-dd-affinity @change=${(e: Event) => set('affinity', (e.target as HTMLSelectElement).value)}><option value="root" ?selected=${x.affinity === 'root'}>a chat and its workers on one member</option><option value="own" ?selected=${x.affinity === 'own'}>each conversation on its own</option></select></label>`
        : nothing}
      ${this.kind === 'record' ? html`<p class="xs muted">A record of ${TYPE_WORD[d.type]} ${d.name} on ${d.members.map((m) => this.nodes.find((n) => n.id === m)?.alias ?? m).join(', ')}. Its engine keeps running as it does; TandemNode starts and stops it only when it started it.</p>` : nothing}
    </div>`;
  }

  private body(): TemplateResult {
    const d = this.dep!;
    if (this.done) return html`<p class="act-done ok-ink" data-act-result>✓ ${this.done}</p>`;
    if (this.kind === 'delete')
      return html`${this.list('What happens', [`${d.name}'s record is removed: TandemApp stops routing ${d.api_id} as one deployment`, 'what its engines serve shows again as Singles derived from them'], 'is-impact')}
        ${this.list('Stays', ['its engines keep running on every member', 'the weights on every Spark'], 'is-keeps')}`;
    if (this.kind === 'edit' || this.kind === 'record') return this.form();
    const p = this.plan;
    return html`${this.kind === 'move' ? this.movePick() : nothing}
      ${this.job ? this.jobView(this.job) : nothing}
      ${!this.job && p
        ? html`${p.blocked ? html`<p class="act-blocked warn-ink" data-act-blocked><span aria-hidden="true">⚠</span> ${p.blocked}</p>` : nothing}
            ${this.list('What happens', p.impact, 'is-impact')}
            ${this.list('Stays', p.keeps, 'is-keeps')}
            ${p.job ? html`<p class="small muted">Last job: ${p.job.action} ${p.job.state}${p.job.ended_at ? ` · ${clock(p.job.ended_at)}` : ''}${p.job.error ? ` · ${p.job.error.message}` : ''}</p>` : nothing}`
        : !this.job && (this.kind !== 'move' || this.to)
          ? html`<div class="skeleton" style="height:96px"></div>`
          : nothing}`;
  }

  private head(): string {
    const d = this.dep!;
    if (this.kind === 'edit') return `Edit ${d.name}`;
    if (this.kind === 'record') return `Record ${d.name}`;
    if (this.kind === 'delete') return `Delete ${d.name}`;
    return this.plan?.title ?? `${this.kind === 'move' ? 'Move' : this.kind === 'stop' ? 'Stop' : 'Start'} ${d.name}`;
  }

  render() {
    const d = this.dep;
    const k = this.kind;
    const p = this.plan;
    const planned = this.isPlanned();
    const danger = (planned && !!p?.destructive && p.allowed) || k === 'delete';
    const ready = planned ? !!p && p.allowed && !this.job : !this.done;
    const label = k === 'delete' ? 'Delete the record' : k === 'record' ? 'Record it' : k === 'edit' ? 'Save' : (p?.title.split(' ')[0] ?? 'Go');
    const finished = !!this.done || (this.job && this.job.state !== 'running');
    return html`<dialog class="act-dialog dd-dialog ${danger ? 'is-danger' : ''}" aria-labelledby="dd-title" data-dep-act=${k ?? nothing} @close=${() => this.close()} @cancel=${() => this.close()}>
      ${d && k
        ? html`<form method="dialog" class="act-body" @submit=${(e: Event) => (e.preventDefault(), ready && void (planned ? this.run() : this.save()))}>
            <header class="act-head"><h2 id="dd-title">${this.head()}</h2><span class="tag dep-type is-${d.type}">${TYPE_WORD[d.type]}</span></header>
            ${this.body()}
            ${this.signIn ? signInCard('Deployments') : nothing}
            ${this.error ? html`<p class="small err-ink" data-act-error>${this.error}</p>` : nothing}
            <footer class="act-foot">
              <button type="button" class="btn btn-ghost" data-act-cancel @click=${() => this.close()}>${finished || (p && !p.allowed) ? 'Close' : 'Cancel'}</button>
              ${ready && !finished ? html`<button type="submit" class="btn ${danger ? 'btn-danger-solid' : 'btn-primary'}" data-act-go ?disabled=${this.busy}>${this.busy ? 'Working…' : label}</button>` : nothing}
            </footer>
          </form>`
        : nothing}
    </dialog>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-deploy-action': QseDeployAction;
  }
}
