// <qse-serve>: Fleet → Models → Serve a model at #/models/place (VIS-252, concept C6 and §8.7, the v3 prototype's Serve
// page): placement first. 1 Model: a Setup Guide model, one of its packs, the context a lane holds and
// the lanes, from the registry a Spark's Setup Guide answers through the controller. 2 Placement: POST
// /v1/fleet/placement plans every Spark from its own estimate: a memory bar per Spark from the plan's
// parts (what runs there now, the new engine's weights, KV and fixed parts, the budget marked), what
// it needs and has free, and why it does not fit in words; the types Single, Replicas, Split and Role
// each allowed or refused with the reason, a Split planned only (not built yet, ENG-307); picking
// Sparks plans those, in pick order for a Split. Each Spark opens its own Setup Guide (?node=, through
// the controller's relay) to set it up first. 3 Serve: the name and API id, a review, then POST
// /v1/fleet/deployments and its start (actions/start), whose job is followed Spark by Spark. The
// choices live in the hash (?model=&pack=&ctx=&slots=&step=), so a reload keeps them.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError, request } from '../../api/client';
import { fleet, type DeploymentJob, type DeploymentType, type FleetDeployment, type FleetNode, type Placement, type PlacementNode } from '../../api/fleet';
import type { SetupModel, SetupModelsList } from '../../api/types';
import { refreshContext, registerContext, type ContextContent } from '../../lib/context';
import { onFleet, refreshFleet, type FleetState } from '../../lib/fleet-state';
import { membersFor, newDeployment, pickType, placeBar, SOURCE_WORD, SPLIT_NOT_BUILT, TYPE_HELP, TYPE_WORD } from '../../lib/models';
import { onRoute, parseHash, setParams } from '../../lib/router';
import { LightElement } from '../../ui/base';
import { nodeChip } from '../../ui/node-chip';
import { clock, signInCard } from '../monitor/bits';
import { checkSession } from '../monitor/data';
import { modelTabs } from './models';

const STEPS = ['model', 'placement', 'serve'] as const;
type Step = (typeof STEPS)[number];
const STEP_LABEL: Record<Step, string> = { model: 'Model', placement: 'Placement', serve: 'Serve' };
const TYPES: DeploymentType[] = ['single', 'replicas', 'split', 'role'];
const FOLLOW_MS = 1500;

/** a Spark's Setup Guide through the controller (?node=), at a model when one is picked */
export const guideHref = (node: string, model?: string) => `#/models/serve${model ? `/${encodeURIComponent(model)}/overview` : ''}?node=${encodeURIComponent(node)}`;

@customElement('qse-serve')
export class QseServe extends LightElement {
  @state() private f: FleetState | null = null;
  @state() private list: SetupModelsList | null = null;
  @state() private model: SetupModel | null = null;
  @state() private regErr = '';
  @state() private plan: Placement | null = null;
  @state() private pickPlan: Placement | null = null;
  @state() private planErr = '';
  @state() private planning = false;
  @state() private picked: string[] = [];
  @state() private type: DeploymentType | null = null;
  @state() private name = '';
  @state() private apiId = '';
  @state() private roleWord = '';
  @state() private roleOf = '';
  @state() private busy = false;
  @state() private err = '';
  @state() private signIn = false;
  @state() private made: FleetDeployment | null = null;
  @state() private job: DeploymentJob | null = null;
  @state() private startErr = '';
  private follow: ReturnType<typeof setTimeout> | null = null;
  private unsub: (() => void)[] = [];
  private seq = 0;

  connectedCallback(): void {
    super.connectedCallback();
    this.unsub.push(
      onFleet((f) => {
        const first = !this.f && f;
        this.f = f;
        if (first) void this.loadList();
        refreshContext();
      }),
      onRoute(() => this.requestUpdate()),
      registerContext('models', () => this.context()),
    );
  }
  disconnectedCallback(): void {
    this.unsub.forEach((u) => u());
    this.unsub = [];
    if (this.follow) clearTimeout(this.follow);
    super.disconnectedCallback();
  }

  // ---- the choices in the hash ---------------------------------------------------------------------
  private get q(): URLSearchParams {
    return parseHash().params;
  }
  private get step(): Step {
    const s = this.q.get('step');
    return (STEPS as readonly string[]).includes(s ?? '') ? (s as Step) : 'model';
  }
  private go(step: Step): void {
    setParams({ step: step === 'model' ? null : step });
    if (step === 'placement') void this.place();
    window.scrollTo?.({ top: 0 });
  }
  private num(k: string, d: number): number {
    const v = Number(this.q.get(k));
    return Number.isInteger(v) && v > 0 ? v : d;
  }

  /** the Spark whose Setup Guide answers the registry: this controller's own, else the first up */
  private get registry(): FleetNode | null {
    const ns = this.f?.nodes ?? [];
    return ns.find((n) => n.pairing.state === 'local' && n.status !== 'off') ?? ns.find((n) => n.status !== 'off') ?? null;
  }
  private setupPath(n: FleetNode, rest: string): string {
    return `/v1/fleet/nodes/${encodeURIComponent(n.id)}/setup/${rest}`;
  }

  private async loadList(): Promise<void> {
    const n = this.registry;
    if (!n) {
      this.regErr = 'No Spark answers: the models come from a Spark’s Setup Guide.';
      return;
    }
    try {
      this.list = await request<SetupModelsList>(this.setupPath(n, 'models'));
      this.regErr = '';
      const id = this.q.get('model') ?? this.list.models.find((m) => m.recommended)?.id ?? this.list.models[0]?.id;
      if (id) await this.loadModel(id, !this.q.get('model'));
      if (this.step === 'placement' || this.step === 'serve') void this.place();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.regErr = `${n.alias}’s Setup Guide did not answer: ${d.message}`;
      // a placement from the hash needs no registry: the planner has its own
      if (this.step !== 'model') void this.place();
    }
  }

  private async loadModel(id: string, fresh: boolean): Promise<void> {
    const n = this.registry;
    if (!n) return;
    const s = ++this.seq;
    try {
      const m = await request<SetupModel>(this.setupPath(n, `models/${encodeURIComponent(id)}`));
      if (s !== this.seq) return;
      this.model = m;
      const rec = m.profiles.find((p) => p.recommended) ?? m.profiles[0];
      const pack = this.q.get('pack');
      setParams({
        model: m.id,
        pack: fresh || !pack || !m.profiles.some((p) => p.id === pack) ? (rec?.id ?? null) : pack,
        ctx: fresh ? String(m.parallelism.window.default) : this.q.get('ctx'),
        slots: fresh ? String(m.parallelism.slots.default) : this.q.get('slots'),
      });
      this.name ||= m.name;
      this.apiId ||= m.id;
    } catch (e) {
      this.regErr = describeError(e).message;
    }
  }

  private changed(patch: Record<string, string | null>): void {
    setParams(patch);
    this.plan = null;
    this.pickPlan = null;
    this.type = null;
  }

  // ---- placement -----------------------------------------------------------------------------------
  private async place(): Promise<void> {
    const model = this.q.get('model');
    const pack = this.q.get('pack');
    if (!model || !pack) return;
    const s = ++this.seq;
    this.planning = true;
    this.planErr = '';
    try {
      // every Spark's row from the plan of all; the types from the plan of the Sparks picked
      const body = { model, pack, ctx: this.num('ctx', 32768), slots: this.num('slots', 1) };
      const [p, mine] = await Promise.all([fleet.placement(body), this.picked.length ? fleet.placement({ ...body, nodes: this.picked }) : Promise.resolve(null)]);
      if (s !== this.seq) return;
      this.plan = p;
      this.pickPlan = mine;
      this.type = pickType(mine ?? p, this.type);
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.planErr = d.message;
    } finally {
      if (s === this.seq) this.planning = false;
    }
  }

  private pick(id: string): void {
    this.picked = this.picked.includes(id) ? this.picked.filter((x) => x !== id) : [...this.picked, id];
    void this.place();
  }

  /** the plan the types come from: the Sparks picked, else all */
  private get typed(): Placement | null {
    return this.pickPlan ?? this.plan;
  }

  private get members(): string[] {
    return this.typed && this.type ? membersFor(this.typed, this.type, this.picked) : [];
  }

  // ---- serve ---------------------------------------------------------------------------------------
  private async serve(): Promise<void> {
    const p = this.typed;
    const t = this.type;
    if (!p || !t || this.busy) return;
    this.busy = true;
    this.err = '';
    this.startErr = '';
    this.signIn = false;
    try {
      const body = newDeployment(p, t, this.members, { name: this.name, api_id: this.apiId, role: this.roleWord, role_of: this.roleOf });
      this.made = await fleet.createDeployment(body);
      refreshFleet();
      if (t === 'split') return;
      try {
        const r = await fleet.deploymentAct(this.made.id, 'start');
        this.job = r.job;
        this.followJob();
      } catch (e) {
        this.startErr = describeError(e).message;
      }
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) {
        await checkSession(true);
        this.signIn = true;
      } else this.err = d.message;
    } finally {
      this.busy = false;
    }
  }

  private followJob(): void {
    if (this.follow) clearTimeout(this.follow);
    const j = this.job;
    if (!j || j.state !== 'running') return;
    this.follow = setTimeout(async () => {
      try {
        this.job = (await fleet.deploymentJob(j.id)).job;
      } catch {
        /* the next tick */
      }
      refreshFleet();
      this.followJob();
    }, FOLLOW_MS);
  }

  private context(): ContextContent {
    const p = this.typed;
    return {
      title: 'Serve a model',
      body: html`<section class="csec"><div class="csec-h"><span class="eyebrow">How placement plans</span></div><p class="small">Each Spark is planned from its own Setup Guide estimate: its device, what is free there now and what it serves already. A Spark that serves something keeps it; the new engine must fit beside it.</p></section>
        ${p ? html`<section class="csec"><div class="csec-h"><span class="eyebrow">This plan</span></div><ul class="sp-ctx-list small">${p.types.map((t) => html`<li><b>${TYPE_WORD[t.type]}</b> <span class=${t.allowed ? 'ok-ink' : 'err-ink'}>${t.allowed ? '✓' : '✕'}</span> <span class="muted">${t.allowed ? (t.built ? TYPE_HELP[t.type] : SPLIT_NOT_BUILT) : t.reason}</span></li>`)}</ul></section>` : nothing}
        <section class="csec"><div class="csec-h"><span class="eyebrow">Setup Guide per Spark</span></div><div class="ctx-nodes">${(this.f?.nodes ?? []).map((n) => html`<a class="mini-now" href=${guideHref(n.id, this.q.get('model') ?? undefined)} data-guide=${n.id}>${nodeChip(n)}<span class="small muted">Setup Guide</span></a>`)}</div></section>`,
    };
  }

  // ---- render --------------------------------------------------------------------------------------
  private stepper(): TemplateResult {
    const cur = STEPS.indexOf(this.step);
    return html`<ol class="sv-steps" aria-label="Serve a model">${STEPS.map(
      (s, i) => html`<li class=${i === cur ? 'is-on' : i < cur ? 'is-done' : ''}><button type="button" ?disabled=${i > cur && !(i === 1 && this.q.get('pack')) && !(i === 2 && this.type)} aria-current=${i === cur ? 'step' : 'false'} data-sv-step=${s} @click=${() => this.go(s)}><span class="n num">${i < cur ? '✓' : i + 1}</span>${STEP_LABEL[s]}</button></li>`,
    )}</ol>`;
  }

  private modelStep(): TemplateResult {
    const l = this.list;
    const m = this.model;
    const reg = this.registry;
    if (this.regErr) return html`<section class="panel"><p class="small err-ink" data-sv-error>${this.regErr}</p></section>`;
    if (!l || !m) return html`<div class="skeleton" style="height:200px"></div>`;
    const pack = m.profiles.find((p) => p.id === this.q.get('pack'));
    const w = m.parallelism.window;
    const sl = m.parallelism.slots;
    return html`<section class="panel sv-model" data-panel="sv-model">
        <div class="sv-grid">
          <label class="field"><span>Model</span><select data-sv-model @change=${(e: Event) => ((this.plan = null), (this.pickPlan = null), (this.picked = []), (this.type = null), (this.name = ''), (this.apiId = ''), void this.loadModel((e.target as HTMLSelectElement).value, true))}>${l.models.map((x) => html`<option value=${x.id} ?selected=${x.id === m.id}>${x.name} · ${x.params}</option>`)}</select></label>
          <label class="field"><span>Pack</span><select data-sv-pack @change=${(e: Event) => this.changed({ pack: (e.target as HTMLSelectElement).value })}>${m.profiles.map((p) => html`<option value=${p.id} ?selected=${p.id === pack?.id}>${p.label} · ${p.disk_gb} GB${p.recommended ? ' · recommended' : ''}</option>`)}</select></label>
          <label class="field"><span>Context a lane holds, tokens</span><input data-sv-ctx class="num" inputmode="numeric" .value=${String(this.num('ctx', w.default))} @change=${(e: Event) => this.changed({ ctx: (e.target as HTMLInputElement).value.replace(/[\s,_]/g, '') })} /><span class="xs muted">${w.min.toLocaleString('en-US')} to ${w.max.toLocaleString('en-US')}</span></label>
          <label class="field"><span>Parallel chats (lanes)</span><input data-sv-slots class="num" type="number" min=${sl.min} max=${sl.engine_max ?? sl.max} .value=${String(this.num('slots', sl.default))} @change=${(e: Event) => this.changed({ slots: (e.target as HTMLInputElement).value })} /><span class="xs muted">${sl.min} to ${sl.engine_max ?? sl.max}</span></label>
        </div>
        ${pack ? html`<p class="small muted">${m.pitch} ${pack.note ?? ''}</p><p class="xs faint num">weights ${pack.weights_gib.toFixed(1)} GiB on the device · ${pack.hf_repo}</p>` : nothing}
        <p class="xs faint">The models and packs come from ${reg ? reg.alias : 'a Spark'}’s Setup Guide; every Spark runs the same release.</p>
      </section>
      <section class="mon-sec">
        <div class="mon-sec-h"><span class="eyebrow">Set up a Spark first</span><span class="xs faint">its own Setup Guide, through the controller</span></div>
        <div class="sv-guides">${(this.f?.nodes ?? []).map((n) => html`<a class="btn btn-sm" href=${guideHref(n.id, m.id)} data-guide=${n.id}>${nodeChip(n)} Setup Guide</a>`)}</div>
      </section>`;
  }

  private bar(n: PlacementNode): TemplateResult {
    const b = placeBar(n);
    if (!b) return html`<span class="small ${n.reason ? 'err-ink' : 'muted'}">${n.reason ?? 'no estimate'}</span>`;
    const tip = b.segs.map((s) => `${s.label} ${s.gib.toFixed(1)} GiB`).join(' · ');
    return html`<span class="pbar ${b.over ? 'is-over' : ''}" role="img" aria-label=${`${n.alias}: ${tip}`} title=${tip}>
        ${b.segs.map((s) => html`<i class="pb-${s.kind} pb-${s.id}" style="width:${(s.f * 100).toFixed(2)}%"></i>`)}
        ${b.budgetAt != null ? html`<span class="pb-budget" style="left:${(b.budgetAt * 100).toFixed(2)}%"></span>` : nothing}
      </span>
      <span class="xs ${b.over ? 'err-ink' : 'muted'} num">${b.text}</span>
      ${n.reason ? html`<span class="small err-ink" data-reason>✕ ${n.reason}</span>` : nothing}`;
  }

  private placementStep(): TemplateResult {
    const p = this.plan;
    const f = this.f!;
    if (this.planErr) return html`<section class="panel"><p class="small err-ink" data-sv-error>${this.planErr}</p><button type="button" class="btn btn-sm" @click=${() => void this.place()}>Plan again</button></section>`;
    if (!p) return html`<div class="skeleton" style="height:260px"></div>`;
    const members = this.members;
    return html`<section class="panel" data-panel="placement">
        <header class="panel-head"><div class="panel-titles"><h2 class="panel-title">Pick Sparks</h2><p class="panel-sub">${this.picked.length ? `Planned for the Sparks picked${this.picked.length > 1 ? ', in this order (a Split’s pipeline order)' : ''}.` : 'Every Spark planned. Pick some to plan those only.'} Bars: what runs there now, plus what this model needs, against the budget.</p></div>${this.planning ? html`<span class="xs muted">planning…</span>` : nothing}</header>
        <div class="pnodes">${p.nodes.map((n) => {
          const fn = f.nodes.find((x) => x.id === n.node);
          const i = this.picked.indexOf(n.node);
          return html`<div class="pnode ${n.fits ? 'is-fit' : 'is-no'} ${members.includes(n.node) ? 'is-member' : ''}" data-place-node=${n.node} data-fits=${n.fits}>
            <label class="pnode-pick"><input type="checkbox" .checked=${i >= 0} data-pick=${n.node} @change=${() => this.pick(n.node)} />${fn ? nodeChip(fn, { lg: true }) : html`<b>${n.alias}</b>`}${i >= 0 && this.picked.length > 1 ? html`<span class="tag num">${i + 1}</span>` : nothing}</label>
            <span class="xs faint">${[n.status, n.version ? `v${n.version}` : '', n.transport === 'tunnel' ? 'rented, SSH tunnel' : 'LAN', n.serving ? 'serves something' : 'serves nothing'].filter(Boolean).join(' · ')}</span>
            <div class="pnode-bar">${this.bar(n)}</div>
            <span class="xs faint">estimate: ${SOURCE_WORD[n.source]}</span>
            <a class="btn btn-sm btn-ghost pnode-guide" href=${guideHref(n.node, p.model)} data-guide=${n.node}>Setup Guide on ${n.alias}</a>
          </div>`;
        })}</div>
      </section>
      <section class="mon-sec" data-panel="types">
        <div class="mon-sec-h"><span class="eyebrow">Deployment type</span><span class="xs faint">refusals in the planner’s words</span></div>
        <div class="ptypes" role="radiogroup" aria-label="Deployment type">${TYPES.map((t) => {
          const tp = (this.typed ?? p).types.find((x) => x.type === t);
          if (!tp) return nothing;
          const on = this.type === t;
          return html`<button type="button" role="radio" aria-checked=${on} class="ptype ${on ? 'is-on' : ''} ${tp.allowed ? '' : 'is-no'}" data-type=${t} data-allowed=${tp.allowed} ?disabled=${!tp.allowed} @click=${() => (this.type = t)}>
            <span class="row"><span class="tag dep-type is-${t}">${TYPE_WORD[t]}</span><span class="small ${tp.allowed ? 'ok-ink' : 'err-ink'}">${tp.allowed ? '✓ allowed' : '✕ refused'}</span>${!tp.built ? html`<span class="tag" data-not-built>not built yet (ENG-307)</span>` : nothing}</span>
            <span class="small">${tp.allowed ? TYPE_HELP[t] : tp.reason}</span>
            ${tp.allowed && tp.nodes.length ? html`<span class="xs muted">${tp.nodes.map((x) => f.nodes.find((n) => n.id === x)?.alias ?? x).join(t === 'split' ? ' → ' : ', ')}${tp.stages.length ? ` · ${tp.stages.map((s) => `${s.gib.toFixed(1)} GiB`).join(' / ')} a stage` : ''}</span>` : nothing}
            ${tp.warn ? html`<span class="xs warn-ink">⚠ ${tp.warn}</span>` : nothing}
          </button>`;
        })}</div>
      </section>`;
  }

  private serveStep(): TemplateResult {
    const p = this.typed;
    const t = this.type;
    const f = this.f!;
    if (!p || !t) return html`<section class="panel"><p class="small muted">Pick an allowed type on the Placement step first.</p></section>`;
    const alias = (id: string) => f.nodes.find((n) => n.id === id)?.alias ?? id;
    const ms = this.members;
    const j = this.job;
    const done = !!this.made && (t === 'split' || (j && j.state !== 'running') || !!this.startErr);
    return html`<section class="panel" data-panel="sv-review">
        <div class="sv-grid">
          <label class="field"><span>Name</span><input data-sv-name .value=${this.name} ?disabled=${!!this.made} @input=${(e: Event) => (this.name = (e.target as HTMLInputElement).value)} /></label>
          <label class="field"><span>Model id in the API</span><input data-sv-api class="num" spellcheck="false" .value=${this.apiId} ?disabled=${!!this.made} @input=${(e: Event) => (this.apiId = (e.target as HTMLInputElement).value)} /></label>
          ${t === 'role'
            ? html`<label class="field"><span>Helps</span><select data-sv-roleof @change=${(e: Event) => (this.roleOf = (e.target as HTMLSelectElement).value)}><option value="">pick a deployment…</option>${f.deployments.filter((d) => d.record !== false).map((d) => html`<option value=${d.id} ?selected=${d.id === this.roleOf}>${d.name}</option>`)}</select></label>
                <label class="field"><span>Role</span><input data-sv-role placeholder="decider, narrator…" .value=${this.roleWord} @input=${(e: Event) => (this.roleWord = (e.target as HTMLInputElement).value)} /></label>`
            : nothing}
        </div>
        <dl class="kv sv-review">
          <div><dt>model</dt><dd>${this.model?.name ?? p.model} · ${p.pack}</dd></div>
          <div><dt>type</dt><dd>${TYPE_WORD[t]} · ${TYPE_HELP[t]}</dd></div>
          <div><dt>Sparks</dt><dd>${ms.map(alias).join(t === 'split' ? ' → ' : ' + ')}</dd></div>
          <div><dt>context</dt><dd class="num">${p.ctx.toLocaleString('en-US')} tokens a lane · ${p.slots} lane${p.slots === 1 ? '' : 's'}</dd></div>
          <div><dt>API</dt><dd>listed once in <code>/v1/models</code> as <code>${this.apiId || p.model}</code></dd></div>
        </dl>
        ${t === 'split' ? html`<p class="dcard-note small" data-not-built>◇ ${SPLIT_NOT_BUILT}. Serve records the plan; its start answers 501 until ENG-307.</p>` : nothing}
        ${this.signIn ? signInCard('Deployments') : nothing}
        ${this.err ? html`<p class="small err-ink" data-sv-error>${this.err}</p>` : nothing}
        ${this.made ? html`<p class="small ok-ink" data-sv-made>✓ ${this.made.name} recorded as ${this.made.api_id}.</p>` : nothing}
        ${this.startErr
          ? html`<p class="small err-ink" data-sv-start-error>Start refused: ${this.startErr}</p><p class="xs muted">${ms.map((m) => html`<a href=${guideHref(m, p.model)}>Setup Guide on ${alias(m)}</a> `)}</p>`
          : nothing}
        ${j
          ? html`<ul class="dj-steps" data-sv-job=${j.state}>${j.steps.map((s) => html`<li data-phase=${s.phase}>${f.nodes.find((n) => n.id === s.node) ? nodeChip(f.nodes.find((n) => n.id === s.node)!) : s.alias} <span class="small ${s.phase === 'failed' ? 'err-ink' : s.phase === 'serving' ? 'ok-ink' : 'muted'}">${s.phase}</span> <span class="xs faint">${s.text}</span></li>`)}</ul>
              ${j.state !== 'running' ? html`<p class="small ${j.state === 'done' ? 'ok-ink' : 'err-ink'}">${j.state === 'done' ? `✓ ${this.made?.name} serves.` : `✕ the start failed: ${j.error?.message ?? ''}`}</p>` : html`<p class="xs muted">since ${clock(j.started_at)}…</p>`}`
          : nothing}
        <div class="sv-acts">
          ${done ? html`<a class="btn btn-primary" href="#/models" data-sv-done>Deployments</a>` : html`<button type="button" class="btn btn-primary" data-sv-go ?disabled=${this.busy || !!this.made || !ms.length || (t === 'role' && !this.roleOf)} @click=${() => void this.serve()}>${this.busy ? 'Working…' : t === 'split' ? 'Record the Split' : 'Serve'}</button>`}
        </div>
      </section>`;
  }

  render() {
    const step = this.step;
    const head = html`<header class="page-head sp-head" data-page="serve"><div><div class="eyebrow">Fleet</div><h1>Models</h1><p class="page-sub">Serve a model: placement first, then each Spark set up with its own Setup Guide.</p></div></header>${modelTabs('serve')}`;
    if (!this.f) return html`${head}<div class="skeleton" style="height:240px"></div>`;
    const body = step === 'placement' ? this.placementStep() : step === 'serve' ? this.serveStep() : this.modelStep();
    const i = STEPS.indexOf(step);
    const next = STEPS[i + 1];
    const can = step === 'model' ? !!this.q.get('pack') : step === 'placement' ? !!this.type && this.members.length > 0 : false;
    return html`${head}${this.stepper()}${body}
      <div class="wiz-foot sv-foot">
        <button type="button" class="btn btn-ghost" ?disabled=${i === 0} @click=${() => this.go(STEPS[Math.max(0, i - 1)])}>Back</button>
        <span class="xs muted">${step === 'placement' && !this.type ? 'Pick an allowed type to go on' : ''}</span>
        ${next ? html`<button type="button" class="btn btn-primary" data-sv-next ?disabled=${!can} @click=${() => this.go(next)}>Next: ${STEP_LABEL[next]}</button>` : nothing}
      </div>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-serve': QseServe;
  }
}
