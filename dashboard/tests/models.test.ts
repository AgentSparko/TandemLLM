// VIS-253 (tests for VIS-252): Fleet → Models. The pure parts of the three pages (src/lib/models.ts):
// a card's actions and words, the scope, the placement bar, the type and members to offer, the body
// that creates a deployment, the weights matrix's cells; the mock's Setup Guide relay and its plans
// (mock/setup-relay.ts) as the controller answers them (proxy/fleetsetup.py), every answer valid
// against fleet-v1; and the Setup Guide of another Spark (src/setup/api.ts): its routes through the
// relay, a write only after the controller's plan and the user's yes, with the plan's confirm.

import { EventEmitter } from 'node:events';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MockFleet } from '../mock/fleet.ts';
import { createMockMiddleware } from '../mock/server.ts';
import { bound, writeOf } from '../mock/setup-relay.ts';
import type { FleetDeployment, Placement, PlacementNode, SetupConfirm, WeightsCopyJob, WeightsMatrix } from '../src/api/fleet.ts';
import { copyWords, countWords, depActions, depCounts, managedWords, membersFor, newDeployment, pickType, placeBar, scoped, shortId, sizeWords, weightsCell } from '../src/lib/models.ts';
import { isCancelled, setConfirmer, setSetupNode, setupApi, setupBase } from '../src/setup/api.ts';

const FDIR = join(__dirname, '..', '..', 'docs', 'contract', 'fleet-v1');
const ajv = new Ajv2020({ allErrors: true, strict: true });
const schema: Record<string, ReturnType<typeof ajv.compile>> = {};
for (const f of readdirSync(FDIR)) if (f.endsWith('.schema.json')) schema[f.replace('.schema.json', '')] = ajv.compile(JSON.parse(readFileSync(join(FDIR, f), 'utf8')));
function valid<T>(name: string, data: unknown): T {
  if (!schema[name](data)) throw new Error(`${name}: ${ajv.errorsText(schema[name].errors, { separator: '\n' })}`);
  return data as T;
}

const T0 = Date.parse('2026-10-28T09:00:00Z') / 1000;
const dep = (o: Partial<FleetDeployment>): FleetDeployment => ({
  id: 'd',
  name: 'D',
  model: 'qwen3.8-27b',
  pack: 'exl3-4.0',
  type: 'single',
  members: ['a'],
  api_id: 'd',
  ctx: 32768,
  status: 'serving',
  status_text: 'serving',
  record: true,
  state: 'serving',
  managed: true,
  member_states: [{ node: 'a', alias: 'A', engine: 'e', backend: 'b', model: 'm', managed: true, state: 'serving', text: 'serving', routable: true }],
  job: null,
  ...o,
});

describe('a deployment card', () => {
  it('a record offers stop, move, edit and delete; stopped, start; a derived Single only Record', () => {
    expect(depActions(dep({})).map((a) => [a.id, a.why])).toEqual([
      ['stop', null],
      ['move', null],
      ['edit', null],
      ['delete', null],
    ]);
    expect(depActions(dep({ state: 'stopped', status: 'stopped', member_states: [] })).map((a) => a.id)).toEqual(['start', 'move', 'edit', 'delete']);
    expect(depActions(dep({ record: false })).map((a) => a.id)).toEqual(['record']);
    expect(managedWords(dep({ record: false })).label).toBe('derived');
    expect(managedWords(dep({ managed: false })).label).toBe('unmanaged');
    expect(managedWords(dep({})).text).toContain('TandemNode starts and stops');
  });
  it('a Split does not start or move: not built yet (ENG-307); a running job holds every action', () => {
    const split = depActions(dep({ type: 'split', state: 'stopped', status: 'stopped', member_states: [] }));
    expect(split.find((a) => a.id === 'start')!.why).toContain('ENG-307');
    expect(split.find((a) => a.id === 'move')!.why).toBe('a Split moves with ENG-307');
    const busy = depActions(dep({ job: { id: 'j', action: 'move', state: 'running', started_at: T0, ended_at: null, text: '', error: null } }));
    expect(busy.every((a) => a.why === 'a job runs: wait for it')).toBe(true);
  });
  it('the scope: a Spark keeps its deployments, a deployment itself; the counts in words', () => {
    const deps = [dep({ id: 'x', members: ['a', 'b'], type: 'replicas' }), dep({ id: 'y', members: ['c'], status: 'stopped' }), dep({ id: 'z', members: ['b'], status: 'degraded' })];
    expect(scoped(deps, { kind: 'node', id: 'b' }).map((d) => d.id)).toEqual(['x', 'z']);
    expect(scoped(deps, { kind: 'deployment', id: 'y' }).map((d) => d.id)).toEqual(['y']);
    expect(scoped(deps, { kind: 'all' })).toHaveLength(3);
    const c = depCounts(deps);
    expect([c.total, c.serving, c.trouble, c.stopped, c.routable, c.members]).toEqual([3, 1, 1, 1, 3, 3]);
    expect(countWords(c)).toBe('3 deployments behind one API: 1 serving, 1 not all serving, 1 stopped.');
    expect(countWords(depCounts([]))).toBe('No deployment yet.');
  });
});

describe('Serve a model: the placement plan', () => {
  const node = (o: Partial<PlacementNode>): PlacementNode => ({
    node: 'a',
    alias: 'A',
    status: 'up',
    version: '0.15.0',
    transport: 'lan',
    fits: true,
    need_gib: 40,
    free_gib: 80,
    budget_gib: 106.6,
    total_gib: 50,
    serving: false,
    source: 'engine',
    reason: null,
    parts: [
      { id: 'os', label: 'OS', gib: 10 },
      { id: 'weights', label: 'Weights', gib: 20 },
      { id: 'kv', label: 'KV', gib: 8 },
      { id: 'engine', label: 'Engine', gib: 12 },
    ],
    ...o,
  });
  it('an idle Spark: the OS and the new parts against its budget', () => {
    const b = placeBar(node({}))!;
    expect(b.segs.map((s) => [s.id, s.kind])).toEqual([
      ['os', 'base'],
      ['weights', 'new'],
      ['kv', 'new'],
      ['engine', 'new'],
    ]);
    expect(b.segs[1].f).toBeCloseTo(20 / 106.6, 4);
    expect(b.budgetAt).toBe(1);
    expect(b.over).toBe(false);
    expect(b.text).toBe('needs 40.0 GiB · 80.0 free · budget 106.6');
  });
  it('a Spark that serves something: what runs now beside the new parts; refused when it does not fit', () => {
    const b = placeBar(node({ serving: true, free_gib: 30, fits: false, reason: 'needs 40 GiB, A has 15 free' }))!;
    expect(b.segs[0]).toMatchObject({ id: 'now', kind: 'now', gib: 76.6 });
    expect(b.segs.some((s) => s.id === 'os')).toBe(false);
    expect(b.over).toBe(true);
    expect(b.budgetAt).toBeLessThan(1);
    expect(placeBar(node({ need_gib: null, parts: [], reason: 'A is off' }))).toBeNull();
  });
  it('the type offered, its members from the picks, and the body that creates it (fleet4 in the mock)', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const r = m.handle('POST', '/v1/fleet/placement', new URLSearchParams(), { model: 'qwen3.8-27b', pack: 'exl3-4.0', ctx: 32768, slots: 1 }, T0 * 1000);
    const p = valid<Placement>('placement', r.body);
    const t = pickType(p, null);
    expect(t).toBe(p.types.find((x) => x.allowed && x.type !== 'role')!.type);
    const split = p.types.find((x) => x.type === 'split')!;
    expect(split.built).toBe(false);
    // Desk has no fabric: a Split picked over Desk and R-1 is refused in words
    const refused = valid<Placement>('placement', m.handle('POST', '/v1/fleet/placement', new URLSearchParams(), { model: 'qwen3.8-27b', pack: 'exl3-4.0', ctx: 32768, nodes: ['desk', 'r1'] }, T0 * 1000).body);
    expect(refused.types.find((x) => x.type === 'split')!.reason).toBe('no fabric link between Desk and R-1');
    const fit = p.nodes.filter((n) => n.fits).map((n) => n.node);
    expect(membersFor(p, 'single', [])).toEqual([fit[0]]);
    if (p.types.find((x) => x.type === 'replicas')!.allowed) expect(membersFor(p, 'replicas', [fit[1], fit[0]])).toEqual([fit[1], fit[0]]);
    const body = valid('deployment-new', newDeployment(p, 'single', [fit[0]], { name: ' Q ', api_id: 'q27' }));
    expect(body).toEqual({ model: 'qwen3.8-27b', pack: 'exl3-4.0', type: 'single', members: [fit[0]], ctx: 32768, name: 'Q', api_id: 'q27' });
    const fab = valid<Placement>('placement', m.handle('POST', '/v1/fleet/placement', new URLSearchParams(), { model: 'qwen3.8-27b', pack: 'exl3-4.0', ctx: 8192, nodes: ['r1', 'r2'] }, T0 * 1000).body);
    if (fab.types.find((x) => x.type === 'split')!.allowed) {
      const sb = valid<{ state: string; stages: unknown[] }>('deployment-new', newDeployment(fab, 'split', ['r1', 'r2']));
      expect(sb.state).toBe('stopped');
      expect(sb.stages).toHaveLength(2);
    }
  });
});

describe('the weights matrix', () => {
  const copy = (o: Partial<WeightsCopyJob>): WeightsCopyJob => ({
    id: 'c1', kind: 'weights', state: 'running', from: 'a', to: 'b', from_alias: 'A', to_alias: 'B', profile: 'p', via: 'relay', via_why: 'B is reached through an SSH tunnel', by: 'admin',
    started_at: T0, ended_at: null, bytes_total: 4 * 2 ** 30, bytes_done: 2 ** 30, files_total: 4, files_done: 1, rate_gbps: 2.4, content_id: { source: 'sha256:aa', expected: 'sha256:aa', copy: null }, verified: false, log: [], error: null, ...o,
  });
  const m: WeightsMatrix = {
    contract_version: '1.0',
    nodes: [],
    packs: [{ profile: 'p', model: 'm', same: true, nodes: [{ node: 'a', present: true, source: 'setup', dir: '/x', bytes: 4 * 2 ** 30, gib: 4, content_id: 'sha256:' + 'ab'.repeat(32), copy: null }, { node: 'b', present: false, source: 'store', dir: null, bytes: null, gib: null, content_id: null, copy: 'c1' }] }],
    copies: [copy({})],
  };
  it('each cell: in use, copying with its share, missing with where it may come from, off', () => {
    const pk = m.packs[0];
    expect(weightsCell(m, pk, 'a', [dep({ members: ['a'], pack: 'p' })]).kind).toBe('in-use');
    expect(weightsCell(m, pk, 'a', []).kind).toBe('present');
    const c = weightsCell(m, pk, 'b', []);
    expect([c.kind, c.pct]).toEqual(['copying', 0.25]);
    expect(weightsCell({ ...m, copies: [] }, { ...pk, nodes: [pk.nodes[0]] }, 'c', []).from).toEqual(['a']);
    expect(weightsCell({ ...m, copies: [] }, { ...pk, nodes: [pk.nodes[0]] }, 'c', [], { a: 'off' }).from).toEqual([]);
    expect(weightsCell(m, pk, 'c', [], { c: 'off' }).kind).toBe('off');
  });
  it('sizes, content ids and a copy in words', () => {
    expect(sizeWords(23.3 * 2 ** 30)).toBe('23.3 GiB');
    expect(sizeWords(4.5 * 2 ** 20)).toBe('5 MB');
    expect(sizeWords(null)).toBe('—');
    expect(shortId('sha256:' + 'ab'.repeat(32))).toBe('sha256:abababab…abab');
    expect(copyWords(copy({}))).toBe('copying 25 % · 1.0 GiB of 4.0 GiB · 2.4 Gb/s · relayed through the controller');
    expect(copyWords(copy({ state: 'done', verified: true, via: 'direct' }))).toBe('copied and verified by content id · direct, node to node');
    expect(copyWords(copy({ state: 'failed', error: { type: 'x', message: 'sha256 mismatch' } }))).toBe('failed: sha256 mismatch');
  });
  it('the mock fleet starts with a copy running R-1 → Desk through the controller', () => {
    const f = new MockFleet({ scenario: 'fleet4', now: T0 });
    const w = valid<WeightsMatrix>('weights', f.handle('GET', '/v1/fleet/weights', new URLSearchParams(), null, T0 * 1000).body);
    const run = w.copies.find((c) => c.state === 'running')!;
    expect([run.from, run.to, run.via]).toEqual(['r1', 'desk', 'relay']);
    const pack = w.packs.find((p) => p.profile === run.profile)!;
    expect(weightsCell(w, pack, 'desk', []).kind).toBe('copying');
  });
});

describe('the Setup Guide relay in the mock', () => {
  it('a write is known by its route, a dry run is a read; the confirm is bound to the choice', () => {
    expect(writeOf('POST', ['apply'], {})).toBe('apply');
    expect(writeOf('POST', ['apply'], { dry_run: true })).toBeNull();
    expect(writeOf('POST', ['jobs', 'j1', 'retry'], {})).toBe('retry');
    expect(writeOf('POST', ['estimate'], {})).toBeNull();
    expect(writeOf('GET', ['apply'], {})).toBeNull();
    expect(bound('apply', { selection: { model: 'm', profile: 'p' } })).toBe('m/p');
    expect(bound('apply', { model: 'm', profile: 'p' })).toBe('m/p');
    expect(bound('remove', { profiles: 'b,a' })).toBe(bound('remove', { profiles: ['a', 'b'] }));
    expect(bound('reset', { level: 2 })).toBe('2');
  });
  it('the plans say what a write does on that Spark, valid against fleet-v1', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const plan = (route: string, qs = '') => valid<SetupConfirm>('setup-confirm', m.handle('GET', `/v1/fleet/nodes/desk/setup-confirm/${route}`, new URLSearchParams(qs), null, T0 * 1000).body);
    const a = plan('apply', 'model=qwen3.8-27b&profile=exl3-6.0');
    expect(a.title).toBe('Apply the Setup Guide on Desk');
    expect(a.impact[0]).toBe("Desk's engine restarts serving qwen3.8-27b with exl3-6.0: what it serves is away while it loads, a minute or more");
    expect(a.impact).toContain('Qwen3.8-27B: new requests go to R-3, another member');
    expect(a.allowed && !!a.confirm).toBe(true);
    expect(plan('assistant/load').title).toBe('Wake Wren on Desk');
    // a Split needs every stage: R-1's restart takes DeepSeek away, R-2 does not stand in
    const r1 = valid<SetupConfirm>('setup-confirm', m.handle('GET', '/v1/fleet/nodes/r1/setup-confirm/apply', new URLSearchParams('model=m&profile=p'), null, T0 * 1000).body);
    expect(r1.impact).toContain("DeepSeek-V4.1-Flash does not answer until R-1's engine is back");
    expect(plan('remove').blocked).toBe('name the profiles to remove (?profiles=a,b)');
    expect(m.handle('GET', '/v1/fleet/nodes/desk/setup-confirm/estimate', new URLSearchParams(), null, T0 * 1000).status).toBe(404);
    const down = new MockFleet({ scenario: 'node-down', now: T0 });
    const off = valid<SetupConfirm>('setup-confirm', down.handle('GET', '/v1/fleet/nodes/r3/setup-confirm/reset', new URLSearchParams('level=2'), null, (T0 + 600) * 1000).body);
    if (!off.allowed) expect(off.blocked).toMatch(/^R-3 does not answer/);
  });

  const mw = createMockMiddleware({ live: false });
  function call(method: string, url: string, body?: unknown): Promise<{ status: number; body: unknown }> {
    return new Promise((resolve) => {
      const req = Object.assign(new EventEmitter(), { method, url, headers: { 'content-type': 'application/json' } as Record<string, string>, socket: { destroy() {} } });
      let status = 200;
      const res = {
        headersSent: false,
        statusCode: 200,
        setHeader: () => undefined,
        appendHeader: () => undefined,
        writeHead(s: number) {
          status = s;
          this.statusCode = s;
        },
        write: () => true,
        end: (s?: string) => resolve({ status, body: s ? JSON.parse(s) : null }),
      };
      mw(req as never, res as never, () => resolve({ status: 404, body: null }));
      queueMicrotask(() => {
        if (body !== undefined) req.emit('data', JSON.stringify(body));
        req.emit('end');
      });
    });
  }

  it('each Spark answers its own guide; a write without the confirm is 428, with it once', async () => {
    await call('POST', '/__mock/fleet', { scenario: 'fleet4', reset: true });
    const models = await call('GET', '/v1/fleet/nodes/r1/setup/models');
    expect(models.status).toBe(200);
    expect((models.body as { models: unknown[] }).models.length).toBeGreaterThan(0);
    const sel = { selection: { model: 'qwen3.8-27b', profile: 'exl3-6.0' } };
    expect((await call('POST', '/v1/fleet/nodes/r1/setup/apply', { ...sel, dry_run: true })).status).toBe(200);
    const no = await call('POST', '/v1/fleet/nodes/r1/setup/assistant/load', {});
    expect(no.status).toBe(428);
    valid('error', no.body);
    const p = (await call('GET', '/v1/fleet/nodes/r1/setup-confirm/assistant/load')).body as SetupConfirm;
    expect((await call('POST', `/v1/fleet/nodes/desk/setup/assistant/load?confirm=${p.confirm}`, {})).status).toBe(409);
    const p2 = (await call('GET', '/v1/fleet/nodes/r1/setup-confirm/assistant/load')).body as SetupConfirm;
    const ok = await call('POST', `/v1/fleet/nodes/r1/setup/assistant/load?confirm=${p2.confirm}`, {});
    expect([ok.status, (ok.body as { state: string }).state]).toEqual([200, 'loading']);
    expect((await call('POST', `/v1/fleet/nodes/r1/setup/assistant/load?confirm=${p2.confirm}`, {})).status).toBe(409);
    expect((await call('GET', '/v1/fleet/nodes/nope/setup/device')).status).toBe(404);
  });
});

describe('the Setup Guide of another Spark (setup/api.ts)', () => {
  const calls: { url: string; method: string }[] = [];
  const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  afterEach(() => {
    setSetupNode(null);
    setConfirmer(null);
    calls.length = 0;
    vi.unstubAllGlobals();
  });
  const plan = (allowed = true): SetupConfirm => ({ contract_version: '1.0', node: 'r 1', route: 'apply', title: 'Apply the Setup Guide on R-1', allowed, blocked: allowed ? null : 'R-1 does not answer', impact: [], keeps: [], confirm: allowed ? 'tok/1' : null, expires_at: null });
  function stub(allowed = true) {
    vi.stubGlobal('fetch', async (url: string, init: RequestInit = {}) => {
      calls.push({ url, method: init.method ?? 'GET' });
      if (url.includes('/setup-confirm/')) return reply(200, plan(allowed));
      return reply(202, { contract_version: '1.0', job: { id: 'j1' } });
    });
  }
  const sel = { model: 'qwen3.8-27b', profile: 'exl3-4.0' } as never;

  it("this server's own guide asks /v1/setup and sends a write at once", async () => {
    stub();
    expect(setupBase()).toBe('/v1/setup');
    await setupApi.apply({ selection: sel });
    expect(calls).toEqual([{ url: '/v1/setup/apply', method: 'POST' }]);
  });
  it("another Spark's goes through the relay; a write reads the plan, asks, then sends the confirm", async () => {
    stub();
    setSetupNode({ id: 'r 1', alias: 'R-1' });
    const asked: SetupConfirm[] = [];
    setConfirmer(async (p) => (asked.push(p), true));
    expect(setupBase()).toBe('/v1/fleet/nodes/r%201/setup');
    expect(setupApi.jobUrl('j 1')).toBe('/v1/fleet/nodes/r%201/setup/jobs/j%201');
    await setupApi.apply({ selection: sel });
    expect(calls.map((c) => c.url)).toEqual(['/v1/fleet/nodes/r%201/setup-confirm/apply?model=qwen3.8-27b&profile=exl3-4.0', '/v1/fleet/nodes/r%201/setup/apply?confirm=tok%2F1']);
    expect(asked[0].title).toBe('Apply the Setup Guide on R-1');
    calls.length = 0;
    await setupApi.assistant.unload();
    expect(calls.map((c) => c.url)).toEqual(['/v1/fleet/nodes/r%201/setup/assistant/unload']);
  });
  it('a no sends nothing (cancelled); a plan that may not run is refused in its words', async () => {
    stub();
    setSetupNode({ id: 'r 1', alias: 'R-1' });
    setConfirmer(async () => false);
    const e = await setupApi.remove(['exl3-4.0']).catch((x) => x);
    expect(isCancelled(e)).toBe(true);
    expect(calls.map((c) => c.url)).toEqual(['/v1/fleet/nodes/r%201/setup-confirm/remove?profiles=exl3-4.0']);
    stub(false);
    setConfirmer(async () => false);
    const b = await setupApi.reset({ level: 2, confirm: true } as never).catch((x) => x);
    expect([b.type, b.status, b.message]).toEqual(['blocked', 409, 'R-1 does not answer']);
  });
});
