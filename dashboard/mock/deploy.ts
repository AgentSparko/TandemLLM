// The deployments, the placement planner and the weights in the mock (`/v1/fleet/deployments/*`,
// `/v1/fleet/placement`, `/v1/fleet/weights*`, docs/contract/fleet-v1, SRV-731), as the controller
// answers them (proxy/deployments.py, proxy/placement.py, proxy/fleetweights.py), over the mock fleet's
// own deployments (Qwen3.8-27B Replicas on Desk + R-3, DeepSeek-V4.1-Flash Split over R-1 + R-2, Wren
// a Role on Desk). Jobs run on the clock, a function of the time since they started:
//
//   start   START_S per member; a Split's start is 501 not_built (ENG-307), its stop allowed (it
//           serves in this fleet's story), its move refused
//   stop    STOP_S; the record turns stopped at once, the status after the job
//   move    MOVE_S; the new member joins halfway, the old one leaves at the end; the API id stays
//   copy    the bytes at COPY_GBPS; done and verified (the source's content id) at the end
//
// The planner's figures are a model of the Setup Guide's estimate: the weights (parameters x bits of
// the pack), the KV of the window, 12 GiB of the engine's fixed parts and 10 of the OS; a node that
// serves something has what its memory split leaves free. R-1, R-2 and R-3 share the fabric switch;
// Desk has no fabric cable: a Split with Desk is refused "no fabric link between Desk and R-1".

import type {
  DeploymentAction,
  DeploymentJob,
  DeploymentMember,
  DeploymentNew,
  DeploymentPlan,
  DeploymentType,
  FleetDeployment,
  FleetNode,
  Placement,
  PlacementNode,
  PlacementType,
  WeightsCopyJob,
  WeightsMatrix,
  WeightsOnNode,
  WeightsPlan,
} from '../src/api/fleet.ts';
import { FLEET_CONTRACT } from '../src/api/fleet.ts';
import type { MockFleet } from './fleet.ts';

type Reply = { status: number; body: unknown };
const bad = (status: number, type: string, message: string): Reply => ({ status, body: { error: { type, message } } });
const ok = (body: unknown): Reply => ({ status: 200, body });

export const START_S = 6;
export const STOP_S = 3;
export const MOVE_S = 10;
export const COPY_GBPS = 2.4;
const CONFIRM_S = 300;
const GIB = 2 ** 30;
const BUDGET = 106.63;
const FLOOR = 15;
const OS = 10;
const FIXED = 12;
/** the parameters (billions) of the Setup Guide's models the planner knows */
const PARAMS: Record<string, number> = {
  'qwen3.8-27b': 27, 'qwen3.8-flash-next': 80, 'ornith-1.5-122b': 122, 'nemotron-3.5-bolt-74b-a9b': 74,
  'nemotron-3.5-super-vl-120b-a12b': 120, 'nemotron-3.5-lightning-30b-a3b': 30, 'lfm2.5-24b-a2b': 24, flint: 32,
};
/** the nodes on the fabric switch (fleet4) or the direct cable (fleet2-split) */
const FABRIC = [['r1', 'r2', 'r3'], ['s1', 's2']];

interface Overlay {
  state: 'serving' | 'stopped';
  members?: string[];
  name?: string;
  api_id?: string;
  ctx?: number;
  pack?: string;
  leaving?: string[];
}

interface Job {
  job: Omit<DeploymentJob, 'state' | 'steps' | 'ended_at' | 'log'>;
  start: number;
  span: number;
  from: string | null;
  to: string | null;
  members: string[];
}

interface Pack {
  profile: string;
  model: string | null;
  gib: number;
  content_id: string;
  on: Record<string, 'setup' | 'store'>;
}

interface Copy {
  job: Omit<WeightsCopyJob, 'state' | 'bytes_done' | 'files_done' | 'ended_at' | 'verified' | 'rate_gbps' | 'log'>;
  start: number;
  span: number;
  stopAt: number | null;
}

const cid = (s: string) => {
  let h = 2166136261;
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0;
  return `sha256:${h.toString(16).padStart(8, '0').repeat(8)}`;
};

export class MockDeploy {
  private over = new Map<string, Overlay>();
  private made: FleetDeployment[] = [];
  private gone = new Set<string>();
  private jobs = new Map<string, Job>();
  private tokens = new Map<string, { key: string; until: number }>();
  private copies: Copy[] = [];
  private packs: Pack[] | null = null;
  private seq = 0;

  constructor(private fleet: MockFleet) {}

  // ---- the deployments -----------------------------------------------------------------------
  /** the scenario's deployments with the records' fields, the user's records and changes */
  decorate(base: FleetDeployment[], nodes: FleetNode[], now: number): FleetDeployment[] {
    const all = [...base, ...this.made].filter((d) => !this.gone.has(d.id));
    return all.map((d) => this.view(d, nodes, now));
  }

  private view(d0: FleetDeployment, nodes: FleetNode[], now: number): FleetDeployment {
    const o = this.over.get(d0.id);
    const j = this.jobs.get(d0.id);
    // a move: the old members until its half, then the new one beside them, the old one leaving
    const mv = j && j.job.action === 'move' && now - j.start < j.span ? j : null;
    const half = !!mv && now - mv.start >= mv.span / 2;
    const members = mv ? (half ? [...mv.members, mv.to!] : mv.members) : (o?.members ?? d0.members);
    const d: FleetDeployment = { ...d0, ...(o?.name ? { name: o.name } : {}), ...(o?.api_id ? { api_id: o.api_id } : {}), ...(o?.ctx != null ? { ctx: o.ctx } : {}), ...(o?.pack != null ? { pack: o.pack } : {}), members };
    const by = new Map(nodes.map((n) => [n.id, n]));
    const leaving = mv && half ? [mv.from!] : [];
    const ms: DeploymentMember[] = members.map((m) => {
      const n = by.get(m);
      const starting = j && j.job.action === 'start' && now - j.start < j.span;
      const state = !n ? 'gone' : n.status === 'off' ? 'off' : leaving.includes(m) ? 'leaving' : n.status === 'draining' ? 'draining' : starting ? 'starting' : 'serving';
      const text = { gone: 'no longer in the fleet', off: n?.status_text ?? 'off', leaving: 'moving away: no new requests', draining: `${n?.alias} is drained`, starting: 'its engine e starts', serving: 'serving' }[state];
      return { node: m, alias: n?.alias ?? m, engine: 'engine', backend: `${m}-engine`, model: d.api_id, managed: true, state, text, routable: state === 'serving' };
    });
    const state = o?.state ?? 'serving';
    const stopping = j && j.job.action === 'stop';
    let status = d0.status;
    let statusText = d0.status_text;
    if (state === 'stopped' && (!stopping || now - j!.start >= j!.span)) {
      status = 'stopped';
      statusText = 'stopped';
    } else if (j && j.job.action === 'start' && now - j.start < j.span) {
      status = 'starting';
      statusText = `start: ${this.jobLine(j, now)}`;
    } else if (this.made.includes(d0) || o) {
      const live = ms.filter((m) => m.state !== 'leaving');
      const up = live.filter((m) => m.routable).length;
      status = up === live.length ? 'serving' : up ? 'degraded' : 'down';
      statusText = status === 'serving' ? (live.length > 1 ? `serving · ${live.length} of ${live.length} members` : 'serving') : `${up} of ${live.length} members serving`;
    }
    return {
      ...d,
      status,
      status_text: statusText,
      record: true,
      state,
      managed: true,
      member_states: ms,
      job: j ? this.jobShort(j, now) : null,
      ...(d.type === 'replicas' ? { affinity: 'root' as const } : {}),
    };
  }

  private list(now: number): FleetDeployment[] {
    return this.fleet.deployments(now);
  }

  private find(id: string, now: number): FleetDeployment | undefined {
    return this.list(now).find((d) => d.id === id);
  }

  private nodes(now: number): FleetNode[] {
    return this.fleet.nodes(now);
  }

  private create(b: unknown, now: number): Reply {
    if (!b || typeof b !== 'object') return bad(400, 'bad_request', 'the body is a deployment object');
    const x = b as DeploymentNew;
    if (!x.model || !x.type || !Array.isArray(x.members) || !x.members.length) return bad(400, 'bad_request', 'deployment: model, type and members are required');
    const types: DeploymentType[] = ['single', 'replicas', 'split', 'role'];
    if (!types.includes(x.type)) return bad(400, 'bad_request', `type is one of ${types.join(', ')}`);
    const members = x.members.map((m) => (typeof m === 'string' ? m : m.node));
    const n = members.length;
    if ((x.type === 'single' || x.type === 'role') && n !== 1) return bad(400, 'bad_request', `a ${x.type[0].toUpperCase()}${x.type.slice(1)} deployment has one member`);
    if (x.type === 'replicas' && n < 2) return bad(400, 'bad_request', 'a Replicas deployment has two or more members');
    if (x.type === 'split' && n !== 2 && n !== 4) return bad(400, 'bad_request', 'a Split runs over 2 or 4 nodes');
    const nodes = new Set(this.nodes(now).map((y) => y.id));
    const missing = members.find((m) => !nodes.has(m));
    if (missing) return bad(404, 'not_found', `no node ${missing}`);
    const api = x.api_id ?? x.model;
    const id = x.id ?? api.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '').slice(0, 64);
    const taken = this.list(now).find((d) => d.id === id || d.api_id === api);
    if (taken) return bad(409, 'conflict', taken.id === id ? `a deployment ${id} exists already` : `${taken.name} answers to ${api} already`);
    const d: FleetDeployment = {
      id,
      name: x.name ?? x.model,
      model: x.model,
      pack: x.pack ?? '',
      type: x.type,
      members,
      api_id: api,
      ctx: x.ctx ?? 0,
      status: 'serving',
      status_text: 'serving',
      ...(x.type === 'split' ? { stages: x.stages ?? members.map((m, i) => ({ node: m, layers: `stage ${i + 1} of ${n}`, gib: 0 })) } : {}),
      ...(x.type === 'role' && x.role ? { role: x.role } : {}),
      ...(x.type === 'role' && x.role_of ? { role_of: x.role_of } : {}),
    };
    this.made.push(d);
    this.over.set(id, { state: x.type === 'split' ? 'stopped' : (x.state ?? 'serving') });
    this.fleet.addEvent(now, 'deploy', n === 1 ? members[0] : 'all', `${d.name} (${x.type[0].toUpperCase()}${x.type.slice(1)}) recorded`, 'a deployment record');
    return ok({ contract_version: FLEET_CONTRACT, ...this.find(id, now)! });
  }

  private patch(id: string, b: unknown, now: number): Reply {
    const d = this.find(id, now);
    if (!d) return bad(404, 'not_found', `no deployment ${id}`);
    if (!b || typeof b !== 'object' || !Object.keys(b).length) return bad(400, 'bad_request', 'the body is an object with what changes');
    if (this.running(id, now)) return bad(409, 'busy', `a job runs on ${d.name}`);
    const x = b as Partial<DeploymentNew>;
    const o = this.over.get(id) ?? { state: 'serving' as const };
    if (x.members) {
      const ms = x.members.map((m) => (typeof m === 'string' ? m : m.node));
      if (d.type === 'replicas' && ms.length < 2) return bad(400, 'bad_request', 'a Replicas deployment has two or more members');
      o.members = ms;
    }
    if (x.name) o.name = x.name;
    if (x.api_id) o.api_id = x.api_id;
    if (x.ctx != null) o.ctx = x.ctx;
    if (x.pack != null) o.pack = x.pack;
    this.over.set(id, o);
    return ok({ contract_version: FLEET_CONTRACT, ...this.find(id, now)! });
  }

  private remove(id: string, now: number): Reply {
    const d = this.find(id, now);
    if (!d) return bad(404, 'not_found', `no deployment ${id}`);
    if (this.running(id, now)) return bad(409, 'busy', `a job runs on ${d.name}`);
    this.gone.add(id);
    this.fleet.addEvent(now, 'deploy', 'all', `${d.name}'s record removed`, 'its engines keep running');
    return ok({ contract_version: FLEET_CONTRACT, deleted: id });
  }

  // ---- plans and jobs ------------------------------------------------------------------------
  private running(id: string, now: number): boolean {
    const j = this.jobs.get(id);
    return !!j && now - j.start < j.span;
  }

  plan(id: string, action: string, q: URLSearchParams, now: number): Reply {
    const d = this.find(id, now);
    if (!d) return bad(404, 'not_found', `no deployment ${id}`);
    if (!['start', 'stop', 'move'].includes(action)) return bad(404, 'not_found', `no action ${action} (one of start, stop, move)`);
    const a = action as DeploymentAction;
    const p: DeploymentPlan = { contract_version: FLEET_CONTRACT, deployment: id, action: a, title: `${a[0].toUpperCase()}${a.slice(1)} ${d.name}`, allowed: true, blocked: null, destructive: a !== 'start', impact: [], keeps: [], confirm: null, expires_at: null, job: d.job ?? null, from: null, to: null };
    const ms = d.member_states ?? [];
    if (this.running(id, now)) Object.assign(p, { allowed: false, blocked: `a job runs on ${d.name}: wait for it` });
    else if (a === 'start') {
      if (d.type === 'split' && d.state === 'stopped') Object.assign(p, { allowed: false, blocked: 'a Split runs once the pipeline split (ENG-307) is built: its record and plan are kept until then' });
      else for (const m of ms) p.impact.push(m.state === 'serving' ? `${m.alias}: serves already; warm-up only` : `${m.alias}: TandemNode starts ${m.engine}, then one token warms it up`);
    } else if (a === 'stop') {
      if (d.state === 'stopped') Object.assign(p, { allowed: false, blocked: `${d.name} is stopped already` });
      p.impact.push(`${d.api_id} takes no new request: the gateway answers 503 deployment_stopped`, 'what is in flight finishes first (at most 600 s)', ...ms.map((m) => `${m.alias}: TandemNode stops ${m.engine}`));
      p.keeps.push('the record, its members and the weights on every node');
    } else {
      const from = q.get('from') ?? (ms.length === 1 ? ms[0].node : null);
      const to = q.get('to');
      Object.assign(p, { from, to });
      const nodes = this.nodes(now);
      const dst = nodes.find((n) => n.id === to);
      if (d.type === 'split') Object.assign(p, { allowed: false, blocked: 'a Split moves with ENG-307' });
      else if (!from || !to) Object.assign(p, { allowed: false, blocked: 'name the node it moves from (from) and to (to)' });
      else if (!d.members.includes(from)) Object.assign(p, { allowed: false, blocked: `${from} is no member of ${d.name}` });
      else if (d.members.includes(to)) Object.assign(p, { allowed: false, blocked: `${to} is a member of ${d.name} already` });
      else if (!dst) Object.assign(p, { allowed: false, blocked: `no node ${to}` });
      else if (dst.status === 'off' || dst.status === 'draining') Object.assign(p, { allowed: false, blocked: `${dst.alias} is ${dst.status === 'off' ? 'off' : 'drained'}: it takes nothing new` });
      else {
        const old = ms.find((m) => m.node === from)!;
        p.engine = 'engine';
        p.impact.push(`${dst.alias}: engine starts and warms up; then it takes ${d.name}'s new requests and conversations`, `${old.alias}: what is in flight finishes, then TandemNode stops ${old.engine}`, `${d.api_id} stays the API id`);
      }
    }
    if (p.allowed && p.destructive) {
      const t = `dp-${id}-${++this.seq}`;
      this.tokens.set(t, { key: `${id}:${a}`, until: now + CONFIRM_S });
      p.confirm = t;
      p.expires_at = now + CONFIRM_S;
    }
    return ok(p);
  }

  act(id: string, action: string, body: unknown, now: number): Reply {
    const b = (body && typeof body === 'object' ? body : {}) as { confirm?: string; from?: string; to?: string };
    const q = new URLSearchParams(Object.entries({ from: b.from, to: b.to }).filter((e): e is [string, string] => !!e[1]));
    const r = this.plan(id, action, q, now);
    if (r.status !== 200) return r;
    const p = r.body as DeploymentPlan;
    if (!p.allowed) return bad(/ENG-307/.test(p.blocked ?? '') ? 501 : 409, /ENG-307/.test(p.blocked ?? '') ? 'not_built' : /a job runs/.test(p.blocked ?? '') ? 'busy' : 'conflict', p.blocked ?? '');
    if (p.destructive) {
      if (p.confirm) this.tokens.delete(p.confirm);
      if (!b.confirm) return bad(428, 'confirm_required', `${action} needs the confirm of its plan (GET .../actions/${action})`);
      const t = this.tokens.get(b.confirm);
      this.tokens.delete(b.confirm);
      if (!t || t.key !== `${id}:${action}` || t.until < now) return bad(409, 'confirm_invalid', "this confirm is spent, stale or another action's: read the plan again");
    }
    const d = this.find(id, now)!;
    const a = action as DeploymentAction;
    const j: Job = {
      job: { id: `deploy-${id}-${now}-${++this.seq}`, kind: 'deploy', deployment: id, action: a, by: 'admin', started_at: now, error: null, from: p.from, to: p.to },
      start: now,
      span: a === 'start' ? START_S : a === 'stop' ? STOP_S : MOVE_S,
      from: p.from,
      to: p.to,
      members: d.members,
    };
    this.jobs.set(id, j);
    const o = this.over.get(id) ?? { state: 'serving' as const };
    if (a === 'stop') o.state = 'stopped';
    if (a === 'start') o.state = 'serving';
    if (a === 'move') o.members = d.members.filter((m) => m !== p.from).concat(p.to!);
    this.over.set(id, o);
    const words = a === 'move' ? `${d.name} moves ${p.from} → ${p.to}` : `${d.name} ${a === 'start' ? 'starts' : 'stops'}`;
    this.fleet.addEvent(now, 'deploy', d.members.length === 1 ? d.members[0] : 'all', words, `by admin`);
    return ok(this.jobView(j, now));
  }

  private jobLine(j: Job, now: number): string {
    const steps = this.steps(j, now);
    return steps.at(-1)?.text ?? j.job.action;
  }

  private steps(j: Job, now: number): DeploymentJob['steps'] {
    const t = now - j.start;
    const done = t >= j.span;
    const alias = (id: string) => this.nodes(now).find((n) => n.id === id)?.alias ?? id;
    if (j.job.action === 'start') return j.members.map((m) => ({ node: m, alias: alias(m), phase: done ? 'serving' : t < j.span / 2 ? 'start' : 'warm', text: done ? 'warm: serves' : t < j.span / 2 ? 'TandemNode starts engine' : 'its engine answers; warming up with one token' }));
    if (j.job.action === 'stop') return j.members.map((m) => ({ node: m, alias: alias(m), phase: done ? 'stopped' : 'stop', text: done ? 'stopped' : 'TandemNode stops engine' }));
    return [
      { node: j.to!, alias: alias(j.to!), phase: t < j.span / 2 ? 'start' : 'serving', text: t < j.span / 2 ? 'TandemNode starts engine' : 'warm: serves' },
      ...(t >= j.span / 2 ? [{ node: j.from!, alias: alias(j.from!), phase: (done ? 'stopped' : 'stop') as 'stopped' | 'stop', text: done ? 'stopped' : 'TandemNode stops engine' }] : []),
    ];
  }

  private jobFull(j: Job, now: number): DeploymentJob {
    const done = now - j.start >= j.span;
    return { ...j.job, state: done ? 'done' : 'running', ended_at: done ? j.start + j.span : null, steps: this.steps(j, now), log: this.steps(j, now).map((s) => ({ t: Math.min(now, j.start + j.span), text: `${s.alias}: ${s.text}` })) };
  }

  private jobShort(j: Job, now: number): NonNullable<FleetDeployment['job']> {
    const f = this.jobFull(j, now);
    return { id: f.id, action: f.action, state: f.state, started_at: f.started_at, ended_at: f.ended_at, text: f.log.at(-1)?.text ?? f.action, error: null };
  }

  private jobView(j: Job, now: number) {
    const f = this.jobFull(j, now);
    return { contract_version: FLEET_CONTRACT, deployment: f.deployment, action: f.action, state: f.state, job: f };
  }

  // ---- the placement planner -----------------------------------------------------------------
  private fabric(a: string, b: string): boolean {
    return FABRIC.some((g) => g.includes(a) && g.includes(b));
  }

  placement(body: unknown, now: number): Reply {
    const b = body as { model?: unknown; pack?: unknown; ctx?: unknown; slots?: unknown; nodes?: unknown } | null;
    if (!b || typeof b.model !== 'string' || typeof b.pack !== 'string' || !b.model || !b.pack) return bad(400, 'bad_request', 'model and pack are a Setup Guide model and one of its weight profiles');
    const ctx = b.ctx;
    const slots = b.slots ?? 1;
    if (typeof ctx !== 'number' || !Number.isInteger(ctx) || ctx < 1 || typeof slots !== 'number' || !Number.isInteger(slots) || slots < 1) return bad(400, 'bad_request', 'ctx and slots are whole numbers of at least 1');
    const all = this.nodes(now);
    const want = b.nodes as string[] | undefined;
    if (want && want.some((x) => !all.some((n) => n.id === x))) return bad(404, 'not_found', `no node ${want.find((x) => !all.some((n) => n.id === x))}`);
    const order = want ? want.map((x) => all.find((n) => n.id === x)!) : all;
    const params = PARAMS[b.model] ?? 27;
    const bits = Number(/(\d+(?:\.\d+)?)/.exec(b.pack)?.[1] ?? (/nvfp4/i.test(b.pack) ? 4.5 : /bf16/i.test(b.pack) ? 16 : 4));
    const weights = Math.round(((params * bits) / 8) * 1.07 * 100) / 100;
    const kv = Math.round(((ctx * slots * 65536) / GIB) * 100) / 100;
    const need = Math.round((weights + kv + FIXED) * 100) / 100;
    const mem = new Map(this.fleet.memory(now).map((m) => [m.node, m]));
    const busy = new Set(this.list(now).filter((d) => d.status !== 'stopped').flatMap((d) => d.members));
    const nodes: PlacementNode[] = order.map((n) => {
      const m = mem.get(n.id);
      const used = m ? m.weights + m.kv + m.cache + m.other : 0;
      const free = m ? Math.round((m.usable - used) * 100) / 100 : null;
      const serving = busy.has(n.id);
      const out: PlacementNode = {
        node: n.id, alias: n.alias, status: n.status, version: n.version, transport: n.transport, fits: false, need_gib: need, free_gib: free, budget_gib: BUDGET, total_gib: Math.round((need + OS) * 100) / 100,
        serving, source: 'engine', reason: null,
        parts: [{ id: 'os', label: 'The OS and other programs', gib: OS }, { id: 'weights', label: `Weights (${b.pack})`, gib: weights }, { id: 'engine', label: 'Engine, graphs and allocator', gib: FIXED }, { id: 'kv', label: 'Conversations (KV)', gib: kv }],
      };
      if (n.status === 'off') Object.assign(out, { source: 'none', need_gib: null, free_gib: null, budget_gib: null, total_gib: null, parts: [], reason: `${n.alias} is off` });
      else if (n.status === 'draining') out.reason = `${n.alias} is drained: it takes nothing new`;
      else if (need + OS > BUDGET) out.reason = `needs ${Math.round(need + OS)} GiB, ${n.alias}'s budget is ${Math.round(BUDGET)}`;
      else if (serving && free != null && need + FLOOR > free) out.reason = `needs ${Math.round(need)} GiB, ${n.alias} has ${Math.max(0, Math.round(free - FLOOR))} free`;
      else out.fits = true;
      return out;
    });
    const fit = nodes.filter((n) => n.fits);
    const first = nodes.find((n) => n.reason)?.reason ?? 'no node in the fleet';
    const one = (type: DeploymentType): PlacementType => ({ type, allowed: fit.length > 0, built: true, nodes: fit.map((n) => n.node), reason: fit.length ? null : first, warn: null, stages: [] });
    const rel = new Set(fit.map((n) => (n.version ?? '?').split('+')[0]));
    const far = fit.filter((n) => n.transport === 'tunnel').map((n) => n.alias);
    const near = fit.filter((n) => n.transport !== 'tunnel').map((n) => n.alias);
    const replicas: PlacementType =
      fit.length < 2
        ? { type: 'replicas', allowed: false, built: true, nodes: [], reason: `needs two nodes that fit: ${fit.length ? `only ${fit[0].alias} fits` : `none fits (${first})`}`, warn: null, stages: [] }
        : rel.size > 1
          ? { type: 'replicas', allowed: false, built: true, nodes: [], reason: 'Replicas members run one release (update the fleet)', warn: null, stages: [] }
          : { type: 'replicas', allowed: true, built: true, nodes: fit.map((n) => n.node), reason: null, warn: far.length && near.length ? `${near.join(', ')} on the LAN, ${far.join(', ')} remote: each conversation stays on one member; a failover moves it across the internet` : null, stages: [] };
    const split = this.split(nodes, !!want, weights, kv);
    return ok({ contract_version: FLEET_CONTRACT, model: b.model, pack: b.pack, ctx, slots, nodes, types: [one('single'), replicas, one('role'), split] } satisfies Placement);
  }

  private split(nodes: PlacementNode[], given: boolean, weights: number, kv: number): PlacementType {
    const out: PlacementType = { type: 'split', allowed: false, built: false, nodes: [], reason: null, warn: 'planned and recorded only: a Split runs once the pipeline split (ENG-307) is built', stages: [] };
    const sets = given ? [nodes] : nodes.flatMap((a, i) => nodes.slice(i + 1).map((b) => [a, b]));
    let reason: string | null = null;
    for (const s of sets) {
      let why: string | null = null;
      if (s.length !== 2 && s.length !== 4) why = `a Split runs over 2 or 4 nodes, not ${s.length}`;
      for (let i = 0; !why && i + 1 < s.length; i++) if (!this.fabric(s[i].node, s[i + 1].node)) why = `no fabric link between ${s[i].alias} and ${s[i + 1].alias}`;
      const stage = Math.round(((weights + kv) / s.length + FIXED) * 100) / 100;
      for (const n of s) if (!why && (n.status === 'off' || (n.free_gib != null && n.serving && stage + FLOOR > n.free_gib))) why = n.status === 'off' ? `${n.alias} is off` : `a stage needs ${Math.round(stage)} GiB, ${n.alias} has ${Math.max(0, Math.round(n.free_gib! - FLOOR))} free`;
      if (!why) return { ...out, allowed: true, nodes: s.map((n) => n.node), stages: s.map((n, i) => ({ node: n.node, layers: `stage ${i + 1} of ${s.length}`, gib: stage })) };
      reason ??= why;
    }
    return { ...out, reason: reason ?? 'a Split runs over 2 or 4 nodes: the fleet has fewer' };
  }

  // ---- the weights ---------------------------------------------------------------------------
  private seedPacks(now: number): Pack[] {
    if (this.packs) return this.packs;
    const ids = new Set(this.nodes(now).map((n) => n.id));
    const on = (...xs: string[]) => Object.fromEntries(xs.filter((x) => ids.has(x)).map((x) => [x, 'setup' as const]));
    this.packs = [
      { profile: 'exl3-4.0', model: 'qwen3.8-27b', gib: 17.2, content_id: cid('qwen27-exl3-4.0'), on: on('desk', 'r3', 'gold') },
      { profile: 'qwen3.5-2b-exl3-6.0', model: 'qwen3.5-2b', gib: 2.1, content_id: cid('wren-6.0'), on: on('desk') },
      { profile: 'deepseek-v4.1-flash-nvfp4', model: 'deepseek-v4.1-flash', gib: 154.7, content_id: cid('dsv41-nvfp4'), on: on('r1', 'r2', 's1', 's2') },
      { profile: 'exl3-6.0', model: 'qwen3.8-27b', gib: 23.4, content_id: cid('qwen27-exl3-6.0'), on: on('desk') },
    ].filter((p) => Object.keys(p.on).length);
    return this.packs;
  }

  private copyState(c: Copy, now: number): WeightsCopyJob {
    const t = (c.stopAt ?? now) - c.start;
    const total = c.job.bytes_total ?? 0;
    const done = !c.stopAt && t >= c.span;
    const bytes = done ? total : Math.min(total, Math.round(((t * COPY_GBPS) / 8) * 1e9));
    const state = c.stopAt ? 'stopped' : done ? 'done' : 'running';
    const files = c.job.files_total ?? 1;
    return {
      ...c.job,
      state,
      bytes_done: bytes,
      files_done: done ? files : Math.floor((bytes / Math.max(1, total)) * files),
      ended_at: state === 'running' ? null : c.start + Math.min(t, c.span),
      verified: done,
      rate_gbps: t > 0 ? COPY_GBPS : null,
      content_id: { ...c.job.content_id, copy: done ? c.job.content_id.source : null },
      error: c.stopAt ? { type: 'stopped', message: 'the copy was stopped' } : null,
      log: [{ t: c.start, text: `${files} files, ${(total / GIB).toFixed(2)} GiB, ${c.job.via}: ${c.job.via_why}` }, ...(done ? [{ t: c.start + c.span, text: `verified: ${c.job.content_id.source}` }] : [])],
    };
  }

  weights(now: number): WeightsMatrix {
    const packs = this.seedPacks(now);
    const nodes = this.nodes(now);
    for (const c of this.copies) {
      const s = this.copyState(c, now);
      const p = packs.find((x) => x.profile === c.job.profile);
      if (s.state === 'done' && p && !p.on[c.job.to]) p.on[c.job.to] = 'store';
    }
    const running = new Map<string, string>();
    for (const c of this.copies) if (this.copyState(c, now).state === 'running') running.set(`${c.job.to}:${c.job.profile}`, c.job.id);
    return {
      contract_version: FLEET_CONTRACT,
      nodes: nodes.map((n) => {
        const held = packs.filter((p) => p.on[n.id]).reduce((a, p) => a + p.gib, 0);
        return { node: n.id, alias: n.alias, status: n.status, store: `~/.tandemllm/node/weights`, free_gib: n.status === 'off' ? null : Math.round((3600 - held) * 100) / 100, note: n.status === 'off' ? 'its agent does not answer' : null };
      }),
      packs: packs.filter((p) => Object.keys(p.on).length || [...running.keys()].some((k) => k.endsWith(`:${p.profile}`))).map((p) => {
        const on: WeightsOnNode[] = Object.entries(p.on).map(([node, source]) => ({ node, present: true, source, dir: source === 'store' ? `~/.tandemllm/node/weights/${p.profile}` : `~/TandemLLM/weights/${p.profile}`, bytes: Math.round(p.gib * GIB), gib: p.gib, content_id: p.content_id, copy: null }));
        for (const [k, jid] of running) {
          const [node, prof] = k.split(':');
          if (prof === p.profile && !p.on[node]) on.push({ node, present: false, source: 'store', dir: null, bytes: null, gib: null, content_id: null, copy: jid });
        }
        return { profile: p.profile, model: p.model, nodes: on, same: true };
      }),
      copies: [...this.copies].reverse().slice(0, 10).map((c) => this.copyState(c, now)),
    };
  }

  private copy(body: unknown, now: number): Reply {
    const b = body as { from?: unknown; to?: unknown; profile?: unknown } | null;
    if (!b || typeof b.from !== 'string' || typeof b.to !== 'string' || typeof b.profile !== 'string') return bad(400, 'bad_request', 'the body is {from, to, profile}');
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(b.profile)) return bad(400, 'bad_request', "a pack's name is 1 to 128 of A-Z, a-z, 0-9, '.', '_', '-'");
    if (b.from === b.to) return bad(400, 'bad_request', 'a copy goes to another node');
    const nodes = this.nodes(now);
    const f = nodes.find((n) => n.id === b.from);
    const t = nodes.find((n) => n.id === b.to);
    if (!f || !t) return bad(404, 'not_found', `no node ${!f ? b.from : b.to}`);
    if (f.status === 'off' || t.status === 'off') return bad(409, 'unreachable', `${f.status === 'off' ? f.alias : t.alias} does not answer`);
    const p = this.seedPacks(now).find((x) => x.profile === b.profile && x.on[b.from as string]);
    if (!p) return bad(404, 'not_found', `${f.alias} has no ${b.profile}`);
    if (this.copies.some((c) => c.job.to === b.to && this.copyState(c, now).state === 'running')) return bad(409, 'busy', `a copy to ${t.alias} runs`);
    const tunnel = f.transport === 'tunnel' || t.transport === 'tunnel';
    const bytes = Math.round(p.gib * GIB);
    const c: Copy = {
      job: {
        id: `weights-${now}-${++this.seq}`, kind: 'weights', from: f.id, to: t.id, from_alias: f.alias, to_alias: t.alias, profile: p.profile,
        via: tunnel ? 'relay' : 'direct', via_why: tunnel ? `${f.transport === 'tunnel' ? f.alias : t.alias} is reached through an SSH tunnel` : 'both nodes are paired over mTLS on the LAN',
        by: 'admin', started_at: now, bytes_total: bytes, files_total: Math.max(2, Math.round(p.gib / 4)), content_id: { source: p.content_id, expected: p.content_id, copy: null }, error: null,
      },
      start: now,
      span: Math.max(2, Math.ceil((bytes * 8) / (COPY_GBPS * 1e9))),
      stopAt: null,
    };
    if (p.on[t.id]) return ok({ contract_version: FLEET_CONTRACT, job: { ...this.copyState(c, now), state: 'failed', ended_at: now, error: { type: 'exists', message: `${t.alias}: ${p.profile} is here already: delete it first` } } });
    this.copies.push(c);
    return ok({ contract_version: FLEET_CONTRACT, job: this.copyState(c, now) });
  }

  private plansDelete(node: string, profile: string, now: number): Reply {
    const n = this.nodes(now).find((x) => x.id === node);
    if (!n) return bad(404, 'not_found', `no node ${node}`);
    const p = this.seedPacks(now).find((x) => x.profile === profile && x.on[node]);
    const plan: WeightsPlan = { contract_version: FLEET_CONTRACT, node, profile, title: `Delete ${profile} on ${n.alias}`, allowed: true, blocked: null, impact: [], keeps: [], bytes: null, confirm: null, expires_at: null };
    if (!p) Object.assign(plan, { allowed: false, blocked: `${n.alias} has no ${profile}` });
    else {
      plan.bytes = Math.round(p.gib * GIB);
      const users = this.list(now).filter((d) => d.status !== 'stopped' && d.members.includes(node) && this.packOf(d) === profile);
      if (users.length) Object.assign(plan, { allowed: false, blocked: `${users.map((d) => d.name).join(', ')} serves ${profile} on ${n.alias}: stop it first` });
      plan.impact.push(`${profile} is removed from ${n.alias}: ${p.gib.toFixed(2)} GiB free again`);
      plan.keeps.push(`${profile} on every other node`);
    }
    if (plan.allowed) {
      const t = `wd-${node}-${++this.seq}`;
      this.tokens.set(t, { key: `${node}:${profile}`, until: now + CONFIRM_S });
      plan.confirm = t;
      plan.expires_at = now + CONFIRM_S;
    }
    return ok(plan);
  }

  /** the pack a deployment serves, as the mock's packs name it */
  private packOf(d: FleetDeployment): string {
    if (d.id === 'qwen27') return 'exl3-4.0';
    if (d.id === 'wren') return 'qwen3.5-2b-exl3-6.0';
    if (d.id === 'dsv41') return 'deepseek-v4.1-flash-nvfp4';
    return d.pack;
  }

  private deleteWeights(node: string, profile: string, confirm: string | null, now: number): Reply {
    const r = this.plansDelete(node, profile, now);
    if (r.status !== 200) return r;
    const plan = r.body as WeightsPlan;
    if (plan.confirm) this.tokens.delete(plan.confirm);
    if (!plan.allowed) return bad(409, /stop it first/.test(plan.blocked ?? '') ? 'in_use' : 'not_found', plan.blocked ?? '');
    if (!confirm) return bad(428, 'confirm_required', `a removal needs the confirm of its plan (GET /v1/fleet/weights/${node}/${profile})`);
    const t = this.tokens.get(confirm);
    this.tokens.delete(confirm);
    if (!t || t.key !== `${node}:${profile}` || t.until < now) return bad(409, 'confirm_invalid', "this confirm is spent, stale or another pack's: read the plan again");
    const p = this.seedPacks(now).find((x) => x.profile === profile)!;
    delete p.on[node];
    this.fleet.addEvent(now, 'weights', node, `${profile} deleted`, `${p.gib.toFixed(2)} GiB freed · by admin`);
    return ok({ contract_version: FLEET_CONTRACT, node, profile, deleted: true, bytes: plan.bytes });
  }

  // ---- the routes ----------------------------------------------------------------------------
  handle(method: string, parts: string[], q: URLSearchParams, body: unknown, now: number): Reply {
    if (parts[0] === 'placement') return method === 'POST' && parts.length === 1 ? this.placement(body, now) : bad(404, 'not_found', `no route ${method} /v1/fleet/placement`);
    if (parts[0] === 'weights') {
      const p = parts.slice(1);
      if (method === 'GET' && !p.length) return ok(this.weights(now));
      if (p[0] === 'copy') {
        if (method === 'POST' && p.length === 1) return this.copy(body, now);
        const c = this.copies.find((x) => x.job.id === p[1]);
        if (!c) return bad(404, 'not_found', `no weights copy ${p[1]}`);
        if (method === 'GET' && p.length === 2) return ok({ contract_version: FLEET_CONTRACT, job: this.copyState(c, now) });
        if (method === 'POST' && p.length === 3 && p[2] === 'stop') {
          if (this.copyState(c, now).state !== 'running') return bad(409, 'conflict', 'this copy is not running');
          c.stopAt = now;
          return ok({ contract_version: FLEET_CONTRACT, job: this.copyState(c, now) });
        }
      } else if (p.length === 2) {
        if (method === 'GET') return this.plansDelete(p[0], p[1], now);
        if (method === 'DELETE') return this.deleteWeights(p[0], p[1], q.get('confirm'), now);
      }
      return bad(404, 'not_found', `no route ${method} /v1/fleet/${parts.join('/')}`);
    }
    const p = parts.slice(1);
    if (!p.length) {
      if (method === 'GET') return ok({ contract_version: FLEET_CONTRACT, deployments: this.list(now) });
      if (method === 'POST') return this.create(body, now);
    } else if (p[0] === 'jobs' && p.length === 2 && method === 'GET') {
      const j = [...this.jobs.values()].find((x) => x.job.id === p[1]);
      return j ? ok(this.jobView(j, now)) : bad(404, 'not_found', `no deployment job ${p[1]}`);
    } else if (p.length === 1) {
      if (method === 'GET') {
        const d = this.find(p[0], now);
        return d ? ok({ contract_version: FLEET_CONTRACT, ...d }) : bad(404, 'not_found', `no deployment ${p[0]}`);
      }
      if (method === 'PATCH') return this.patch(p[0], body, now);
      if (method === 'DELETE') return this.remove(p[0], now);
    } else if (p.length === 3 && p[1] === 'actions') {
      if (method === 'GET') return this.plan(p[0], p[2], q, now);
      if (method === 'POST') return this.act(p[0], p[2], body, now);
    }
    return bad(404, 'not_found', `no route ${method} /v1/fleet/${parts.join('/')}`);
  }
}
