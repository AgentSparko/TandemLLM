// The fleet update's order and the mixed-version state (OPS-234), the same rules as the controller's
// proxy/fleetupdate.py `order` and proxy/fleetwatch.py's version-mismatch rule: the mock answers
// with them and the page words a job's progress from them.
//
//   idle     a Spark that serves nothing: first, no drain
//   replica  every deployment on it is served by another member too: drain, update, rejoin, one
//            Spark at a time, the model stays served
//   single   a deployment only it serves: drain, update, rejoin, with the downtime named
//   split    a Split's members together, last; a Split never updates partly: one member that cannot
//            be updated refuses the Split, a selection with one member and not another blocks it

import type { FleetDeployment, FleetNode, NodeStatus, UpdateJob, UpdatePhase, UpdateRefused, UpdateStep, UpdateStepKind } from '../api/fleet';

export interface PlanNode {
  id: string;
  alias: string;
  version: string | null;
  status: NodeStatus;
  updatable: boolean;
  why: string | null;
}

export interface Order {
  steps: Omit<UpdateStep, 'words'>[];
  skipped: { node: string; why: string }[];
  refused: UpdateRefused[];
  blocked: string | null;
  current: string[];
}

/** A version as pip writes it: `0.15.0-test` and `0.15.0.dev0` are one, a local label stays. */
export function normVersion(v: string | null | undefined): string {
  const s = (v ?? '').trim().toLowerCase();
  const m = /^(\d+(?:\.\d+)*)(?:-(test|dev|rc\d*|a\d*|b\d*))?(\+[a-z0-9.]+)?$/.exec(s);
  if (!m) return s;
  const [, base, tag, local = ''] = m;
  if (!tag) return base + local;
  if (tag === 'test' || tag === 'dev') return `${base}.dev0${local}`;
  const n = tag.replace(/\D/g, '') || '0';
  return base + tag.slice(0, tag.startsWith('rc') ? 2 : 1) + n + local;
}

/** The release of a version: its local build label is the release's. */
export const releaseOf = (v: string | null | undefined): string => normVersion(v).split('+')[0];

/** Whether a Spark that runs `version` needs the update to `target` (the whole version counts). */
export const needsUpdate = (version: string | null | undefined, target: string): boolean => !!target && normVersion(version) !== normVersion(target);

const RANK: Record<UpdateStepKind, number> = { idle: 0, replica: 1, single: 2, split: 3 };
const byAlias = (a: string, b: string) => a.toLowerCase().localeCompare(b.toLowerCase());

export function order(nodes: PlanNode[], deployments: Pick<FleetDeployment, 'id' | 'name' | 'type' | 'members'>[], target: string, only: string[] | null = null): Order {
  const by = new Map(nodes.map((n) => [n.id, n]));
  const want = new Set(nodes.filter((n) => needsUpdate(n.version, target)).map((n) => n.id));
  const out: Order = { steps: [], skipped: [], refused: [], blocked: null, current: nodes.filter((n) => !want.has(n.id)).map((n) => n.alias).sort(byAlias) };
  if (only) {
    const unknown = only.find((x) => !by.has(x));
    if (unknown) return { ...out, current: [], blocked: `no node ${unknown} in this fleet` };
  }
  const picked = new Set(only ?? nodes.map((n) => n.id));
  const serving = new Map(deployments.map((d) => [d.id, d.members.filter((m) => ['up', 'alert'].includes(by.get(m)?.status ?? 'off'))]));
  const alias = (m: string) => by.get(m)?.alias ?? m;
  const inSplit = new Map<string, Pick<FleetDeployment, 'id' | 'name' | 'type' | 'members'> | null>();
  const splits = deployments.filter((d) => d.type === 'split');
  for (const d of splits) {
    const need = d.members.filter((m) => want.has(m));
    if (!need.length) continue;
    const sel = need.filter((m) => picked.has(m));
    if (sel.length && sel.length < need.length) {
      out.blocked = `${d.name} runs Split over ${d.members.filter((m) => by.has(m)).map(alias).join(', ')}: its members update together, so ${need.filter((m) => !picked.has(m)).map(alias).join(', ')} must be in the update too`;
      for (const m of d.members) inSplit.set(m, null);
      continue;
    }
    if (!sel.length) continue;
    const bad = d.members.filter((m) => !by.get(m)?.updatable);
    if (bad.length) {
      const why = bad.filter((m) => by.has(m)).map((m) => by.get(m)!.why || 'off').join('; ') || 'gone';
      out.refused.push({ deployment: d.id, name: d.name, nodes: [...d.members], why: `${d.name} runs Split: ${bad.map(alias).join(', ')} cannot be updated now (${why}), and a Split never updates partly: it stays on its version` });
      for (const m of d.members) inSplit.set(m, null);
      continue;
    }
    for (const m of d.members) inSplit.set(m, d);
  }
  const done = new Set<string>();
  for (const d of splits) {
    const members = d.members.filter((m) => inSplit.get(m) === d && want.has(m));
    if (!members.length || members.some((m) => done.has(m))) continue;
    members.forEach((m) => done.add(m));
    const other = deployments.filter((x) => x !== d && members.some((m) => x.members.includes(m)));
    out.steps.push({ n: 0, kind: 'split', nodes: members, drain: true, deployments: [d.id, ...other.map((x) => x.id)], downtime: `${d.name} is down while its members update together` });
  }
  for (const id of [...want].sort((a, b) => byAlias(alias(a), alias(b)))) {
    if (done.has(id) || inSplit.has(id) || !picked.has(id)) continue;
    const n = by.get(id)!;
    if (!n.updatable) {
      out.skipped.push({ node: id, why: n.why || 'it cannot be updated now' });
      continue;
    }
    const deps = deployments.filter((d) => d.members.includes(id));
    const alone = deps.filter((d) => !(serving.get(d.id) ?? []).some((m) => m !== id));
    const kind: UpdateStepKind = !deps.length ? 'idle' : alone.length ? 'single' : 'replica';
    out.steps.push({ n: 0, kind, nodes: [id], drain: kind !== 'idle', deployments: deps.map((d) => d.id), downtime: kind === 'single' ? `${alone.map((d) => d.name).join(', ')} not served while ${n.alias} updates, unless its route has a fallback` : null });
  }
  out.steps.sort((a, b) => RANK[a.kind] - RANK[b.kind] || byAlias(alias(a.nodes[0]), alias(b.nodes[0])));
  out.steps.forEach((s, i) => (s.n = i + 1));
  return out;
}

/** A step's line, as the controller words it. */
export function stepWords(s: Pick<UpdateStep, 'kind' | 'nodes' | 'deployments'>, alias: (id: string) => string, names: (id: string) => string): string {
  const a = s.nodes.map(alias).join(', ');
  const served = s.deployments.map(names);
  if (s.kind === 'idle') return `${a}: serves nothing; updated without a drain`;
  if (s.kind === 'replica') return `${a}: drain, update, rejoin; ${served.join(', ')} stays served by the other member${served.length > 1 ? 's' : ''}`;
  if (s.kind === 'single') return `${a}: drain, update, rejoin`;
  return `${a} together: drain, update, rejoin`;
}

/** {release: aliases} when the answering Sparks run more than one release, else null. */
export function mixedVersions(nodes: Pick<FleetNode, 'alias' | 'version' | 'status'>[]): Map<string, string[]> | null {
  const got = new Map<string, string[]>();
  for (const n of nodes) {
    if (!n.version || n.status === 'off') continue;
    const r = releaseOf(n.version);
    got.set(r, [...(got.get(r) ?? []), n.alias]);
  }
  return got.size > 1 ? got : null;
}

/** The alert's title for a mixed fleet (fleetwatch's words). */
export function mixedTitle(m: Map<string, string[]>): string {
  return `Mixed versions: ${[...m.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([v, a]) => `${v} on ${a.join(', ')}`).join(' · ')}`;
}

export const PHASE_WORDS: Record<UpdatePhase, string> = {
  todo: 'waits',
  drain: 'draining',
  drained: 'drained',
  update: 'updating',
  rejoin: 'rejoining',
  done: 'updated',
  failed: 'failed',
  skipped: 'skipped',
};

/** how far a job is: Sparks done of all, and the step that runs */
export function jobProgress(job: UpdateJob): { done: number; total: number; pct: number; running: UpdateJob['steps'][number] | null } {
  const nodes = Object.values(job.nodes);
  const done = nodes.filter((n) => n.phase === 'done').length;
  const weight: Record<UpdatePhase, number> = { todo: 0, drain: 0.15, drained: 0.25, update: 0.5, rejoin: 0.85, done: 1, failed: 1, skipped: 1 };
  const pct = nodes.length ? Math.round((nodes.reduce((a, n) => a + weight[n.phase], 0) / nodes.length) * 100) : 100;
  return { done, total: nodes.length, pct, running: job.steps.find((s) => s.state === 'running') ?? null };
}
