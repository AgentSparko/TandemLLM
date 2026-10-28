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

const FDIR = join(__dirname, '..', '..', 'docs', 'contract', 'fleet-v1');
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
    // VIS-254: the Split is recorded stopped, as the controller keeps it until ENG-307
    expect(deps.map((d) => [d.id, d.state, d.status, d.record && d.managed])).toEqual([
      ['qwen27', 'serving', 'serving', true],
      ['dsv41', 'stopped', 'stopped', true],
      ['wren', 'serving', 'serving', true],
    ]);
    expect(deps[1].status_text).toBe('stopped · not built yet (ENG-307)');
    expect(deps[1].member_states!.map((x) => [x.alias, x.state, x.routable])).toEqual([
      ['R-1', 'down', false],
      ['R-2', 'down', false],
    ]);
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
    expect(plan(m, 'dsv41', 'move', T0, 'to=desk').blocked).toBe('a Split moves with ENG-307');
  });

  it('a Split is recorded stopped: its stop says so, its start is 501 not_built until ENG-307', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    expect(plan(m, 'dsv41', 'stop').blocked).toBe('DeepSeek-V4.1-Flash is stopped already');
    const r = at(m, 'POST', '/v1/fleet/deployments/dsv41/actions/start', {});
    expect(r.status).toBe(501);
    expect(valid<{ error: { type: string } }>('error', r.body).error.type).toBe('not_built');
  });
});

describe('the placement planner', () => {
  const body = { model: 'qwen3.8-27b', pack: 'exl3-6.0', ctx: 65536 };
  it('Gherkin: a Split without fabric is refused with the reason; R-1 + R-2 on the switch are allowed, not built', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 });
    const p = valid<Placement>('placement', at(m, 'POST', '/v1/fleet/placement', { ...body, nodes: ['desk', 'r1'] }).body);
    const split = p.types.find((t) => t.type === 'split')!;
    expect([split.allowed, split.built, split.reason]).toEqual([false, false, 'no fabric link between Desk and R-1']);
    // R-1 and R-2 on the switch, DeepSeek's Split only recorded there: they take a Split, planned and not built
    const ok = valid<Placement>('placement', at(m, 'POST', '/v1/fleet/placement', { ...body, nodes: ['r1', 'r2'] }).body).types.find((t) => t.type === 'split')!;
    expect([ok.allowed, ok.built, ok.nodes, ok.stages.length]).toEqual([true, false, ['r1', 'r2'], 2]);
    expect(ok.warn).toContain('ENG-307');
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
