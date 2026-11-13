// <qse-weights>: Fleet → Models → Weights (VIS-252, concept §8.7 and C9, the v3 prototype's matrix).
// Every model pack × every Spark in scope from GET /v1/fleet/weights: present with its size, where it
// sits (the Setup Guide's folder or TandemNode's weights store) and its content id, in use by a
// deployment, copying with its progress, or missing. Copy here starts a copy from a Spark that has it
// (POST /v1/fleet/weights/copy): it goes node to node when both are paired on the LAN, else through
// the controller, and is verified by content id at the end; the copies below follow it every second.
// Delete reads the controller's plan (what it frees, what stays, refused while a deployment serves
// that pack there) and runs only with its confirm. On a phone the matrix is a card per pack.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { describeError } from '../../api/client';
import { fleet, type FleetNode, type WeightsCopyJob, type WeightsMatrix, type WeightsPlan } from '../../api/fleet';
import { type ContextContent } from '../../lib/context';
import { copyWords, packBytes, shortId, sizeWords, weightsCell, type WeightsCell } from '../../lib/models';
import type { View } from '../../lib/router';
import { nodeChip } from '../../ui/node-chip';
import { MonitorPage } from '../monitor/base';
import { clock, signInCard } from '../monitor/bits';
import { checkSession, needsSignIn, onSession } from '../monitor/data';
import { modelTabs } from './models';

type Pack = WeightsMatrix['packs'][number];

@customElement('qse-weights')
export class QseWeights extends MonitorPage {
  protected machines = true;
  protected readonly view: View = 'models';
  @state() private m: WeightsMatrix | null = null;
  @state() private err = '';
  @state() private busy = '';
  @state() private plan: WeightsPlan | null = null;
  @state() private planErr = '';
  @state() private signIn = false;
  @state() private from: Record<string, string> = {};
  private fast: ReturnType<typeof setTimeout> | null = null;
  private offSession: (() => void) | null = null;
  private get dialog(): HTMLDialogElement | null {
    return this.querySelector('dialog.wd-dialog');
  }

  connectedCallback(): void {
    super.connectedCallback();
    void checkSession();
    this.offSession = onSession(() => this.requestUpdate());
  }
  disconnectedCallback(): void {
    this.offSession?.();
    if (this.fast) clearTimeout(this.fast);
    super.disconnectedCallback();
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    try {
      const m = await fleet.weights();
      if (!isCurrent()) return;
      this.m = m;
      this.err = '';
      this.follow();
    } catch (e) {
      if (isCurrent()) this.err = describeError(e).message;
    }
  }

  /** a copy runs: the matrix every second until none does */
  private follow(): void {
    if (this.fast) clearTimeout(this.fast);
    this.fast = null;
    if (this.m?.copies.some((c) => c.state === 'running')) this.fast = setTimeout(() => this.reload(), 1000);
  }

  protected context(): ContextContent {
    const m = this.m;
    return {
      title: 'Weights',
      body: html`<section class="csec"><div class="csec-h"><span class="eyebrow">Stores</span></div>
          ${m
            ? html`<ul class="sp-ctx-list small">${m.nodes.map((n) => html`<li><b>${n.alias}</b> <span class="muted">${n.free_gib != null ? `${n.free_gib.toFixed(1)} GiB free` : 'free space not read'}</span>${n.note ? html`<div class="xs warn-ink">${n.note}</div>` : nothing}${n.store ? html`<div class="xs faint num">${n.store}</div>` : nothing}</li>`)}</ul>`
            : html`<p class="small muted">Reading…</p>`}</section>
        <section class="csec"><div class="csec-h"><span class="eyebrow">How a copy goes</span></div><p class="small">Download once, then copy between Sparks, never a second download. A copy goes from the source’s TandemNode straight to the receiver’s when both are paired on the LAN, else through the controller; every file is hashed on both ends and the copy is verified by the pack’s content id.</p></section>`,
    };
  }

  private nodesShown(): WeightsMatrix['nodes'] {
    const ids = new Set(this.inScope.map((n) => n.id));
    return (this.m?.nodes ?? []).filter((n) => !this.fleetState || ids.has(n.node));
  }

  private node(id: string): FleetNode | undefined {
    return this.fleetState?.nodes.find((n) => n.id === id);
  }

  private chip(id: string, alias?: string): TemplateResult {
    const n = this.node(id);
    return n ? nodeChip(n, { status: false }) : html`<span class="small">${alias ?? id}</span>`;
  }

  private async copy(p: Pack, to: string, from: string): Promise<void> {
    this.busy = `${p.profile}@${to}`;
    this.err = '';
    try {
      await fleet.copyWeights({ from, to, profile: p.profile });
      this.reload();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) await checkSession(true);
      this.err = d.status === 401 ? 'Sign in first: a copy is a write.' : d.message;
    } finally {
      this.busy = '';
    }
  }

  private async stop(j: WeightsCopyJob): Promise<void> {
    try {
      await fleet.stopWeightsCopy(j.id);
    } catch (e) {
      this.err = describeError(e).message;
    }
    this.reload();
  }

  private async askDelete(node: string, profile: string): Promise<void> {
    this.plan = null;
    this.planErr = '';
    this.signIn = false;
    void this.updateComplete.then(() => this.dialog && !this.dialog.open && this.dialog.showModal());
    try {
      this.plan = await fleet.weightsPlan(node, profile);
    } catch (e) {
      this.planErr = describeError(e).message;
    }
  }

  private async runDelete(): Promise<void> {
    const p = this.plan;
    if (!p?.confirm || this.busy) return;
    this.busy = 'delete';
    try {
      await fleet.deleteWeights(p.node, p.profile, p.confirm);
      this.dialog?.close();
      this.reload();
    } catch (e) {
      const d = describeError(e);
      if (d.status === 401) {
        await checkSession(true);
        this.signIn = true;
      } else {
        this.planErr = d.message;
        // the confirm is spent: read the plan again
        this.plan = await fleet.weightsPlan(p.node, p.profile).catch(() => null);
      }
    } finally {
      this.busy = '';
    }
  }

  private cellOf(p: Pack, node: string): WeightsCell {
    const status = Object.fromEntries((this.fleetState?.nodes ?? []).map((n) => [n.id, n.status]));
    return weightsCell(this.m!, p, node, this.fleetState?.deployments ?? [], status);
  }

  private cell(p: Pack, node: string): TemplateResult {
    const c = this.cellOf(p, node);
    const key = `${p.profile}@${node}`;
    if (c.kind === 'copying' && c.copy) {
      const pct = c.pct != null ? Math.round(c.pct * 100) : null;
      return html`<div class="wcell is-copying" data-cell=${key} data-kind="copying"><span class="small">copying${pct != null ? ` ${pct} %` : ''}</span><span class="wprog" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${pct ?? nothing}><i style="width:${pct ?? 0}%"></i></span><span class="xs faint">from ${this.node(c.copy.from)?.alias ?? c.copy.from_alias} · ${c.copy.via}</span></div>`;
    }
    if (c.kind === 'present' || c.kind === 'in-use') {
      return html`<div class="wcell is-${c.kind}" data-cell=${key} data-kind=${c.kind}>
        ${c.kind === 'in-use' ? html`<span class="small ok-ink" title=${`served by ${c.usedBy.join(', ')}`}>● in use</span>` : html`<span class="small">✓ ${sizeWords(c.bytes)}</span>`}
        <span class="xs faint num" title=${c.contentId ?? 'no content id: no hashes recorded for this pack'}>${c.source === 'store' ? 'store' : 'Setup Guide'} · ${shortId(c.contentId)}</span>
        ${c.kind === 'present' ? html`<button type="button" class="btn btn-sm btn-ghost" data-weights-delete=${key} @click=${() => void this.askDelete(node, p.profile)}>Delete…</button>` : html`<span class="xs muted">${c.usedBy.join(', ')}</span>`}
      </div>`;
    }
    if (c.kind === 'off') return html`<div class="wcell is-off" data-cell=${key} data-kind="off"><span class="small faint">— off</span></div>`;
    const src = this.from[key] && c.from.includes(this.from[key]) ? this.from[key] : c.from[0];
    return html`<div class="wcell is-missing" data-cell=${key} data-kind="missing">
      <span class="small faint">—</span>
      ${c.from.length
        ? html`${c.from.length > 1 ? html`<select class="wsrc" aria-label="Copy from" @change=${(e: Event) => (this.from = { ...this.from, [key]: (e.target as HTMLSelectElement).value })}>${c.from.map((f) => html`<option value=${f} ?selected=${f === src}>from ${this.node(f)?.alias ?? f}</option>`)}</select>` : html`<span class="xs faint">from ${this.node(src)?.alias ?? src}</span>`}
            <button type="button" class="btn btn-sm" data-weights-copy=${key} ?disabled=${this.busy === key} @click=${() => void this.copy(p, node, src)}>${this.busy === key ? 'Starting…' : 'Copy here'}</button>`
        : html`<span class="xs faint">no Spark has it</span>`}
    </div>`;
  }

  private matrix(): TemplateResult {
    const m = this.m!;
    const cols = this.nodesShown();
    return html`<section class="panel wm-panel" data-panel="weights">
      <div class="wm-wrap">
        <table class="wm-table" aria-label="Weights per Spark">
          <thead><tr><th scope="col">Pack</th><th scope="col" class="r">Size</th>${cols.map((n) => html`<th scope="col">${this.chip(n.node, n.alias)}</th>`)}</tr></thead>
          <tbody>${m.packs.map(
            (p) => html`<tr data-pack=${p.profile}>
              <th scope="row"><b>${p.profile}</b><div class="xs muted">${p.model ?? 'model not named'}${p.same ? html` · <span class="ok-ink" title="every copy has the same content id">same everywhere</span>` : p.nodes.filter((x) => x.present).length > 1 ? html` · <span class="warn-ink" title="the copies' content ids differ">copies differ</span>` : nothing}</div></th>
              <td class="r num small">${sizeWords(packBytes(p))}</td>
              ${cols.map((n) => html`<td>${this.cell(p, n.node)}</td>`)}
            </tr>`,
          )}</tbody>
          <tfoot><tr><th scope="row">Store</th><td></td>${cols.map((n) => html`<td class="xs"><span class="num">${n.free_gib != null ? `${n.free_gib.toFixed(1)} GiB free` : '—'}</span>${n.note ? html`<div class="warn-ink">${n.note}</div>` : nothing}</td>`)}</tr></tfoot>
        </table>
      </div>
      <div class="wm-cards">${m.packs.map(
        (p) => html`<article class="wm-card" data-pack-card=${p.profile}>
          <header><b>${p.profile}</b> <span class="xs muted">${p.model ?? ''} · ${sizeWords(packBytes(p))}</span></header>
          ${cols.map((n) => html`<div class="wm-card-row">${this.chip(n.node, n.alias)}${this.cell(p, n.node)}</div>`)}
        </article>`,
      )}</div>
      ${m.packs.length ? nothing : html`<p class="small muted">No pack on any Spark yet. The Setup Guide downloads one; a copy brings it to another Spark.</p>`}
    </section>`;
  }

  private copies(): TemplateResult {
    const list = this.m?.copies ?? [];
    if (!list.length) return html``;
    return html`<section class="mon-sec" data-panel="copies">
      <div class="mon-sec-h"><span class="eyebrow">Copies</span><span class="xs faint">${list.filter((c) => c.state === 'running').length} running</span></div>
      <div class="wcopies">${list.map((j) => {
        const pct = j.bytes_total ? Math.round((j.bytes_done / j.bytes_total) * 100) : null;
        return html`<article class="panel wcopy is-${j.state}" data-copy=${j.id} data-state=${j.state}>
          <div class="row small"><b>${j.profile}</b> ${this.chip(j.from, j.from_alias)}<span class="faint" aria-hidden="true">→</span>${this.chip(j.to, j.to_alias)}<span class="tag" title=${j.via_why}>${j.via}</span>${j.state === 'done' ? html`<span class=${j.verified ? 'ok-ink' : 'err-ink'} data-verified=${j.verified}>${j.verified ? '✓ verified' : '✕ not verified'}</span>` : nothing}</div>
          ${j.state === 'running' ? html`<span class="wprog" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${pct ?? nothing}><i style="width:${pct ?? 0}%"></i></span>` : nothing}
          <p class="xs ${j.state === 'failed' ? 'err-ink' : 'muted'}">${copyWords(j)}</p>
          <p class="xs faint num">${clock(j.started_at)}${j.ended_at ? `–${clock(j.ended_at)}` : ''} · by ${j.by}${j.content_id.copy ? ` · ${shortId(j.content_id.copy)}` : ''} · ${j.via_why}</p>
          ${j.state === 'running' ? html`<button type="button" class="btn btn-sm btn-ghost" data-copy-stop=${j.id} @click=${() => void this.stop(j)}>Stop</button>` : nothing}
        </article>`;
      })}</div>
    </section>`;
  }

  private deleteDialog(): TemplateResult {
    const p = this.plan;
    const list = (t: string, xs: string[], cls: string) => (xs.length ? html`<section class="act-sec ${cls}"><h3 class="eyebrow">${t}</h3><ul>${xs.map((x) => html`<li>${x}</li>`)}</ul></section>` : nothing);
    return html`<dialog class="act-dialog wd-dialog ${p?.allowed ? 'is-danger' : ''}" aria-labelledby="wd-title" data-weights-plan=${p?.profile ?? nothing}>
      <form method="dialog" class="act-body" @submit=${(e: Event) => (e.preventDefault(), void this.runDelete())}>
        <header class="act-head"><h2 id="wd-title">${p?.title ?? 'Delete weights'}</h2>${p ? this.chip(p.node) : nothing}</header>
        ${p
          ? html`${p.blocked ? html`<p class="act-blocked warn-ink" data-act-blocked><span aria-hidden="true">⚠</span> ${p.blocked}</p>` : nothing}${list('What happens', p.impact, 'is-removes')}${list('Stays', p.keeps, 'is-keeps')}`
          : this.planErr
            ? nothing
            : html`<div class="skeleton" style="height:90px"></div>`}
        ${this.signIn ? signInCard('Weights') : nothing}
        ${this.planErr ? html`<p class="small err-ink" data-act-error>${this.planErr}</p>` : nothing}
        <footer class="act-foot">
          <button type="button" class="btn btn-ghost" data-act-cancel @click=${() => this.dialog?.close()}>${p?.allowed ? 'Cancel' : 'Close'}</button>
          ${p?.allowed ? html`<button type="submit" class="btn btn-danger-solid" data-act-go ?disabled=${this.busy === 'delete'}>${this.busy === 'delete' ? 'Deleting…' : `Delete ${sizeWords(p.bytes)}`}</button>` : nothing}
        </footer>
      </form>
    </dialog>`;
  }

  render() {
    const m = this.m;
    const head = html`<header class="page-head sp-head" data-page="weights"><div><div class="eyebrow">Fleet</div><h1>Models</h1><p class="page-sub">Weights per Spark. A copy goes node to node and is verified by content id; a pack a deployment serves cannot be deleted there.</p></div></header>${modelTabs('weights')}`;
    return html`${head}
      ${needsSignIn() ? signInCard('Copies and deletes', false) : nothing}
      ${this.err ? html`<p class="small err-ink" role="alert" data-weights-error>${this.err}</p>` : nothing}
      ${m ? html`${this.matrix()}${this.copies()}` : this.err ? nothing : html`<div class="skeleton" style="height:240px"></div>`}
      <p class="xs faint mon-foot">read ${clock(Date.now() / 1000)} · every second while a copy runs</p>
      ${this.deleteDialog()}`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-weights': QseWeights;
  }
}
