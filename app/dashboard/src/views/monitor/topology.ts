// <qse-topology>: the fleet as a graph (VIS-248, concept §8 B2, the v3 prototype's topology): the
// controller's host, the LAN, the internet, the rental gateway and the fabric switch, every Spark with
// what it serves and its figures now, the links with their speed, latency and state, traffic moving
// along them as fast as it flows, and each Split and Replicas deployment drawn over its members. A
// Spark, the host or a link is a button: the page decides what a pick opens (`qse-topo-pick`). The
// layout is lib/topology.ts; Sparks → Topology (VIS-250) draws the same element full size.

import { html, nothing, svg, type SVGTemplateResult } from 'lit';
import { customElement, property } from 'lit/decorators.js';
import type { FleetDeployment, FleetTopology } from '../../api/fleet';
import { hwShort, topoLayout, type TBox, type TEdge, type THull, type TLayout } from '../../lib/topology';
import { LightElement } from '../../ui/base';
import { nodeColour, isHollow, STATUS_GLYPH, statusWords } from '../../ui/node-chip';

export interface TopoPick {
  kind: 'node' | 'host' | 'infra' | 'link';
  id: string;
}

/** the figures the graph shows now: metric → node → value */
export type TopoNow = Partial<Record<'tps' | 'temp' | 'power' | 'lan' | 'fabric', Record<string, number | null>>>;

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

@customElement('qse-topology')
export class QseTopology extends LightElement {
  @property({ attribute: false }) topo: FleetTopology | null = null;
  @property({ attribute: false }) deployments: FleetDeployment[] = [];
  @property({ attribute: false }) now: TopoNow = {};
  @property({ type: Boolean }) compact = false;
  /** `node:<id>`, `link:<id>`, `host` */
  @property({ type: String }) selected = '';
  private laidFor: FleetTopology | null = null;
  private lay: TLayout | null = null;

  private layout(): TLayout | null {
    if (!this.topo) return null;
    if (this.laidFor !== this.topo) {
      this.laidFor = this.topo;
      this.lay = topoLayout(this.topo);
    }
    return this.lay;
  }

  private pick(p: TopoPick) {
    this.dispatchEvent(new CustomEvent<TopoPick>('qse-topo-pick', { detail: p, bubbles: true }));
  }
  private key(e: KeyboardEvent, p: TopoPick) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    this.pick(p);
  }

  private v(k: keyof TopoNow, id: string): number | null {
    return this.now[k]?.[id] ?? null;
  }

  /** how much moves on a link now, 0..1: LAN MB/s, fabric Gb/s, a tunnel's share of the remote's */
  private load(e: TEdge, l: TLayout): number | null {
    if (e.ghost || e.state === 'down') return null;
    const nodeEnd = [e.a, e.b].find((x) => l.boxes.some((b) => b.id === x && b.kind === 'node'));
    if (e.kind === 'fabric') {
      const ids = [e.a, e.b].filter((x) => l.boxes.some((b) => b.id === x && b.kind === 'node'));
      const g = ids.reduce((a, id) => a + (this.v('fabric', id) ?? 0), 0);
      return ids.some((id) => this.v('fabric', id) != null) ? clamp(g / 12, 0.04, 1) : null;
    }
    const lanOf = (ids: string[]) => ids.reduce((a, id) => a + (this.v('lan', id) ?? 0), 0);
    if (nodeEnd) {
      const v = this.v('lan', nodeEnd);
      return v == null ? null : clamp(v / (e.kind === 'wan' ? 4 : 8), 0.04, 1);
    }
    // between infra: what the Sparks behind it move
    const behind = l.boxes.filter((b) => b.kind === 'node' && (e.kind === 'wan' ? b.node?.transport === 'tunnel' : b.node?.transport !== 'tunnel')).map((b) => b.id);
    return behind.length ? clamp(lanOf(behind) / 10, 0.04, 1) : null;
  }

  private edge(e: TEdge, l: TLayout): SVGTemplateResult {
    const load = this.load(e, l);
    const sel = this.selected === `link:${e.id}`;
    const showLbl = !this.compact || l.edges.length <= 6 || e.kind === 'fabric';
    const cls = e.kind === 'fabric' ? 'l-fab' : e.kind === 'wan' ? 'l-wan' : 'l-lan';
    const title = e.link ? `${e.link.kind === 'fabric' ? 'Fabric' : e.link.kind === 'wan' ? 'Internet / tunnel' : 'LAN'} · ${e.label} · ${e.state}${e.link.mtu ? ` · MTU ${e.link.mtu}` : ''}${e.link.note ? ` · ${e.link.note}` : ''}` : e.label;
    return svg`<g class="link-g is-${e.state} ${sel ? 'is-sel' : ''} ${e.ghost ? 'is-ghost' : ''}" data-link=${e.id}>
      ${e.ghost ? nothing : svg`<path class="l-hit" d=${e.d} tabindex="0" role="button" aria-label=${title} @click=${() => this.pick({ kind: 'link', id: e.id })} @keydown=${(ev: KeyboardEvent) => this.key(ev, { kind: 'link', id: e.id })}><title>${title}</title></path>`}
      ${e.kind === 'fabric' ? svg`<path class="l-fab-glow" d=${e.d} />` : nothing}
      <path class="l-show ${cls}" d=${e.d} />
      ${load != null ? svg`<path class="flow" d=${e.d} style=${`--fd:${(2.6 - 1.9 * load).toFixed(2)}s;stroke-width:${(1.4 + 1.6 * load).toFixed(1)}px;opacity:${(0.35 + 0.65 * load).toFixed(2)}`} />` : nothing}
      ${showLbl && e.label ? svg`<text class="t-link-lbl" x=${e.lx} y=${e.ly} text-anchor=${e.ghost ? 'start' : 'middle'}>${e.label}${e.state !== 'up' ? ` · ${e.state}` : ''}</text>` : nothing}
    </g>`;
  }

  private hull(h: THull): SVGTemplateResult {
    const d = this.deployments.find((x) => x.id === h.deployment);
    const tag = `${h.type === 'split' ? 'SPLIT' : 'REPLICAS'} · ${(d?.name ?? h.deployment).toUpperCase()}`;
    const tw = tag.length * 6.6 + 16;
    return svg`<g class="dep-g is-${h.type}" data-hull=${h.deployment}>
      ${h.rect ? svg`<rect class="hull" x=${h.rect.x} y=${h.rect.y} width=${h.rect.w} height=${h.rect.h} rx="14" />` : svg`<path class="rep" d=${h.arc} />`}
      <rect class="dep-tag-bg" x=${h.rect ? h.tx : h.tx - tw / 2} y=${h.ty - 13} width=${tw} height="18" rx="4" />
      <text class="dep-tag" x=${h.rect ? h.tx + 8 : h.tx} y=${h.ty} text-anchor=${h.rect ? 'start' : 'middle'}>${tag}</text>
    </g>`;
  }

  private node(b: TBox): SVGTemplateResult {
    const n = b.node!;
    const x0 = b.x - b.w / 2;
    const y0 = b.y - b.h / 2;
    const st = n.status;
    const sel = this.selected === `node:${n.id}`;
    const deps = this.deployments.filter((d) => d.members.includes(n.id));
    const what = deps.length
      ? deps.map((d) => (d.type === 'split' ? `${d.name.split(/[\s-]/)[0]} stage ${d.members.indexOf(n.id) + 1}` : d.type === 'replicas' ? `${d.name} ${d.members.indexOf(n.id) + 1}/${d.members.length}` : d.type === 'role' ? `${d.name} (role)` : d.name)).join(' · ')
      : 'serves nothing';
    const f = (k: keyof TopoNow, u: string) => {
      const v = this.v(k, n.id);
      return v == null ? null : `${Math.round(v)} ${u}`;
    };
    const figs = st === 'off' ? n.status_text : [f('tps', 'tok/s'), f('temp', '°C'), f('power', 'W')].filter(Boolean).join(' · ') || 'no figures yet';
    const c = nodeColour(n.colour);
    const label = `${n.alias}, ${statusWords(n)}. ${what}. ${figs}${b.controller ? '. Runs the controller' : ''}`;
    const maxCh = Math.floor((b.w - 24) / 6.4);
    const cut = (s: string) => (s.length > maxCh ? `${s.slice(0, maxCh - 1)}…` : s);
    return svg`<g class="node-g is-${st} ${sel ? 'is-sel' : ''}" data-node=${n.id} tabindex="0" role="button" aria-label=${label} @click=${() => this.pick({ kind: 'node', id: n.id })} @keydown=${(e: KeyboardEvent) => this.key(e, { kind: 'node', id: n.id })}>
      <title>${label}</title>
      ${b.controller ? svg`<text class="t-lbl" x=${x0 + 2} y=${y0 - 7}>CONTROLLER · API</text>` : nothing}
      <rect class="node-box" x=${x0} y=${y0} width=${b.w} height=${b.h} rx="9" style=${`--nc:${c}`} />
      <rect class="node-wash" x=${x0 + 1} y=${y0 + 1} width=${b.w - 2} height="3" rx="1.5" fill=${c} />
      <rect x=${x0 + 12} y=${y0 + 14} width="11" height="11" rx="2.5" fill=${isHollow(n.colour) ? 'none' : c} stroke=${c} stroke-width=${isHollow(n.colour) ? 2 : 0} />
      <text class="t-name" x=${x0 + 30} y=${y0 + 24}>${n.alias}</text>
      <text class="t-sub t-st is-${st}" x=${x0 + b.w - 12} y=${y0 + 24} text-anchor="end">${STATUS_GLYPH[st]} ${st === 'draining' && n.drain?.state === 'drained' ? 'drained' : st}</text>
      <text class="t-sub" x=${x0 + 12} y=${y0 + 42}>${cut(`${hwShort(n)} · ${n.transport === 'tunnel' ? 'rented · tunnel' : 'LAN'}`)}</text>
      <text class="t-sub t-serves" x=${x0 + 12} y=${y0 + 60}>${cut(what)}</text>
      <text class="t-sub t-figs" x=${x0 + 12} y=${y0 + 78}>${cut(figs)}</text>
    </g>`;
  }

  private infra(b: TBox): SVGTemplateResult {
    const t = this.topo!;
    if (b.kind === 'host') {
      const x0 = b.x - b.w / 2;
      const sel = this.selected === 'host';
      return svg`<g class="node-g host-g ${sel ? 'is-sel' : ''}" data-host tabindex="0" role="button" aria-label=${`${t.host.name}: runs the controller and the API${t.host.may_sleep ? '; it may sleep' : ''}`} @click=${() => this.pick({ kind: 'host', id: 'host' })} @keydown=${(e: KeyboardEvent) => this.key(e, { kind: 'host', id: 'host' })}>
        <rect class="host-box" x=${x0} y=${b.y - b.h / 2} width=${b.w} height=${b.h} rx="9" />
        <text class="t-lbl" x=${x0 + 12} y=${b.y - 22}>CONTROLLER · API</text>
        <text class="t-name" x=${x0 + 12} y=${b.y - 3}>${t.host.name}</text>
        <text class="t-sub" x=${x0 + 12} y=${b.y + 13}>${t.host.address}</text>
        ${t.host.may_sleep ? svg`<text class="t-sub t-warn" x=${x0 + 12} y=${b.y + 29}>⚠ may sleep</text>` : nothing}
      </g>`;
    }
    if (b.kind === 'switch') {
      return svg`<g class="infra-g" data-infra=${b.id}><rect class="infra-box" x=${b.x - b.w / 2} y=${b.y - b.h / 2} width=${b.w} height=${b.h} rx="8" /><text class="t-lbl" transform=${`translate(${b.x + 4} ${b.y}) rotate(-90)`} text-anchor="middle">${b.label.toUpperCase()}</text></g>`;
    }
    const sub = b.kind === 'lan' ? subnet(t.host.address) : b.kind === 'internet' ? (b.ghost ? 'rented Sparks come in here' : 'uplink') : b.kind === 'gateway' ? 'SSH jump host' : '';
    return svg`<g class="infra-g ${b.ghost ? 'is-ghost' : ''}" data-infra=${b.id}>
      <rect class="infra-box" x=${b.x - b.w / 2} y=${b.y - b.h / 2} width=${b.w} height=${b.h} rx=${b.h / 2} />
      <text class="t-lbl" x=${b.x} y=${b.y - 2} text-anchor="middle">${b.label.toUpperCase()}</text>
      <text class="t-sub" x=${b.x} y=${b.y + 11} text-anchor="middle">${sub}</text>
    </g>`;
  }

  render() {
    const l = this.layout();
    if (!l) return nothing;
    const t = this.topo!;
    const desc = `Fleet topology: ${t.nodes.length} Spark${t.nodes.length === 1 ? '' : 's'}${t.infra.length ? `, ${t.infra.map((i) => i.label).join(', ')}` : ''}; the controller on ${t.host.name}`;
    return html`<div class="topo-wrap ${this.compact ? 'is-compact' : ''}">
      <svg class="topo" viewBox="0 0 ${l.w} ${l.h}" style=${`--topo-h:${l.h}`} role="group" aria-label=${desc}>
        <defs><filter id="tglow" x="-20%" y="-50%" width="140%" height="200%"><feGaussianBlur stdDeviation="3" /></filter></defs>
        ${l.hulls.map((h) => this.hull(h))}
        ${l.edges.map((e) => this.edge(e, l))}
        ${l.boxes.filter((b) => b.kind !== 'node').map((b) => this.infra(b))}
        ${l.boxes.filter((b) => b.kind === 'node').map((b) => this.node(b))}
      </svg>
    </div>
    ${this.compact ? nothing : legend()}`;
  }
}

/** a /24 for an IPv4 address, else the address */
function subnet(a: string): string {
  const m = /^(\d+\.\d+\.\d+)\.\d+$/.exec(a);
  return m ? `${m[1]}.0/24` : a;
}

export function legend() {
  const ln = (cls: string) => html`<svg viewBox="0 0 26 10" aria-hidden="true"><path class=${cls} d="M1 5H25" /></svg>`;
  return html`<div class="topo-legend small">
    <span>${ln('lg-lan')}LAN (mTLS)</span><span>${ln('lg-wan')}internet / SSH tunnel</span><span>${ln('lg-fab')}fabric</span><span>${ln('lg-flow')}traffic now</span>
    <span><svg viewBox="0 0 26 10" aria-hidden="true"><rect x="1" y="1" width="24" height="8" rx="3" class="lg-hull" /></svg>Split</span><span>${ln('lg-rep')}Replicas</span>
    <span>● up · ◐ draining · ○ off · ⚠ alert</span>
  </div>`;
}

declare global {
  interface HTMLElementTagNameMap {
    'qse-topology': QseTopology;
  }
}
