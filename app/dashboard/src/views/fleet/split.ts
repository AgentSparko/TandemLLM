// A Split on the Models pages (VIS-272): one model cut across two Sparks. Two stage bars (the front
// and the tail: its Spark, its layer range, what it needs against what the Spark may give, its state),
// the link between them (up or down with the reason, its epoch and transport, a fabric or a LAN pair),
// and the phase in plain words. Simple mode stops there; advanced adds every number of the plan with
// its basis (computed from the engine's estimate, the planner's margin, or measured), why the cut was
// chosen and why the others were not. The same view shows a plan before its start (no member states).

import { html, nothing, type TemplateResult } from 'lit';
import type { DeploymentMember, FleetNode, SplitPlan, SplitSide } from '../../api/fleet';
import { basisWord, ink, LINK_KIND, linkWords, MEMBER_WORD, memberTone, phaseWords, stageBar } from '../../lib/models';
import { nodeChip } from '../../ui/node-chip';

const g1 = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(1));
/** a measured figure: a number, or a range [low, high] */
const fig = (v: unknown) => (v == null ? '—' : Array.isArray(v) ? v.join(' to ') : String(v));

function stage(s: SplitSide, m: DeploymentMember | undefined, nodes: FleetNode[]): TemplateResult {
  const n = nodes.find((x) => x.id === s.node);
  const b = stageBar(s);
  const tip = b.segs.map((x) => `${x.label} ${x.gib.toFixed(1)} GiB`).join(' · ');
  return html`<div class="sstage" data-stage=${s.role} data-stage-state=${m?.state ?? 'planned'}>
    <div class="sstage-h">
      <span class="tag">${s.role}</span>${n ? nodeChip(n, { href: `#/sparks/${encodeURIComponent(s.node)}`, status: false }) : html`<b class="small">${s.alias}</b>`}
      <span class="xs muted num">layers ${s.layers}</span>
      ${m ? html`<span class="small ${ink(memberTone(m.state))}" data-member-state=${m.state} title=${m.text}>${MEMBER_WORD[m.state]}${m.routable ? ' · takes the requests' : ''}</span>` : html`<span class="small muted">planned</span>`}
    </div>
    <span class="pbar ${b.over ? 'is-over' : ''}" role="img" aria-label=${`${s.role} stage on ${s.alias}: ${tip}; budget ${s.budget_gib.toFixed(1)} GiB`} title=${tip}>
      ${b.segs.map((x) => html`<i class="pb-new pb-${x.id}" style="width:${(x.f * 100).toFixed(2)}%"></i>`)}
      ${b.budgetAt != null ? html`<span class="pb-budget" style="left:${(b.budgetAt * 100).toFixed(2)}%"></span>` : nothing}
    </span>
    <span class="xs ${b.over ? 'err-ink' : 'muted'} num">${b.text}</span>
    ${m && m.state !== 'serving' && m.text ? html`<span class="xs faint">${m.text}</span>` : nothing}
  </div>`;
}

function advanced(p: SplitPlan): TemplateResult {
  const ids = [...new Set([...p.front.parts, ...p.tail.parts].map((x) => x.id))];
  const part = (s: SplitSide, id: string) => s.parts.find((x) => x.id === id);
  const lost = p.candidates.filter((c) => !c.chosen);
  const sh = p.shape as { window?: number; slots?: number; side_window?: number; caches?: string; table?: string | null };
  return html`<div class="sadv" data-split-advanced>
    <p class="xs muted">Why this cut: ${p.why ?? `cut ${p.cut} of ${p.layers} layers`}. The rule: ${p.rule}.</p>
    <div class="dcard-tbl-wrap"><table class="dcard-tbl">
      <thead><tr><th>Part</th><th class="r">Front GiB</th><th class="r">Tail GiB</th><th>Figure</th></tr></thead>
      <tbody>
        ${ids.map((id) => html`<tr><td class="small">${id.replace(/_/g, ' ')}</td><td class="r num">${g1(part(p.front, id)?.gib)}</td><td class="r num">${g1(part(p.tail, id)?.gib)}</td><td class="xs muted">${basisWord((part(p.front, id) ?? part(p.tail, id))!.basis)}</td></tr>`)}
        <tr><td class="small"><b>need</b></td><td class="r num"><b>${g1(p.front.need_gib)}</b></td><td class="r num"><b>${g1(p.tail.need_gib)}</b></td><td class="xs muted">computed</td></tr>
        <tr><td class="small">budget</td><td class="r num">${g1(p.front.budget_gib)}</td><td class="r num">${g1(p.tail.budget_gib)}</td><td class="xs muted">each Spark’s own</td></tr>
        <tr><td class="small">disk: pack / cache</td><td class="r num">${g1(p.front.disk.pack_gib)} / ${g1(p.front.disk.cache_gib)}</td><td class="r num">${g1(p.tail.disk.pack_gib)} / ${g1(p.tail.disk.cache_gib)}</td><td class="xs muted">computed, GB</td></tr>
      </tbody>
    </table></div>
    <p class="xs muted">Margin: ${p.margin.text}.</p>
    ${p.measured.length
      ? html`<div class="dcard-tbl-wrap"><table class="dcard-tbl" data-split-measured>
          <thead><tr><th>Measured</th><th class="r">GiB</th><th class="r">s</th><th>Cut, date</th></tr></thead>
          <tbody>${p.measured.map((m) => html`<tr><td class="xs">${String(m.what ?? '')}</td><td class="r num xs">${fig(m.gib)}</td><td class="r num xs">${fig(m.seconds)}</td><td class="num xs muted">${fig(m.cut)}${m.date ? `, ${String(m.date)}` : ''}</td></tr>`)}</tbody>
        </table></div>
        <p class="xs faint">measured on one GPU box; every other figure here is computed</p>`
      : nothing}
    <dl class="kv small">
      <div><dt>shape</dt><dd class="num">${[sh.window ? `window ${sh.window.toLocaleString('en-US')}` : '', sh.slots ? `${sh.slots} slot${sh.slots === 1 ? '' : 's'}` : '', sh.side_window ? `side window ${sh.side_window.toLocaleString('en-US')}` : '', sh.caches ? `caches ${sh.caches}` : '', sh.table ? `table ${sh.table}` : ''].filter(Boolean).join(' · ')}</dd></div>
      <div><dt>link</dt><dd class="num">the front listens on ${p.link.listen}; the tail dials ${p.link.peer}</dd></div>
      ${lost.length ? html`<div><dt>other cuts</dt><dd><details><summary class="xs">${lost.length} not chosen</summary><ul class="xs muted sp-ctx-list">${lost.map((c) => html`<li class="num">cut ${c.cut}: front ${g1(c.front_gib)}, tail ${g1(c.tail_gib)} · ${c.why ?? ''}</li>`)}</ul></details></dd></div>` : nothing}
    </dl>
  </div>`;
}

/** A Split's two stages and their link: a deployment's (with its members' states) or a plan's. */
export function splitView(p: SplitPlan, ms: DeploymentMember[] | null, nodes: FleetNode[], simple: boolean): TemplateResult {
  const link = linkWords(p.link);
  const words = phaseWords(p.phase, p.front.alias, p.tail.alias);
  return html`<div class="sstages" data-split data-phase=${p.phase ?? 'plan'}>
    ${stage(p.front, ms?.[0], nodes)}
    <div class="slink" data-link=${p.link.state ?? 'planned'}><span class="small ${ms ? ink(link.tone) : 'muted'}">${ms ? link.text : `link: ${LINK_KIND[p.link.kind]} · ${p.link.listen}`}</span></div>
    ${stage(p.tail, ms?.[1], nodes)}
    ${words ? html`<p class="dcard-note small ${ink(words.tone)}" data-split-phase><b>${words.title}.</b> ${words.text}</p>` : nothing}
    ${!ms && p.link.warn ? html`<p class="dcard-warn small warn-ink"><span aria-hidden="true">⚠</span> ${p.link.warn}</p>` : nothing}
    ${simple ? nothing : advanced(p)}
  </div>`;
}

/** Simple or advanced, for the Models pages: two buttons, one pressed. */
export function modeSwitch(simple: boolean, set: (simple: boolean) => void): TemplateResult {
  const b = (on: boolean, label: string) => html`<button type="button" class="btn btn-sm ${simple === on ? 'btn-primary' : 'btn-ghost'}" aria-pressed=${simple === on} data-models-mode=${on ? 'simple' : 'advanced'} @click=${() => set(on)}>${label}</button>`;
  return html`<span class="row" role="group" aria-label="Detail">${b(true, 'Simple')}${b(false, 'Advanced')}</span>`;
}
