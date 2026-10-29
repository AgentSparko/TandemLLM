// The fleet update and the controller's host in the mock (`/v1/fleet/update*`, `/v1/fleet/controller*`,
// contracts/fleet-v1, OPS-234), as the controller answers them (proxy/fleetupdate.py,
// proxy/ctlmove.py). Both are jobs on the clock, a function of the time since they started, so every
// request at one instant sees one state:
//
//   update   the order of lib/fleet-update.ts; each step drains its Spark for DRAIN_S (none for an idle
//            one), updates it for UPDATE_S (its version turns to the target at the end) and rejoins it
//            for REJOIN_S; a Split's members run their step together. Stop ends the job before the next
//            step. The mock controller carries 0.15.1 (fleet1: 0.15.0, today's box: nothing to do)
//   move     six steps of MOVE_STEP_S each; at the end the controller says moved_to the Spark's URL
//   take     the pull, five steps of the same length
//   mixed    the scenario `mixed`: R-3 runs 0.15.1 since 8 minutes, the others 0.15.0: the
//            version-mismatch alert fires a minute after (held while an update runs)

import type { ControllerView, FleetAlert, FleetNode, MoveJob, MoveNode, MovePlan, UpdateJob, UpdatePhase, UpdatePlan, UpdateView } from '../src/api/fleet.ts';
import { FLEET_CONTRACT } from '../src/api/fleet.ts';
import { MIN_PASSWORD } from '../src/lib/controller-host.ts';
import { mixedTitle, mixedVersions, order, stepWords, type PlanNode } from '../src/lib/fleet-update.ts';
import type { MockFleet } from './fleet.ts';

type Reply = { status: number; body: unknown };
const bad = (status: number, type: string, message: string): Reply => ({ status, body: { error: { type, message } } });

export const DRAIN_S = 4;
export const UPDATE_S = 8;
export const REJOIN_S = 3;
export const MOVE_STEP_S = 2;
const CONFIRM_S = 300;
const MIXED_FOR_S = 60;

interface UpdateRun {
  id: string;
  start: number;
  target: string;
  by: string;
  steps: UpdatePlan['steps'];
  refused: UpdatePlan['refused'];
  skipped: UpdatePlan['skipped'];
  from: Record<string, string | null>;
  alias: Record<string, string>;
  /** stop asked at: the job ends before the next step after this */
  stopAt: number | null;
}

interface MoveRun {
  job: Omit<MoveJob, 'state' | 'steps' | 'ended_at' | 'result' | 'log'>;
  labels: [string, string][];
  start: number;
  moves: boolean;
}

export class MockOps {
  private run: UpdateRun | null = null;
  private tokens = new Map<string, { key: string; until: number }>();
  private moveRun: MoveRun | null = null;
  private seq = 0;
  /** the version each Spark ran before any update here (a scenario's own) */
  private base = new Map<string, string>();

  constructor(private fleet: MockFleet) {}

  target(): string {
    return this.fleet.scenario === 'fleet1' ? '0.15.0' : '0.15.1';
  }

  /** A scenario's starting versions (`mixed`: R-3 updated by hand). */
  seed(id: string, v: string): void {
    this.base.set(id, v);
  }

  // ---- the update on the clock --------------------------------------------------------------------
  private schedule(r: UpdateRun): { s: UpdatePlan['steps'][number]; from: number; drainEnd: number; updEnd: number; to: number }[] {
    let t = r.start;
    return r.steps.map((s) => {
      const from = t;
      const drainEnd = from + (s.drain ? DRAIN_S : 0);
      const updEnd = drainEnd + UPDATE_S;
      const to = updEnd + REJOIN_S;
      t = to;
      return { s, from, drainEnd, updEnd, to };
    });
  }

  /** the step the job stopped before (index), or null */
  private stoppedAt(r: UpdateRun): number | null {
    if (r.stopAt == null) return null;
    const sch = this.schedule(r);
    const i = sch.findIndex((x) => r.stopAt! < x.to);
    return i < 0 ? null : i + 1 < sch.length ? i + 1 : null;
  }

  private phase(r: UpdateRun, id: string, now: number): { phase: UpdatePhase; at: number | null; end: number | null } {
    const sch = this.schedule(r);
    const stop = this.stoppedAt(r);
    const i = sch.findIndex((x) => x.s.nodes.includes(id));
    const x = sch[i];
    if (!x || (stop != null && i >= stop && now >= sch[stop].from)) return { phase: 'todo', at: null, end: null };
    if (now < x.from) return { phase: 'todo', at: null, end: null };
    if (now < x.drainEnd) return { phase: now < x.drainEnd - 1 ? 'drain' : 'drained', at: x.from, end: null };
    if (now < x.updEnd) return { phase: 'update', at: x.from, end: null };
    if (now < x.to) return { phase: 'rejoin', at: x.from, end: null };
    return { phase: 'done', at: x.from, end: x.to };
  }

  /** the version a Spark runs at `now`: the target once its update reached rejoin */
  version(id: string, fallback: string, now: number): string {
    const r = this.run;
    if (r && r.steps.some((s) => s.nodes.includes(id))) {
      const p = this.phase(r, id, now).phase;
      if (p === 'rejoin' || p === 'done') return r.target;
    }
    return this.base.get(id) ?? fallback;
  }

  /** a drain the update holds on a Spark at `now` (draining for its first second, then drained) */
  drain(id: string, now: number): { state: 'draining' | 'drained'; since: number; by: string; inflight: number } | null {
    const r = this.run;
    if (!r) return null;
    const sch = this.schedule(r).find((x) => x.s.nodes.includes(id));
    if (!sch || !sch.s.drain) return null;
    const p = this.phase(r, id, now).phase;
    if (!['drain', 'drained', 'update'].includes(p)) return null;
    return { state: now < sch.from + 1 ? 'draining' : 'drained', since: sch.from, by: 'fleet update', inflight: now < sch.from + 1 ? 1 : 0 };
  }

  running(now: number): boolean {
    return !!this.run && this.jobState(this.run, now) === 'running';
  }

  private jobState(r: UpdateRun, now: number): UpdateJob['state'] {
    const sch = this.schedule(r);
    const stop = this.stoppedAt(r);
    if (stop != null && now >= sch[stop - 1].to) return 'stopped';
    return now >= (sch.at(-1)?.to ?? r.start) ? 'done' : 'running';
  }

  private job(r: UpdateRun, now: number): UpdateJob {
    const sch = this.schedule(r);
    const state = this.jobState(r, now);
    const stop = this.stoppedAt(r);
    const nodes: UpdateJob['nodes'] = {};
    const log: UpdateJob['log'] = [{ t: r.start, text: `update to ${r.target} started by ${r.by}: ${r.steps.map((s) => s.words).join('; ')}` }];
    const detail: Record<UpdatePhase, string | null> = { todo: null, drain: 'no new requests; those in flight finish', drained: 'nothing in flight', update: `installing ${r.target} on the node`, rejoin: `runs ${r.target}; rejoining`, done: `runs ${r.target}; serves again`, failed: null, skipped: null };
    for (const x of sch) {
      for (const id of x.s.nodes) {
        const p = this.phase(r, id, now);
        nodes[id] = { alias: r.alias[id], from: r.from[id], to: r.target, phase: p.phase, detail: x.s.kind === 'idle' && p.phase === 'update' ? `installing ${r.target}; it serves nothing` : detail[p.phase], started_at: p.at, ended_at: p.end, drained_before: false };
        if (now >= x.from && x.s.drain) log.push({ t: x.from, text: `${r.alias[id]}: draining` });
        if (now >= x.drainEnd) log.push({ t: x.drainEnd, text: `${r.alias[id]}: installing ${r.target}` });
        if (now >= x.updEnd) log.push({ t: x.updEnd, text: `${r.alias[id]}: runs ${r.target}` });
        if (now >= x.to) log.push({ t: x.to, text: `${r.alias[id]}: rejoined` });
      }
    }
    if (r.stopAt != null) log.push({ t: r.stopAt, text: 'stop asked by admin: the update stops after the step that runs' });
    if (state === 'done') log.push({ t: sch.at(-1)!.to, text: `every step done: the fleet runs ${r.target}` });
    if (state === 'stopped') log.push({ t: sch[stop! - 1].to, text: `stopped before step ${stop! + 1}` });
    log.sort((a, b) => a.t - b.t);
    return {
      id: r.id, kind: 'update', state, target: r.target, by: r.by, started_at: r.start,
      ended_at: state === 'running' ? null : state === 'stopped' ? sch[stop! - 1].to : sch.at(-1)!.to,
      stop: r.stopAt != null, error: null, log, refused: r.refused, skipped: r.skipped,
      steps: sch.map((x, i) => ({ ...x.s, state: stop != null && i >= stop && state === 'stopped' ? 'todo' : now >= x.to ? 'done' : now >= x.from ? 'running' : 'todo' })),
      nodes,
    };
  }

  private planNodes(now: number): PlanNode[] {
    return this.fleet.nodes(now).map((n) => ({
      id: n.id, alias: n.alias, version: n.version, status: n.status,
      updatable: n.status !== 'off', why: n.status === 'off' ? `does not answer: ${n.status_text}` : null,
    }));
  }

  plan(now: number, only: string[] | null): UpdatePlan {
    const target = this.target();
    const nodes = this.planNodes(now);
    const deps = this.fleet.deployments(now);
    const got = order(nodes, deps, target, only);
    const alias = (id: string) => nodes.find((n) => n.id === id)?.alias ?? id;
    const names = (id: string) => deps.find((d) => d.id === id)?.name ?? id;
    const steps = got.steps.map((s) => ({ ...s, words: stepWords(s, alias, names) }));
    const blocked = got.blocked ?? (this.running(now) ? 'an update runs' : null) ?? (steps.length ? null : !got.refused.length && !got.skipped.length ? `every node runs ${target} already` : 'no node can be updated now');
    const p: UpdatePlan = {
      contract_version: FLEET_CONTRACT, target, controller: target, allowed: blocked == null, blocked, steps, skipped: got.skipped, refused: got.refused, current: got.current,
      nodes, selected: only, confirm: null, expires_at: null,
    };
    if (p.allowed) {
      const token = `upd-${++this.seq}-${Math.floor(now)}`;
      this.tokens.set(token, { key: `${target}|${(only ?? []).join(',')}`, until: now + CONFIRM_S });
      p.confirm = token;
      p.expires_at = now + CONFIRM_S;
    }
    return p;
  }

  view(now: number): UpdateView {
    return { contract_version: FLEET_CONTRACT, target: this.target(), job: this.run ? this.job(this.run, now) : null };
  }

  handleUpdate(method: string, parts: string[], q: URLSearchParams, body: unknown, now: number): Reply {
    const p = parts.slice(1);
    if (method === 'GET' && !p.length) return { status: 200, body: this.view(now) };
    if (method === 'GET' && p[0] === 'plan' && p.length === 1) {
      const only = (q.get('nodes') ?? '').split(',').filter(Boolean);
      return { status: 200, body: this.plan(now, only.length ? only : null) };
    }
    if (method === 'POST' && p[0] === 'stop' && p.length === 1) {
      if (!this.run || !this.running(now)) return bad(409, 'conflict', 'no update runs');
      this.run.stopAt ??= now;
      return { status: 200, body: this.view(now) };
    }
    if (method === 'POST' && !p.length) {
      const b = body as { confirm?: unknown; nodes?: unknown } | null;
      if (!b || typeof b !== 'object' || Object.keys(b).some((k) => !['confirm', 'nodes'].includes(k))) return bad(400, 'bad_request', 'the body is {confirm, nodes?}');
      const only = Array.isArray(b.nodes) && b.nodes.length ? (b.nodes as string[]) : null;
      const plan = this.plan(now, only);
      if (plan.confirm) this.tokens.delete(plan.confirm);
      if (!plan.allowed) return bad(409, (plan.blocked ?? '').includes('Split') ? 'partial_split' : 'conflict', plan.blocked ?? 'refused');
      if (!b.confirm) return bad(428, 'confirm_required', 'the fleet update needs its plan’s confirm: GET /v1/fleet/update/plan first');
      const tok = this.tokens.get(String(b.confirm));
      this.tokens.delete(String(b.confirm));
      if (!tok || tok.key !== `${plan.target}|${(only ?? []).join(',')}` || tok.until <= now) return bad(409, 'confirm_invalid', 'that confirm is spent, too old or another plan’s: read the plan again');
      const nodes = this.fleet.nodes(now);
      this.run = {
        id: `u-mock${++this.seq}`, start: now, target: plan.target, by: 'admin', steps: plan.steps, refused: plan.refused, skipped: plan.skipped,
        from: Object.fromEntries(nodes.map((n) => [n.id, n.version])), alias: Object.fromEntries(nodes.map((n) => [n.id, n.alias])), stopAt: null,
      };
      this.fleet.addEvent(now, 'update', 'all', `Fleet update to ${plan.target} started`, `${plan.steps.length} steps · by admin`);
      return { status: 200, body: this.view(now) };
    }
    return bad(404, 'not_found', `no route ${method} /v1/fleet/${parts.join('/')}`);
  }

  // ---- the mixed-version alert --------------------------------------------------------------------
  private mixedSince: number | null = null;

  alerts(now: number, nodes: FleetNode[], since: number): FleetAlert[] {
    const m = mixedVersions(nodes);
    if (!m || this.running(now)) {
      if (!m) this.mixedSince = null;
      return [];
    }
    this.mixedSince ??= Math.min(now, since);
    const at = this.mixedSince + MIXED_FOR_S;
    if (now < at) return [];
    return [{
      id: 'version-mismatch:all', severity: 'warn', node: 'all', title: mixedTitle(m), since: at, rule: 'One release on every node nodes run different releases for 60 s',
      detail: `Replicas and Split members must run one release, and a node on another one is refused by Add Spark’s Install. This controller carries ${this.target()}: update the fleet.`,
      action: { label: 'Update fleet', route: '#/sparks?update=1' }, acked: false, acked_at: null,
    }];
  }

  // ---- the controller's host --------------------------------------------------------------------
  private moved(now: number): ControllerView['moved_to'] {
    const r = this.moveRun;
    if (!r || !r.moves || now < r.start + r.labels.length * MOVE_STEP_S) return null;
    return { api_url: r.job.api_url, name: `TandemApp on ${r.job.alias}`, fingerprint: '9a'.repeat(32), at: r.start + r.labels.length * MOVE_STEP_S };
  }

  private moveJob(now: number): MoveJob | null {
    const r = this.moveRun;
    if (!r) return null;
    const n = r.labels.length;
    const k = Math.floor((now - r.start) / MOVE_STEP_S);
    const done = k >= n;
    const steps = r.labels.map(([id, label], i) => ({ id, label, state: (i < k ? 'done' : i === k ? 'running' : 'todo') as 'done' | 'running' | 'todo', detail: i < k ? DETAIL[id]?.(r) ?? null : null }));
    const nodes = r.job.nodes.map((x) => (x.how === 'stays' || k < 3 ? x : { ...x, state: 'handed' as const, ...(k >= 4 ? { accepted: true } : {}) }));
    return {
      ...r.job, nodes, steps, state: done ? 'done' : 'running', ended_at: done ? r.start + n * MOVE_STEP_S : null,
      result: done ? { api_url: r.job.api_url, nodes } : null,
      log: steps.filter((s) => s.state === 'done').map((s, i) => ({ t: r.start + (i + 1) * MOVE_STEP_S, text: `${s.label}: ${s.detail ?? 'done'}` })),
      ...(r.moves && done ? { old_refused: true } : {}),
    };
  }

  controller(now: number): ControllerView {
    const f = this.fleet;
    const h = f.hostInfo();
    const nodes = f.nodes(now);
    const local = nodes.find((n) => n.pairing.state === 'local') ?? null;
    return {
      contract_version: FLEET_CONTRACT,
      host: { name: h.name, os: h.may_sleep ? 'macOS 26.1' : 'DGX OS 7.2.1', kind: h.may_sleep ? 'mac' : local ? 'spark' : 'linux', node: local?.id ?? null, may_sleep: h.may_sleep, address: h.address },
      api_url: h.api_url, moved_to: this.moved(now),
      sleep_warning: h.may_sleep ? 'This Mac can sleep: while it sleeps the API is down and no metrics are kept. Keep it awake, or run the controller on a Spark.' : null,
      targets: nodes.filter((n) => n !== local).map((n) => ({ node: n.id, alias: n.alias, allowed: !this.notHost(n), why: this.notHost(n), virtual: false })),
      job: this.moveJob(now),
    };
  }

  private notHost(n: FleetNode): string | null {
    if (n.lease_end != null) return `${n.alias} is rented: its lease ends, the controller must outlive it`;
    if (n.transport === 'tunnel') return `${n.alias} is reached through an SSH tunnel: not on the LAN`;
    if (n.status === 'off') return `${n.alias} does not answer: ${n.status_text}`;
    return null;
  }

  movePlan(to: string, port: number, now: number): MovePlan | Reply {
    const f = this.fleet;
    const nodes = f.nodes(now);
    const t = nodes.find((n) => n.id === to);
    if (!t) return bad(404, 'not_found', `no node ${to}`);
    if (!(port >= 1024 && port < 65536)) return bad(400, 'bad_request', 'port is a whole number from 1024 to 65535');
    const host = t.address.split(':')[0];
    const url = `http://${host}:${port}`;
    const mn: MoveNode[] = nodes.map((n) =>
      n.pairing.state === 'local'
        ? { id: n.id, alias: n.alias, how: 'own', why: 'this machine’s node: its agent is handed to the new controller with its LAN address' }
        : n.transport === 'tunnel'
          ? { id: n.id, alias: n.alias, how: 'stays', why: 'reached through an SSH tunnel with a key on this machine: release it here, add it from the new one' }
          : n.id === to
            ? { id: n.id, alias: n.alias, how: 'release', why: 'the new controller’s own machine: read over its loopback' }
            : { id: n.id, alias: n.alias, how: 'repin', why: null },
    );
    const moved = mn.filter((x) => x.how !== 'stays');
    const needsPw = f.hostInfo().may_sleep;
    let blocked = this.notHost(t);
    if (!blocked && this.moved(now)) blocked = 'this controller moved already: open the new one';
    if (!blocked && this.moveJob(now)?.state === 'running') blocked = 'a move runs';
    const p: MovePlan = {
      contract_version: FLEET_CONTRACT, to, title: `Move the controller to ${t.alias}`, allowed: !blocked, blocked, api_url: url, port,
      impact: [
        `${t.alias} runs TandemApp’s controller from now on (a systemd user unit, port ${port}); this controller starts it there with this fleet’s export`,
        `${moved.length} node${moved.length === 1 ? '' : 's'} trust the new controller and refuse this one`,
        `the API moves to ${url}/v1: point clients there`,
        'this page follows to the new address',
        ...mn.filter((x) => x.how === 'stays').map((x) => `${x.alias}: ${x.why}`),
        ...(needsPw ? [`the new controller listens on the LAN: give it an admin password (${MIN_PASSWORD} or more characters)`] : []),
      ],
      removes: [],
      keeps: [
        'backends, routes, the proxy’s keys, alert rules, aliases, colours and layouts go with the export; stored API keys too, under the new controller’s own master key',
        'the metric history and the event log stay on this machine (fleet.sqlite3)',
        'this controller keeps running for its page; it no longer reads the moved nodes',
      ],
      nodes: mn, needs_password: needsPw, confirm: null, expires_at: null,
    };
    if (p.allowed) {
      const token = `mv-${++this.seq}-${Math.floor(now)}`;
      this.tokens.set(token, { key: `${to}|${port}`, until: now + CONFIRM_S });
      p.confirm = token;
      p.expires_at = now + CONFIRM_S;
    }
    return p;
  }

  handleController(method: string, parts: string[], q: URLSearchParams, body: unknown, now: number): Reply {
    const p = parts.slice(1);
    if (method === 'GET' && !p.length) return { status: 200, body: this.controller(now) };
    if (p[0] === 'move' && p.length === 1 && method === 'GET') {
      const got = this.movePlan(q.get('to') ?? '', Number(q.get('port') || 8080), now);
      return 'status' in got ? got : { status: 200, body: got };
    }
    if (p[0] === 'move' && p.length === 1 && method === 'POST') {
      const b = (body ?? {}) as { to?: string; confirm?: string; port?: number; password?: string };
      const port = Number(b.port || 8080);
      const got = this.movePlan(String(b.to ?? ''), port, now);
      if ('status' in got) return got;
      if (got.confirm) this.tokens.delete(got.confirm);
      if (!got.allowed) return bad(409, 'conflict', got.blocked!);
      if (!b.confirm) return bad(428, 'confirm_required', 'a move needs its plan’s confirm: GET /v1/fleet/controller/move?to=… first');
      const tok = this.tokens.get(b.confirm);
      this.tokens.delete(b.confirm);
      if (!tok || tok.key !== `${b.to}|${port}` || tok.until <= now) return bad(409, 'confirm_invalid', 'that confirm is spent, too old or another move’s: read the plan again');
      if (got.needs_password && !(typeof b.password === 'string' && b.password.length >= MIN_PASSWORD && !/\s/.test(b.password))) return bad(409, 'password_needed', `the new controller listens on the LAN: give it an admin password (${MIN_PASSWORD} or more characters, no spaces)`);
      const alias = this.fleet.nodes(now).find((n) => n.id === b.to)!.alias;
      this.moveRun = {
        start: now, moves: true,
        labels: [['check', `Check ${alias}`], ['export', 'Export the fleet'], ['start', `Start the controller on ${alias}`], ['repin', 'Hand the nodes to the new controller'], ['verify', 'The nodes answer the new controller'], ['done', `This controller points to ${got.api_url}`]],
        job: { id: `m-mock${++this.seq}`, kind: 'move', direction: 'push', by: 'admin', started_at: now, error: null, api_url: got.api_url, nodes: got.nodes, to: b.to, alias, port },
      };
      return { status: 200, body: this.moveJob(now) };
    }
    if (p[0] === 'take' && p.length === 1 && method === 'POST') {
      const b = (body ?? {}) as { url?: string; password?: string };
      if (typeof b.url !== 'string' || !/^https?:\/\//.test(b.url)) return bad(400, 'bad_request', 'the body is {url, password}: the controller that runs the fleet now, and its admin password');
      if (this.fleet.nodes(now).some((n) => n.pairing.state !== 'local')) return bad(409, 'not_empty', 'this controller has a fleet of its own: it takes another only while it has none');
      this.moveRun = {
        start: now, moves: false,
        labels: [['check', `Reach the controller at ${b.url}`], ['export', 'Its export, sealed'], ['import', 'Import here'], ['repin', 'It hands its nodes here'], ['verify', 'The nodes answer this controller']],
        job: { id: `m-mock${++this.seq}`, kind: 'move', direction: 'pull', by: 'admin', started_at: now, error: null, api_url: this.fleet.hostInfo().api_url, nodes: [], url: b.url, from: b.url },
      };
      return { status: 200, body: this.moveJob(now) };
    }
    if (p[0] === 'handover') return bad(409, 'conflict', 'the mock controller hands over through its page only');
    return bad(404, 'not_found', `no route ${method} /v1/fleet/${parts.join('/')}`);
  }
}

const DETAIL: Record<string, (r: MoveRun) => string> = {
  check: (r) => `${r.job.alias ?? r.job.url} answers; TandemNode 0.15.0`,
  export: () => '212 KiB, 2 stored keys',
  start: (r) => `tandemapp.service on port ${r.job.port ?? 8080}, certificate sha256 9a9a9a9a9a9a9a9a`,
  repin: (r) => `${r.job.nodes.filter((x) => x.how !== 'stays').length} nodes handed over`,
  verify: (r) => `${r.job.nodes.filter((x) => x.how !== 'stays').length} of ${r.job.nodes.filter((x) => x.how !== 'stays').length} up at the new controller; this one refused`,
  done: (r) => `the API is ${r.job.api_url}/v1`,
  import: () => '4 nodes, 3 backends, 2 stored keys',
};
