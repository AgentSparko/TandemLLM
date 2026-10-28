// Where something ran in the fleet (VIS-254): a ledger row, a lane in flight, an answer, a model the
// Chat tab offers. A controller's ledger names the node and the deployment of every request its
// gateway sent to a node; the server this page talks to may be an engine instead (a reverse proxy
// sends /v1/* to an engine and /v1/fleet/* to the controller), whose own rows and lanes name
// neither. Those are attributed through the deployments' members: each member says which backend
// reaches which node's engine, and the backend `engine` is the server's own engine (the box's,
// the member the controller derives for it). One Spark in the fleet is today's box: the pages show
// no fleet extras then (`isFleet`).

import type { FleetDeployment, FleetNode } from '../api/fleet';
import type { LedgerSource, Where } from '../api/types';
import type { Scope } from './scope';

export interface FleetView {
  nodes: FleetNode[];
  deployments: FleetDeployment[];
}

/** the backend id of the server's own engine, in its ledger and its lanes (server/parallel.py) */
const OWN = 'engine';
const NOWHERE: Where = { node: null, deployment: null };

/** More than one Spark: the pages add their fleet parts. One Spark (or none) looks like today. */
export function isFleet(f: Pick<FleetView, 'nodes'> | null | undefined): f is FleetView {
  return !!f && f.nodes.length > 1;
}

interface Member {
  node: string;
  deployment: string;
  /** what the backend serves the model under, and the deployment's ids */
  ids: string[];
}

function members(f: FleetView, backend: string): Member[] {
  const out: Member[] = [];
  for (const d of f.deployments) {
    for (const m of d.member_states ?? []) {
      if (m.backend === backend) out.push({ node: m.node, deployment: d.id, ids: [m.model, d.api_id, d.id, d.model] });
    }
  }
  return out;
}

/** The node and deployment a backend's request ran on. `backend` null is the server's own engine.
 *  A backend that serves several deployments (two models on one engine) is told apart by `model`;
 *  one no member names (a cloud API, a backend outside the fleet) is nowhere. */
export function whereOf(f: FleetView | null | undefined, backend: string | null | undefined, model?: string | null): Where {
  if (!f) return NOWHERE;
  const ms = members(f, backend ?? OWN);
  if (!ms.length) return NOWHERE;
  const m = (model ? ms.find((x) => x.ids.includes(model)) : null) ?? (ms.length === 1 ? ms[0] : null);
  return m ? { node: m.node, deployment: m.deployment } : { node: ms.every((x) => x.node === ms[0].node) ? ms[0].node : null, deployment: null };
}

/** A ledger row's place: its own `node` and `deployment` (a controller's ledger), else attributed. */
export function rowWhere(f: FleetView | null | undefined, r: { node?: string | null; deployment?: string | null; backend?: { id: string } | null; model: string }): Where {
  if (r.node || r.deployment) return { node: r.node ?? null, deployment: r.deployment ?? null };
  return whereOf(f, r.backend?.id ?? null, r.model);
}

/** The deployment a model the Chat tab offers is served by: a record's API id (or its id), else
 *  what this server's own engine serves under that id (`own`: the Chat tab's engine model); null
 *  for one the fleet does not serve (a cloud model). */
export function deploymentOf(f: FleetView | null | undefined, model: string | null | undefined, own = false): FleetDeployment | null {
  if (!f || !model) return null;
  const d = f.deployments.find((x) => x.api_id === model || x.id === model);
  if (d) return d;
  const w = own ? whereOf(f, null, model) : null;
  return w?.deployment ? (f.deployments.find((x) => x.id === w.deployment) ?? null) : null;
}

/** The node this server's own engine is: the member reached through its own backend, else the
 *  controller's own machine when it is a Spark; null when this server runs no fleet node's engine
 *  (a controller on a Mac). */
export function hereNode(f: FleetView | null | undefined): FleetNode | null {
  if (!f) return null;
  const id = members(f, OWN)[0]?.node ?? f.nodes.find((n) => n.pairing?.state === 'local')?.id;
  return f.nodes.find((n) => n.id === id) ?? null;
}

/** The Sparks a scope covers: all, one, or a deployment's members. */
export function scopeNodes(f: FleetView, s: Pick<Scope, 'kind' | 'id'>): FleetNode[] {
  if (s.kind === 'node') return f.nodes.filter((n) => n.id === s.id);
  if (s.kind === 'deployment') {
    const d = f.deployments.find((x) => x.id === s.id);
    return f.nodes.filter((n) => d?.members.includes(n.id));
  }
  return f.nodes;
}

/** Whether a place is in the scope; nowhere (a cloud backend) is only in All. */
export function inScope(w: Where, s: Pick<Scope, 'kind' | 'id'>): boolean {
  if (s.kind === 'node') return w.node === s.id;
  if (s.kind === 'deployment') return w.deployment === s.id;
  return true;
}

/** The ledger a page reads. This server's by default: it holds every request that came to it,
 *  the ones sent straight to its engine included (a controller's gateway sees only its own). A
 *  Spark or a deployment in scope, or a node or deployment filter, needs the controller's: the
 *  only one that knows nodes. The page may pick either (`?ledger=fleet`, `?ledger=here`) on a
 *  fleet, or when the data plane runs through the controller (`gateway`, SRV-733): then the
 *  controller's is the default, every client request with its Spark, each once. */
export function ledgerFor(f: (Pick<FleetView, 'nodes'> & { gateway?: boolean }) | null | undefined, s: Pick<Scope, 'kind'>, picked: LedgerSource | null, filtered = false): LedgerSource {
  if (!f?.nodes.length) return 'here';
  if (s.kind !== 'all' || filtered) return 'fleet';
  return picked ?? (f.gateway ? 'fleet' : 'here');
}

export interface LaneGroup<T> {
  key: string;
  deployment: FleetDeployment | null;
  node: FleetNode | null;
  items: T[];
}

/** Items grouped by deployment, then node: deployments by name, nodes by alias, then what ran on a
 *  node outside any deployment, and last what ran on no Spark (a cloud API). */
export function groupByPlace<T>(items: T[], whereOfItem: (x: T) => Where, f: FleetView): LaneGroup<T>[] {
  const groups = new Map<string, LaneGroup<T>>();
  for (const x of items) {
    const w = whereOfItem(x);
    const key = `${w.deployment ?? ''}|${w.node ?? ''}`;
    let g = groups.get(key);
    if (!g) {
      g = { key, deployment: f.deployments.find((d) => d.id === w.deployment) ?? null, node: f.nodes.find((n) => n.id === w.node) ?? null, items: [] };
      groups.set(key, g);
    }
    g.items.push(x);
  }
  const rank = (g: LaneGroup<T>) => (g.deployment ? 0 : g.node ? 1 : 2);
  return [...groups.values()].sort((a, b) => rank(a) - rank(b) || (a.deployment?.name ?? '').localeCompare(b.deployment?.name ?? '') || (a.node?.alias ?? '').localeCompare(b.node?.alias ?? ''));
}
