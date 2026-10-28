// Fleet → Models (VIS-252, concept §8.7): the pure parts of its three pages, tested in
// tests/models.test.ts. Deployments: what a card says (its status, each member's state, a record or a
// Single derived from what an engine serves, managed or not), the actions it offers and why one is off,
// the deployments of the scope and their counts. Serve a model: the memory bar of a node from the
// placement plan's parts, the nodes a type takes, the body that creates the deployment. Weights: each
// cell of the packs × Sparks matrix (present, in use, copying, missing, off) and a copy's words.

import type { DeploymentNew, DeploymentStatus, DeploymentType, FleetDeployment, MemberState, Placement, PlacementNode, PlacementType, WeightsCopyJob, WeightsMatrix } from '../api/fleet';
import type { Scope } from './scope';

export const TYPE_WORD: Record<DeploymentType, string> = { single: 'Single', replicas: 'Replicas', split: 'Split', role: 'Role' };
export const TYPE_HELP: Record<DeploymentType, string> = {
  single: 'one Spark serves it',
  replicas: 'the same model on two or more Sparks; each conversation stays on one',
  split: 'one model across 2 or 4 Sparks joined by the fabric',
  role: 'a helper model beside another deployment',
};
/** the Split's honest state until the pipeline split exists */
export const SPLIT_NOT_BUILT = 'not built yet (ENG-307): planned and recorded, it does not run';

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
};

export function memberTone(s: MemberState): Tone {
  if (s === 'serving') return 'ok';
  if (s === 'starting' || s === 'mismatch') return 'warn';
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
  const out: DepAction[] = [];
  if (stopped || d.status === 'down') out.push({ id: 'start', label: 'Start', why: split ? SPLIT_NOT_BUILT : busy });
  if (!stopped || (d.member_states ?? []).some((m) => m.state === 'serving')) out.push({ id: 'stop', label: 'Stop…', why: busy });
  out.push({ id: 'move', label: 'Move…', why: split ? 'a Split moves with ENG-307' : busy });
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
  if (t === 'role') {
    if (o.role?.trim()) out.role = o.role.trim();
    if (o.role_of) out.role_of = o.role_of;
  }
  if (t === 'split') out.state = 'stopped';
  return out;
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
