// SRV-732: the deployments in the mock (mock/deploy.ts) as the controller answers them
// (proxy/deployments.py, proxy/placement.py, proxy/fleetweights.py): the records over the mock fleet's
// own deployments, the planner's refusals, the jobs and the weights copy on the clock. Every answer
// validates against fleet-v1.

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { describe, expect, it } from 'vitest';
import { COPY_GBPS, MOVE_S, START_S, STOP_S } from '../mock/deploy.ts';
import { MockFleet } from '../mock/fleet.ts';
import type { DeploymentJobView, DeploymentPlan, FleetDeployment, Placement, WeightsCopyJob, WeightsMatrix, WeightsPlan } from '../src/api/fleet.ts';

const FDIR = join(__dirname, '..', '..', 'contracts', 'fleet-v1');
const ajv = new Ajv2020({ allErrors: true, strict: true });
const schema: Record<string, ReturnType<typeof ajv.compile>> = {};
for (const f of readdirSync(FDIR)) if (f.endsWith('.schema.json')) schema[f.replace('.schema.json', '')] = ajv.compile(JSON.parse(readFileSync(join(FDIR, f), 'utf8')));
function valid<T>(name: string, data: unknown): T {
  if (!schema[name](data)) throw new Error(`${name}: ${ajv.errorsText(schema[name].errors, { separator: '\n' })}`);
  return data as T;
}

const T0 = Date.parse('2026-10-28T09:00:00Z') / 1000;
const q = (s = '') => new URLSearchParams(s);
const at = (m: MockFleet, method: string, path: string, body: unknown = null, t = T0, qs = '') => m.handle(method, path, q(qs), body, t * 1000);
const get = <T>(m: MockFleet, path: string, t = T0, qs = '') => {
  const r = at(m, 'GET', path, null, t, qs);
  expect(r.status, path).toBe(200);
  return r.body as T;
};
const dep = (m: MockFleet, id: string, t = T0) => valid<FleetDeployment>('deployment', get(m, `/v1/fleet/deployments/${id}`, t));
const plan = (m: MockFleet, id: string, action: string, t = T0, qs = '') => valid<DeploymentPlan>('deployment-plan', get(m, `/v1/fleet/deployments/${id}/actions/${action}`, t, qs));

describe('the records over the mock fleet', () => {
  it("fleet4's deployments are records with their members' states", () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const deps = valid<{ deployments: FleetDeployment[] }>('deployments', get(m, '/v1/fleet/deployments')).deployments;
    expect(deps.map((d) => [d.id, d.type, d.members])).toEqual([
      ['qwen27', 'replicas', ['desk', 'r3']],
      ['dsv41', 'split', ['r1', 'r2']],
      ['wren', 'role', ['desk']],
    ]);
    // the Split is recorded with its plan and stopped until it is started (SRV-817)
    expect(deps.map((d) => [d.id, d.state, d.status, d.record && d.managed])).toEqual([
      ['qwen27', 'serving', 'serving', true],
      ['dsv41', 'stopped', 'stopped', true],
      ['wren', 'serving', 'serving', true],
    ]);
    expect(deps[1].status_text).toBe('stopped');
    expect(deps[1].member_states!.map((x) => [x.alias, x.state, x.routable, x.stage?.role, x.backend])).toEqual([
      ['R-1', 'down', false, 'front', null],
      ['R-2', 'down', false, 'tail', 'r2-engine'],
    ]);
    const sp = deps[1].split!;
    expect([sp.phase, sp.built, sp.link.kind, sp.link.state, sp.front.alias, sp.tail.alias]).toEqual(['stopped', false, 'fabric', 'unknown', 'R-1', 'R-2']);
    expect(deps[1].stages).toEqual([sp.front, sp.tail].map((x) => ({ node: x.node, layers: x.layers, gib: x.need_gib })));
    expect(sp.front.layers).toBe(`0:${sp.cut}`);
    expect(sp.tail.layers).toBe(`${sp.cut}:${sp.layers}`);
    // the server this page talks to is Desk's engine: its members reach it through `engine`
    expect(deps[0].member_states!.map((x) => x.backend)).toEqual(['engine', 'r3-engine']);
    const q27 = deps[0];
    expect(q27.affinity).toBe('root');
    expect(q27.member_states!.map((x) => [x.alias, x.state, x.routable])).toEqual([
      ['Desk', 'serving', true],
      ['R-3', 'serving', true],
    ]);
  });

  it('a record made, changed and removed', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const r = at(m, 'POST', '/v1/fleet/deployments', { model: 'GLM-4.5-Air', type: 'single', members: ['r3'], pack: 'EXL3 4.0', api_id: 'glm-4.5-air' });
    expect(r.status).toBe(200);
    const d = valid<FleetDeployment>('deployment', r.body);
    expect([d.id, d.api_id, d.members, d.status]).toEqual(['glm-4.5-air', 'glm-4.5-air', ['r3'], 'serving']);
    expect(at(m, 'POST', '/v1/fleet/deployments', { model: 'x', type: 'single', members: ['r3'], api_id: 'glm-4.5-air' }).status).toBe(409);
    expect(valid('error', at(m, 'POST', '/v1/fleet/deployments', { model: 'x', type: 'replicas', members: ['r3'] }).body)).toBeTruthy();
    expect(at(m, 'POST', '/v1/fleet/deployments', { model: 'x', type: 'single', members: ['nope'] }).status).toBe(404);
    const p = at(m, 'PATCH', '/v1/fleet/deployments/glm-4.5-air', { name: 'GLM Air' });
    expect(valid<FleetDeployment>('deployment', p.body).name).toBe('GLM Air');
    expect(valid('deployment-deleted', at(m, 'DELETE', '/v1/fleet/deployments/glm-4.5-air').body)).toEqual({ contract_version: '1.0', deleted: 'glm-4.5-air' });
    expect(at(m, 'GET', '/v1/fleet/deployments/glm-4.5-air').status).toBe(404);
  });
});

describe('the actions and their jobs on the clock', () => {
  it('stop needs the confirm, then the record stops; start brings it back after its warm-up', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const p = plan(m, 'wren', 'stop');
    expect(p.allowed && p.destructive && p.confirm).toBeTruthy();
    expect(at(m, 'POST', '/v1/fleet/deployments/wren/actions/stop', {}).status).toBe(428);
    expect(at(m, 'POST', '/v1/fleet/deployments/wren/actions/stop', { confirm: 'made-up' }).status).toBe(409);
    const j = valid<DeploymentJobView>('deployment-job', at(m, 'POST', '/v1/fleet/deployments/wren/actions/stop', { confirm: plan(m, 'wren', 'stop').confirm }).body);
    expect(j.state).toBe('running');
    expect(dep(m, 'wren', T0 + STOP_S).status).toBe('stopped');
    expect(valid<DeploymentJobView>('deployment-job', get(m, `/v1/fleet/deployments/jobs/${j.job.id}`, T0 + STOP_S)).state).toBe('done');
    expect(plan(m, 'wren', 'stop', T0 + STOP_S).blocked).toBe('Wren is stopped already');
    const s = plan(m, 'wren', 'start', T0 + STOP_S);
    expect(s.allowed && !s.destructive && s.confirm === null).toBe(true);
    at(m, 'POST', '/v1/fleet/deployments/wren/actions/start', {}, T0 + STOP_S);
    const mid = dep(m, 'wren', T0 + STOP_S + 1);
    expect(mid.status).toBe('starting');
    expect(mid.member_states![0].state).toBe('starting');
    expect(dep(m, 'wren', T0 + STOP_S + START_S).status).toBe('serving');
  });

  it("a move keeps the API id: the new member joins halfway, the old one leaves", () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const p = plan(m, 'wren', 'move', T0, 'to=r3');
    expect(p.allowed && p.from === 'desk' && p.to === 'r3').toBe(true);
    expect(p.impact).toContain('wren stays the API id');
    at(m, 'POST', '/v1/fleet/deployments/wren/actions/move', { to: 'r3', confirm: p.confirm });
    expect(dep(m, 'wren', T0 + 1).members).toEqual(['desk']);
    const half = dep(m, 'wren', T0 + MOVE_S / 2 + 1);
    expect(half.member_states!.map((x) => [x.node, x.state])).toEqual([
      ['desk', 'leaving'],
      ['r3', 'serving'],
    ]);
    const end = dep(m, 'wren', T0 + MOVE_S);
    expect([end.members, end.api_id, end.status]).toEqual([['r3'], 'wren', 'serving']);
    expect(plan(m, 'dsv41', 'move', T0, 'to=desk').blocked).toBe("a Split's two stages move together: stop it, plan it on the new nodes (Serve a model) and start it");
    const mv = at(m, 'POST', '/v1/fleet/deployments/dsv41/actions/move', { from: 'r1', to: 'desk' });
    expect([mv.status, valid<{ error: { type: string } }>('error', mv.body).error.type]).toEqual([409, 'partial_split']);
  });

  it('VIS-273: a Split starts as a pair (the front, then the tail, then linked) and stops as one, the tail first', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    expect(plan(m, 'dsv41', 'stop').blocked).toBe('DeepSeek-V4.1-Flash is stopped already');
    const p = plan(m, 'dsv41', 'start');
    expect(p.allowed).toBe(true);
    expect(p.impact[0]).toMatch(/^R-1: TandemNode starts the front stage \(layers 0:\d+, [\d.]+ GiB\); up to 600 s$/);
    expect(p.impact[1]).toMatch(/^R-2: TandemNode starts the tail stage \(layers \d+:60, [\d.]+ GiB\), after the front answers; up to 600 s$/);
    expect(p.impact[2]).toBe("a new link key goes to both nodes' agents; requests go to the tail on R-2");
    const r = at(m, 'POST', '/v1/fleet/deployments/dsv41/actions/start', {});
    expect(r.status).toBe(200);
    const job = valid<DeploymentJobView>('deployment-job', r.body).job;
    const states = (d: FleetDeployment) => d.member_states!.map((x) => x.state);
    const a = dep(m, 'dsv41', T0 + 1);
    expect([a.status, a.split!.phase, states(a)]).toEqual(['starting', 'starting-front', ['starting', 'down']]);
    const b = dep(m, 'dsv41', T0 + START_S + 1);
    expect([b.status, b.split!.phase, states(b), b.member_states![0].stage!.reason]).toEqual(['starting', 'starting-tail', ['waiting', 'starting'], 'no peer yet']);
    const c = dep(m, 'dsv41', T0 + 2 * START_S);
    expect([c.status, c.split!.phase, c.split!.built, states(c)]).toEqual(['serving', 'linked', true, ['serving', 'serving']]);
    expect(c.member_states!.map((x) => x.routable)).toEqual([false, true]);
    expect([c.split!.link.state, c.split!.link.epoch, c.split!.link.transport]).toEqual(['up', 1, 'tcp']);
    expect(c.status_text).toBe('serving · the front on R-1, the tail on R-2, link up (epoch 1)');
    const done = valid<DeploymentJobView>('deployment-job', get(m, `/v1/fleet/deployments/jobs/${job.id}`, T0 + 2 * START_S)).job;
    expect(done.steps.map((s) => [s.alias, s.phase])).toEqual([['R-1', 'serving'], ['R-2', 'serving']]);
    // the stop: the tail first, then the front
    const t1 = T0 + 2 * START_S + 60;
    const sp = plan(m, 'dsv41', 'stop', t1);
    expect(sp.impact.slice(-3)).toEqual(['R-2: TandemNode stops the tail stage first', 'R-1: TandemNode stops the front stage', 'the link key is removed from both nodes']);
    const stop = valid<DeploymentJobView>('deployment-job', at(m, 'POST', '/v1/fleet/deployments/dsv41/actions/stop', { confirm: sp.confirm }, t1).body).job;
    expect(stop.steps.map((s) => [s.alias, s.phase])).toEqual([['R-2', 'stop']]);
    expect(dep(m, 'dsv41', t1 + 1).split!.phase).toBe('stopping');
    const end = dep(m, 'dsv41', t1 + STOP_S);
    expect([end.status, end.split!.phase, end.split!.built, states(end)]).toEqual(['stopped', 'stopped', false, ['down', 'down']]);
  });

  it('VIS-273: a link that drops is degraded and heals under the next epoch; a stage that stops is partial until Start', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const knob = (mock: string, t: number) => at(m, 'POST', '/v1/fleet/deployments/dsv41/actions/start', { mock }, t);
    expect(knob('link-down', T0).status).toBe(409);
    at(m, 'POST', '/v1/fleet/deployments/dsv41/actions/start', {});
    const up = T0 + 2 * START_S;
    expect(knob('link-down', up).status).toBe(200);
    const d = dep(m, 'dsv41', up + 1);
    expect([d.status, d.split!.phase, d.split!.link.state, d.split!.link.reason]).toEqual(['degraded', 'degraded', 'down', 'peer closed']);
    expect(d.member_states!.map((x) => [x.state, x.routable])).toEqual([['waiting', false], ['waiting', false]]);
    expect(d.status_text).toBe('the link between R-1 and R-2 is down (peer closed): requests get 503 with Retry-After 5 s. The stages link again by themselves; nothing to do');
    knob('link-up', up + 2);
    const h = dep(m, 'dsv41', up + 3);
    expect([h.status, h.split!.phase, h.split!.link.epoch]).toEqual(['serving', 'linked', 2]);
    knob('front-stop', up + 4);
    const half = dep(m, 'dsv41', up + 5);
    expect([half.status, half.split!.phase, half.member_states!.map((x) => x.state)]).toEqual(['down', 'partial', ['down', 'waiting']]);
    expect(half.status_text).toMatch(/^only the tail runs \(on R-2\); the front on R-1 does not/);
    expect(plan(m, 'dsv41', 'start', up + 5).impact).toContain('R-2: the tail stage runs already');
    at(m, 'POST', '/v1/fleet/deployments/dsv41/actions/start', {}, up + 6);
    const back = dep(m, 'dsv41', up + 6 + 2 * START_S);
    expect([back.status, back.split!.phase, back.split!.link.epoch]).toEqual(['serving', 'linked', 3]);
  });

  it('VIS-273: a Split recorded without a plan is kept and does not start', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const r = at(m, 'POST', '/v1/fleet/deployments', { model: 'bare', type: 'split', members: ['r1', 'r2'] });
    const d = valid<FleetDeployment>('deployment', r.body);
    expect([d.status, d.split, d.status_text]).toEqual(['stopped', undefined, 'Split: recorded without a plan. Plan it again on Serve a model to start it']);
    const s = at(m, 'POST', `/v1/fleet/deployments/${d.id}/actions/start`, {});
    expect([s.status, valid<{ error: { type: string } }>('error', s.body).error.type]).toEqual([409, 'no_plan']);
    expect(at(m, 'POST', '/v1/fleet/deployments', { model: 'three', type: 'split', members: ['r1', 'r2', 'r3'] }).status).toBe(400);
  });
});

describe('the placement planner', () => {
  const body = { model: 'qwen3.8-27b', pack: 'exl3-6.0', ctx: 65536 };
  it('VIS-273: a Split is planned when asked for: no shared subnet is refused in words; a fabric pair gets a plan with every number', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const split = (b: object) => valid<Placement>('placement', at(m, 'POST', '/v1/fleet/placement', b).body).types.find((t) => t.type === 'split')!;
    // a model one Spark holds is not split unless the body asks
    const unasked = split({ ...body, nodes: ['r1', 'r2'] });
    expect([unasked.allowed, unasked.built, unasked.reason]).toEqual([false, true, 'R-1 holds the model alone: a Split is planned when asked for (split)']);
    // Desk on the LAN and a rented Spark share no subnet
    const far = split({ ...body, nodes: ['desk', 'r1'], split: true });
    expect([far.allowed, far.reason]).toEqual([false, 'no interface pair in one subnet between Desk and R-1: join them with the fabric cable, or put both on one LAN (allowed with a warning)']);
    // R-1 and R-2 on the fabric switch: the plan
    const ok = split({ ...body, slots: 4, nodes: ['r1', 'r2'], split: true });
    expect([ok.allowed, ok.built, ok.nodes, ok.warn]).toEqual([true, true, ['r1', 'r2'], null]);
    const p = ok.split!;
    expect(ok.stages).toEqual([{ node: 'r1', layers: `0:${p.cut}`, gib: p.front.need_gib }, { node: 'r2', layers: `${p.cut}:${p.layers}`, gib: p.tail.need_gib }]);
    expect([p.link.kind, p.computed, p.measured.length, p.front.fits && p.tail.fits]).toEqual(['fabric', true, 1, true]);
    expect(p.front.parts.at(-1)).toMatchObject({ id: 'slot_margin', basis: 'margin' });
    const best = p.candidates.find((c) => c.chosen)!;
    expect(best.cut).toBe(p.cut);
    expect(p.candidates.filter((c) => !c.chosen).every((c) => !!c.why)).toBe(true);
    expect(Math.max(...p.candidates.filter((c) => c.fits).map((c) => c.headroom_gib))).toBe(best.headroom_gib);
    expect(split({ ...body, nodes: ['r1', 'r2', 'r3'], split: true }).reason).toBe("a Split runs over two nodes (the front stage's first), not 3");
  });

  it("each node's fit: a node that serves keeps what it serves; Replicas warn across the WAN", () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const p = valid<Placement>('placement', at(m, 'POST', '/v1/fleet/placement', { model: 'ornith-1.5-122b', pack: 'exl3-4.0', ctx: 65536 }).body);
    const desk = p.nodes.find((n) => n.node === 'desk')!;
    expect(desk.serving && !desk.fits).toBe(true);
    expect(desk.reason).toMatch(/^needs \d+ GiB, Desk has \d+ free$/);
    const small = valid<Placement>('placement', at(m, 'POST', '/v1/fleet/placement', { model: 'qwen3.8-27b', pack: 'exl3-2.5', ctx: 8192, nodes: ['desk', 'r3'] }).body);
    const rep = small.types.find((t) => t.type === 'replicas')!;
    expect(rep.allowed).toBe(true);
    expect(rep.warn).toBe('Desk on the LAN, R-3 remote: each conversation stays on one member; a failover moves it across the internet');
    expect(at(m, 'POST', '/v1/fleet/placement', { ...body, ctx: 0 }).status).toBe(400);
    expect(at(m, 'POST', '/v1/fleet/placement', { ...body, nodes: ['nope'] }).status).toBe(404);
  });
});

describe('the weights', () => {
  it('a copy runs at its rate, ends verified, and the pack shows on the target', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const w0 = valid<WeightsMatrix>('weights', get(m, '/v1/fleet/weights'));
    const q4 = w0.packs.find((p) => p.profile === 'exl3-4.0')!;
    expect(q4.nodes.map((n) => n.node).sort()).toEqual(['desk', 'r3']);
    const r = at(m, 'POST', '/v1/fleet/weights/copy', { from: 'desk', to: 'r1', profile: 'exl3-4.0' });
    const job = valid<{ job: WeightsCopyJob }>('weights-job', r.body).job;
    expect([job.state, job.via]).toEqual(['running', 'relay']);
    const span = Math.ceil(((job.bytes_total ?? 0) * 8) / (COPY_GBPS * 1e9));
    const mid = valid<{ job: WeightsCopyJob }>('weights-job', get(m, `/v1/fleet/weights/copy/${job.id}`, T0 + 10)).job;
    expect(mid.bytes_done).toBeGreaterThan(0);
    expect(mid.bytes_done).toBeLessThan(mid.bytes_total!);
    expect(valid<WeightsMatrix>('weights', get(m, '/v1/fleet/weights', T0 + 10)).packs.find((p) => p.profile === 'exl3-4.0')!.nodes.find((n) => n.node === 'r1')!.copy).toBe(job.id);
    expect(at(m, 'POST', '/v1/fleet/weights/copy', { from: 'desk', to: 'r1', profile: 'exl3-6.0' }, T0 + 10).status).toBe(409);
    const end = get<{ job: WeightsCopyJob }>(m, `/v1/fleet/weights/copy/${job.id}`, T0 + span).job;
    expect([end.state, end.verified, end.content_id.copy]).toEqual(['done', true, end.content_id.source]);
    const after = valid<WeightsMatrix>('weights', get(m, '/v1/fleet/weights', T0 + span)).packs.find((p) => p.profile === 'exl3-4.0')!;
    expect(after.nodes.find((n) => n.node === 'r1')).toMatchObject({ present: true, source: 'store' });
    expect(at(m, 'POST', '/v1/fleet/weights/copy', { from: 'desk', to: 'desk', profile: 'exl3-4.0' }).status).toBe(400);
  });

  it('a removal needs its confirm and is refused while a deployment serves the pack there', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const used = valid<WeightsPlan>('weights-plan', get(m, '/v1/fleet/weights/desk/exl3-4.0'));
    expect([used.allowed, used.blocked]).toEqual([false, 'Qwen3.8-27B serves exl3-4.0 on Desk: stop it first']);
    const p = valid<WeightsPlan>('weights-plan', get(m, '/v1/fleet/weights/desk/exl3-6.0'));
    expect(p.allowed && p.confirm).toBeTruthy();
    expect(at(m, 'DELETE', '/v1/fleet/weights/desk/exl3-6.0').status).toBe(428);
    const r = at(m, 'DELETE', '/v1/fleet/weights/desk/exl3-6.0', null, T0, `confirm=${get<WeightsPlan>(m, '/v1/fleet/weights/desk/exl3-6.0').confirm}`);
    expect(r.status).toBe(200);
    valid('weights-deleted', r.body);
    expect(get<WeightsMatrix>(m, '/v1/fleet/weights').packs.some((x) => x.profile === 'exl3-6.0')).toBe(false);
  });
});
