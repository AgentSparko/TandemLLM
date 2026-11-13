// Fleet → Models (VIS-252, concept §8.7): the pure parts of its three pages, tested in
// tests/models.test.ts. Deployments: what a card says (its status, each member's state, a record or a
// Single derived from what an engine serves, managed or not), the actions it offers and why one is off,
// the deployments of the scope and their counts. Serve a model: the memory bar of a node from the
// placement plan's parts, the nodes a type takes, the body that creates the deployment. Weights: each
// cell of the packs × Sparks matrix (present, in use, copying, missing, off) and a copy's words.

import type { DeploymentNew, DeploymentStatus, DeploymentType, FleetDeployment, MemberState, Placement, PlacementNode, PlacementType, SplitLink, SplitPhase, SplitSide, WeightsCopyJob, WeightsMatrix } from '../api/fleet';
import type { Scope } from './scope';

export const TYPE_WORD: Record<DeploymentType, string> = { single: 'Single', replicas: 'Replicas', split: 'Split', role: 'Role' };
export const TYPE_HELP: Record<DeploymentType, string> = {
  single: 'one Spark serves it',
  replicas: 'the same model on two or more Sparks; each conversation stays on one',
  split: 'one model cut across two Sparks: the front stage on one, the tail on the other',
  role: 'a helper model beside another deployment',
};
/** a Split recorded before it could run: it has no plan to start from */
export const SPLIT_NO_PLAN = 'recorded without a plan: plan it again on Serve a model to start it';
export const SPLIT_MOVE = 'a Split’s two stages move together: stop it, plan it on the new Sparks and start it';

export type Tone = 'ok' | 'warn' | 'err' | 'drain' | 'muted';
/** a tone's text class */
export const ink = (t: Tone): string => (t === 'muted' ? 'muted' : `${t}-ink`);

export const MEMBER_WORD: Record<MemberState, string> = {
  serving: 'serving',
  starting: 'starting',
  down: 'down',
  draining: 'draining',
  leaving: 'leaving',
  mismatch: 'mismatch',
  off: 'off',
  gone: 'gone',
  waiting: 'waiting for the link',
};

export function memberTone(s: MemberState): Tone {
  if (s === 'serving') return 'ok';
  if (s === 'starting' || s === 'mismatch' || s === 'waiting') return 'warn';
  if (s === 'draining' || s === 'leaving') return 'drain';
  if (s === 'gone') return 'muted';
  return 'err';
}

/** the Overview's glyphs: a half for not all serving, an empty ring for none */
export const STATUS_GLYPH: Record<DeploymentStatus, string> = { serving: '●', starting: '◐', degraded: '◐', down: '○', stopped: '○' };

export function statusTone(s: DeploymentStatus): Tone {
  return s === 'serving' ? 'ok' : s === 'down' ? 'err' : s === 'stopped' ? 'muted' : 'warn';
}

/** a record or derived, managed by TandemNode or not, in words */
export function managedWords(d: Pick<FleetDeployment, 'record' | 'managed' | 'type'>): { label: string; text: string } {
  if (d.record === false)
    return { label: 'derived', text: 'An engine serves it that no record covers; TandemNode does not start or stop it. Record it to name it, scale it or move it.' };
  if (d.managed) return { label: 'managed', text: 'A record; TandemNode starts and stops its engines.' };
  return { label: 'unmanaged', text: 'A record; its engines were started outside TandemNode: start and stop them where they run.' };
}

export type DepActionId = 'start' | 'stop' | 'move' | 'edit' | 'delete' | 'record';
export interface DepAction {
  id: DepActionId;
  label: string;
  /** why it is off here; null: on (the controller's plan has the last word) */
  why: string | null;
}

/** The buttons of a card, each on or off with its reason. A derived Single offers Record only. */
export function depActions(d: FleetDeployment): DepAction[] {
  if (d.record === false) return [{ id: 'record', label: 'Record…', why: null }];
  const busy = d.job?.state === 'running' ? 'a job runs: wait for it' : null;
  const split = d.type === 'split';
  const stopped = d.state === 'stopped' || d.status === 'stopped';
  const ms = d.member_states ?? [];
  const out: DepAction[] = [];
  // a Split starts and stops as a pair: one action for both stages
  if (stopped || d.status === 'down') out.push({ id: 'start', label: 'Start', why: split && !d.split ? SPLIT_NO_PLAN : busy });
  if (!stopped || ms.some((m) => m.state === 'serving' || m.stage?.runs)) out.push({ id: 'stop', label: 'Stop…', why: busy });
  out.push({ id: 'move', label: 'Move…', why: split ? SPLIT_MOVE : busy });
  out.push({ id: 'edit', label: 'Edit…', why: busy });
  out.push({ id: 'delete', label: 'Delete…', why: busy });
  return out;
}

/** The deployments the scope covers: all, a Spark's, or the one. */
export function scoped(deps: FleetDeployment[], s: Pick<Scope, 'kind' | 'id'>): FleetDeployment[] {
  if (s.kind === 'node') return deps.filter((d) => d.members.includes(s.id!));
  if (s.kind === 'deployment') return deps.filter((d) => d.id === s.id);
  return deps;
}

export interface DepCounts {
  total: number;
  serving: number;
  /** degraded, starting or down: not all its members serve */
  trouble: number;
  stopped: number;
  /** members that take requests now, of all members */
  routable: number;
  members: number;
}

export function depCounts(deps: FleetDeployment[]): DepCounts {
  const ms = deps.flatMap((d) => d.member_states ?? []);
  return {
    total: deps.length,
    serving: deps.filter((d) => d.status === 'serving').length,
    trouble: deps.filter((d) => d.status === 'degraded' || d.status === 'starting' || d.status === 'down').length,
    stopped: deps.filter((d) => d.status === 'stopped').length,
    routable: ms.filter((m) => m.routable).length,
    members: ms.length,
  };
}

/** "3 deployments behind one API: 2 serving, 1 stopped" */
export function countWords(c: DepCounts): string {
  if (!c.total) return 'No deployment yet.';
  const parts = [c.serving ? `${c.serving} serving` : '', c.trouble ? `${c.trouble} not all serving` : '', c.stopped ? `${c.stopped} stopped` : ''].filter(Boolean);
  return `${c.total} deployment${c.total === 1 ? '' : 's'} behind one API: ${parts.join(', ')}.`;
}

// ---- Serve a model: the placement plan -----------------------------------------------------------

export interface BarSeg {
  id: string;
  label: string;
  gib: number;
  /** its share of the bar, 0..1 */
  f: number;
  /** `now`: what runs on the node already; `new`: the new engine's part; `base`: the OS */
  kind: 'now' | 'new' | 'base';
}
export interface PlaceBar {
  segs: BarSeg[];
  /** where the budget sits on the bar, 0..1 (null without one) */
  budgetAt: number | null;
  over: boolean;
  text: string;
}

const r1 = (v: number) => (Math.round(v * 10) / 10).toFixed(1);

/** A node's memory bar from its plan: on a node that serves something, what runs now (its budget
 *  less what is free) and the new engine's own parts beside it; on an idle one the OS and the new
 *  engine's parts; the budget marked. A node without an estimate has no bar, only its reason. */
export function placeBar(n: PlacementNode): PlaceBar | null {
  if (n.need_gib == null || !n.parts.length) return null;
  const budget = n.budget_gib ?? n.total_gib ?? n.need_gib;
  const now = n.serving && n.free_gib != null ? Math.max(0, budget - n.free_gib) : 0;
  const raw: Omit<BarSeg, 'f'>[] = [];
  if (now > 0) raw.push({ id: 'now', label: 'in use now', gib: now, kind: 'now' });
  for (const p of n.parts) {
    if (p.id === 'os') {
      if (!n.serving) raw.push({ id: 'os', label: p.label, gib: p.gib, kind: 'base' });
    } else if (p.gib > 0) raw.push({ id: p.id, label: p.label, gib: p.gib, kind: 'new' });
  }
  const sum = raw.reduce((a, s) => a + s.gib, 0);
  const scale = Math.max(budget, sum, 1e-9);
  const free = n.free_gib != null ? `${r1(n.free_gib)} free` : 'free memory not read';
  return {
    segs: raw.map((s) => ({ ...s, f: s.gib / scale })),
    budgetAt: n.budget_gib != null ? Math.min(1, n.budget_gib / scale) : null,
    over: !n.fits,
    text: `needs ${r1(n.need_gib)} GiB · ${free}${n.budget_gib != null ? ` · budget ${r1(n.budget_gib)}` : ''}`,
  };
}

export const SOURCE_WORD: Record<PlacementNode['source'], string> = {
  engine: 'its engine’s Setup Guide',
  agent: 'its agent’s estimate (no engine serves the guide)',
  process: 'this controller, in-process',
  none: 'no estimate',
};

/** The type to offer first: the one picked while still allowed, else the first allowed and built. */
export function pickType(p: Placement, cur: DeploymentType | null): DeploymentType | null {
  const ok = (t: PlacementType) => t.allowed;
  if (cur && p.types.some((t) => t.type === cur && ok(t))) return cur;
  return (p.types.find((t) => ok(t) && t.built && t.type !== 'role') ?? p.types.find((t) => ok(t) && t.type !== 'role'))?.type ?? null;
}

/** The members a type takes from the plan and the Sparks picked (in pick order): one for Single and
 *  Role, every fitting pick (at least two) for Replicas, the picks in order for a Split. */
export function membersFor(p: Placement, t: DeploymentType, picked: string[]): string[] {
  const tp = p.types.find((x) => x.type === t);
  if (!tp?.allowed) return [];
  const fit = new Set(p.nodes.filter((n) => n.fits).map((n) => n.node));
  if (t === 'single' || t === 'role') return [picked.find((x) => fit.has(x)) ?? tp.nodes[0]].filter(Boolean);
  if (t === 'replicas') {
    const mine = picked.filter((x) => tp.nodes.includes(x));
    return mine.length >= 2 ? mine : tp.nodes;
  }
  return tp.nodes;
}

/** The body of POST /v1/fleet/deployments for the plan's choice. */
export function newDeployment(p: Placement, t: DeploymentType, members: string[], o: { name?: string; api_id?: string; role?: string; role_of?: string } = {}): DeploymentNew {
  const tp = p.types.find((x) => x.type === t);
  const out: DeploymentNew = { model: p.model, pack: p.pack, type: t, members, ctx: p.ctx };
  if (o.name?.trim()) out.name = o.name.trim();
  if (o.api_id?.trim()) out.api_id = o.api_id.trim();
  if (t === 'split' && tp?.stages.length) out.stages = tp.stages;
  if (t === 'split' && tp?.split) out.split = tp.split;
  if (t === 'role') {
    if (o.role?.trim()) out.role = o.role.trim();
    if (o.role_of) out.role_of = o.role_of;
  }
  return out;
}

// ---- a Split: its two stages and the link between them (VIS-272) ---------------------------------

/** One stage's bar: its need by part against its Spark's budget, the budget marked. Weights and
 *  KV keep their colours; the rest is one segment a part. */
export function stageBar(s: SplitSide): PlaceBar {
  const scale = Math.max(s.budget_gib, s.need_gib, 1e-9);
  return {
    segs: s.parts.filter((p) => p.gib > 0).map((p) => ({ id: p.id, label: p.basis === 'margin' ? 'slot margin' : p.id.replace(/_/g, ' '), gib: p.gib, f: p.gib / scale, kind: 'new' as const })),
    budgetAt: Math.min(1, s.budget_gib / scale),
    over: !s.fits,
    text: `needs ${r1(s.need_gib)} of ${r1(s.budget_gib)} GiB · ${s.fits ? `${r1(s.headroom_gib)} headroom` : `${r1(-s.headroom_gib)} short`}`,
  };
}

/** computed or measured, in a word: every figure of a plan is computed from the engine's estimate
 *  but the planner's own margin */
export const basisWord = (basis: string): string => (basis === 'margin' ? 'margin' : 'computed');

export const LINK_KIND: Record<SplitLink['kind'], string> = { fabric: 'fabric pair', lan: 'LAN pair', loopback: 'loopback, one machine', given: 'addresses given by hand' };

/** "link up · epoch 2 · tcp · fabric pair" */
export function linkWords(l: SplitLink): { tone: Tone; text: string } {
  const st = l.state ?? 'unknown';
  const parts = [st === 'up' ? 'link up' : st === 'down' ? `link down${l.reason ? ` (${l.reason})` : ''}` : 'link not up yet', l.epoch ? `epoch ${l.epoch}` : '', l.transport ?? '', LINK_KIND[l.kind]];
  return { tone: st === 'up' ? 'ok' : st === 'down' ? 'err' : 'muted', text: parts.filter(Boolean).join(' · ') };
}

/** A Split's phase in plain words, for the card: what it is, and what to do or not do. */
export function phaseWords(phase: SplitPhase | undefined, front: string, tail: string): { tone: Tone; title: string; text: string } | null {
  switch (phase) {
    case 'starting-front':
      return { tone: 'warn', title: 'Starting the front', text: `The front stage loads on ${front}. The tail on ${tail} starts once it answers.` };
    case 'starting-tail':
      return { tone: 'warn', title: 'Starting the tail', text: `The front is up. The tail stage loads on ${tail} and links to it.` };
    case 'degraded':
      return { tone: 'warn', title: 'Degraded', text: `Both stages run, but they are not linked. Requests get a 503 with Retry-After until they are. The stages link again by themselves: nothing to do.` };
    case 'partial':
      return { tone: 'err', title: 'Half running', text: `Only one of the two stages runs, so nothing is served. Start brings the other one back; Stop ends the one that runs.` };
    case 'stopping':
      return { tone: 'muted', title: 'Stopping', text: `The tail on ${tail} stops first, then the front on ${front}.` };
    case 'down':
      return { tone: 'err', title: 'Down', text: 'Neither stage runs. Start starts the front, then the tail.' };
    default:
      return null;
  }
}

export interface TileFigures {
  tps: Record<string, number | null>;
  mem: Record<string, number | null>;
  cap: number | null;
  /** a running stage counted by its plan's need: the member's reading showed less */
  planned: boolean;
  /** the backends in scope: no host, so no memory figure */
  backends: string[];
}

/** The Decode and Engine memory tiles' figure of each member in scope (VIS-272). A Split's stages
 *  count: the decode of the tail, the stage that serves; the memory of each running stage, by its
 *  plan's need where the member's reading shows less. A backend has no host figures: null, never 0. */
export function tileFigures(
  nodes: { id: string; alias: string; status: string; class?: string }[],
  deps: FleetDeployment[],
  tps: Record<string, number | null | undefined>,
  rates: Record<string, Record<string, number | null>>,
  mems: { node: string; weights: number; kv: number; cache: number; usable: number }[],
): TileFigures {
  const t: TileFigures = { tps: {}, mem: {}, cap: null, planned: false, backends: [] };
  let cap = 0;
  for (const n of nodes) {
    const st = deps.flatMap((d) => (d.type === 'split' ? (d.member_states ?? []).filter((m) => m.node === n.id && m.stage?.runs).map((m) => ({ d, s: m.stage! })) : []));
    const tail = st.find((x) => x.s.role === 'tail');
    const vs = [n.status === 'off' ? null : tps[n.id], tail ? rates[tail.d.id]?.[n.id] : null].filter((v): v is number => v != null);
    t.tps[n.id] = vs.length ? Math.max(...vs) : null;
    if (n.class === 'backend') {
      t.mem[n.id] = null;
      t.backends.push(n.alias);
      continue;
    }
    const r = mems.find((m) => m.node === n.id);
    const read = r ? r.weights + r.kv + r.cache : null;
    const need = st.reduce((a, x) => a + (x.s.need_gib ?? 0), 0);
    if (need > (read ?? 0)) t.planned = true;
    t.mem[n.id] = need > (read ?? 0) ? need : read;
    cap += r?.usable ?? st[0]?.s.budget_gib ?? 0;
  }
  t.cap = cap || null;
  return t;
}

const SIMPLE_KEY = 'tandem.models.simple';
/** the Models page's mode: simple (the default) shows the two stages and the link; advanced adds every number */
export function simpleMode(): boolean {
  try {
    return localStorage.getItem(SIMPLE_KEY) !== '0';
  } catch {
    return true;
  }
}
export function setSimpleMode(on: boolean): void {
  try {
    localStorage.setItem(SIMPLE_KEY, on ? '1' : '0');
  } catch {
    /* private mode: per page */
  }
}

// ---- Weights -------------------------------------------------------------------------------------

const GIB = 2 ** 30;

/** "23.3 GiB", "412 MB"; null as "—" */
export function sizeWords(bytes: number | null | undefined): string {
  if (bytes == null) return '—';
  return bytes >= GIB ? `${(bytes / GIB).toFixed(1)} GiB` : `${Math.max(1, Math.round(bytes / 2 ** 20))} MB`;
}

/** a content id short enough for a cell: sha256:1a2b3c4d…9f0e */
export function shortId(cid: string | null | undefined): string {
  if (!cid) return '—';
  const [alg, hex = ''] = cid.includes(':') ? cid.split(':') : ['', cid];
  return hex.length > 12 ? `${alg ? alg + ':' : ''}${hex.slice(0, 8)}…${hex.slice(-4)}` : cid;
}

export type CellKind = 'present' | 'in-use' | 'copying' | 'missing' | 'off';
export interface WeightsCell {
  kind: CellKind;
  bytes: number | null;
  source: 'setup' | 'store' | null;
  contentId: string | null;
  /** a copy arriving there now */
  copy: WeightsCopyJob | null;
  /** 0..1 of a copy */
  pct: number | null;
  /** the Sparks a copy may come from (present there, up) */
  from: string[];
  /** the deployments that serve the pack there */
  usedBy: string[];
}

type MatrixPack = WeightsMatrix['packs'][number];

/** One cell of the matrix: the pack on one Spark. */
export function weightsCell(m: WeightsMatrix, pack: MatrixPack, node: string, deps: FleetDeployment[], status: Record<string, string> = {}): WeightsCell {
  const on = pack.nodes.find((x) => x.node === node);
  const copy = m.copies.find((c) => c.state === 'running' && c.to === node && c.profile === pack.profile) ?? (on?.copy ? m.copies.find((c) => c.id === on.copy) ?? null : null);
  const usedBy = deps.filter((d) => d.status !== 'stopped' && d.pack === pack.profile && d.members.includes(node)).map((d) => d.name);
  const from = pack.nodes.filter((x) => x.present && x.node !== node && status[x.node] !== 'off').map((x) => x.node);
  const base = { bytes: on?.bytes ?? null, source: on?.source ?? null, contentId: on?.content_id ?? null, copy: null, pct: null, from, usedBy };
  if (status[node] === 'off') return { ...base, kind: on?.present ? 'present' : 'off', from: on?.present ? from : [] };
  if (copy && copy.state === 'running') return { ...base, kind: 'copying', copy, pct: copy.bytes_total ? Math.min(1, copy.bytes_done / copy.bytes_total) : null };
  if (on?.present) return { ...base, kind: usedBy.length ? 'in-use' : 'present' };
  return { ...base, kind: 'missing' };
}

/** the pack's size: the largest copy of it any Spark has */
export function packBytes(pack: MatrixPack): number | null {
  const b = pack.nodes.map((x) => x.bytes).filter((x): x is number => x != null);
  return b.length ? Math.max(...b) : null;
}

/** what a copy's job says in a line */
export function copyWords(j: WeightsCopyJob): string {
  const how = `${j.via === 'direct' ? 'direct, node to node' : 'relayed through the controller'}`;
  if (j.state === 'running') {
    const pct = j.bytes_total ? Math.round((j.bytes_done / j.bytes_total) * 100) : null;
    return [`copying${pct != null ? ` ${pct} %` : ''}`, j.bytes_total ? `${sizeWords(j.bytes_done)} of ${sizeWords(j.bytes_total)}` : '', j.rate_gbps != null ? `${j.rate_gbps.toFixed(1)} Gb/s` : '', how].filter(Boolean).join(' · ');
  }
  if (j.state === 'done') return j.verified ? `copied and verified by content id · ${how}` : `copied, not verified: the content ids differ · ${how}`;
  if (j.state === 'stopped') return 'stopped: nothing was left on the receiver';
  return `failed: ${j.error?.message ?? 'no reason given'}`;
}
