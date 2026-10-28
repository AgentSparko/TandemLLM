// What the Sparks pages compute (VIS-250, concept §8.6): the fleet list's rows with their sort keys
// and filters, a node's link and lease in words, the identity colours a node may take, the tags an
// edit sends, the actions a node offers and what a plan's job says. Pure: views/fleet/ draws it.

import type { ActionPlan, AgentJob, FleetDeployment, FleetLink, FleetNode, NodeAction, NodePatch, NodeStatus } from '../api/fleet';

export type SortKey = 'alias' | 'status' | 'serves' | 'mem' | 'temp' | 'power' | 'version' | 'link' | 'lease';
export type StatusFilter = 'all' | NodeStatus | 'drained';
export type KindFilter = 'all' | 'owned' | 'rented';

export interface SparkRow {
  node: FleetNode;
  deps: FleetDeployment[];
  mem: number | null;
  memCap: number | null;
  temp: number | null;
  power: number | null;
  link: string;
  /** the link's round trip (ms), null when there is none to time */
  rtt: number | null;
  lease: string;
}

const STATUS_RANK: Record<NodeStatus, number> = { alert: 0, off: 1, draining: 2, up: 3 };

/** The links that reach a node: its LAN or tunnel link, and its fabric ones. */
export function linksOf(id: string, links: FleetLink[]): FleetLink[] {
  return links.filter((l) => l.a === id || l.b === id);
}

/** "LAN · 0.4 ms", "tunnel · 38 ms", "the controller's own"; the fabric beside it, or none */
export function linkWords(n: Pick<FleetNode, 'id' | 'transport'>, links: FleetLink[]): { main: string; fabric: string; rtt: number | null } {
  const mine = linksOf(n.id, links);
  const reach = mine.find((l) => l.kind !== 'fabric');
  const fab = mine.filter((l) => l.kind === 'fabric');
  const rtt = reach?.rtt_ms ?? null;
  const kind = n.transport === 'tunnel' ? 'tunnel' : 'LAN';
  const main = !reach ? `${kind} · no link reported` : rtt == null ? `${kind} · the controller runs here` : `${kind} · ${rtt < 10 ? rtt.toFixed(1) : Math.round(rtt)} ms`;
  const fabric = fab.length ? fab.map((l) => `fabric ${l.speed} ${l.state === 'up' ? '✓' : l.state}`).join(' · ') : 'no fabric link';
  return { main, fabric, rtt };
}

/** "owned", "ends in 2 d 4 h", "ended" */
export function leaseWords(n: Pick<FleetNode, 'lease_end' | 'owned'>, now = Date.now() / 1000): string {
  if (n.lease_end == null) return n.owned ? 'owned' : 'no lease';
  const s = n.lease_end - now;
  if (s <= 0) return 'ended';
  if (s >= 86400) return `ends in ${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
  if (s >= 3600) return `ends in ${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
  return `ends in ${Math.max(1, Math.floor(s / 60))} min`;
}

export interface RowInputs {
  nodes: FleetNode[];
  deployments: FleetDeployment[];
  links: FleetLink[];
  /** metric -> node -> value now */
  now: Partial<Record<'mem' | 'temp' | 'power', Record<string, number | null>>>;
  /** node -> usable memory, GiB */
  cap: Record<string, number>;
}

export function sparkRows(i: RowInputs, t = Date.now() / 1000): SparkRow[] {
  return i.nodes.map((n) => {
    const off = n.status === 'off';
    const v = (k: 'mem' | 'temp' | 'power') => (off ? null : (i.now[k]?.[n.id] ?? null));
    const lw = linkWords(n, i.links);
    return {
      node: n,
      deps: i.deployments.filter((d) => d.members.includes(n.id)),
      mem: v('mem'),
      memCap: i.cap[n.id] ?? null,
      temp: v('temp'),
      power: v('power'),
      link: lw.main,
      rtt: lw.rtt,
      lease: leaseWords(n, t),
    };
  });
}

/** Sorts a copy; a missing value sorts last either way. */
export function sortRows(rows: SparkRow[], key: SortKey, dir: 'asc' | 'desc'): SparkRow[] {
  const val = (r: SparkRow): string | number | null => {
    switch (key) {
      case 'alias':
        return r.node.alias.toLowerCase();
      case 'status':
        return STATUS_RANK[r.node.status] * 10 + (r.node.drain?.state === 'drained' ? 1 : 0);
      case 'serves':
        return r.deps.map((d) => d.name).join(' ').toLowerCase() || null;
      case 'mem':
        return r.mem;
      case 'temp':
        return r.temp;
      case 'power':
        return r.power;
      case 'version':
        return r.node.version ? r.node.version.split(/[.-]/).map((x) => (/^\d+$/.test(x) ? x.padStart(6, '0') : x)).join('.') : null;
      case 'link':
        return r.rtt ?? (r.link.includes('runs here') ? 0 : null);
      case 'lease':
        return r.node.lease_end;
    }
  };
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = val(a);
    const y = val(b);
    if (x == null && y == null) return a.node.alias.localeCompare(b.node.alias);
    if (x == null) return 1;
    if (y == null) return -1;
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
    return c ? sign * c : a.node.alias.localeCompare(b.node.alias);
  });
}

export interface RowFilter {
  q: string;
  status: StatusFilter;
  kind: KindFilter;
}

/** A row matches the text in its alias, tags, hardware, address, version or what it serves. */
export function filterRows(rows: SparkRow[], f: RowFilter): SparkRow[] {
  const q = f.q.trim().toLowerCase();
  return rows.filter((r) => {
    const n = r.node;
    if (f.kind === 'owned' && !n.owned) return false;
    if (f.kind === 'rented' && n.owned) return false;
    if (f.status === 'drained' && n.drain?.state !== 'drained') return false;
    if (f.status !== 'all' && f.status !== 'drained' && n.status !== f.status) return false;
    if (!q) return true;
    const hay = [n.alias, ...n.tags, n.hw, n.dmi, n.address, n.version, ...r.deps.map((d) => d.name)].join(' ').toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  });
}

/** Each identity colour 1..8 and the node that has it (another node's colour is not on offer). */
export function colourChoices(nodes: Pick<FleetNode, 'id' | 'alias' | 'colour'>[], self: string): { colour: number; usedBy: string | null }[] {
  return [1, 2, 3, 4, 5, 6, 7, 8].map((c) => ({ colour: c, usedBy: nodes.find((n) => n.colour === c && n.id !== self)?.alias ?? null }));
}

/** "desk, rented,  east" -> ["desk", "rented", "east"]: trimmed, once each, at most 16 */
export function parseTags(text: string): string[] {
  return [...new Set(text.split(',').map((t) => t.trim()).filter(Boolean))].slice(0, 16);
}

export interface EditDraft {
  alias: string;
  colour: number;
  icon: FleetNode['icon'];
  tags: string;
}

/** The PATCH of an edit: only what changed; an error in words for what the controller refuses. */
export function editPatch(n: FleetNode, d: EditDraft, others: Pick<FleetNode, 'id' | 'alias' | 'colour'>[]): { patch: NodePatch | null; error?: string } {
  const alias = d.alias.trim();
  if (!alias || alias.length > 40) return { patch: null, error: 'An alias is 1 to 40 characters.' };
  if (others.some((o) => o.id !== n.id && o.alias.toLowerCase() === alias.toLowerCase())) return { patch: null, error: `Another Spark is called ${alias}.` };
  const held = others.find((o) => o.id !== n.id && o.colour === d.colour);
  if (held) return { patch: null, error: `Colour ${d.colour} is ${held.alias}’s.` };
  const tags = parseTags(d.tags);
  if (tags.some((t) => t.length > 32)) return { patch: null, error: 'A tag is at most 32 characters.' };
  const p: NodePatch = {};
  if (alias !== n.alias) p.alias = alias;
  if (d.colour !== n.colour) p.colour = d.colour;
  if (d.icon !== n.icon) p.icon = d.icon;
  if (tags.join('\n') !== n.tags.join('\n')) p.tags = tags;
  return { patch: Object.keys(p).length ? p : null };
}

export interface ActionButton {
  action: NodeAction;
  label: string;
  danger: boolean;
}

/** The actions a node's page offers: Drain or Undrain, Restart engine, Reboot, Update, and Forget
 *  for an owned node or Release lease for a rented one. */
export function nodeActions(n: Pick<FleetNode, 'drain' | 'lease_end'>): ActionButton[] {
  return [
    n.drain ? { action: 'undrain', label: '◐ Undrain', danger: false } : { action: 'drain', label: '◐ Drain', danger: false },
    { action: 'restart-engine', label: 'Restart engine', danger: false },
    { action: 'reboot', label: 'Reboot', danger: false },
    { action: 'update', label: 'Update', danger: false },
    n.lease_end != null ? { action: 'release-lease', label: 'Release lease…', danger: true } : { action: 'forget', label: 'Forget…', danger: true },
  ];
}

/** The button that runs a plan: what it says, and whether the alias must be typed first. */
export function confirmButton(p: Pick<ActionPlan, 'action' | 'title'>): { label: string; typeAlias: boolean } {
  const label: Record<NodeAction, string> = {
    drain: 'Drain',
    undrain: 'Undrain',
    'restart-engine': 'Restart engine',
    reboot: 'Reboot',
    update: 'Update',
    forget: 'Forget',
    'release-lease': 'Release and wipe',
  };
  return { label: label[p.action], typeAlias: p.action === 'release-lease' };
}

/** A job in words: "running since 14:02", "done: up after 41 s", "failed: …" */
export function jobWords(j: AgentJob | null | undefined, clock: (t: number) => string): string | null {
  if (!j || !j.state) return null;
  const sim = j.simulated ? ' (simulated)' : '';
  if (j.state === 'running') return `${j.action ?? 'job'} running${sim}${j.started_at ? ` since ${clock(j.started_at)}` : ''}`;
  return `${j.action ?? 'job'} ${j.state}${sim}${j.message ? `: ${j.message}` : ''}`;
}

/** The node's drain in one line for its page's banner, or null when it takes requests. */
export function drainLine(n: Pick<FleetNode, 'alias' | 'drain' | 'status'>): { title: string; text: string } | null {
  const d = n.drain;
  if (!d) return null;
  if (n.status === 'off') return { title: `${n.alias} is drained and off`, text: 'It takes no new requests when it is back, until Undrain.' };
  if (d.state === 'draining')
    return { title: `Draining: ${d.inflight} request${d.inflight === 1 ? '' : 's'} in flight`, text: `${n.alias} takes no new requests: they go to a fallback or wait for Retry-After. What runs finishes.` };
  return { title: 'Drained: nothing in flight', text: `${n.alias} can be updated or rebooted now. Undrain takes new requests again.` };
}
