// The topology graph's layout (VIS-248, concept §8 B2): where the controller's host, the LAN, the
// internet, the rental gateway, the fabric switch and each Spark sit, the path of every link and the
// deployments drawn over their members, from /v1/fleet/topology alone. Columns left to right: the
// host, the networks, the Sparks on the LAN (and the gateway below them), the Sparks behind the
// gateway, the switch. A fleet of one draws its box beside the LAN and the internet it would reach
// rented Sparks through. Pure: <qse-topology> (views/monitor/topology.ts) draws what this says.

import type { FleetLink, FleetNode, FleetTopology, InfraItem, LinkKind, LinkState, TopologyGroup } from '../api/fleet';

export const TOPO_W = 1040;

export interface TBox {
  id: string;
  kind: InfraItem['kind'] | 'node';
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  node?: FleetNode;
  /** the controller runs on this node (no host of its own) */
  controller?: boolean;
  /** drawn for context, not reported by the controller (a fleet of one's internet) */
  ghost?: boolean;
}

export interface TEdge {
  id: string;
  a: string;
  b: string;
  kind: LinkKind;
  state: LinkState;
  /** the SVG path */
  d: string;
  /** where its label sits */
  lx: number;
  ly: number;
  label: string;
  link?: FleetLink;
  ghost?: boolean;
}

export interface THull {
  deployment: string;
  type: TopologyGroup['type'];
  members: string[];
  /** a rectangle around members in one column, else an arc over them */
  rect?: { x: number; y: number; w: number; h: number };
  arc?: string;
  tx: number;
  ty: number;
}

export interface TLayout {
  w: number;
  h: number;
  boxes: TBox[];
  edges: TEdge[];
  hulls: THull[];
}

const NODE_W = 210;
const NODE_H = 92;
const NODE_GAP = 22;
const PILL = { w: 150, h: 40 };
const HOST = { w: 176, h: 84 };
const SW_W = 54;
const TOP = 48;

/** a short name for the box: the board's maker and model */
export function hwShort(n: Pick<FleetNode, 'hw' | 'dmi'>): string {
  if (/GX10/i.test(n.dmi)) return 'ASUS GX10';
  return n.hw.replace(/^NVIDIA /, '').replace(/ Founders Edition$/, ' FE');
}

const hostOf = (a: string) => a.replace(/:\d+$/, '');

/** The node the controller runs on when the topology has no host of its own. */
export function controllerNode(t: Pick<FleetTopology, 'host' | 'infra' | 'nodes'>): string | null {
  if (t.infra.some((i) => i.kind === 'host')) return null;
  const n = t.nodes.find((x) => hostOf(x.address) === t.host.address) ?? t.nodes.find((x) => x.alias === t.host.name);
  return n?.id ?? null;
}

/** A node reached through the rental gateway or a tunnel sits right of the gateway. */
function remote(n: FleetNode, links: FleetLink[], gw: string | null): boolean {
  if (gw && links.some((l) => (l.a === gw && l.b === n.id) || (l.b === gw && l.a === n.id))) return true;
  return n.transport === 'tunnel' && !links.some((l) => l.kind === 'lan' && (l.a === n.id || l.b === n.id));
}

const curve = (a: [number, number], b: [number, number]) => `M${a[0]} ${a[1]} C ${(a[0] + b[0]) / 2} ${a[1]}, ${(a[0] + b[0]) / 2} ${b[1]}, ${b[0]} ${b[1]}`;

export function linkLabel(l: FleetLink): string {
  if (l.kind === 'fabric') return l.test ? `${l.speed} · ${l.test.gbps} Gb/s` : l.speed;
  const rtt = l.rtt_ms == null ? '' : ` · ${l.rtt_ms < 1 ? l.rtt_ms.toFixed(1) : Math.round(l.rtt_ms)} ms`;
  return `${l.speed}${rtt}`;
}

export function topoLayout(t: FleetTopology): TLayout {
  const infra = (k: InfraItem['kind']) => t.infra.find((i) => i.kind === k) ?? null;
  const hostI = infra('host');
  const lanI = infra('lan');
  const gwI = infra('gateway');
  const swI = infra('switch');
  let inetI: InfraItem | null = infra('internet');
  const ctl = controllerNode(t);
  // a fleet of one: its internet, which rented Sparks come through, drawn for context
  const ghostInet = !inetI && t.nodes.length === 1 && !!lanI;
  if (ghostInet) inetI = { id: 'inet', kind: 'internet', label: 'Internet' };
  const isFar = t.nodes.map((n) => remote(n, t.links, gwI?.id ?? null));
  const near = t.nodes.filter((_, i) => !isFar[i]);
  // a remote Spark that shares a deployment with one on the LAN goes first, level with it
  const linked = (n: FleetNode) => t.groups.some((g) => g.members.includes(n.id) && g.members.some((m) => near.some((x) => x.id === m)));
  const far = t.nodes.filter((_, i) => isFar[i]).sort((a, b) => Number(linked(b)) - Number(linked(a)));

  // columns present, left to right, and their widths
  const cols: { key: string; w: number }[] = [];
  if (hostI) cols.push({ key: 'host', w: HOST.w });
  if (lanI || inetI) cols.push({ key: 'net', w: PILL.w });
  if (near.length || gwI) cols.push({ key: 'near', w: t.nodes.length === 1 ? NODE_W + 40 : NODE_W });
  if (far.length) cols.push({ key: 'far', w: NODE_W - 10 });
  if (swI) cols.push({ key: 'sw', w: SW_W });
  const used = cols.reduce((a, c) => a + c.w, 0);
  const gap = Math.max(36, (TOPO_W - used) / (cols.length + 1));
  const x: Record<string, number> = {};
  let at = gap;
  for (const c of cols) {
    x[c.key] = at + c.w / 2;
    at += c.w + gap;
  }
  const W = Math.max(TOPO_W, at);
  const boxes: TBox[] = [];
  const nodeW = (key: string) => cols.find((c) => c.key === key)!.w;

  // the Sparks: stacked from the top in their column
  let y = TOP;
  for (const n of near) {
    boxes.push({ id: n.id, kind: 'node', x: x.near, y: y + NODE_H / 2, w: nodeW('near'), h: NODE_H, label: n.alias, node: n, controller: n.id === ctl });
    y += NODE_H + NODE_GAP;
  }
  const nearBottom = y - NODE_GAP;
  let fy = TOP;
  for (const n of far) {
    boxes.push({ id: n.id, kind: 'node', x: x.far, y: fy + NODE_H / 2, w: nodeW('far'), h: NODE_H, label: n.alias, node: n, controller: n.id === ctl });
    fy += NODE_H + NODE_GAP;
  }
  const farBottom = fy - NODE_GAP;
  const nearMid = near.length ? (TOP + nearBottom) / 2 : TOP + NODE_H / 2;
  if (gwI) boxes.push({ id: gwI.id, kind: 'gateway', x: x.near, y: Math.max(nearBottom + 70, farBottom - PILL.h / 2), w: PILL.w + 20, h: PILL.h, label: gwI.label });
  const gwBox = gwI ? boxes[boxes.length - 1] : null;
  if (lanI) boxes.push({ id: lanI.id, kind: 'lan', x: x.net, y: near.length ? nearMid : TOP + PILL.h, w: PILL.w, h: PILL.h, label: lanI.label });
  const lanBox = lanI ? boxes[boxes.length - 1] : null;
  if (inetI) boxes.push({ id: inetI.id, kind: 'internet', x: x.net, y: gwBox ? gwBox.y : (lanBox ? lanBox.y : TOP) + 100, w: PILL.w, h: PILL.h, label: inetI.label, ghost: ghostInet });
  const inetBox = inetI ? boxes[boxes.length - 1] : null;
  if (hostI) {
    const ys = [lanBox?.y, inetBox?.y].filter((v): v is number => v != null);
    boxes.push({ id: hostI.id, kind: 'host', x: x.host, y: ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : TOP + HOST.h / 2, w: HOST.w, h: HOST.h, label: t.host.name });
  }
  if (swI) {
    const fab = t.links.filter((l) => l.kind === 'fabric' && (l.a === swI.id || l.b === swI.id)).map((l) => (l.a === swI.id ? l.b : l.a));
    const ys = boxes.filter((b) => fab.includes(b.id)).map((b) => b.y);
    const lo = ys.length ? Math.min(...ys) - NODE_H / 2 : TOP;
    const hi = ys.length ? Math.max(...ys) + NODE_H / 2 : TOP + NODE_H;
    boxes.push({ id: swI.id, kind: 'switch', x: x.sw, y: (lo + hi) / 2, w: SW_W, h: hi - lo, label: swI.label });
  }
  const box = (id: string) => boxes.find((b) => b.id === id);
  // a link whose host end is missing (the controller runs on a node) starts at that node
  const end = (id: string) => box(id) ?? (id === 'host' && ctl ? box(ctl) : undefined);

  const edges: TEdge[] = [];
  for (const l of t.links) {
    let a = end(l.a);
    let b = end(l.b);
    if (!a || !b || a === b) continue;
    if (a.x > b.x) [a, b] = [b, a];
    let d: string;
    let lx: number;
    let ly: number;
    if (Math.abs(a.x - b.x) < 1) {
      // same column (a direct fabric cable): an arc out to the right
      const top = a.y < b.y ? a : b;
      const bot = top === a ? b : a;
      const rx = a.x + a.w / 2;
      const bulge = 46;
      d = `M${rx} ${top.y} C ${rx + bulge} ${top.y}, ${rx + bulge} ${bot.y}, ${rx} ${bot.y}`;
      lx = rx + bulge * 0.75 + 6;
      ly = (top.y + bot.y) / 2;
    } else if (b.kind === 'switch') {
      const p: [number, number] = [a.x + a.w / 2, a.y];
      d = `M${p[0]} ${p[1]} L${b.x - b.w / 2} ${p[1]}`;
      lx = (p[0] + b.x - b.w / 2) / 2;
      ly = p[1] - 8;
    } else {
      const p: [number, number] = [a.x + a.w / 2, a.y];
      const q: [number, number] = [b.x - b.w / 2, b.y];
      d = curve(p, q);
      lx = (p[0] + q[0]) / 2;
      ly = (p[1] + q[1]) / 2 - 7;
    }
    edges.push({ id: l.id, a: l.a, b: l.b, kind: l.kind, state: l.state, d, lx, ly, label: linkLabel(l), link: l });
  }
  if (ghostInet && lanBox && inetBox) {
    const p: [number, number] = [lanBox.x, lanBox.y + lanBox.h / 2];
    const q: [number, number] = [inetBox.x, inetBox.y - inetBox.h / 2];
    edges.push({ id: 'lan-inet', a: lanBox.id, b: inetBox.id, kind: 'wan', state: 'up', d: `M${p[0]} ${p[1]} L${q[0]} ${q[1]}`, lx: p[0] + 10, ly: (p[1] + q[1]) / 2 + 4, label: 'no remote Sparks', ghost: true });
  }

  // deployments over their members: a Split in one column gets a hull, members apart an arc
  const hulls: THull[] = [];
  for (const g of t.groups) {
    if (g.type !== 'split' && g.type !== 'replicas') continue;
    const mb = g.members.map((m) => box(m)).filter((b): b is TBox => !!b);
    if (mb.length < 2) continue;
    const oneCol = mb.every((b) => Math.abs(b.x - mb[0].x) < 1);
    if (g.type === 'split' && oneCol) {
      const pad = 12;
      const x0 = Math.min(...mb.map((b) => b.x - b.w / 2)) - pad;
      const x1 = Math.max(...mb.map((b) => b.x + b.w / 2)) + pad;
      const y0 = Math.min(...mb.map((b) => b.y - b.h / 2)) - pad - 8;
      const y1 = Math.max(...mb.map((b) => b.y + b.h / 2)) + pad;
      hulls.push({ deployment: g.deployment, type: g.type, members: g.members, rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, tx: x0 + 10, ty: y0 - 2 });
    } else {
      const s = [...mb].sort((p, q) => p.x - q.x || p.y - q.y);
      const a = s[0];
      const b = s[s.length - 1];
      const ay = a.y - a.h / 2;
      const by = b.y - b.h / 2;
      const lift = 44;
      const top = Math.min(ay, by) - lift;
      hulls.push({ deployment: g.deployment, type: g.type, members: g.members, arc: `M${a.x} ${ay} C ${a.x} ${top}, ${b.x} ${top}, ${b.x} ${by}`, tx: (a.x + b.x) / 2, ty: top + lift * 0.25 });
    }
  }
  const bottom = Math.max(...boxes.map((b) => b.y + b.h / 2), TOP + NODE_H);
  const topMost = Math.min(...boxes.map((b) => b.y - b.h / 2), ...hulls.map((h) => (h.rect ? h.rect.y - 16 : h.ty - 12)));
  // everything moves down when an arc or a tag rises above the top margin
  const shift = topMost < 12 ? 12 - topMost : 0;
  if (shift) {
    for (const b of boxes) b.y += shift;
    for (const e of edges) {
      e.d = e.d.replace(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g, (_, px, py) => `${px} ${Number(py) + shift}`);
      e.ly += shift;
    }
    for (const h of hulls) {
      if (h.rect) h.rect.y += shift;
      if (h.arc) h.arc = h.arc.replace(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g, (_, px, py) => `${px} ${Number(py) + shift}`);
      h.ty += shift;
    }
  }
  return { w: W, h: Math.round(bottom + shift + 24), boxes, edges, hulls };
}
