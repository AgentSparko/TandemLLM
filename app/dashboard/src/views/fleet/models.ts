// <qse-models>: Fleet → Models → Deployments (VIS-252, concept §8.7, the v3 prototype's Models page).
// Every deployment behind the one API as a card: its type (Single, Replicas, Split, Role), status,
// members with each one's state, decode and TTFT, its model, pack, context and API id, whether it is a
// record TandemNode manages, a record of engines started elsewhere, or a Single derived from what an
// engine serves, its warning and its last job; Start, Stop, Move, Edit and Delete through the
// controller's plan (<qse-deploy-action>), Record for a derived one. A Split shows its two stages and
// the link between them (./split.ts, VIS-272), started and stopped as one; Simple and Advanced pick how
// much of its plan shows. The scope bar narrows it to a Spark or one deployment; the tiles sum the scope with each
// Spark's share when it holds more than one. A card's head opens its detail in the right sidebar.

import { html, nothing, type TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { fleet, type FleetDeployment, type MetricSeries, type NodeMemory } from '../../api/fleet';
import { openSelection, type ContextContent } from '../../lib/context';
import { lastValue, shareParts } from '../../lib/monitor';
import { countWords, depActions, ink, depCounts, managedWords, MEMBER_WORD, memberTone, scoped, setSimpleMode, simpleMode, SPLIT_NO_PLAN, STATUS_GLYPH, statusTone, TYPE_HELP, TYPE_WORD, type DepActionId } from '../../lib/models';
import type { View } from '../../lib/router';
import { nodeChip } from '../../ui/node-chip';
import { MonitorPage } from '../monitor/base';
import { clock, fmt, members as memberChips, since, signInCard, sparkline, tile, typeBadge } from '../monitor/bits';
import { checkSession, needsSignIn, onSession } from '../monitor/data';
import './deploy-dialog';
import type { QseDeployAction } from './deploy-dialog';
import { modeSwitch, splitView } from './split';

interface Figures {
  /** each member's decode and TTFT now */
  tps: Record<string, number | null>;
  ttft: Record<string, number | null>;
  /** the deployment's decode over the scope's range, its members summed */
  line: (number | null)[];
}

/** Fleet → Models' own tabs, the panel's sub-pages: the Setup Guide is a page of its own (setup chunk) */
export function modelTabs(on: 'deployments' | 'serve' | 'weights'): TemplateResult {
  const a = (id: typeof on | 'guide', href: string, label: string) => html`<a href=${href} class=${on === id ? 'is-on' : ''} aria-current=${on === id ? 'page' : 'false'}>${label}</a>`;
  return html`<nav class="sp-tabs" aria-label="Models views">${a('deployments', '#/models', 'Deployments')}${a('serve', '#/models/place', 'Serve a model')}${a('guide', '#/models/serve', 'Setup Guide')}${a('weights', '#/models/weights', 'Weights')}</nav>`;
}

function sumLine(series: MetricSeries['series']): (number | null)[] {
  const n = Math.max(0, ...series.map((s) => s.values.length));
  return Array.from({ length: n }, (_, i) => {
    const vs = series.map((s) => s.values[i]).filter((v): v is number => v != null);
    return vs.length ? vs.reduce((a, v) => a + v, 0) : null;
  });
}

@customElement('qse-models')
export class QseModels extends MonitorPage {
  protected readonly view: View = 'models';
  @state() private figs: Record<string, Figures> = {};
  @state() private memory: NodeMemory[] = [];
  @state() private simple = simpleMode();
  private offSession: (() => void) | null = null;
  private get dialog(): QseDeployAction | null {
    return this.querySelector('qse-deploy-action');
  }

  connectedCallback(): void {
    super.connectedCallback();
    void checkSession();
    this.offSession = onSession(() => this.requestUpdate());
  }
  disconnectedCallback(): void {
    this.offSession?.();
    super.disconnectedCallback();
  }

  private get deps(): FleetDeployment[] {
    return this.fleetState ? scoped(this.fleetState.deployments, this.resolved) : [];
  }

  protected async load(isCurrent: () => boolean): Promise<void> {
    const deps = this.deps;
    const range = this.scope.range;
    const [mem, ...rows] = await Promise.all([
      fleet.memory().catch(() => null),
      ...deps.map((d) =>
        Promise.all([
          fleet.metrics({ source: `deployment:${d.id}`, metric: 'tps', range }).catch(() => null),
          fleet.metrics({ source: `deployment:${d.id}`, metric: 'ttft', range: '5m' }).catch(() => null),
        ]),
      ),
    ]);
    if (!isCurrent()) return;
    const figs: Record<string, Figures> = {};
    deps.forEach((d, i) => {
      const [tps, ttft] = rows[i] as [MetricSeries | null, MetricSeries | null];
      const now = (m: MetricSeries | null) => Object.fromEntries((m?.series ?? []).map((s) => [s.node, lastValue(s.values)]));
      figs[d.id] = { tps: now(tps), ttft: now(ttft), line: tps ? sumLine(tps.series) : [] };
    });
    this.figs = figs;
    if (mem) this.memory = mem.nodes;
  }

  protected context(): ContextContent {
    const f = this.fleetState;
    if (!f) return { title: 'Models', body: html`<p class="muted small">The deployments show here once TandemApp’s controller answers on this server.</p>` };
    const sec = (t: string, body: TemplateResult) => html`<section class="csec"><div class="csec-h"><span class="eyebrow">${t}</span></div>${body}</section>`;
    const deps = this.deps;
    return {
      title: 'Models',
      body: html`${sec(
        'Deployments in scope',
        deps.length
          ? html`<ul class="sp-ctx-list small">${deps.map((d) => html`<li><button type="button" class="link-btn" @click=${() => this.detail(d)}>${d.name}</button> <span class="${ink(statusTone(d.status))}">${STATUS_GLYPH[d.status]} ${d.status}</span></li>`)}</ul>`
          : html`<p class="small muted">Nothing in this scope.</p>`,
      )}
      ${sec('One API', html`<p class="small">Clients call TandemApp once; <code>/v1/models</code> lists each deployment once under its model id, and each answer names the Spark that served it (<code>X-Tandem-Node</code>, <code>X-Tandem-Deployment</code>).</p>`)}
      ${sec('Types', html`<dl class="kv small">${(Object.keys(TYPE_WORD) as (keyof typeof TYPE_WORD)[]).map((t) => html`<div><dt>${TYPE_WORD[t]}</dt><dd>${TYPE_HELP[t]}</dd></div>`)}</dl>`)}`,
    };
  }

  /** a deployment's members and its last job, at the top of the right sidebar */
  private detail(d: FleetDeployment): void {
    const f = this.fleetState!;
    const mw = managedWords(d);
    openSelection(
      d.name,
      html`<dl class="kv small">
          <div><dt>type</dt><dd>${TYPE_WORD[d.type]} · ${TYPE_HELP[d.type]}</dd></div>
          <div><dt>model</dt><dd class="num">${d.model}${d.pack ? ` · ${d.pack}` : ''}</dd></div>
          <div><dt>API id</dt><dd class="num">${d.api_id}</dd></div>
          <div><dt>status</dt><dd>${d.status_text}</dd></div>
          <div><dt>${mw.label}</dt><dd>${mw.text}</dd></div>
        </dl>
        <div class="csec-h"><span class="eyebrow">Members</span></div>
        <ul class="dm-list">${(d.member_states ?? []).map((m) => {
          const n = f.nodes.find((x) => x.id === m.node);
          return html`<li>${n ? nodeChip(n, { href: `#/sparks/${encodeURIComponent(m.node)}` }) : m.alias} <span class="small ${ink(memberTone(m.state))}">${MEMBER_WORD[m.state]}</span><div class="xs muted">${m.text}${m.engine ? ` · engine ${m.engine}` : ''}${m.managed ? ' · managed' : ' · not managed by TandemNode'}</div></li>`;
        })}</ul>
        ${d.job ? html`<div class="csec-h"><span class="eyebrow">Last job</span></div><p class="small">${d.job.action} · ${d.job.state} · ${since(d.job.started_at)}${d.job.error ? html`<br /><span class="err-ink">${d.job.error.message}</span>` : nothing}</p>` : nothing}
        <a class="btn btn-sm" href=${`#/performance?scope=deployment:${encodeURIComponent(d.id)}`}>Performance of ${d.name}</a>`,
    );
  }

  private act(d: FleetDeployment, id: DepActionId): void {
    void this.updateComplete.then(() => this.dialog?.open(d, id, this.fleetState?.nodes ?? []));
  }

  private tiles(): TemplateResult {
    const f = this.fleetState!;
    const nodes = this.inScope;
    const many = nodes.length > 1;
    const deps = this.deps;
    const c = depCounts(deps);
    const tps = shareParts(nodes, Object.fromEntries(nodes.map((n) => [n.id, n.status === 'off' ? null : (f.tps[n.id] ?? null)])));
    const total = tps.some((p) => p.v != null) ? tps.reduce((a, p) => a + (p.v ?? 0), 0) : null;
    const mems = this.memory.filter((m) => nodes.some((n) => n.id === m.node));
    const engine = shareParts(nodes, Object.fromEntries(mems.map((m) => [m.node, m.weights + m.kv + m.cache])));
    const used = mems.length ? mems.reduce((a, m) => a + m.weights + m.kv + m.cache, 0) : null;
    const cap = mems.reduce((a, m) => a + m.usable, 0) || null;
    return html`<div class="mtiles is-4" data-tiles>
      ${tile({ id: 'tps', label: 'Decode', agg: 'sum', value: fmt(total), unit: 'tok/s', parts: many ? tps : [], sub: many ? 'every Spark in scope, now' : 'now' })}
      ${tile({ id: 'deps', label: 'Deployments', agg: null, value: String(c.total), sub: c.total ? `${c.serving} serving${c.trouble ? ` · ${c.trouble} not all serving` : ''}${c.stopped ? ` · ${c.stopped} stopped` : ''}` : 'none yet' })}
      ${tile({ id: 'members', label: 'Members serving', agg: null, value: c.members ? `${c.routable}` : '—', unit: c.members ? `/ ${c.members}` : '', sub: 'take requests now' })}
      ${tile({ id: 'mem', label: 'Engine memory', agg: 'sum', value: fmt(used, 1), unit: cap ? `/ ${fmt(cap, 1)} GiB` : 'GiB', parts: many ? engine : [], cap, fmt: (v) => fmt(v, 1), sub: 'weights, KV and caches' })}
    </div>`;
  }

  private card(d: FleetDeployment): TemplateResult {
    const f = this.fleetState!;
    const fig = this.figs[d.id];
    const rate = fig ? Object.values(fig.tps).reduce<number | null>((a, v) => (v == null ? a : (a ?? 0) + v), null) : null;
    const mw = managedWords(d);
    const role = d.role_of ? f.deployments.find((x) => x.id === d.role_of) : null;
    const first = f.nodes.find((n) => n.id === d.members[0]);
    const ms = d.member_states ?? d.members.map((m) => ({ node: m, alias: m, engine: null, backend: null, model: d.model, managed: false, state: 'off' as const, text: '', routable: false }));
    const unmanaged = d.record === false || !d.managed ? ms.find((m) => !m.managed) : null;
    const job = d.job;
    return html`<article class="panel dcard is-${d.status}" data-dep=${d.id} data-type=${d.type}>
      <button type="button" class="dcard-h" @click=${() => this.detail(d)} aria-label=${`${d.name}: details`}>
        <span class="row">${typeBadge(d.type)}<span class="small ${ink(statusTone(d.status))}" data-dep-status=${d.status} title=${d.status_text}><span aria-hidden="true">${STATUS_GLYPH[d.status]}</span> ${d.status}</span><span class="tag dcard-m is-${mw.label}" title=${mw.text}>${mw.label}</span></span>
        <span class="num small"><b data-dep-rate>${fmt(rate)}</b> tok/s</span>
      </button>
      <div><h3 class="dcard-name">${d.name}</h3><div class="xs muted num dcard-meta">${[d.model, d.pack, d.ctx ? `ctx ${d.ctx.toLocaleString('en-US')}` : ''].filter(Boolean).join(' · ')} · model id <code>${d.api_id}</code></div></div>
      ${role || d.role ? html`<p class="xs muted">${d.role ?? 'role'}${role ? ` · beside ${role.name}` : ''}</p>` : nothing}
      <div class="dcard-members">${memberChips(d, f.nodes)}${d.type === 'split' ? html`<span class="xs muted">${d.split ? `two stages, cut at layer ${d.split.cut} of ${d.split.layers}` : 'two stages'}</span>` : nothing}</div>
      ${fig?.line.length && first ? sparkline(fig.line, first.colour, 32, `${d.name} decode over ${this.scope.range}`) : nothing}
      ${d.split
        ? splitView(d.split, ms, f.nodes, this.simple)
        : html`<div class="dcard-tbl-wrap"><table class="dcard-tbl">
        <thead><tr>${d.type === 'split' ? html`<th>Stage</th><th>Layers</th><th class="r">GiB</th><th>State</th>` : html`<th>Member</th><th>State</th><th class="r">tok/s</th><th class="r">TTFT p50</th>`}</tr></thead>
        <tbody>${ms.map((m, i) => {
          const n = f.nodes.find((x) => x.id === m.node);
          const chip = n ? nodeChip(n, { href: `#/sparks/${encodeURIComponent(m.node)}`, status: false }) : html`<span class="small">${m.alias}</span>`;
          const st = html`<span class="small ${ink(memberTone(m.state))}" data-member-state=${m.state} title=${m.text}>${MEMBER_WORD[m.state]}</span>${m.state !== 'serving' && m.text ? html`<div class="xs faint">${m.text}</div>` : nothing}`;
          if (d.type === 'split') {
            const s = d.stages?.[i];
            return html`<tr data-member=${m.node}><td>${chip}</td><td class="small">${s?.layers ?? `stage ${i + 1}`}</td><td class="r num">${s ? fmt(s.gib, 1) : '—'}</td><td>${st}</td></tr>`;
          }
          return html`<tr data-member=${m.node}><td>${chip}</td><td>${st}</td><td class="r num">${fmt(fig?.tps[m.node])}</td><td class="r num">${fig?.ttft[m.node] != null ? `${fmt(fig.ttft[m.node])} ms` : '—'}</td></tr>`;
        })}</tbody>
      </table></div>`}
      ${d.type === 'split' && !d.split ? html`<p class="dcard-note small" data-no-plan><span aria-hidden="true">◇</span> Split: ${SPLIT_NO_PLAN}.</p>` : nothing}
      ${d.warn ? html`<p class="dcard-warn small warn-ink"><span aria-hidden="true">⚠</span> ${d.warn}</p>` : nothing}
      ${unmanaged ? html`<p class="xs muted">${mw.text} <a href=${`#/models/serve?node=${encodeURIComponent(unmanaged.node)}`}>Setup Guide on ${unmanaged.alias}</a></p>` : nothing}
      ${job ? html`<p class="xs ${job.state === 'failed' ? 'err-ink' : 'muted'}" data-dep-job=${job.state}>${job.state === 'running' ? `${job.action} runs since ${clock(job.started_at)}: ${job.text}` : `last ${job.action} ${job.state} ${since(job.ended_at ?? job.started_at)}${job.error ? `: ${job.error.message}` : ''}`}</p>` : nothing}
      <div class="dcard-acts">
        ${depActions(d).map((a) => html`<button type="button" class="btn btn-sm ${a.id === 'delete' ? 'btn-ghost' : ''}" data-dep-act=${a.id} ?disabled=${!!a.why} title=${a.why ?? ''} @click=${() => this.act(d, a.id)}>${a.label}</button>`)}
        <a class="btn btn-sm btn-ghost" href=${`#/performance?scope=deployment:${encodeURIComponent(d.id)}`}>Performance</a>
      </div>
    </article>`;
  }

  render() {
    const f = this.fleetState;
    const deps = this.deps;
    const head = html`<header class="page-head sp-head" data-page="models"><div><div class="eyebrow">Fleet</div><h1>Models</h1>${f ? html`<p class="page-sub">${countWords(depCounts(deps))} Each is Single, Replicas, Split or Role.</p>` : nothing}</div><div class="sp-head-acts">${f && deps.some((d) => d.split) ? modeSwitch(this.simple, (on) => (setSimpleMode(on), (this.simple = on))) : nothing}<a class="btn btn-primary" href="#/models/place">Serve a model</a></div></header>${modelTabs('deployments')}`;
    if (!f) return html`${head}<div class="skeleton" style="height:240px"></div>`;
    return html`${head}${this.tiles()}
      ${needsSignIn() ? signInCard('Deployments', false) : nothing}
      ${deps.length
        ? html`<div class="dcards">${deps.map((d) => this.card(d))}</div>`
        : html`<section class="panel"><p class="small muted">${f.deployments.length ? 'No deployment in this scope.' : 'Nothing is deployed yet.'} <a href="#/models/place">Serve a model</a> plans where it fits and starts it.</p></section>`}
      <p class="xs faint mon-foot">read ${clock(Date.now() / 1000)} · every 10 s while live</p>
      <qse-deploy-action @qse-deploy-changed=${() => this.reload()}></qse-deploy-action>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-models': QseModels;
  }
}
