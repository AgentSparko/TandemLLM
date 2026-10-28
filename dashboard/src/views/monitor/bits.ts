// The Monitor pages' building blocks (VIS-248, the v3 prototype's monitor pages): the aggregate tile
// with each Spark's share, the unified memory bar, a node's sparkline, an event row, an alert card,
// a deployment's type and members, the node inspector and the controller's sign-in card.

import { html, nothing, svg, type TemplateResult } from 'lit';
import { fleet, type Agg, type DeploymentType, type FleetAlert, type FleetDeployment, type FleetEvent, type FleetNode, type NodeMemory, type NodeSample } from '../../api/fleet';
import { linear, monotonePath, type Pt } from '../../charts/svg';
import { ackInfo, aggregateNow, eventGlyph, eventTone, NOT_EXPOSED, shareSegments, shareTip, type SharePart } from '../../lib/monitor';
import { openWindow } from '../../ui/workspace';
import { icon } from '../../ui/bits';
import { nodeChip, nodeColour, isHollow, statusWords } from '../../ui/node-chip';
import '../../setup/signin';

export const fmt = (v: number | null | undefined, d = 0): string => (v == null || !Number.isFinite(v) ? '—' : v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }));
export const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

/** "4 min ago", "3 h ago", "2 d ago" */
export function since(t: number, now = Date.now() / 1000): string {
  const s = Math.max(0, now - t);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

/** "2 d 4 h", "5 h 12 min" */
export function span(s: number): string {
  if (s >= 86400) return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
  if (s >= 3600) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
  return `${Math.max(0, Math.floor(s / 60))} min`;
}

// ---- tiles ---------------------------------------------------------------------------------------

const OP: Record<Agg, string> = { sum: 'Σ', mean: 'mean', max: 'max', maxmean: 'max' };

export interface TileOpts {
  label: string;
  agg: Agg | null;
  value: string;
  unit?: string;
  sub?: string | TemplateResult;
  parts?: SharePart[];
  /** the share bar's free part (memory) */
  cap?: number | null;
  fmt?: (v: number) => string;
  id?: string;
}

/** An aggregate tile: the total (its operation stated), a thin bar of each Spark's share (a sum only)
 *  whose tooltip lists every Spark's value, and a line under it. */
export function tile(o: TileOpts): TemplateResult {
  const f = o.fmt ?? ((v: number) => fmt(v));
  const parts = o.parts ?? [];
  const share = o.agg === 'sum' && parts.length ? shareSegments(parts, o.cap ?? null) : [];
  const tip = parts.length ? shareTip(parts, o.unit?.replace(/^\/.*$/, '') ?? '', f, o.agg ? aggregateNow(parts, o.agg) : null) : '';
  return html`<div class="mtile" data-tile=${o.id ?? nothing}>
    <div class="mtile-label">${o.label}${o.agg ? html`<span class="op">${OP[o.agg]}</span>` : nothing}</div>
    <div class="mtile-val num">${o.value}${o.unit ? html`<span class="u">${o.unit}</span>` : nothing}</div>
    ${share.length
      ? html`<div class="share" title=${tip} role="img" aria-label=${tip.replace(/\n/g, '; ')}>${share.map((s) => html`<i class=${s.id ? (isHollow(s.colour ?? 1) ? 'is-hollow' : '') : 'is-free'} style="flex-grow:${s.f.toFixed(4)};--c:${s.colour ? nodeColour(s.colour) : 'transparent'}"></i>`)}</div>`
      : parts.length > 1
        ? html`<div class="share-list small muted" title=${tip}>${parts.map((p) => html`<span><i class="node-sw ${isHollow(p.colour) ? 'is-hollow' : ''}" style="--c:${nodeColour(p.colour)}"></i>${p.v == null ? '—' : f(p.v)}</span>`)}</div>`
        : nothing}
    ${o.sub ? html`<div class="mtile-sub">${o.sub}</div>` : nothing}
  </div>`;
}

// ---- the unified memory bar ----------------------------------------------------------------------

const MEM_PARTS: [keyof Pick<NodeMemory, 'weights' | 'kv' | 'cache' | 'other' | 'page'>, string][] = [
  ['weights', 'weights'],
  ['kv', 'KV pool'],
  ['cache', 'caches'],
  ['other', 'other processes'],
  ['page', 'page cache'],
];

/** The unified memory split of one Spark: weights, KV, caches, other processes, page cache and what
 *  is free, with the engine's ceiling and the 24 h peak marked (`sm`: the thin bar of a card). */
export function memBar(m: NodeMemory | null | undefined, sm = false): TemplateResult {
  if (!m) return html`<div class="umem ${sm ? 'is-sm' : ''} is-empty" title="no memory split from this Spark"></div>`;
  const used = MEM_PARTS.reduce((a, [k]) => a + m[k], 0);
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / m.usable) * 100)).toFixed(2)}%`;
  const title = `${MEM_PARTS.map(([k, l]) => `${l} ${m[k].toFixed(1)}`).join(' · ')} · free ${Math.max(0, m.usable - used).toFixed(1)} of ${m.usable} GiB`;
  return html`<div class="umem ${sm ? 'is-sm' : ''}" role="img" aria-label=${title} title=${title}>
    ${MEM_PARTS.map(([k]) => html`<i class="m-${k}" style="width:${pct(m[k])}"></i>`)}
    ${m.ceiling > 0 ? html`<span class="umem-mark" style="left:${pct(m.ceiling)}">${sm ? nothing : html`<span>ceiling ${m.ceiling}</span>`}</span>` : nothing}
    ${m.peak > 0 ? html`<span class="umem-mark is-peak" style="left:${pct(m.peak)}">${sm ? nothing : html`<span>24 h peak ${m.peak.toFixed(1)}</span>`}</span>` : nothing}
  </div>`;
}

export function memLegend(m: NodeMemory): TemplateResult {
  const used = MEM_PARTS.reduce((a, [k]) => a + m[k], 0);
  return html`<ul class="umem-legend small">
    ${MEM_PARTS.map(([k, l]) => html`<li><i class="m-${k}"></i>${l} <span class="num">${m[k].toFixed(1)}</span></li>`)}
    <li><i class="m-free"></i>free <span class="num">${Math.max(0, m.usable - used).toFixed(1)}</span></li>
    <li class="muted">of <span class="num">${m.usable}</span> GiB usable</li>
  </ul>`;
}

// ---- a node's sparkline --------------------------------------------------------------------------

/** The newest minutes of a value as a line in the node's colour, nulls as gaps. */
export function sparkline(values: (number | null)[], colour: number, h = 30, label = ''): TemplateResult {
  const W = 200;
  const finite = values.filter((v): v is number => v != null);
  const top = Math.max(1e-9, ...finite) * 1.1;
  const x = linear([0, Math.max(1, values.length - 1)], [1, W - 1]);
  const y = linear([0, top], [h - 1, 2]);
  const pts: (Pt | null)[] = values.map((v, i) => (v == null ? null : { x: x(i), y: y(v) }));
  const d = monotonePath(pts);
  const c = nodeColour(colour);
  return html`<svg class="nspark" viewBox="0 0 ${W} ${h}" preserveAspectRatio="none" role="img" aria-label=${label} height=${h}>
    ${finite.length ? svg`<path d=${`${d} L${x(values.length - 1)} ${h} L${x(values.findIndex((v) => v != null))} ${h} Z`} fill=${c} opacity="0.14" /><path d=${d} fill="none" stroke=${c} stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-dasharray=${isHollow(colour) ? '4 3' : nothing} />` : nothing}
  </svg>`;
}

// ---- deployments ---------------------------------------------------------------------------------

const TYPE_WORD: Record<DeploymentType, string> = { single: 'Single', replicas: 'Replicas', split: 'Split', role: 'Role' };
export const typeBadge = (t: DeploymentType) => html`<span class="tag dep-type is-${t}">${TYPE_WORD[t]}</span>`;

/** A deployment's members as chips: a Split's stages joined by arrows in pipeline order. */
export function members(d: FleetDeployment, nodes: FleetNode[]): TemplateResult {
  const chip = (id: string) => {
    const n = nodes.find((x) => x.id === id);
    return n ? nodeChip(n, { href: `#/sparks/${encodeURIComponent(id)}` }) : html`<span class="small muted">${id}</span>`;
  };
  return html`<span class="dep-members">${d.members.map((m, i) => html`${i && d.type === 'split' ? html`<span class="faint num" aria-label="then">→</span>` : nothing}${chip(m)}`)}</span>`;
}

/** What a Spark serves, one line per deployment: its type, name and place in it. */
export function serves(n: FleetNode, deps: FleetDeployment[]): TemplateResult {
  const mine = deps.filter((d) => d.members.includes(n.id));
  if (!mine.length) return html`<span class="muted">serves nothing</span>`;
  return html`${mine.map((d) => {
    const i = d.members.indexOf(n.id);
    const where = d.type === 'split' ? `stage ${i + 1}/${d.members.length}` : d.type === 'replicas' ? `${i + 1} of ${d.members.length}` : '';
    return html`<div>${typeBadge(d.type)} ${d.name}${where ? html` <span class="faint">${where}</span>` : nothing}</div>`;
  })}`;
}

// ---- events and alerts ---------------------------------------------------------------------------

/** Who an event or alert is about: a Spark's chip, the fleet, or the controller's machine. */
export function subject(id: string, nodes: FleetNode[], hostName = 'controller host'): TemplateResult {
  if (id === 'all') return html`<span class="node-chip"><i class="node-sw is-fleet"></i><span class="node-alias">fleet</span></span>`;
  if (id === 'host') return html`<span class="node-chip"><i class="node-sw is-host"></i><span class="node-alias">${hostName}</span></span>`;
  const n = nodes.find((x) => x.id === id);
  return n ? nodeChip(n, { status: false }) : html`<span class="small muted">${id}</span>`;
}

export function eventRow(e: FleetEvent, nodes: FleetNode[], deps: FleetDeployment[], hostName?: string): TemplateResult {
  const d = e.deployment ? deps.find((x) => x.id === e.deployment) : null;
  return html`<div class="ev-item" data-event=${e.id} data-type=${e.type}>
    <span class="ev-time num" title=${new Date(e.at * 1000).toLocaleString('en-GB')}>${clock(e.at)}</span>
    <span class="ev-glyph is-${eventTone(e) || 'plain'}" aria-hidden="true">${eventGlyph(e)}</span>
    <div class="ev-text">${e.text}${e.detail ? html`<div class="d">${e.detail}</div>` : nothing}</div>
    <span class="ev-who">${subject(e.node, nodes, hostName)}${d ? html`<span class="tag">${d.name.split(/[\s-]/)[0]}</span>` : nothing}</span>
  </div>`;
}

export interface AlertCardOpts {
  nodes: FleetNode[];
  events: FleetEvent[];
  hostName?: string;
  /** the controller wants its password first: the button says so and is off */
  locked: boolean;
  busy?: boolean;
  onAck: (a: FleetAlert) => void;
  compact?: boolean;
}

/** An alert: severity, title, who, what, since when and by which rule, and Acknowledge (or who
 *  acknowledged it and when) beside its action; an acknowledged one stays until it resolves. */
export function alertCard(a: FleetAlert, o: AlertCardOpts): TemplateResult {
  const ack = ackInfo(a, o.events);
  return html`<div class="alert-card is-${a.severity} ${a.acked ? 'is-acked' : ''} ${o.compact ? 'is-compact' : ''}" data-alert=${a.id}>
    <span class="stripe" aria-hidden="true"></span>
    <div class="alert-main">
      <div class="alert-h">
        <span class="sev ${a.severity === 'err' ? 'err-ink' : 'warn-ink'}">⚠ ${a.severity === 'err' ? 'Serious' : 'Warning'}</span>
        <b>${a.title}</b>
        ${subject(a.node, o.nodes, o.hostName)}
      </div>
      ${o.compact ? nothing : html`<p class="small muted">${a.detail}</p>`}
      <p class="xs faint num">since ${clock(a.since)} (${since(a.since)}) · ${a.rule}${ack ? html` · <span data-acked>acknowledged ${ack.at != null ? `at ${clock(ack.at)}` : ''}${ack.who ? ` by ${ack.who}` : ''}</span>` : nothing}</p>
    </div>
    <div class="alert-acts">
      ${a.acked
        ? html`<span class="tag">acknowledged</span>`
        : html`<button type="button" class="btn btn-sm" ?disabled=${o.locked || o.busy} title=${o.locked ? 'Sign in with the admin password first' : 'It stays listed until the cause clears'} @click=${() => o.onAck(a)}>Acknowledge</button>`}
      ${a.action ? html`<a class="btn btn-ghost btn-sm" href=${a.action.route}>${a.action.label}</a>` : nothing}
    </div>
  </div>`;
}

/** The controller's sign-in, for its writes: open when something waits for it, else one line. */
export function signInCard(what: string, open = true): TemplateResult {
  return html`<details class="mon-signin-box" ?open=${open}><summary class="small warn-ink">⚠ ${what} need TandemApp’s admin password: sign in</summary><qse-setup-signin
    class="mon-signin"
    .signIn=${(p: string) => fleet.signIn(p)}
    .texts=${{ title: 'Sign in to TandemApp’s controller', body: `${what} need TandemApp’s admin password: the TANDEM_ADMIN_TOKEN its controller runs with. Reading needs none.`, kept: 'The session lasts 12 hours in this browser.' }}
  ></qse-setup-signin></details>`;
}

// ---- the node inspector --------------------------------------------------------------------------

export interface InspectorData {
  node: FleetNode;
  deps: FleetDeployment[];
  now: Record<string, number | null>;
  memory: NodeMemory | null;
  sample?: NodeSample | null;
}

/** One Spark at a glance: status, figures now, memory, what it serves, how it is reached, its
 *  hardware, version, trust and lease, and the windows to open on it. */
export function inspector(d: InspectorData): TemplateResult {
  const n = d.node;
  const v = (k: string, dec = 0) => fmt(d.now[k], dec);
  const p = n.pairing;
  const trust = p.state === 'local' ? 'this machine' : p.state === 'paired' ? `paired by ${p.via}${p.fingerprint ? ` · ${p.fingerprint.slice(0, 12)}…` : ''}` : 'not paired';
  const lease = n.lease_end ? `ends in ${span(n.lease_end - Date.now() / 1000)}${n.cost_per_hour ? ` · ${n.cost_per_hour.toFixed(2)}/h` : ''}` : n.owned ? 'owned' : 'none';
  return html`<div class="insp" data-inspector=${n.id}>
    <div class="insp-h">${nodeChip(n, { lg: true })}<span class="small muted">${statusWords(n)}</span></div>
    ${n.status === 'alert' || n.status === 'off' ? html`<p class="small ${n.status === 'off' ? 'err-ink' : 'warn-ink'}">⚠ ${n.status_text}</p>` : nothing}
    <div class="insp-figs">
      <div><span>tok/s</span><b class="num">${v('tps')}</b></div>
      <div><span>GPU °C</span><b class="num">${v('temp')}</b></div>
      <div><span>W</span><b class="num">${v('power')}</b></div>
      <div><span>GPU %</span><b class="num">${v('gpu')}</b></div>
    </div>
    ${d.memory ? html`<div class="insp-mem"><div class="small muted">Unified memory <span class="num">${(d.memory.weights + d.memory.kv + d.memory.cache + d.memory.other).toFixed(1)} of ${d.memory.usable} GiB</span></div>${memBar(d.memory, true)}</div>` : nothing}
    <dl class="kv">
      <div><dt>serves</dt><dd>${serves(n, d.deps)}</dd></div>
      <div><dt>reach</dt><dd>${n.transport === 'tunnel' ? 'SSH tunnel' : 'LAN'} · <span class="num">${n.address}</span></dd></div>
      <div><dt>hardware</dt><dd>${n.dmi || n.hw}</dd></div>
      <div><dt>version</dt><dd class="num">${n.version || '—'}</dd></div>
      <div><dt>trust</dt><dd>${trust}</dd></div>
      <div><dt>lease</dt><dd>${lease}</dd></div>
      ${d.sample !== undefined ? html`<div><dt>fan</dt><dd>${d.sample?.board?.fans?.length ? d.sample.board.fans.map((f) => `${fmt(f.rpm)} rpm`).join(' · ') : d.sample ? NOT_EXPOSED : '—'}</dd></div>` : nothing}
    </dl>
    <div class="insp-acts">
      <button type="button" class="btn btn-sm" @click=${() => openWindow('inspector', { node: n.id })}>${icon('window')} Inspector window</button>
      <button type="button" class="btn btn-sm" @click=${() => openWindow('logs', { node: n.id })}>${icon('doc')} Logs</button>
      <button type="button" class="btn btn-ghost btn-sm" @click=${() => openWindow('terminal', { node: n.id })}>${icon('terminal')} Terminal</button>
      <a class="btn btn-ghost btn-sm" href=${`#/sparks/${encodeURIComponent(n.id)}`}>Node page</a>
    </div>
  </div>`;
}
