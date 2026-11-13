// OPS-235: the fleet update's planner (lib/fleet-update.ts: the order, Split refusal, Replicas one at a
// time, the mixed-version state), the mock's update and move jobs on the clock, and App settings'
// controller-host logic (lib/controller-host.ts). Every mock answer validates against fleet-v1.

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { describe, expect, it } from 'vitest';
import { MockFleet } from '../mock/fleet.ts';
import { DRAIN_S, MOVE_STEP_S, REJOIN_S, UPDATE_S } from '../mock/fleetops.ts';
import type { ControllerView, FleetNode, MoveJob, MovePlan, UpdatePlan, UpdateView } from '../src/api/fleet.ts';
import { apiOf, followUrl, hostChoices, landing, moveFormError, moveWords, newPassword, planGroups, takeFormError } from '../src/lib/controller-host.ts';
import { jobProgress, mixedTitle, mixedVersions, needsUpdate, normVersion, order, releaseOf, type PlanNode } from '../src/lib/fleet-update.ts';

const FDIR = join(__dirname, '..', '..', 'contracts', 'fleet-v1');
const ajv = new Ajv2020({ allErrors: true, strict: true });
const schema: Record<string, ReturnType<typeof ajv.compile>> = {};
for (const f of readdirSync(FDIR)) if (f.endsWith('.schema.json')) schema[f.replace('.schema.json', '')] = ajv.compile(JSON.parse(readFileSync(join(FDIR, f), 'utf8')));
function valid<T>(name: string, data: T): T {
  if (!schema[name](data)) throw new Error(`${name}: ${ajv.errorsText(schema[name].errors, { separator: '\n' })}`);
  return data;
}

const T0 = Date.parse('2026-10-28T09:00:00Z');
const PW = 'move-fixture-password-0123456789ab';
const q = (s = '') => new URLSearchParams(s);
const n = (id: string, alias: string, o: Partial<PlanNode> = {}): PlanNode => ({ id, alias, version: '0.15.0', status: 'up', updatable: true, why: null, ...o });
const DEPS = [
  { id: 'qwen', name: 'Qwen3.8-27B', type: 'replicas' as const, members: ['g', 'r3'] },
  { id: 'wren', name: 'Wren', type: 'single' as const, members: ['g'] },
  { id: 'ds', name: 'DeepSeek', type: 'split' as const, members: ['r1', 'r2'] },
];
const NODES = [n('g', 'Gold'), n('r3', 'R-3'), n('r1', 'R-1'), n('r2', 'R-2'), n('i', 'Idle'), n('c', 'Current', { version: '0.15.1' })];

describe('the planner', () => {
  it('orders idle, Replicas one at a time, Single, then each Split together', () => {
    const o = order(NODES, DEPS, '0.15.1');
    expect(o.blocked).toBeNull();
    expect(o.steps.map((s) => [s.n, s.kind, s.nodes])).toEqual([
      [1, 'idle', ['i']],
      [2, 'replica', ['r3']],
      [3, 'single', ['g']],
      [4, 'split', ['r1', 'r2']],
    ]);
    expect(o.steps.filter((s) => s.kind === 'replica').every((s) => s.nodes.length === 1)).toBe(true);
    expect(o.steps[0].drain).toBe(false);
    expect(o.steps[1].downtime).toBeNull();
    expect(o.steps[2].downtime).toBe('Wren not served while Gold updates, unless its route has a fallback');
    expect(o.steps[3].downtime).toBe('DeepSeek is down while its members update together');
    expect(o.current).toEqual(['Current']);
  });

  it('refuses a Split with a member that cannot be updated, and blocks a selection with one member', () => {
    const off = NODES.map((x) => (x.id === 'r2' ? { ...x, status: 'off' as const, updatable: false, why: 'does not answer' } : x));
    const o = order(off, DEPS, '0.15.1');
    expect(o.steps.some((s) => s.nodes.includes('r1') || s.nodes.includes('r2'))).toBe(false);
    expect(o.refused).toHaveLength(1);
    expect(o.refused[0].why).toContain('a Split never updates partly');
    expect(o.refused[0].why).toContain('R-2 cannot be updated now (does not answer)');
    const part = order(NODES, DEPS, '0.15.1', ['r1', 'g']);
    expect(part.blocked).toBe('DeepSeek runs Split over R-1, R-2: its members update together, so R-2 must be in the update too');
    expect(part.steps.some((s) => s.nodes.includes('r1'))).toBe(false);
    expect(order(NODES, DEPS, '0.15.1', ['r1', 'r2']).steps.map((s) => s.nodes)).toEqual([['r1', 'r2']]);
    expect(order(NODES, DEPS, '0.15.1', ['nope']).blocked).toBe('no node nope in this fleet');
  });

  it('a Replicas member whose partner is down is a Single for the update; off nodes are skipped', () => {
    const down = NODES.map((x) => (x.id === 'r3' ? { ...x, status: 'off' as const, updatable: false, why: 'off' } : x));
    const o = order(down, DEPS, '0.15.1');
    const g = o.steps.find((s) => s.nodes[0] === 'g')!;
    expect(g.kind).toBe('single');
    expect(g.downtime).toContain('Qwen3.8-27B');
    expect(o.skipped).toEqual([{ node: 'r3', why: 'off' }]);
  });

  it('versions: the whole version for the update, the release for the alert', () => {
    expect(normVersion('0.15.0-test')).toBe('0.15.0.dev0');
    expect(normVersion('0.15.1-rc2+Fix')).toBe('0.15.1rc2+fix');
    expect(needsUpdate('0.15.0', '0.15.0+test')).toBe(true);
    expect(needsUpdate('0.15.0-test', '0.15.0.dev0')).toBe(false);
    expect(releaseOf('0.15.0+test')).toBe('0.15.0');
    const nodes = (vs: string[]) => vs.map((v, i) => ({ alias: `N${i}`, version: v, status: 'up' as FleetNode['status'] }));
    expect(mixedVersions(nodes(['0.15.0', '0.15.0+test']))).toBeNull();
    const m = mixedVersions(nodes(['0.15.0', '0.15.1', '0.15.0']))!;
    expect(mixedTitle(m)).toBe('Mixed versions: 0.15.0 on N0, N2 · 0.15.1 on N1');
    expect(mixedVersions([...nodes(['0.15.0']), { alias: 'X', version: '0.16.0', status: 'off' }])).toBeNull();
  });
});

describe('the mock update', () => {
  const at = (s: number) => T0 + s * 1000;
  const get = <T>(m: MockFleet, path: string, s: number, qs = '') => {
    const r = m.handle('GET', path, q(qs), null, at(s));
    expect(r.status, path).toBe(200);
    return r.body as T;
  };

  it('Gherkin "Rolling update": one member drains and updates at a time, the model stays served', () => {
    const m = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 / 1000 });
    const p = valid('update-plan', get<UpdatePlan>(m, '/v1/fleet/update/plan', 0));
    expect(p.target).toBe('0.15.1');
    expect(p.steps.map((s) => [s.kind, s.nodes])).toEqual([
      ['replica', ['r3']],
      ['single', ['desk']],
      ['split', ['r1', 'r2']],
    ]);
    expect(p.steps[0].words).toBe('R-3: drain, update, rejoin; Qwen3.8-27B stays served by the other member');
    expect(m.handle('POST', '/v1/fleet/update', q(), {}, at(0)).status).toBe(428);
    expect(m.handle('POST', '/v1/fleet/update', q(), { confirm: 'nope' }, at(0)).status).toBe(409);
    const r = m.handle('POST', '/v1/fleet/update', q(), { confirm: p.confirm }, at(1));
    expect(r.status).toBe(200);
    valid('update', r.body);
    expect(m.handle('POST', '/v1/fleet/update', q(), { confirm: p.confirm }, at(2)).status).toBe(409);
    const step = DRAIN_S + UPDATE_S + REJOIN_S;
    for (let s = 1; s <= 1 + 3 * step + 2; s++) {
      const nodes = get<{ nodes: FleetNode[] }>(m, '/v1/fleet/nodes', s).nodes;
      const deps = get<{ deployments: { id: string; status: string }[] }>(m, '/v1/fleet/deployments', s).deployments;
      const qwen = ['desk', 'r3'].filter((id) => nodes.find((x) => x.id === id)!.status === 'draining');
      expect(qwen.length, `second ${s}`).toBeLessThanOrEqual(1);
      expect(deps.find((d) => d.id === 'qwen27')!.status).not.toBe('down');
      valid('update', get<UpdateView>(m, '/v1/fleet/update', s));
    }
    // R-3 drains first, then Desk; the Split's members together
    const v1 = get<UpdateView>(m, '/v1/fleet/update', 2);
    expect(v1.job!.nodes.r3.phase).toBe('drain');
    expect(v1.job!.nodes.desk.phase).toBe('todo');
    const v2 = get<UpdateView>(m, '/v1/fleet/update', 1 + step + DRAIN_S + 1);
    expect(v2.job!.nodes.r3.phase).toBe('done');
    expect(v2.job!.nodes.desk.phase).toBe('update');
    const v3 = get<UpdateView>(m, '/v1/fleet/update', 1 + 2 * step + DRAIN_S + 1);
    expect([v3.job!.nodes.r1.phase, v3.job!.nodes.r2.phase]).toEqual(['update', 'update']);
    expect(jobProgress(v3.job!).done).toBe(2);
    const end = get<UpdateView>(m, '/v1/fleet/update', 1 + 3 * step + 1);
    expect(end.job!.state).toBe('done');
    expect(get<{ nodes: FleetNode[] }>(m, '/v1/fleet/nodes', 1 + 3 * step + 1).nodes.map((x) => x.version)).toEqual(['0.15.1', '0.15.1', '0.15.1', '0.15.1']);
    expect(get<UpdatePlan>(m, '/v1/fleet/update/plan', 1 + 3 * step + 2).blocked).toBe('every node runs 0.15.1 already');
  });

  it('Split refuses a partial update (409 partial_split); stop ends after the step that runs', () => {
    const m = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 / 1000 });
    const p = get<UpdatePlan>(m, '/v1/fleet/update/plan', 0, 'nodes=r1,desk');
    expect(p.allowed).toBe(false);
    expect(p.blocked).toContain('R-2 must be in the update too');
    const full = get<UpdatePlan>(m, '/v1/fleet/update/plan', 0);
    const r = m.handle('POST', '/v1/fleet/update', q(), { confirm: full.confirm, nodes: ['r1'] }, at(0));
    expect(r.status).toBe(409);
    expect((r.body as { error: { type: string } }).error.type).toBe('partial_split');
    valid('error', r.body);
    const p2 = get<UpdatePlan>(m, '/v1/fleet/update/plan', 0);
    m.handle('POST', '/v1/fleet/update', q(), { confirm: p2.confirm }, at(1));
    expect(m.handle('POST', '/v1/fleet/update/stop', q(), {}, at(3)).status).toBe(200);
    const after = get<UpdateView>(m, '/v1/fleet/update', 1 + DRAIN_S + UPDATE_S + REJOIN_S + 5);
    expect(after.job!.state).toBe('stopped');
    expect(after.job!.nodes.r3.phase).toBe('done');
    expect(after.job!.nodes.desk.phase).toBe('todo');
    valid('update-job', after.job);
  });

  it('mixed versions: the alert fires for the fleet, is held while the update runs, and goes', () => {
    const m = new MockFleet({ seed: 42, scenario: 'mixed', now: T0 / 1000 });
    const alerts = (s: number) => get<{ alerts: { id: string; title: string; node: string; action: { route: string } }[] }>(m, '/v1/fleet/alerts', s).alerts;
    const a = alerts(0).find((x) => x.id === 'version-mismatch:all')!;
    expect(a.title).toBe('Mixed versions: 0.15.0 on Desk, R-1, R-2 · 0.15.1 on R-3');
    expect(a.node).toBe('all');
    expect(a.action.route).toBe('#/sparks?update=1');
    const p = get<UpdatePlan>(m, '/v1/fleet/update/plan', 1);
    expect(p.current).toEqual(['R-3']);
    m.handle('POST', '/v1/fleet/update', q(), { confirm: p.confirm }, at(1));
    expect(alerts(5).some((x) => x.id === 'version-mismatch:all')).toBe(false);
    expect(alerts(200).some((x) => x.id === 'version-mismatch:all')).toBe(false);
  });
});

describe('the controller host', () => {
  const at = (s: number) => T0 + s * 1000;

  it('fleet4 on a Mac: the sleep warning, a Spark recommended, rented ones refused; the move plan and its job', () => {
    const m = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 / 1000 });
    const v = valid('controller', m.handle('GET', '/v1/fleet/controller', q(), null, at(0)).body as ControllerView);
    expect(v.host.kind).toBe('mac');
    const c = hostChoices(v);
    expect(c[0]).toMatchObject({ id: 'here', title: 'This Mac', current: true });
    expect(c[0].warn).toContain('This Mac can sleep');
    expect(c.find((x) => x.id === 'desk')).toMatchObject({ allowed: true, recommended: true, title: 'Spark Desk' });
    expect(c.find((x) => x.id === 'r1')).toMatchObject({ allowed: false });
    expect(c.find((x) => x.id === 'r1')!.why).toContain('rented');
    const p = valid('move-plan', m.handle('GET', '/v1/fleet/controller/move', q('to=desk&port=8080'), null, at(0)).body as MovePlan);
    expect(p.api_url).toBe('http://192.0.2.40:8080');
    expect(apiOf(p.api_url)).toBe('http://192.0.2.40:8080/v1');
    expect(p.needs_password).toBe(true);
    const g = planGroups(p);
    expect(g.moved.map((x) => [x.id, x.how])).toEqual([['desk', 'release']]);
    expect(g.stays.map((x) => x.id)).toEqual(['r1', 'r2', 'r3']);
    expect(moveFormError(p, '8080', 'short-password-of-17')).toContain('32 or more');
    expect(newPassword()).toMatch(/^[A-Za-z0-9]{32}$/);
    expect(moveFormError(p, '80', PW)).toContain('1024');
    expect(moveFormError(p, '8080', PW)).toBeNull();
    expect(m.handle('POST', '/v1/fleet/controller/move', q(), { to: 'desk', port: 8080, confirm: p.confirm }, at(1)).status).toBe(409);
    const p2 = m.handle('GET', '/v1/fleet/controller/move', q('to=desk'), null, at(1)).body as MovePlan;
    const r = m.handle('POST', '/v1/fleet/controller/move', q(), { to: 'desk', port: 8080, confirm: p2.confirm, password: PW }, at(1));
    expect(r.status).toBe(200);
    const job = valid('move-job', r.body as MoveJob);
    expect(moveWords(job)).toBe('Check Desk…');
    const done = valid('controller', m.handle('GET', '/v1/fleet/controller', q(), null, at(1 + 6 * MOVE_STEP_S)).body as ControllerView);
    expect(done.job!.state).toBe('done');
    expect(moveWords(done.job!)).toBe('Moved. The API is http://192.0.2.40:8080/v1.');
    expect(done.moved_to!.api_url).toBe('http://192.0.2.40:8080');
    expect(followUrl(done.moved_to!, '#/app')).toBe('http://192.0.2.40:8080/#/app');
    expect(m.handle('GET', '/v1/fleet/controller/move', q('to=desk'), null, at(30)).body).toMatchObject({ allowed: false, blocked: 'this controller moved already: open the new one' });
  });

  it('Gherkin "Install on a Mac": the first open with no Spark lands on Get started; a route given is kept', () => {
    const e = new MockFleet({ seed: 42, scenario: 'empty', now: T0 / 1000 });
    const nodes = (e.handle('GET', '/v1/fleet/nodes', q(), null, at(0)).body as { nodes: FleetNode[] }).nodes;
    expect(nodes).toEqual([]);
    expect(landing('', nodes)).toBe('start');
    expect(landing('#/', nodes)).toBe('start');
    expect(landing('#/usage', nodes)).toBeNull();
    expect(landing('', [{ id: 'desk' }])).toBeNull();
    const v = valid('controller', e.handle('GET', '/v1/fleet/controller', q(), null, at(0)).body as ControllerView);
    expect(v.host.kind).toBe('mac');
    expect(v.api_url).toBe('http://127.0.0.1:8080');
  });

  it('take: refused with a fleet of its own, a job on an empty controller', () => {
    const m = new MockFleet({ seed: 42, scenario: 'fleet4', now: T0 / 1000 });
    expect(m.handle('POST', '/v1/fleet/controller/take', q(), { url: 'http://192.0.2.40:8080', password: 'x' }, at(0)).status).toBe(409);
    expect(takeFormError('gold')).not.toBeNull();
    expect(takeFormError('http://192.0.2.40:8080')).toBeNull();
    const e = new MockFleet({ seed: 42, scenario: 'empty', now: T0 / 1000 });
    const r = e.handle('POST', '/v1/fleet/controller/take', q(), { url: 'http://192.0.2.40:8080', password: 'x' }, at(0));
    expect(r.status).toBe(200);
    expect(valid('move-job', r.body as MoveJob).direction).toBe('pull');
    const v = valid('controller', e.handle('GET', '/v1/fleet/controller', q(), null, at(20)).body as ControllerView);
    expect(v.job!.state).toBe('done');
    expect(v.moved_to).toBeNull();
    expect(hostChoices(v)).toHaveLength(1);
  });
});
