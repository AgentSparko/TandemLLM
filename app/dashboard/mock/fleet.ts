// The fleet in the mock (`/v1/fleet/*`, docs/contract/fleet-v1, VIS-244): Sparks, links, deployments,
// per-node metric series, memory, events, alerts, rules, each node's log and window layouts, as TandemApp's
// controller will answer them. Every metric is a function of (seed, node, time): the same seed gives
// the same answer at the same instant, and the values move as the clock moves. Ranges are what a
// GB10 shows: up to ~180 W a box, 54-86 °C, 121.6 GiB of usable unified memory, SM clock to 2.4 GHz.
//
// Scenarios (mock/server.ts picks one per request: the `qse_fleet` cookie that `?fleet=<name>` on the
// page sets, else `POST /__mock/fleet {scenario}`, else TANDEM_MOCK_FLEET, else fleet4):
//   fleet1        one Spark serving the main model and Wren, the controller on it: today's box
//   fleet4        Desk on the LAN (Replicas with R-3, and Wren as a Role), R-1 + R-2 rented over a fabric
//                 switch with a Split recorded over both (stopped: not built yet, ENG-307), R-3 rented
//                 and throttling (a thermal alert since 14 min)
//   fleet2-split  two Sparks on the LAN with one direct fabric cable, one model Split recorded over both
//
// The server this page talks to (/v1/dashboard/*) is the first Spark's engine (Desk, Gold, Spark 1):
// its deployment members' backend is `engine`, the others' `<node>-engine` (lib/where.ts). A Split
// serves nothing until ENG-307 builds the pipeline split, as on the controller: its record is
// stopped and its Sparks idle.
//   node-down     fleet4, and R-3 stops answering 30 s after the scenario starts
//   alert-thermal fleet4 without the old alert: R-3 heats up and the alert fires a minute in
//   mixed         fleet4 where R-3 was updated by hand to 0.15.1 eight minutes ago: the version-mismatch
//                 alert (OPS-234) fires a minute in
//   empty         TandemApp alone on a Mac, no Spark yet: the page opens on Add Spark (OPS-234)
//   gold3         the controller on a Mac with three Sparks on the LAN: Qwen3.8-27B as Replicas on Gold
//                 and R-3, Wren on Bronze; the move to Gold and the rolling update (OPS-234's Gherkin)
//   quad          four Sparks on the LAN, every one decoding, the controller on Gold: Qwen3.8-27B as
//                 Replicas on Gold and R-3, Qwen3.8-Flash-Next on R-1, Nemotron 3.5 Bolt on R-2
//                 (VIS-254's Gherkin "Live per node")
//
// The fleet update and the controller's host (OPS-234, /v1/fleet/update*, /v1/fleet/controller*) are
// mock/fleetops.ts: jobs on the clock; a Spark's version and its drain follow the update.
//
// The deployment records, the placement planner and the weights (SRV-731, /v1/fleet/deployments/*,
// /v1/fleet/placement, /v1/fleet/weights*) are mock/deploy.ts, over the scenario's own deployments.
//
// The node actions (VIS-250, proxy/fleetops.py): a drain ends the requests in flight one by one
// (4 s apart) while the node's rate falls with them, then the node is drained; a reboot takes the
// node off for 20 s, an engine's restart and an update are jobs of a few seconds; a release of a
// rented node removes it, its links and a Split it was a stage of. fleet1 is today's box: its
// engine is not managed by TandemNode and its reboot and update are not set up, as on the real
// one; the other scenarios' nodes have both. Each destructive action needs its plan's confirm.
//
// Discovery (SRV-727) is mock/discovery.ts: its devices line up with MOCK_PAIR, and what a pairing
// learns (a bare GX10's DMI, a node that joined) shows in its rows. The Add Spark wizard's session
// (VIS-246, /v1/fleet/add) is mock/addspark.ts: it pairs through the routes below and adds a node
// that joins with `admit` (a LAN link to it, or the rental gateway's tunnel).
//
// The rules, alerts and events are the controller's (proxy/fleetwatch.py: its seeded rules, an
// alert's id `<rule>:<subject>`, its title, rule text and action route, "Acknowledged: …" events),
// plus two rules the controller does not have yet (fabric, power) with mock-only scopes. The lease
// rule is the controller's (24 h before a lease ends); fleet4's operator raised it to 72 h.
// Each node's newest sample (`/nodes/{id}/sample`) and the controller's host follow the same
// clock; a field a GB10 does not expose (the power cap, Desk's fan) is absent, as on the board.

import type {
  ActionPlan,
  ActionResult,
  AgentJob,
  DeploymentStatus,
  FleetAlert,
  FleetDeployment,
  FleetEvent,
  FleetHost,
  FleetLogLine,
  FleetLink,
  FleetNode,
  FleetRule,
  FleetSample,
  EventType,
  FleetTopology,
  InfraItem,
  Layout,
  LayoutDevice,
  LogLevel,
  MetricKey,
  MetricSeries,
  NodeAction,
  NodeDrain,
  NodeMemory,
  NodePairing,
  NodeSample,
  NodePatch,
  NodeStatus,
  PairBySsh,
  Range,
  RulePatch,
  TrustAudit,
} from '../src/api/fleet.ts';
import { EVENT_TYPES, FLEET_CONTRACT, METRICS, NODE_ACTIONS, RANGES, WINDOW_KINDS } from '../src/api/fleet.ts';
import { MockWizard } from './addspark.ts';
import { MockDeploy } from './deploy.ts';
import { MockSetupRelay } from './setup-relay.ts';
import { MockOps } from './fleetops.ts';
import { MockApp } from './appsettings.ts';
import { MockDiscovery, MOCK_LAN } from './discovery.ts';

export const FLEET_SCENARIOS = ['fleet1', 'fleet4', 'fleet2-split', 'node-down', 'alert-thermal', 'mixed', 'empty', 'gold3', 'quad'] as const;
export type FleetScenario = (typeof FLEET_SCENARIOS)[number];
export const isFleetScenario = (s: unknown): s is FleetScenario => (FLEET_SCENARIOS as readonly unknown[]).includes(s);

const MIN = 60;
const H = 3600;
const D = 86400;
/** usable unified memory of a GB10, GiB (128 GB less what the OS keeps) */
export const USABLE_GIB = 121.6;
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

/** FNV-1a of a string, as a fraction in [0, 1) */
function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

/** five sines from seconds to days, about -1..1 */
function wave(ph: number, t: number): number {
  return (
    (Math.sin(t / 9.7 + ph * 6.3) * 0.18 + Math.sin(t / 37 + ph * 12.1) * 0.3 + Math.sin(t / 141 + ph * 3.7) * 0.32 + Math.sin(t / 1700 + ph * 2.1) * 0.45 + Math.sin(t / 6100 + ph * 5.3) * 0.35) / 1.25
  );
}

/** the day's rhythm: quiet at night, busy in the afternoon (UTC+1) */
function dayCurve(t: number): number {
  const h = ((((t + H) / H) % 24) + 24) % 24;
  return 0.18 + 0.82 * Math.max(0, Math.sin(((h - 7) / 17) * Math.PI)) ** 0.8;
}

interface MemPlan {
  weights: number;
  kv: number;
  cache: number;
  other: number;
  page: number;
  ceiling: number;
  peak: number;
}

/** how one node behaves over time */
interface NodeSim {
  node: Omit<FleetNode, 'status' | 'status_text' | 'drain'>;
  /** the load curve it follows (a Split's stages share one) */
  curve: string;
  /** before this it idles (nothing deployed yet) */
  busyFrom: number;
  idleMem: number;
  plan: MemPlan;
  ttft: [number, number];
  gpuSpan: number;
  fabric: boolean;
  rtt: [number, number];
  heatFrom: number | null;
  throttleFrom: number | null;
  offFrom: number | null;
}

interface Member {
  node: string;
  from: number;
  base: number;
  span: number;
  curve: string;
  /** a Split's tokens count once per stage at this share, so per-node values add up to the total */
  share: number;
}

interface DepSim {
  dep: Omit<FleetDeployment, 'status' | 'status_text'>;
  members: Member[];
}

interface Scheduled<T> {
  at: number;
  item: T;
  /** listed only while this holds */
  when?: (t: number) => boolean;
}

interface EventFilter {
  since?: number;
  before?: number;
  node?: string;
  types?: EventType[];
  limit?: number;
}

interface Fleet {
  host: FleetTopology['host'];
  infra: InfraItem[];
  nodes: NodeSim[];
  deps: DepSim[];
  links: FleetLink[];
  events: Scheduled<Omit<FleetEvent, 'at'>>[];
  alerts: Scheduled<Omit<FleetAlert, 'since' | 'acked' | 'acked_at'>>[];
  rules: FleetRule[];
}

/** node trust (SEC-4): the devices the mock's pairing routes know. A node at MOCK_PAIR.code's address
 *  shows the code of the Gherkin; one other TandemApp owns; one SSH host takes the password; one
 *  has nothing installed. */
export const MOCK_PAIR = { address: '192.0.2.60', code: '482913', owned: '192.0.2.62', ssh: '192.0.2.61', password: 'spark-pass', bare: '192.0.2.63' } as const;
/** the hosts an SSH login reaches: the ssh-config Spark, the bare GX10, the owned one (take-over)
 *  and the rental (a key only) */
const SSH_HOSTS: string[] = [MOCK_PAIR.ssh, MOCK_PAIR.bare, MOCK_PAIR.owned, MOCK_LAN.rented];
const MOCK_HOST_KEY = 'SHA256:qQ6Rk0mOcKhOsTkEyFoRtHeMoCkNoDe0192o2o61x';
const paired = (via: 'code' | 'ssh', at: number, fp: string): NodePairing => ({ state: 'paired', via, fingerprint: fp.repeat(64 / fp.length), paired_at: at, host_key: via === 'ssh' ? MOCK_HOST_KEY : null });
const LOCAL: NodePairing = { state: 'local', via: null, fingerprint: null, paired_at: null, host_key: null };

const INVENTORY_RENTED = {
  os: 'DGX OS 7.2.1',
  kernel: '6.11.0-1015-nvidia',
  driver: '580.95.05',
  cuda: '13.0',
  cx7: 'FW 28.45.1020 · 200G up',
  disk: 'NVMe 4 TB · 3.2 TB free',
  nics: 'enP7s7 (10GbE) · enp1s0f0np0 / enP2p1s0f1np1 (CX7 200G)',
};

const fmtNum = (v: number) => String(Math.round(v * 1000) / 1000);
const holdFor = (s: number) => (s >= 120 && s % 60 === 0 ? `${s / 60} min` : `${fmtNum(s)} s`);

/** A rule's condition in words, as the controller writes it (proxy/fleetwatch.py Rule.condition). */
export function ruleCondition(r: Pick<FleetRule, 'metric' | 'threshold' | 'unit' | 'for_s'>): string {
  const t = r.threshold == null ? '' : `${fmtNum(r.threshold)} ${r.unit ?? ''}`.trim();
  const hold = r.for_s ? ` for ${holdFor(r.for_s)}` : '';
  switch (r.metric) {
    case 'temp':
      return `≥ ${t}${hold}`;
    case 'throttle':
      return `thermal or power-brake clock event${hold}`;
    case 'mem_avail':
      return `MemAvailable < ${t}${hold}`;
    case 'disk_free':
      return `free < ${t} on a mount${hold}`;
    case 'reachable':
      return `no answer${hold}`;
    case 'engine':
      return `health check fails${hold}`;
    case 'host_sleep':
      return 'sleep allowed';
    case 'fabric':
      return `< ${t} or one logical interface${hold}`;
    case 'lease_left':
      return `lease ends in < ${t}`;
    case 'versions':
      return `nodes run different releases${hold}`;
    default:
      return `> ${t}${hold}`;
  }
}

/** the lease rule as the controller seeds it: 24 h before the end */
const leaseDefault = (r: FleetRule): FleetRule => {
  const x = { ...r, threshold: 24 };
  return { ...x, condition: ruleCondition(x) };
};

function build(scenario: FleetScenario, t0: number): Fleet {
  const rentStart = t0 - 5 * D + 3 * H;
  const leaseEnd = t0 + 2 * D + 4 * H + 20 * MIN;
  const desk: NodeSim = {
    node: {
      id: 'desk', alias: 'Desk', colour: 1, icon: 'desk', tags: ['desk', 'owned'], class: 'spark', hw: 'ASUS Ascent GX10', dmi: 'ASUS Ascent GX10 (GB10)', owned: true,
      transport: 'lan', address: '192.0.2.40:7700', version: '0.15.0', since: t0 - 60 * D, lease_end: null, cost_per_hour: null,
      inventory: { serial: 'R9NX4A00217', os: 'DGX OS 7.2.1', kernel: '6.11.0-1015-nvidia', driver: '580.95.05', cuda: '13.0', cx7: 'not cabled', disk: 'NVMe 4 TB · 2.71 TB free', nics: 'enP7s7 (10GbE, LAN) · CX7 ×2 (no cable)' },
      pairing: paired('code', t0 - 60 * D, '3d'),
    },
    curve: 'desk', busyFrom: t0 - 60 * D, idleMem: 6.4, plan: { weights: 17.2, kv: 48, cache: 8, other: 4.6, page: 12.4, ceiling: 112, peak: 104.8 },
    ttft: [310, 280], gpuSpan: 94, fabric: false, rtt: [0.42, 0.18], heatFrom: null, throttleFrom: null, offFrom: null,
  };
  const rented = (id: string, alias: string, colour: number, port: number, serial: string, since: number): NodeSim['node'] => ({
    id, alias, colour, icon: 'cloud', tags: ['rented'], class: 'spark', hw: 'DGX Spark Founders Edition', dmi: 'NVIDIA DGX Spark', owned: false,
    transport: 'tunnel', address: `203.0.113.${20 + colour - 1}:${port}`, version: '0.15.0', since, lease_end: leaseEnd, cost_per_hour: 2.4,
    inventory: { serial, ...INVENTORY_RENTED },
    pairing: paired('ssh', since, (colour * 17).toString(16).padStart(2, '0')),
  });
  // VIS-254: the Split is recorded, never started (ENG-307): its Sparks hold no model
  const dsvFrom = Number.POSITIVE_INFINITY;
  const splitPlan = (w: number, peak: number): MemPlan => ({ weights: w, kv: 22, cache: 5, other: 3.2, page: 6.1, ceiling: 116, peak });
  const r1: NodeSim = {
    node: rented('r1', 'R-1', 2, 2201, 'NVS1-2511-0441', rentStart), curve: 'dsv', busyFrom: dsvFrom, idleMem: 6.2, plan: splitPlan(78.2, 115.1),
    ttft: [1050, 520], gpuSpan: 84, fabric: true, rtt: [37, 2.6], heatFrom: null, throttleFrom: null, offFrom: null,
  };
  const r2: NodeSim = { ...r1, node: rented('r2', 'R-2', 3, 2202, 'NVS1-2511-0458', rentStart), plan: splitPlan(76.5, 113.8) };
  const r3From = t0 - 42 * MIN;
  const r3: NodeSim = {
    node: rented('r3', 'R-3', 4, 2203, 'NVS1-2511-0462', t0 - 5.1 * H), curve: 'r3', busyFrom: r3From, idleMem: 21.6,
    plan: { weights: 15.6, kv: 46, cache: 8, other: 3.4, page: 4.2, ceiling: 112, peak: 81.9 },
    ttft: [520, 380], gpuSpan: 94, fabric: true, rtt: [37.6, 2.6], heatFrom: t0 - 40 * MIN, throttleFrom: t0 - 15 * MIN, offFrom: null,
  };
  if (scenario === 'alert-thermal') {
    // the ramp reaches 85 °C 1,313 s in: as the scenario starts; the clock throttles, and the rule
    // (85 °C or more for 60 s) fires a minute later
    r3.heatFrom = t0 - 1313;
    r3.throttleFrom = t0;
  }
  if (scenario === 'node-down') r3.offFrom = t0 + 30;

  const qwen = (members: Member[], type: 'single' | 'replicas'): DepSim => ({
    dep: {
      id: 'qwen27', name: 'Qwen3.8-27B', model: 'Qwen3.8-27B', pack: 'exl3-4.0', type, members: members.map((m) => m.node), api_id: 'qwen3.8-27b', ctx: 262144,
      ...(type === 'replicas' ? { warn: 'Desk is on the LAN, R-3 is remote: 38 ms between them. Each conversation stays on one member; a failover moves it across the internet.' } : {}),
    },
    members,
  });
  const wren: DepSim = {
    dep: { id: 'wren', name: 'Wren', model: 'Qwen3.5-2B', pack: 'qwen3.5-2b-exl3-6.0', type: 'role', members: ['desk'], api_id: 'wren', ctx: 65536, role_of: 'qwen27', role: 'assistant · setup and fleet help' },
    members: [{ node: 'desk', from: t0 - 4.2 * D, base: 4, span: 14, curve: 'wren', share: 1 }],
  };
  const dsv = (a: string, b: string): DepSim => ({
    dep: {
      id: 'dsv41', name: 'DeepSeek-V4.1-Flash', model: 'DeepSeek-V4.1-Flash', pack: 'deepseek-v4.1-flash-nvfp4', type: 'split', members: [a, b], api_id: 'deepseek-v4.1-flash', ctx: 131072, state: 'stopped',
      stages: [{ node: a, layers: 'layers 0–30', gib: 78.2 }, { node: b, layers: 'layers 31–60 + head', gib: 76.5 }],
    },
    members: [a, b].map((node) => ({ node, from: dsvFrom, base: 80, span: 110, curve: 'dsv', share: 0.5 })),
  });
  const rule = (id: string, name: string, metric: string, threshold: number | null, unit: string | null, for_s: number, severity: 'warn' | 'err', notify: FleetRule['notify'], scope = 'all', enabled = true): FleetRule => {
    const r: FleetRule = { id, name, metric, condition: '', scope, notify, enabled, threshold, unit, for_s, severity };
    return { ...r, condition: ruleCondition(r) };
  };
  const rules: FleetRule[] = [
    rule('gpu-temp', 'GPU temperature', 'temp', 85, '°C', 60, 'err', ['app', 'system']),
    rule('throttle', 'GPU clock throttled', 'throttle', null, null, 30, 'warn', ['app']),
    rule('mem-avail', 'Memory headroom', 'mem_avail', 10, 'GiB', 30, 'warn', ['app', 'system']),
    rule('disk-free', 'Disk almost full', 'disk_free', 10, '%', 300, 'warn', ['app']),
    rule('node-down', 'Node reachable', 'reachable', null, null, 30, 'err', ['app', 'system']),
    rule('engine-down', 'Engine answers', 'engine', null, null, 60, 'err', ['app', 'system']),
    rule('host-sleep', 'Controller host may sleep', 'host_sleep', null, null, 0, 'warn', ['app'], 'host'),
    rule('fabric', 'Fabric link', 'fabric', 150, 'Gb/s', 0, 'warn', ['app'], 'fabric'),
    rule('lease', 'Lease ends', 'lease_left', 72, 'h', 0, 'warn', ['app', 'system']),
    rule('power', 'Power draw', 'power', 170, 'W', 300, 'warn', ['app'], 'node:r3', false),
    rule('version-mismatch', 'One release on every node', 'versions', null, null, 60, 'warn', ['app']),
  ];

  if (scenario === 'empty') {
    return {
      host: { name: 'Mac', address: '127.0.0.1', api_url: 'http://127.0.0.1:8080', may_sleep: true },
      infra: [{ id: 'host', kind: 'host', label: 'Mac' }],
      nodes: [],
      deps: [],
      links: [],
      events: [{ at: t0 - 40, item: { id: 'e-ctl', type: 'controller', severity: null, node: 'host', deployment: null, text: "TandemApp's controller started (0.15.1)", detail: 'the first start with this store' } }],
      alerts: [],
      rules: rules.filter((r) => !['fabric', 'power'].includes(r.id)).map((r) => (r.id === 'lease' ? leaseDefault(r) : r)),
    };
  }

  if (scenario === 'fleet1') {
    const one = { ...desk, node: { ...desk.node, pairing: LOCAL }, rtt: [0.05, 0.03] as [number, number] };
    return {
      host: { name: 'Desk', address: '192.0.2.40', api_url: 'http://192.0.2.40:8091', may_sleep: false },
      infra: [{ id: 'lan', kind: 'lan', label: 'LAN' }],
      nodes: [one],
      deps: [qwen([{ node: 'desk', from: t0 - 60 * D, base: 48, span: 150, curve: 'desk', share: 1 }], 'single'), wren],
      links: [{ id: 'lan-desk', a: 'lan', b: 'desk', kind: 'lan', speed: '10 GbE', rtt_ms: null, mtu: 1500, state: 'up', note: 'enP7s7; the controller runs on this node' }],
      events: [
        { at: t0 - 2 * H, item: { id: 'e-ctl', type: 'controller', severity: null, node: 'host', deployment: null, text: "TandemApp's controller started (0.15.0)", detail: 'stopped for 4 s before' } },
        { at: t0 - 26 * H, item: { id: 'e-update', type: 'update', severity: null, node: 'desk', deployment: null, text: 'Desk updated to TandemLLM 0.15.0', detail: 'The engine restarted with the new build; 41 s without the API.' } },
        { at: t0 - 4.2 * D, item: { id: 'e-wren', type: 'deploy', severity: null, node: 'desk', deployment: 'wren', text: 'Wren (Role) started on Desk', detail: 'Qwen3.5-2B EXL3 6.0 beside the main model.' } },
      ],
      alerts: [],
      rules: rules.filter((r) => !['fabric', 'power'].includes(r.id)).map((r) => (r.id === 'lease' ? leaseDefault(r) : r)),
    };
  }

  if (scenario === 'fleet2-split') {
    const s1: NodeSim = { ...r1, node: { ...r1.node, id: 's1', alias: 'Spark 1', colour: 1, icon: 'desk', tags: ['owned'], owned: true, transport: 'lan', address: '192.0.2.41:7700', since: t0 - 20 * D, lease_end: null, cost_per_hour: null, pairing: LOCAL }, rtt: [0.42, 0.18] };
    const s2: NodeSim = { ...s1, node: { ...s1.node, id: 's2', alias: 'Spark 2', colour: 2, address: '192.0.2.42:7700', inventory: { ...s1.node.inventory, serial: 'NVS1-2511-0458' }, pairing: paired('code', t0 - 20 * D, '5e') }, plan: splitPlan(76.5, 113.8) };
    return {
      host: { name: 'Spark 1', address: '192.0.2.41', api_url: 'http://192.0.2.41:8080', may_sleep: false },
      infra: [{ id: 'lan', kind: 'lan', label: 'LAN' }],
      nodes: [s1, s2],
      deps: [dsv('s1', 's2')],
      links: [
        { id: 'lan-s1', a: 'lan', b: 's1', kind: 'lan', speed: '10 GbE', rtt_ms: 0.4, mtu: 1500, state: 'up', note: 'the controller runs on Spark 1' },
        { id: 'lan-s2', a: 'lan', b: 's2', kind: 'lan', speed: '10 GbE', rtt_ms: 0.4, mtu: 1500, state: 'up', note: 'mTLS to the node agent :7700' },
        { id: 's1-s2', a: 's1', b: 's2', kind: 'fabric', speed: '200G', rtt_ms: 0.009, mtu: 9000, state: 'up', note: 'direct QSFP cable', test: { gbps: 197, at: t0 - 3.5 * H }, roce: 'on', interfaces: { up: 2, total: 2 }, port: 'enp1s0f0np0' },
      ],
      events: [
        { at: t0 - 3.2 * H, item: { id: 'e-dsv', type: 'deploy', severity: null, node: 's1', deployment: 'dsv41', text: 'DeepSeek-V4.1-Flash (Split) recorded on Spark 1 + Spark 2', detail: 'Stage 1: layers 0–30 on Spark 1. Stage 2: layers 31–60 + head on Spark 2. Not built yet (ENG-307): the record and its plan only.' } },
        { at: t0 - 3.5 * H, item: { id: 'e-link', type: 'link', severity: null, node: 's2', deployment: null, text: 'Link test Spark 1 ↔ Spark 2: 197 Gb/s, RTT 0.009 ms, MTU 9000', detail: 'RoCE on, both logical interfaces up.' } },
      ],
      alerts: [],
      rules: rules.filter((r) => r.id !== 'power').map((r) => (r.id === 'lease' ? leaseDefault(r) : r)),
    };
  }

  if (scenario === 'quad') {
    const lan = (id: string, alias: string, colour: number, ip: string, serial: string, fp: string | null): NodeSim['node'] => ({
      ...desk.node, id, alias, colour, tags: ['owned'], address: `${ip}:7700`, since: t0 - 30 * D, inventory: { ...desk.node.inventory, serial }, pairing: fp ? paired('code', t0 - 30 * D, fp) : LOCAL,
    });
    const gold: NodeSim = { ...desk, node: lan('gold', 'Gold', 1, '192.0.2.40', 'R9NX4A00217', null), rtt: [0.05, 0.03] };
    const q1: NodeSim = { ...desk, node: lan('r1', 'R-1', 2, '192.0.2.41', 'R9NX4A00219', '2b'), curve: 'flash', plan: { weights: 46.1, kv: 30, cache: 8, other: 3.4, page: 4.2, ceiling: 112, peak: 96.4 } };
    const q2: NodeSim = { ...desk, node: lan('r2', 'R-2', 3, '192.0.2.42', 'R9NX4A00224', '5e'), curve: 'bolt', plan: { weights: 41.8, kv: 34, cache: 8, other: 3.1, page: 4.0, ceiling: 112, peak: 99.0 } };
    const q3: NodeSim = { ...desk, node: lan('r3', 'R-3', 4, '192.0.2.43', 'R9NX4A00231', '6a'), curve: 'r3', plan: { weights: 17.2, kv: 46, cache: 8, other: 3.4, page: 4.2, ceiling: 112, peak: 88.2 } };
    const single = (id: string, name: string, model: string, pack: string, node: string, base: number, span: number, curve: string): DepSim => ({
      dep: { id, name, model, pack, type: 'single', members: [node], api_id: model.toLowerCase(), ctx: 262144 },
      members: [{ node, from: t0 - 30 * D, base, span, curve, share: 1 }],
    });
    const q27 = qwen(
      [
        { node: 'gold', from: t0 - 30 * D, base: 48, span: 150, curve: 'desk', share: 1 },
        { node: 'r3', from: t0 - 30 * D, base: 46, span: 140, curve: 'r3', share: 1 },
      ],
      'replicas',
    );
    q27.dep = { ...q27.dep, warn: undefined };
    return {
      host: { name: 'Gold', address: '192.0.2.40', api_url: 'http://192.0.2.40:8091', may_sleep: false },
      infra: [{ id: 'lan', kind: 'lan', label: 'LAN' }],
      nodes: [gold, q1, q2, q3],
      deps: [q27, single('flash', 'Qwen3.8-Flash-Next', 'Qwen3.8-Flash-Next', 'flashnext-3.05', 'r1', 60, 120, 'flash'), single('bolt', 'Nemotron 3.5 Bolt', 'Nemotron-3.5-Bolt', 'bolt-exl3-4.0', 'r2', 70, 110, 'bolt')],
      links: (['gold', 'r1', 'r2', 'r3'] as const).map((id): FleetLink => ({ id: `lan-${id}`, a: 'lan', b: id, kind: 'lan', speed: '10 GbE', rtt_ms: id === 'gold' ? null : 0.4, mtu: 1500, state: 'up', note: id === 'gold' ? 'the controller runs on this node' : 'mTLS to the node agent :7700' })),
      events: [{ at: t0 - 3 * H, item: { id: 'e-ctl', type: 'controller', severity: null, node: 'host', deployment: null, text: "TandemApp's controller started (0.15.1)", detail: 'stopped for 5 s before' } }],
      alerts: [],
      rules: rules.filter((r) => !['fabric', 'power'].includes(r.id)).map((r) => (r.id === 'lease' ? leaseDefault(r) : r)),
    };
  }

  if (scenario === 'gold3') {
    const lan = (id: string, alias: string, colour: number, ip: string, serial: string, fp: string): NodeSim['node'] => ({
      ...desk.node, id, alias, colour, tags: ['owned'], address: `${ip}:7700`, since: t0 - 30 * D, inventory: { ...desk.node.inventory, serial }, pairing: paired('code', t0 - 30 * D, fp),
    });
    const gold: NodeSim = { ...desk, node: lan('gold', 'Gold', 1, '192.0.2.40', 'R9NX4A00217', '3d') };
    const r3l: NodeSim = { ...desk, node: lan('r3', 'R-3', 4, '192.0.2.43', 'R9NX4A00231', '6a'), curve: 'r3', plan: { weights: 17.2, kv: 46, cache: 8, other: 3.4, page: 4.2, ceiling: 112, peak: 88.2 } };
    const bronze: NodeSim = { ...desk, node: lan('bronze', 'Bronze', 2, '192.0.2.42', 'R9NX4A00224', '5e'), curve: 'wren', idleMem: 5.1, plan: { weights: 4.4, kv: 12, cache: 3, other: 2.8, page: 3.1, ceiling: 112, peak: 31.2 } };
    const wrenSingle: DepSim = {
      dep: { id: 'wren', name: 'Wren', model: 'Qwen3.5-2B', pack: 'qwen3.5-2b-exl3-6.0', type: 'single', members: ['bronze'], api_id: 'wren', ctx: 65536 },
      members: [{ node: 'bronze', from: t0 - 30 * D, base: 4, span: 14, curve: 'wren', share: 1 }],
    };
    const q3 = qwen(
      [
        { node: 'gold', from: t0 - 30 * D, base: 48, span: 150, curve: 'desk', share: 1 },
        { node: 'r3', from: t0 - 30 * D, base: 46, span: 140, curve: 'r3', share: 1 },
      ],
      'replicas',
    );
    q3.dep = { ...q3.dep, warn: undefined };
    return {
      host: { name: 'Mac', address: '192.0.2.10', api_url: 'http://192.0.2.10:8080', may_sleep: true },
      infra: [
        { id: 'host', kind: 'host', label: 'Mac' },
        { id: 'lan', kind: 'lan', label: 'LAN' },
      ],
      nodes: [gold, r3l, bronze],
      deps: [q3, wrenSingle],
      links: [
        { id: 'host-lan', a: 'host', b: 'lan', kind: 'lan', speed: '10 GbE', rtt_ms: 0.3, mtu: 1500, state: 'up', note: 'the controller host on the LAN' },
        ...(['gold', 'r3', 'bronze'] as const).map((id): FleetLink => ({ id: `lan-${id}`, a: 'lan', b: id, kind: 'lan', speed: '10 GbE', rtt_ms: 0.4, mtu: 1500, state: 'up', note: 'mTLS to the node agent :7700' })),
      ],
      events: [{ at: t0 - 3 * H, item: { id: 'e-ctl', type: 'controller', severity: null, node: 'host', deployment: null, text: "TandemApp's controller started (0.15.1)", detail: 'stopped for 5 s before' } }],
      alerts: [{ at: t0 - 30 * D, item: { id: 'host-sleep:host', severity: 'warn', node: 'host', title: 'The controller runs on a machine that can sleep', rule: 'Controller host may sleep sleep allowed', detail: 'If it sleeps, the API and every metric stop. Keep it awake or move the controller to a Spark.', action: { label: 'App settings', route: '#/app' } } }],
      rules: rules.filter((r) => !['fabric', 'power'].includes(r.id)).map((r) => (r.id === 'lease' ? leaseDefault(r) : r)),
    };
  }

  // fleet4 and its variants
  const events: Fleet['events'] = [
    { at: t0 - 42 * MIN, item: { id: 'e-scale', type: 'deploy', severity: null, node: 'r3', deployment: 'qwen27', text: 'Qwen3.8-27B scaled Single → Replicas: R-3 joined', detail: 'Identity check passed (same pack, same engine 0.15.0). Output canary matched.' } },
    { at: t0 - 58 * MIN, item: { id: 'e-link-r3', type: 'link', severity: null, node: 'r3', deployment: null, text: 'Link test R-3 ↔ switch: 194 Gb/s, RTT 0.011 ms, MTU 9000', detail: 'RoCE on, both logical interfaces up.' } },
    { at: t0 - 2.4 * H, item: { id: 'e-lease', type: 'alert', severity: 'warn', node: 'all', deployment: null, text: 'Leases end in 2 days: R-1, R-2, R-3', detail: 'Release wipes weights, caches, keys and the node agent.' } },
    { at: t0 - 2.9 * H, item: { id: 'e-eng-r2', type: 'engine', severity: null, node: 'r2', deployment: null, text: "R-2's engine restarted", detail: 'back after 38 s; it serves nothing until the Split is built' } },
    { at: t0 - 9 * H, item: { id: 'e-ctl', type: 'controller', severity: null, node: 'host', deployment: null, text: "TandemApp's controller started (0.15.0)", detail: 'stopped for 6 s before' } },
    { at: t0 - 3.2 * H, item: { id: 'e-dsv', type: 'deploy', severity: null, node: 'r1', deployment: 'dsv41', text: 'DeepSeek-V4.1-Flash (Split) recorded on R-1 + R-2', detail: 'Stage 1: layers 0–30 on R-1. Stage 2: layers 31–60 + head on R-2. Not built yet (ENG-307): the record and its plan only.' } },
    { at: t0 - 3.7 * H, item: { id: 'e-weights', type: 'weights', severity: null, node: 'r2', deployment: null, text: 'Weights copied R-1 → R-2 over the fabric', detail: '76.5 GiB in 4 min 12 s (2.4 Gb/s average, disk-bound).' } },
    { at: t0 - 5.1 * H, item: { id: 'e-pair-r3', type: 'node', severity: null, node: 'r3', deployment: null, text: 'R-3 paired (rental, SSH tunnel)', detail: 'Host key confirmed. Certificate pinned.' } },
    { at: t0 - 6.4 * H, item: { id: 'e-half', type: 'alert', severity: 'resolved', node: 'r2', deployment: null, text: 'Resolved: R-2 fabric on one logical interface (half bandwidth)', detail: 'Cable re-seated by the provider. Link test 196 Gb/s.' } },
    { at: t0 - 6.9 * H, item: { id: 'e-half-on', type: 'alert', severity: 'warn', node: 'r2', deployment: null, text: 'R-2 fabric on one logical interface (half bandwidth)', detail: 'enp1s0f1np1 carries no traffic; the port runs at 100 Gb/s.' } },
    { at: t0 - 26 * H, item: { id: 'e-update', type: 'update', severity: null, node: 'all', deployment: null, text: 'Fleet updated to TandemLLM 0.15.0 (rolling)', detail: 'Desk first, then R-1 and R-2 together (their Split record), no downtime: the Split serves nothing yet.' } },
    { at: t0 - 27.5 * H, item: { id: 'e-mem', type: 'alert', severity: 'resolved', node: 'desk', deployment: null, text: 'Resolved: Desk memory low: 8.6 GiB available', detail: '12.4 GiB available' } },
    { at: t0 - 27.9 * H, item: { id: 'e-mem-on', type: 'alert', severity: 'warn', node: 'desk', deployment: null, text: 'Desk memory low: 8.6 GiB available', detail: 'Unified memory: the GPU and the system share it. Below this the engine’s memory gate refuses work and the kernel may kill a process.' } },
    { at: t0 - 4.2 * D, item: { id: 'e-wren', type: 'deploy', severity: null, node: 'desk', deployment: 'wren', text: 'Wren (Role) started on Desk', detail: 'Qwen3.5-2B EXL3 6.0 beside the main model.' } },
    { at: rentStart + 600, item: { id: 'e-pair-r12', type: 'node', severity: null, node: 'r1', deployment: null, text: 'R-1 and R-2 paired (rental, SSH tunnel)', detail: 'Lease 7 days, 2.40 per hour per node.' } },
  ];
  const ruleText = (id: string) => {
    const r = rules.find((x) => x.id === id)!;
    return `${r.name} ${r.condition}`;
  };
  const alerts: Fleet['alerts'] = [
    { at: t0 - 2.4 * H, item: { id: 'lease:all', severity: 'warn', node: 'all', title: 'Leases end in 2 days: R-1, R-2, R-3', rule: ruleText('lease'), detail: 'The second Qwen replica stops when the leases end; the Split recorded over R-1 + R-2 goes with them.', action: { label: 'Open Sparks', route: '#/sparks' } } },
    { at: t0 - 60 * D, item: { id: 'host-sleep:host', severity: 'warn', node: 'host', title: 'The controller runs on a machine that can sleep', rule: ruleText('host-sleep'), detail: 'If it sleeps, the API and every metric stop. Keep it awake or move the controller to a Spark.', action: { label: 'App settings', route: '#/app' } } },
  ];
  const therm = r3.throttleFrom as number;
  events.push({ at: therm + MIN, item: { id: 'e-therm', type: 'alert', severity: 'err', node: 'r3', deployment: null, text: 'R-3 GPU 86 °C', detail: 'At 85 °C or more for 60 s. A GB10 slows its clocks when it stays this hot.' } });
  if (scenario !== 'alert-thermal') {
    events.push({ at: therm - 4 * MIN, item: { id: 'e-failover', type: 'failover', severity: null, node: 'r3', deployment: 'qwen27', text: '1 conversation moved R-3 → Desk', detail: 'R-3 answered a health check after 4.8 s. The replica set moved the conversation; it keeps its context.' } });
  }
  alerts.unshift({
    at: therm + MIN,
    item: { id: 'gpu-temp:r3', severity: 'err', node: 'r3', title: 'R-3 GPU 86 °C', rule: ruleText('gpu-temp'), detail: 'At 85 °C or more for 60 s. A GB10 slows its clocks when it stays this hot.', action: { label: 'Open R-3 in Hardware', route: '#/hardware?scope=node:r3' } },
  });
  if (r3.offFrom != null) {
    const down = r3.offFrom + 30;
    events.push({ at: r3.offFrom + 10, item: { id: 'e-off', type: 'node', severity: 'err', node: 'r3', deployment: null, text: 'R-3 is off', detail: 'not reachable' } });
    events.push({ at: r3.offFrom + 10, item: { id: 'e-degraded', type: 'deploy', severity: null, node: 'r3', deployment: 'qwen27', text: 'Qwen3.8-27B degraded · 1 of 2 members', detail: 'Its conversations on R-3 moved to Desk.' } });
    events.push({ at: down, item: { id: 'e-down', type: 'alert', severity: 'err', node: 'r3', deployment: null, text: 'R-3 not reachable', detail: 'The SSH tunnel is up, the node agent does not answer.' } });
    alerts.unshift({ at: down, item: { id: 'node-down:r3', severity: 'err', node: 'r3', title: 'R-3 not reachable', rule: ruleText('node-down'), detail: 'The SSH tunnel is up, the node agent does not answer. Qwen3.8-27B runs on 1 of 2 members until R-3 answers again.', action: { label: 'Open R-3', route: '#/sparks/r3' } } });
  }
  return {
    host: { name: 'Mac', address: '192.0.2.10', api_url: 'http://192.0.2.10:8080', may_sleep: true },
    infra: [
      { id: 'host', kind: 'host', label: 'Mac' },
      { id: 'lan', kind: 'lan', label: 'LAN' },
      { id: 'inet', kind: 'internet', label: 'Internet' },
      { id: 'gw', kind: 'gateway', label: 'Rental gateway' },
      { id: 'sw', kind: 'switch', label: 'Fabric switch' },
    ],
    nodes: [desk, r1, r2, r3],
    deps: [
      qwen(
        [
          { node: 'desk', from: t0 - 60 * D, base: 48, span: 150, curve: 'desk', share: 1 },
          { node: 'r3', from: r3From, base: 40, span: 120, curve: 'r3', share: 1 },
        ],
        'replicas',
      ),
      dsv('r1', 'r2'),
      wren,
    ],
    links: [
      { id: 'host-lan', a: 'host', b: 'lan', kind: 'lan', speed: '10 GbE', rtt_ms: 0.3, mtu: 1500, state: 'up', note: 'the controller host on the LAN' },
      { id: 'lan-desk', a: 'lan', b: 'desk', kind: 'lan', speed: '10 GbE', rtt_ms: 0.4, mtu: 1500, state: 'up', note: 'mTLS to the node agent :7700' },
      { id: 'host-inet', a: 'host', b: 'inet', kind: 'wan', speed: '1 Gb/s uplink', rtt_ms: 12, mtu: 1500, state: 'up', note: 'the uplink' },
      { id: 'inet-gw', a: 'inet', b: 'gw', kind: 'wan', speed: 'SSH tunnel', rtt_ms: 26, mtu: 1500, state: 'up', note: 'jump host, keepalive 15 s' },
      ...(['r1', 'r2', 'r3'] as const).map((id, i): FleetLink => ({ id: `gw-${id}`, a: 'gw', b: id, kind: 'wan', speed: `tunnel :4701${i + 1}`, rtt_ms: [37.2, 38.1, 38.9][i], mtu: 1500, state: 'up', note: 'mTLS inside the SSH tunnel' })),
      ...(['r1', 'r2', 'r3'] as const).map((id, i): FleetLink => ({
        id: `${id}-sw`, a: id, b: 'sw', kind: 'fabric', speed: '200G', rtt_ms: 0.011, mtu: 9000, state: 'up', note: null,
        test: { gbps: [196, 196, 194][i], at: t0 - [3.4 * H, 6.4 * H, 58 * MIN][i] }, roce: 'on', interfaces: { up: 2, total: 2 }, port: `swp${i + 1}`,
      })),
    ],
    events,
    alerts,
    rules,
  };
}

/** step and the first sample of a range ending at `now` (the end on a 30 s grid, or the step when finer) */
export function window(range: Range, now: number): { step: number; t0: number; count: number } {
  const r = RANGES[range];
  const step = r.span / (r.count - 1);
  const grid = Math.min(step, 30);
  const end = Math.floor(now / grid) * grid;
  return { step, t0: end - r.span, count: r.count };
}

type Reply = { status: number; body: unknown };
const bad = (status: number, type: string, message: string): Reply => ({ status, body: { error: { type, message } } });

export class MockFleet {
  readonly scenario: FleetScenario;
  readonly t0: number;
  private f: Fleet;
  private ph: (key: string) => number;
  private acked = new Map<string, number>();
  private ruleEdits = new Map<string, RulePatch>();
  private layouts = new Map<string, Layout>();
  /** the code window of MOCK_PAIR.address: tries left (5 per code, as the node allows) */
  private triesLeft = 5;
  private audit: TrustAudit[] = [];
  /** drained nodes: since when, by whom, and when each request in flight then ends */
  private drains = new Map<string, { since: number; by: string; ends: number[] }>();
  /** per node, its newest job: an engine's restart, a reboot (the node is off meanwhile), an update */
  private jobs = new Map<string, AgentJob & { action: 'restart' | 'reboot' | 'update'; started_at: number; ends: number }>();
  /** a plan's confirm: single use, five minutes */
  private tokens = new Map<string, { node: string; action: NodeAction; until: number }>();
  private seq = 0;
  /** the Add Spark wizard's Find step (mock/discovery.ts) */
  readonly discovery: MockDiscovery;
  /** the Add Spark wizard's session (mock/addspark.ts) */
  readonly wizard: MockWizard;
  /** the fleet update and the controller's host (mock/fleetops.ts) */
  readonly ops: MockOps;
  /** the deployment records, the placement planner and the weights (mock/deploy.ts, SRV-731) */
  readonly deploy: MockDeploy;
  /** each Spark's Setup Guide through the controller (mock/setup-relay.ts, VIS-252) */
  readonly setup = new MockSetupRelay();
  /** App settings: permissions, the service, the configuration file (mock/appsettings.ts, 6.3b) */
  readonly app: MockApp;

  /** `now` in Unix seconds: the scenario's start */
  constructor(o: { seed?: number; scenario?: FleetScenario; now?: number } = {}) {
    this.scenario = o.scenario ?? 'fleet4';
    this.t0 = Math.floor(o.now ?? Date.now() / 1000);
    this.f = build(this.scenario, this.t0);
    this.discovery = new MockDiscovery({ onMac: this.f.host.may_sleep, host: this.f.host.address, nodes: () => this.f.nodes.map((n) => n.node) });
    this.wizard = new MockWizard(this);
    this.ops = new MockOps(this);
    this.deploy = new MockDeploy(this);
    this.app = new MockApp(this);
    if (this.scenario === 'mixed') this.ops.seed('r3', '0.15.1');
    const seed = o.seed ?? 42;
    this.ph = (key) => hash(`${seed}:${key}`);
    this.audit = this.f.nodes
      .filter((n) => n.node.pairing.state === 'paired')
      .map((n) => ({ t: n.node.pairing.paired_at as number, action: 'pair' as const, node: n.node.id, who: 'dashboard session', via: n.node.pairing.via, fingerprint: n.node.pairing.fingerprint, detail: `paired by ${n.node.pairing.via}` }))
      .sort((a, b) => b.t - a.t);
  }

  // ---- the generators ------------------------------------------------------------------------
  private load(curve: string, t: number): number {
    return clamp(0.12 + 0.6 * dayCurve(t) + 0.3 * wave(this.ph(curve), t), 0.04, 1);
  }
  private alive(n: NodeSim, t: number): boolean {
    return t >= n.node.since && (n.offFrom == null || t < n.offFrom) && !this.rebooting(n.node.id, t);
  }
  private rebooting(id: string, t: number): boolean {
    const j = this.jobs.get(id);
    return !!j && j.action === 'reboot' && t >= j.started_at && t < j.ends;
  }
  /** the share of a drained node's requests at its drain still in flight at t (1 before a drain) */
  private drainLeft(id: string, t: number): number {
    const d = this.drains.get(id);
    if (!d && this.ops.drain(id, t)?.state === 'drained') return 0;
    if (!d || t < d.since) return 1;
    return d.ends.length ? d.ends.filter((e) => e > t).length / d.ends.length : 0;
  }
  private drainView(id: string, now: number): NodeDrain | null {
    const d = this.drains.get(id);
    if (!d) return this.ops.drain(id, now);
    const inflight = d.ends.filter((e) => e > now).length;
    return { state: inflight ? 'draining' : 'drained', since: d.since, by: d.by, inflight };
  }
  private throttled(n: NodeSim, t: number): boolean {
    return n.throttleFrom != null && t >= n.throttleFrom;
  }
  private nodeLoad(n: NodeSim, t: number): number {
    return t < n.busyFrom ? 0.02 : Math.max(0.02, this.load(n.curve, t) * this.drainLeft(n.node.id, t));
  }
  private memberTps(n: NodeSim, m: Member, t: number): number {
    if (t < m.from) return 0;
    return (m.base + m.span * this.load(m.curve, t)) * m.share * (this.throttled(n, t) ? 0.74 : 1);
  }
  private tps(n: NodeSim, t: number, dep?: string): number {
    let v = 0;
    for (const d of this.f.deps) {
      if (dep && d.dep.id !== dep) continue;
      for (const m of d.members) if (m.node === n.node.id) v += this.memberTps(n, m, t);
    }
    return v * this.drainLeft(n.node.id, t);
  }

  /** one node's value of one metric at t (null: no sample); `dep` narrows tok/s and requests to a deployment */
  value(key: MetricKey, nodeId: string, t: number, dep?: string): number | null {
    const n = this.f.nodes.find((x) => x.node.id === nodeId);
    if (!n || !this.alive(n, t)) return null;
    const L = this.nodeLoad(n, t);
    const w = (k: string, speed = 1) => wave(this.ph(nodeId + k), t * speed);
    const hot = this.throttled(n, t);
    switch (key) {
      case 'tps':
        return this.tps(n, t, dep);
      case 'req': {
        const d = this.drains.get(nodeId);
        if (d && t >= d.since) return d.ends.filter((e) => e > t).length;
        return Math.max(0, Math.round(this.tps(n, t, dep) / 31 + 0.4 * w('q')));
      }
      case 'ttft':
        return n.ttft[0] + n.ttft[1] * L + (hot ? 220 : 0);
      case 'gpu':
        return clamp(n.gpuSpan * L + 3 + 2 * w('g', 3), 1, 99);
      case 'power':
        return Math.min(hot ? 152 : 180, 58 + 122 * L + 3 * w('p', 2));
      case 'temp':
        if (n.heatFrom != null && t >= n.heatFrom) return clamp(78 + 8 * clamp((t - n.heatFrom) / 1500, 0, 1) + 0.8 * w('t'), 55, 86.4);
        return 54 + 24 * L + (n.node.id === 'r3' ? 6 : 0);
      case 'sm':
        return Math.round((hot ? 1740 : 2418) * (0.86 + 0.14 * L));
      case 'memclk':
        // an ASUS GX10's NVML reports no memory clock (as the real one does)
        if (/GX10/.test(n.node.dmi)) return null;
        return L < 0.06 ? 3200 : 4266;
      case 'mem': {
        const p = n.plan;
        return (t < n.busyFrom ? n.idleMem : p.weights + p.kv + p.cache + p.other) + 1.6 * L;
      }
      case 'cpu':
        return clamp(6 + 24 * L + 3 * w('c', 2), 1, 100);
      case 'disk':
        return 4 + 220 * Math.max(0, w('d', 4)) ** 3;
      case 'lan':
        return n.node.transport === 'lan' ? 1.4 + 5 * L : 0.5 + 1.8 * L;
      case 'fabric':
        if (!n.fabric) return null;
        return t >= n.busyFrom && n.curve === 'dsv' ? 2 + 9 * L + 0.6 * w('f', 3) : 0.04 + 0.08 * Math.abs(w('f'));
      case 'rtt':
        // the controller on the node itself has no network to time
        if (this.f.host.name === n.node.alias) return null;
        return n.rtt[0] + n.rtt[1] * Math.abs(w('r', 3));
    }
  }

  // ---- state at a time ---------------------------------------------------------------------
  private status(n: NodeSim, now: number): { status: NodeStatus; text: string } {
    if (!this.alive(n, now)) return { status: 'off', text: this.rebooting(n.node.id, now) ? 'rebooting' : 'not reachable' };
    const d = this.drainView(n.node.id, now);
    if (d) return { status: 'draining', text: d.state === 'draining' ? `draining · ${d.inflight} in flight` : 'drained · nothing in flight' };
    if (this.throttled(n, now)) return { status: 'alert', text: `thermal throttle, GPU ${Math.round(this.value('temp', n.node.id, now) ?? 0)} °C` };
    return { status: 'up', text: 'up' };
  }

  nodes(now: number): FleetNode[] {
    return this.f.nodes.filter((n) => n.node.since <= now).map((n) => {
      const s = this.status(n, now);
      return { ...n.node, version: this.ops.version(n.node.id, n.node.version, now), status: s.status, status_text: s.text, drain: this.drainView(n.node.id, now) };
    });
  }

  /** the controller's host as the topology shows it */
  hostInfo(): FleetTopology['host'] {
    return this.f.host;
  }

  /** one line in the event log */
  addEvent(now: number, type: EventType, node: string, text: string, detail: string): void {
    this.f.events.push({ at: now, item: { id: `e-ops-${++this.seq}`, type, severity: null, node, deployment: null, text, detail } });
  }

  /** VIS-254: when the controller's ledger begins: its first Spark joined (Unix seconds) */
  ledgerStart(): number {
    return Math.min(...this.f.nodes.map((n) => n.node.since), this.t0);
  }

  /** VIS-254: where a mock ledger row ran, as the controller's ledger files it: a relayed row (every
   *  seventh, the cloud backend) on no Spark; the others on a member of a deployment that served at
   *  its time (a Split never does), by a hash of its id weighted by the member's base rate (the main
   *  model far above Wren). */
  attribute(id: number, tsMs: number): { node: string | null; deployment: string | null } {
    const none = { node: null, deployment: null };
    if (id % 7 === 0) return none;
    const t = tsMs / 1000;
    const cands = this.f.deps
      .filter((d) => d.dep.type !== 'split')
      .flatMap((d) => d.members.filter((m) => m.from <= t && (this.f.nodes.find((n) => n.node.id === m.node)?.node.since ?? Infinity) <= t).map((m) => ({ node: m.node, deployment: d.dep.id, w: m.base })));
    const total = cands.reduce((a, c) => a + c.w, 0);
    let x = hash(`row-${id}`) * total;
    for (const c of cands) {
      if ((x -= c.w) < 0) return { node: c.node, deployment: c.deployment };
    }
    return none;
  }

  /** the Spark whose engine is the server this page talks to: the scenario's first */
  hereId(): string | null {
    return this.f.nodes[0]?.node.id ?? null;
  }

  deployments(now: number): FleetDeployment[] {
    return this.deploy.decorate(this.baseDeployments(now), this.nodes(now), now);
  }

  /** the scenario's own deployments, as the story has them */
  private baseDeployments(now: number): FleetDeployment[] {
    return this.f.deps.map((d) => {
      const up = d.members.filter((m) => m.from <= now && this.alive(this.f.nodes.find((n) => n.node.id === m.node)!, now)).length;
      const all = d.members.length;
      let status: DeploymentStatus = 'serving';
      let text = d.dep.type === 'replicas' ? `serving · ${up} of ${all} members` : 'serving';
      if (d.dep.type === 'split') {
        status = 'stopped';
        text = 'stopped · not built yet (ENG-307)';
      } else if (up < all) {
        status = 'degraded';
        text = `degraded · ${up} of ${all} members`;
      }
      return { ...d.dep, status, status_text: text };
    });
  }

  topology(now: number): FleetTopology {
    return {
      contract_version: FLEET_CONTRACT,
      host: this.f.host,
      infra: this.f.infra,
      nodes: this.nodes(now),
      links: this.f.links,
      groups: this.deployments(now).map((d) => ({ deployment: d.id, type: d.type, members: d.members })),
    };
  }

  metrics(source: string, metric: MetricKey, range: Range, now: number): MetricSeries {
    const info = METRICS[metric];
    const { step, t0, count } = window(range, now);
    const [kind, id] = source === 'all' ? ['all', ''] : [source.slice(0, source.indexOf(':')), source.slice(source.indexOf(':') + 1)];
    const ids = kind === 'node' ? [id] : kind === 'deployment' ? this.f.deps.find((d) => d.dep.id === id)!.dep.members : this.nodes(now).map((n) => n.id);
    const dep = kind === 'deployment' && (metric === 'tps' || metric === 'req') ? id : undefined;
    const round = (v: number | null) => (v == null ? null : Math.round(v * 1000) / 1000);
    return {
      contract_version: FLEET_CONTRACT,
      source,
      metric,
      unit: info.unit,
      agg: info.agg,
      range,
      step_s: step,
      t0,
      cap: metric === 'mem' ? USABLE_GIB : null,
      series: ids.map((node) => ({ node, values: Array.from({ length: count }, (_, i) => round(this.value(metric, node, t0 + i * step, dep))) })),
    };
  }

  memory(now: number): NodeMemory[] {
    return this.f.nodes
      .filter((n) => this.alive(n, now))
      .map((n) => {
        const idle = now < n.busyFrom;
        const p = idle ? { weights: 0, kv: 0, cache: 0, other: n.idleMem, page: n.plan.page } : n.plan;
        const kv = Math.round((p.kv + (idle ? 0 : 1.6 * this.nodeLoad(n, now))) * 10) / 10;
        return { node: n.node.id, weights: p.weights, kv, cache: p.cache, other: p.other, page: p.page, ceiling: n.plan.ceiling, peak: n.plan.peak, usable: USABLE_GIB };
      });
  }

  events(now: number, f: EventFilter = {}): FleetEvent[] {
    return this.f.events
      .filter((e) => e.at <= now && (f.since == null || e.at > f.since) && (f.before == null || e.at < f.before))
      .map((e) => ({ ...e.item, at: e.at }))
      .filter((e) => (f.node == null || e.node === f.node) && (!f.types?.length || f.types.includes(e.type)))
      .sort((a, b) => b.at - a.at)
      .slice(0, f.limit ?? 200);
  }

  /** a rule with its edits over it; the condition says the edited threshold and hold */
  rule(r: FleetRule): FleetRule {
    const e = this.ruleEdits.get(r.id);
    if (!e) return r;
    const out = { ...r, ...e };
    return { ...out, condition: ruleCondition(out) };
  }

  alerts(now: number): FleetAlert[] {
    const mixed = this.ops.alerts(now, this.nodes(now), this.scenario === 'mixed' ? this.t0 - 8 * MIN : now).map((a) => ({ ...a, acked: this.acked.has(a.id), acked_at: this.acked.get(a.id) ?? null }));
    return [
      ...mixed,
      ...this.f.alerts
        .filter((a) => a.at <= now && (!a.when || a.when(now)))
        .map((a) => ({ ...a.item, since: a.at, acked: this.acked.has(a.item.id), acked_at: this.acked.get(a.item.id) ?? null })),
    ];
  }

  // ---- a node's newest sample and the controller's host ------------------------------------------
  /** What the node's agent read in its newest second: every block a GB10 exposes. Desk (an ASUS
   *  GX10) shows no fan, no GPU on the board exposes a power cap, Desk's ConnectX ports have no
   *  cable; an off node has no sample. */
  sample(id: string, now: number): FleetSample {
    const v = FLEET_CONTRACT;
    const n = this.f.nodes.find((x) => x.node.id === id)!;
    if (!this.alive(n, now)) return { contract_version: v, node: id, via: 'agent', t: null, age_s: null, sample: null, note: 'not reachable' };
    const t = now - 1;
    const val = (k: MetricKey) => this.value(k, id, t);
    const w = (k: string, speed = 1) => wave(this.ph(id + k), t * speed);
    const r1 = (x: number, d = 1) => Math.round(x * 10 ** d) / 10 ** d;
    const hot = this.throttled(n, t);
    const mem = this.memory(now).find((m) => m.node === id)!;
    const used = val('mem')!;
    const cpu = val('cpu')!;
    const rented = !n.node.owned;
    const fab = val('fabric');
    const disk = val('disk')!;
    const lan = val('lan')!;
    const cpuTemp = r1(46 + cpu * 0.6 + (id === 'r3' ? 12 : 0) + w('ct'));
    const sample: NodeSample = {
      node: Object.fromEntries((['gpu', 'power', 'temp', 'sm', 'memclk', 'mem', 'cpu', 'disk', 'lan', 'fabric'] as MetricKey[]).map((k) => [k, val(k)]).filter(([, x]) => x != null).map(([k, x]) => [k, r1(x as number, 3)])),
      gpus: [{ index: 0, name: 'NVIDIA GB10', util: r1(val('gpu')!), sm_mhz: val('sm')!, ...(val('memclk') != null ? { mem_mhz: val('memclk')! } : {}), power_w: r1(val('power')!, 2), temp_c: Math.round(val('temp')!), throttled: hot, throttle: hot ? ['hw_slowdown', 'sw_thermal'] : [], enc: 0, dec: 0 }],
      memory: { total: USABLE_GIB, available: r1(USABLE_GIB - used, 2), used: r1(used, 2), swap_total: 16, swap_used: 0.3, unified: true, weights: mem.weights, kv: mem.kv, cache: mem.cache, other: mem.other, ceiling: mem.ceiling, page: mem.page },
      cpu: {
        util: r1(cpu, 2),
        load: [r1(cpu / 5), r1(cpu / 5.5), r1(cpu / 6)],
        temp_c: cpuTemp,
        cores: Array.from({ length: 20 }, (_, c) => {
          const perf = c % 10 >= 5;
          return { cpu: c, util: r1(clamp(cpu * (perf ? 1.4 : 0.6) + 9 * wave(this.ph(`${id}core${c}`), t * 2), 0, 100)), mhz: perf ? 3900 : 2808, kind: perf ? ('performance' as const) : ('efficiency' as const) };
        }),
      },
      disks: {
        devices: [{ name: 'nvme0n1', nvme: true, read_mbps: r1(disk * 0.7, 3), write_mbps: r1(disk * 0.3, 3), busy: r1(Math.min(1, disk / 400), 3) }],
        fs: [{ path: '/', total_gb: 3936.4, free_gb: rented ? 3204.1 : 2710.3 }, { path: '/boot/efi', total_gb: 0.5, free_gb: 0.5 }],
        nvme: [{ name: 'nvme0', temp_c: r1((id === 'r3' ? 61 : 44) + w('nv')) }],
      },
      net: {
        lan: [{ iface: 'enP7s7', rx_mbps: r1(lan * 0.6, 3), tx_mbps: r1(lan * 0.4, 3), speed_mbps: 10000, state: 'up' }],
        fabric: n.fabric
          ? ['enp1s0f0np0', 'enP2p1s0f1np1'].map((iface, i) => ({ iface, state: 'up', speed_mbps: 200000, mtu: 9000, rx_gbps: r1((fab ?? 0) * (i ? 0.24 : 0.26), 4), tx_gbps: r1((fab ?? 0) * (i ? 0.26 : 0.24), 4), rdma: { device: `rocep${i + 1}s0f${i}`, port: '1', state: 'active', link_layer: 'Ethernet', rx_gbps: r1((fab ?? 0) * 0.23, 4), tx_gbps: r1((fab ?? 0) * 0.23, 4) } }))
          : ['enp1s0f0np0', 'enP2p1s0f1np1'].map((iface) => ({ iface, state: 'down', mtu: 1500 })),
      },
      board: {
        zones: Array.from({ length: 7 }, (_, z) => ({ zone: `thermal_zone${z}`, type: 'acpitz', temp_c: r1(cpuTemp - 2 + 2 * wave(this.ph(`${id}z${z}`), t)) })),
        ...(rented ? { fans: [{ name: 'gb10_fan/fan1', rpm: Math.round(2400 + 3200 * this.nodeLoad(n, t) + (hot ? 900 : 0)) }] } : {}),
        uptime_s: Math.round(now - Math.max(n.node.since, now - 11 * D - 3 * H)),
      },
    };
    return { contract_version: v, node: id, via: 'agent', t, age_s: 1, sample, note: null };
  }

  /** The machine the controller runs on: the Mac in fleet4 (no node, may sleep), the node it
   *  runs on in fleet1 and fleet2-split. */
  host(now: number): FleetHost {
    const h = this.f.host;
    const node = this.f.nodes.find((n) => n.node.alias === h.name)?.node.id ?? null;
    const w = (k: string) => wave(this.ph(`host:${k}`), now);
    const r1 = (x: number) => Math.round(x * 10) / 10;
    return {
      contract_version: FLEET_CONTRACT,
      name: h.name,
      os: node ? 'DGX OS 7.2.1 (Ubuntu 24.04.3 LTS)' : 'macOS 15.6',
      address: h.address,
      node,
      may_sleep: h.may_sleep,
      cpu: node ? { util: r1(this.value('cpu', node, now) ?? 0), cores: 20 } : { util: r1(14 + 6 * w('cpu')), cores: 24 },
      memory: node ? { used_gib: r1(this.value('mem', node, now) ?? 0), total_gib: USABLE_GIB } : { used_gib: r1(41 + 3 * w('mem')), total_gib: 96 },
      controller: { rss_mib: r1(168 + 12 * this.f.nodes.length + 4 * w('rss')), cpu: r1(1.1 + 0.6 * Math.abs(w('ctl'))), threads: 14 + this.f.nodes.length * 3, uptime_s: now - this.t0 + 9 * H },
    };
  }

  // ---- logs ----------------------------------------------------------------------------------
  /** Each node's log: on a 3 s grid a line or none, the same for the same seed and instant, and
   *  what it says follows the node (its deployments, a throttle, the agent's metric push). */
  logs(node: string, level: LogLevel | null, q: string, limit: number, now: number): FleetLogLine[] {
    const rank = { debug: 0, info: 1, warn: 2, error: 3 } as const;
    const nodes = this.f.nodes.filter((n) => node === 'all' || n.node.id === node);
    const lines: FleetLogLine[] = [];
    const end = Math.floor(now / 3) * 3;
    const needle = q.toLowerCase();
    for (let t = end - 30 * MIN; t <= end; t += 3) {
      for (const n of nodes) {
        if (!this.alive(n, t)) continue;
        const r = this.ph(`log:${n.node.id}:${t}`);
        if (r > 0.42) continue;
        const l = this.logLine(n, t, r / 0.42);
        if ((level && rank[l.level] < rank[level]) || (needle && !l.msg.toLowerCase().includes(needle))) continue;
        lines.push(l);
      }
    }
    return lines.slice(-limit);
  }

  private logLine(n: NodeSim, t: number, r: number): FleetLogLine {
    const id = n.node.id;
    const deps = this.f.deps.filter((d) => d.members.some((m) => m.node === id && m.from <= t)).map((d) => d.dep);
    const dep = deps[Math.floor(r * 97) % Math.max(1, deps.length)];
    const tps = Math.round(this.nodeLoad(n, t) * 60);
    const line = (level: LogLevel, source: string, msg: string): FleetLogLine => ({ t, node: id, level, source, msg });
    if (this.throttled(n, t) && r < 0.12) return line('warn', 'agent', 'GPU clocks event: HW slowdown (thermal), SW thermal slowdown');
    if (r < 0.2) return line('debug', 'agent', `metrics push ok, ${58 + Math.floor(r * 40)} series`);
    if (!dep || t < n.busyFrom) return line('info', 'agent', 'idle: no deployment on this node');
    if (r < 0.55) return line('info', 'engine', `${dep.api_id}: request done, ${120 + Math.floor(r * 900)} tok at ${tps} tok/s`);
    if (r < 0.75) return line('info', 'engine', `${dep.api_id}: prefix cache hit ${40 + Math.floor(r * 55)} %`);
    if (r < 0.9) return line('debug', 'gateway', `route ${dep.api_id} -> ${n.node.alias}`);
    if (r < 0.97) return line('info', 'engine', `${dep.api_id}: lane ${1 + Math.floor(r * 4)} started, prompt ${2 + Math.floor(r * 30)}k tokens`);
    return line('error', 'engine', `${dep.api_id}: client closed the stream before the first token`);
  }

  // ---- layouts -------------------------------------------------------------------------------
  defaultLayout(page: string, device: LayoutDevice): Layout {
    const wide = device === 'desk';
    return { page, device, rail: { open: wide, w: 248 }, ctx: { open: wide, w: 340 }, windows: [], docks: { right: { f: 0.38, act: null }, bottom: { f: 0.34, act: null } }, saved_at: null };
  }

  // ---- node trust (SEC-4) ----------------------------------------------------------------------
  private pair(rest: string[], body: unknown, now: number): Reply {
    const v = FLEET_CONTRACT;
    const ssh = rest[0] === 'ssh';
    if (ssh && rest[1] === 'hostkey') {
      const p = body as { host?: unknown; port?: unknown } | null;
      if (!p || typeof p.host !== 'string' || !p.host) return bad(400, 'bad_request', 'the body is {host, port?}');
      if (!SSH_HOSTS.includes(p.host)) return bad(502, 'unreachable', `no SSH server answers at ${p.host}:${p.port ?? 22}`);
      const known = this.f.nodes.some((n) => n.node.address.startsWith(`${p.host}:`)) ? 'same' : 'new';
      return { status: 200, body: { contract_version: v, host: p.host, port: (p.port as number) ?? 22, type: 'ssh-ed25519', fingerprint: MOCK_HOST_KEY, known, stored: known === 'same' ? MOCK_HOST_KEY : null } };
    }
    if (rest.length > (ssh ? 1 : 0)) return bad(404, 'not_found', `no route POST /v1/fleet/pair/${rest.join('/')}`);
    const why = pairError(body, ssh);
    if (why) return bad(400, 'bad_request', why);
    if (ssh) {
      const p = body as PairBySsh;
      if (!SSH_HOSTS.includes(p.host)) return bad(502, 'unreachable', `no SSH server answers at ${p.host}:${p.port ?? 22}`);
      if (p.host_key != null && p.host_key !== MOCK_HOST_KEY) return bad(409, 'host_key_changed', `the host key of ${p.host} is ${MOCK_HOST_KEY}, not the confirmed ${p.host_key}; refused`);
      if (p.tunnel && p.password != null) return bad(400, 'bad_request', 'a node behind a tunnel is reached with a key or the agent for as long as it is in the fleet: give its key file, not a password');
      if (p.host === MOCK_LAN.rented) {
        if (p.key == null && p.key_file == null) return bad(502, 'ssh_failed', `ssh to ${p.host} failed: ${p.user}@${p.host}: Permission denied (publickey).`);
        return bad(409, 'not_installed', `TandemNode is not installed on ${p.host} (NVIDIA DGX Spark, a GB10): the Add Spark wizard's Install step installs it`);
      }
      if (p.key == null && p.key_file == null && p.password !== MOCK_PAIR.password) return bad(502, 'ssh_failed', `ssh to ${p.host} failed: ${p.user}@${p.host}: Permission denied (publickey,password).`);
      if (p.host === MOCK_PAIR.bare) {
        this.discovery.learn(p.host, 'gx10', now * 1000);
        return bad(409, 'not_installed', `TandemNode is not installed on ${p.host} (ASUS Ascent GX10, a GB10): the Add Spark wizard's Install step installs it`);
      }
      if (p.host === MOCK_PAIR.owned) {
        if (!p.takeover) return bad(409, 'owned', `${p.host} is paired with another TandemApp: send takeover: true to move it here (SSH access proves it is yours)`);
        return this.join(p.host, 'ssh', now, { version: '0.14.2', hw: 'ASUS Ascent GX10' });
      }
      return this.join(p.host, 'ssh', now);
    }
    const p = body as { address: string; code: string };
    if (p.address === MOCK_PAIR.owned) return bad(409, 'owned', `${p.address} is paired with another TandemApp: take it over on the node (tandem node takeover) or release it there`);
    if (p.address !== MOCK_PAIR.address || this.f.nodes.some((n) => n.node.address.startsWith(`${p.address}:`))) {
      return bad(502, 'unreachable', `no pairing window answers at ${p.address}:7701 (ConnectionRefusedError); run \`tandem node pair\` on the node`);
    }
    if (this.triesLeft <= 0) return bad(410, 'code_spent', '5 wrong codes: this code is spent; show a new one on the node (tandem node pair)');
    this.triesLeft -= 1;
    if (p.code !== MOCK_PAIR.code) {
      return this.triesLeft > 0 ? bad(403, 'wrong_code', `the code does not match: ${this.triesLeft} tries left`) : bad(403, 'wrong_code', 'the code does not match; 5 wrong codes spent it');
    }
    this.triesLeft = 5;
    return this.join(p.address, 'code', now);
  }

  /** a paired device joins the fleet: an idle Spark with the next free colour */
  private join(address: string, via: 'code' | 'ssh', now: number, o: Partial<Pick<FleetNode, 'version' | 'hw'>> = {}): Reply {
    return { status: 200, body: this.admit(address, via, now, o) };
  }

  /** A node that joins (a pairing, or the wizard's install ending): idle, the next free colour, a LAN
   *  link from the LAN, or for a tunnelled one the rental gateway's tunnel and its lease. */
  admit(address: string, via: 'code' | 'ssh', now: number, o: Partial<Pick<FleetNode, 'version' | 'hw' | 'transport' | 'lease_end' | 'cost_per_hour'>> = {}): FleetNode {
    const used = new Set(this.f.nodes.map((n) => n.node.colour));
    const colour = [1, 2, 3, 4, 5, 6, 7, 8].find((c) => !used.has(c)) ?? 1;
    const k = this.f.nodes.length + 1;
    const id = `n${k}-${address.split('.').pop()}`;
    const tmpl = this.f.nodes[0];
    const tunnel = o.transport === 'tunnel';
    const sim: NodeSim = {
      ...tmpl,
      node: {
        ...tmpl.node, id, alias: `Spark ${k}`, colour, icon: tunnel ? 'cloud' : 'desk', tags: tunnel ? ['rented'] : [], owned: o.lease_end == null, transport: tunnel ? 'tunnel' : 'lan',
        address: tunnel ? `${address}:2201` : `${address}:7700`, version: o.version ?? tmpl.node.version, hw: o.hw ?? tmpl.node.hw, since: now, lease_end: o.lease_end ?? null, cost_per_hour: o.cost_per_hour ?? null,
        inventory: { ...tmpl.node.inventory, serial: `MOCK-${address}` }, pairing: paired(via, now, (k * 29).toString(16).padStart(2, '0')),
      },
      curve: id, busyFrom: Number.POSITIVE_INFINITY, heatFrom: null, throttleFrom: null, offFrom: null, fabric: false,
    };
    this.f.nodes.push(sim);
    const gw = this.f.infra.some((i) => i.id === 'gw') ? 'gw' : this.f.infra.some((i) => i.id === 'inet') ? 'inet' : 'lan';
    this.f.links.push(
      tunnel
        ? { id: `${gw}-${id}`, a: gw, b: id, kind: 'wan', speed: 'SSH tunnel', rtt_ms: 38.4, mtu: 1500, state: 'up', note: 'mTLS inside the SSH tunnel' }
        : { id: `lan-${id}`, a: 'lan', b: id, kind: 'lan', speed: '10 GbE', rtt_ms: 0.4, mtu: 1500, state: 'up', note: 'mTLS to the node agent :7700' },
    );
    this.audit.unshift({ t: now, action: 'pair', node: id, who: 'dashboard session', via, fingerprint: sim.node.pairing.fingerprint, detail: `paired by ${via} at ${address}` });
    this.f.events.push({ at: now, item: { id: `e-pair-${id}`, type: 'node', severity: null, node: id, deployment: null, text: `Spark ${k} paired by ${via}`, detail: tunnel ? 'through an SSH tunnel; certificate pinned' : 'certificate pinned' } });
    return this.nodes(now).find((x) => x.id === id)!;
  }

  /** App settings' import (6.3b): a node the file has and this fleet not, added unpaired at its
   *  address (off until Add Spark pairs it); its colour when no node has it, else the next free. */
  importNode(n: { id: string; alias: string; colour?: number; icon?: FleetNode['icon']; tags?: string[]; address: string }, now: number): void {
    const used = new Set(this.f.nodes.map((x) => x.node.colour));
    const colour = n.colour != null && !used.has(n.colour) ? n.colour : ([1, 2, 3, 4, 5, 6, 7, 8].find((c) => !used.has(c)) ?? 1);
    const tmpl = this.f.nodes[0];
    this.f.nodes.push({
      ...tmpl,
      node: {
        ...tmpl.node, id: n.id, alias: n.alias, colour, icon: n.icon ?? 'desk', tags: n.tags ?? [], owned: true, transport: 'lan', address: `${n.address}:7700`, since: now, lease_end: null, cost_per_hour: null,
        inventory: { ...tmpl.node.inventory, serial: `MOCK-${n.address}` }, pairing: { state: 'unpaired', via: null, fingerprint: null, paired_at: null, host_key: null },
      },
      curve: n.id, busyFrom: Number.POSITIVE_INFINITY, heatFrom: null, throttleFrom: null, offFrom: now, fabric: false,
    });
    this.f.links.push({ id: `lan-${n.id}`, a: 'lan', b: n.id, kind: 'lan', speed: '10 GbE', rtt_ms: null, mtu: 1500, state: 'down', note: 'not paired' });
  }

  /** every saved layout (the export) */
  savedLayouts(): Layout[] {
    return [...this.layouts.values()];
  }

  /** a saved layout dropped: the page's default again (an import that replaces) */
  dropLayout(page: string, device: LayoutDevice): void {
    this.layouts.delete(`${page}|${device}`);
  }

  /** a rule back to its seed (an import that replaces) */
  resetRule(id: string): void {
    this.ruleEdits.delete(id);
  }

  /** the lease rule's hours as the scenario has it */
  leaseHours(): number {
    const r = this.f.rules.find((x) => x.id === 'lease');
    return r ? (this.rule(r).threshold ?? 24) : 24;
  }

  /** a released node leaves the fleet, its deployments' members and its links */
  private forget(id: string): void {
    this.f.nodes = this.f.nodes.filter((n) => n.node.id !== id);
    // a Split that loses a stage stops: it needs every stage
    this.f.deps = this.f.deps.filter((d) => !(d.dep.type === 'split' && d.dep.members.includes(id)));
    for (const d of this.f.deps) {
      d.members = d.members.filter((m) => m.node !== id);
      d.dep = { ...d.dep, members: d.dep.members.filter((m) => m !== id) };
    }
    this.f.deps = this.f.deps.filter((d) => d.members.length > 0);
    this.f.links = this.f.links.filter((l) => l.a !== id && l.b !== id);
    this.drains.delete(id);
    this.jobs.delete(id);
  }

  // ---- node actions (VIS-250) --------------------------------------------------------------------
  /** fleet1 is today's box: its engine and its machine are not TandemNode's to restart or reboot */
  private get managed(): boolean {
    return this.scenario !== 'fleet1';
  }
  private sim(id: string): NodeSim | undefined {
    return this.f.nodes.find((x) => x.node.id === id);
  }
  private job(id: string, action: 'restart' | 'reboot' | 'update', now: number): AgentJob | null {
    const j = this.jobs.get(id);
    if (!j || j.action !== action) return null;
    const { ends, ...rest } = j;
    if (now < ends) return { ...rest, state: 'running', ended_at: null, message: null };
    return { ...rest, state: 'done', ended_at: ends, message: action === 'update' ? 'TandemLLM 0.15.0 is the newest release: nothing to update' : action === 'reboot' ? `up again after ${ends - j.started_at} s` : `up after ${ends - j.started_at} s` };
  }
  private inflight(id: string, now: number): number {
    const d = this.drains.get(id);
    return d ? d.ends.filter((e) => e > now).length : Math.max(0, Math.round(this.value('req', id, now) ?? 0));
  }
  /** what an action stops on the node: each deployment it serves, the requests in flight */
  private stops(p: ActionPlan, n: NodeSim, now: number, what: string): void {
    const a = n.node.alias;
    const deps = this.deployments(now).filter((d) => d.members.includes(n.node.id));
    p.impact.push(...(deps.length ? deps.map((d) => (d.type === 'split' ? `${d.name} stops on ${a} ${what}: a Split needs every stage` : d.type === 'replicas' ? `${d.name} keeps serving on ${d.members.filter((m) => m !== n.node.id).map((m) => this.sim(m)?.node.alias ?? m).join(', ')}; on ${a} it stops ${what}` : `${d.name} stops on ${a} ${what}`)) : [`${a} serves nothing through this gateway now`]));
    const k = this.inflight(n.node.id, now);
    if (k) p.impact.push(`${k} request${k === 1 ? '' : 's'} in flight on ${a} are cut off: drain it first to let them finish`);
  }

  plan(id: string, action: NodeAction, now: number, engine?: string | null): ActionPlan {
    const n = this.sim(id)!;
    const a = n.node.alias;
    const p: ActionPlan = { contract_version: FLEET_CONTRACT, node: id, action, title: '', allowed: true, blocked: null, destructive: !['drain', 'undrain'].includes(action), impact: [], removes: [], keeps: [], engines: [], job: null, confirm: null, expires_at: null };
    const block = (why: string) => Object.assign(p, { allowed: false, blocked: why });
    const local = n.node.pairing.state === 'local';
    const deps = this.deployments(now).filter((d) => d.members.includes(id));
    const off = !this.alive(n, now);
    switch (action) {
      case 'drain': {
        p.title = `Drain ${a}`;
        if (this.drains.has(id)) block(`${a} is drained already: Undrain ends it`);
        const k = this.inflight(id, now);
        p.impact.push(k ? `${k} request${k === 1 ? '' : 's'} in flight on ${a} finish` : `nothing is in flight on ${a}: it is drained at once`);
        for (const d of deps) {
          const others = d.type === 'replicas' ? d.members.filter((m) => m !== id && !this.drains.has(m) && this.alive(this.sim(m)!, now)) : [];
          p.impact.push(others.length ? `${d.name}: new requests go to ${others.map((m) => this.sim(m)!.node.alias).join(', ')}, its other replica` : `${d.name}: new requests wait, the gateway answers 503 with Retry-After 30 s until ${a} takes them again`);
        }
        p.keeps.push(`everything on ${a} keeps running: only new requests through this gateway stop; a client that calls an engine's port directly is not turned away`, 'the drain lasts until Undrain, across a restart of the controller');
        break;
      }
      case 'undrain':
        p.title = `Undrain ${a}`;
        if (!this.drains.has(id)) block(`${a} is not drained`);
        p.impact.push(`new requests go to ${a} again`);
        break;
      case 'restart-engine': {
        p.title = `Restart the engine on ${a}`;
        if (off) {
          block(this.status(n, now).text);
          break;
        }
        const j = this.job(id, 'restart', now);
        p.engines = [{ id: 'engine', kind: 'tandemllm', port: 8000, managed: this.managed, state: j?.state === 'running' ? 'restarting' : 'up', job: j }];
        if (engine && engine !== 'engine') {
          block(`${a} has no engine ${engine}`);
          break;
        }
        p.job = j;
        if (!this.managed) {
          block('this engine (engine, port 8000) is not managed by TandemNode: it was started outside TandemNode, so TandemNode never stops or starts it');
          break;
        }
        this.stops(p, n, now, 'until the engine answers its health check again');
        p.keeps.push('the weights stay on the disk; a warm page cache makes the start short');
        break;
      }
      case 'reboot':
      case 'update': {
        p.title = `${action === 'reboot' ? 'Reboot' : 'Update'} ${a}`;
        if (off) {
          block(this.status(n, now).text);
          break;
        }
        p.job = this.job(id, action, now);
        if (!this.managed) {
          block(action === 'reboot' ? "reboot is not set up on this node: add a reboot command to node.json in the agent's state directory (it needs a right to reboot: a sudoers or polkit rule for the agent's user)" : "update is not set up on this node: add an update command to node.json in the agent's state directory");
          break;
        }
        if (action === 'update') p.impact.push(`${a} runs TandemLLM ${n.node.version}; this controller runs 0.15.0`);
        this.stops(p, n, now, action === 'reboot' ? 'until it is up again' : 'while its engine restarts with the update');
        if (action === 'reboot') p.impact.push(`${a} does not answer while it starts: a few minutes${n.node.owned ? '' : '; a rented node reboots through its provider and the tunnel finds it again by its id'}`);
        p.keeps.push(action === 'reboot' ? `the record, the pairing and the drain: ${a} rejoins by its id` : 'the weights, the record and the pairing');
        break;
      }
      case 'forget': {
        p.title = `Forget ${a}`;
        if (local) {
          block(`${a} is the machine this controller runs on: it is its own node and cannot be forgotten`);
          break;
        }
        p.removes.push(`this controller's pin of ${a}'s certificate, and ${a}'s pin of this controller (it can be paired again: tandem node pair)`, `${a}'s record here: alias, colour, icon, tags and the drain`, `${a}'s metric history on this controller`);
        p.keeps.push(`TandemNode stays installed and running on ${a}, without an owner`, `its engines keep running${deps.length ? `: ${deps.map((d) => d.name).join(', ')}` : ''}`, `weights and caches stay on ${a}'s disk`, `the event log's lines about ${a}`);
        if (off) p.impact.push(`${a} does not answer: it is forgotten here only, and keeps its pin until \`tandem node reset\` on it`);
        break;
      }
      case 'release-lease': {
        p.title = `Release ${a}`;
        if (n.node.lease_end == null) {
          block(`${a} is not rented: Forget removes an owned Spark`);
          break;
        }
        const mem = this.memory(now).find((m) => m.node === id);
        p.removes.push(`the weights and caches on ${a}, wiped${mem ? ` (${Math.round((mem.weights + mem.cache) * 10) / 10} GiB)` : ''}`, `the node certificate, this controller's pin and the app's SSH key on ${a}`, `TandemNode, uninstalled from ${a}`, `${a}'s record and its history here`);
        p.keeps.push(`nothing of TandemApp stays on ${a}; the lease ends with the provider${n.node.cost_per_hour ? ` (${n.node.cost_per_hour.toFixed(2)} an hour until then)` : ''}`);
        this.stops(p, n, now, 'for good');
        break;
      }
    }
    if (p.allowed && p.destructive) {
      const token = `mock-confirm-${++this.seq}-${id}`;
      this.tokens.set(token, { node: id, action, until: now + 300 });
      Object.assign(p, { confirm: token, expires_at: now + 300 });
    }
    return p;
  }

  private act(id: string, action: NodeAction, body: unknown, now: number): Reply {
    const b = (body ?? {}) as { confirm?: unknown; engine?: unknown };
    if (typeof b !== 'object' || Array.isArray(b) || Object.keys(b).some((k) => k !== 'confirm' && k !== 'engine')) return bad(400, 'bad_request', 'the body is {confirm?, engine?}');
    const p = this.plan(id, action, now, typeof b.engine === 'string' ? b.engine : null);
    if (p.confirm) this.tokens.delete(p.confirm);
    if (!p.allowed) {
      const why = p.blocked ?? '';
      const kind = action === 'release-lease' ? 'not_rented' : /not managed|not set up/.test(why) ? 'not_managed' : 'conflict';
      return bad(409, kind, why);
    }
    if (p.destructive) {
      if (!b.confirm) return bad(428, 'confirm_required', `${action} needs its plan's confirm: GET the plan (/v1/fleet/nodes/${id}/actions/${action}) and send its \`confirm\``);
      const t = typeof b.confirm === 'string' ? this.tokens.get(b.confirm) : undefined;
      if (typeof b.confirm === 'string') this.tokens.delete(b.confirm);
      if (!t || t.node !== id || t.action !== action || t.until <= now) return bad(409, 'confirm_invalid', "that confirm is spent, too old or another action's: read the plan again");
    }
    const n = this.sim(id)!;
    const a = n.node.alias;
    const ev = (type: EventType, text: string, detail: string, at = now) => this.f.events.push({ at, item: { id: `e-${action}-${id}-${++this.seq}`, type, severity: null, node: id, deployment: null, text, detail } });
    const out: ActionResult = { contract_version: FLEET_CONTRACT, node: id, action, state: 'done', text: '', job: null, at: now };
    switch (action) {
      case 'drain': {
        const k = this.inflight(id, now);
        const ends = Array.from({ length: k }, (_, i) => now + 4 * (i + 1));
        this.drains.set(id, { since: now, by: 'admin', ends });
        ev('node', `${a} drains: no new requests`, `${k} request${k === 1 ? '' : 's'} in flight · by admin`);
        this.f.events.push({ at: ends.at(-1) ?? now, item: { id: `e-drained-${id}-${++this.seq}`, type: 'node', severity: null, node: id, deployment: null, text: `${a} is drained`, detail: 'nothing in flight: it can be updated or rebooted' } });
        out.text = `${a} takes no new requests; ${k} request${k === 1 ? '' : 's'} in flight finish`;
        break;
      }
      case 'undrain':
        this.drains.delete(id);
        this.f.events = this.f.events.filter((e) => !(e.item.id.startsWith(`e-drained-${id}-`) && e.at > now));
        ev('node', `${a} takes new requests again`, 'by admin');
        out.text = `${a} takes new requests again`;
        break;
      case 'restart-engine':
      case 'reboot':
      case 'update': {
        const kind = action === 'restart-engine' ? 'restart' : action;
        const span = kind === 'reboot' ? 20 : kind === 'update' ? 8 : 6;
        this.jobs.set(id, { id: `${kind}-${now}`, action: kind, who: 'owner', simulated: false, started_at: now, ends: now + span, rc: null });
        const what = kind === 'restart' ? 'the engine engine restarts' : `${kind}s`;
        ev(kind === 'update' ? 'update' : kind === 'restart' ? 'engine' : 'node', `${a}: ${what}`, 'by admin');
        if (kind === 'reboot') ev('node', `${a} is up again`, `after ${span} s`, now + span);
        out.state = 'running';
        out.job = this.job(id, kind, now);
        out.text = kind === 'restart' ? `on ${a}, ${what}` : `${a} ${what}`;
        break;
      }
      case 'forget':
      case 'release-lease': {
        const rel = action === 'release-lease';
        this.forget(id);
        this.audit.unshift({ t: now, action: 'release', node: id, who: 'dashboard session', via: n.node.pairing.via, fingerprint: n.node.pairing.fingerprint, detail: rel ? `${a} released from its lease: wiped and uninstalled` : `${a} released (node side: unpinned)` });
        this.f.events.push({ at: now, item: { id: `e-${action}-${id}-${++this.seq}`, type: 'node', severity: null, node: 'all', deployment: null, text: rel ? `${a} released: weights and caches wiped, keys removed, TandemNode uninstalled` : `${a} released`, detail: rel ? 'it left the fleet; the lease ends with the provider' : 'node side: unpinned' } });
        out.text = rel ? `${a} released: weights and caches wiped, keys removed, TandemNode uninstalled; it left the fleet` : `${a} forgotten; its side: unpinned`;
        break;
      }
    }
    return { status: 200, body: out };
  }

  /** a link test: a fabric link over its port, a tunnel over the internet, a LAN link to the agent */
  private testLink(lid: string, now: number): Reply {
    const l = this.f.links.find((x) => x.id === lid);
    if (!l) return bad(404, 'not_found', `no link ${lid}`);
    const node = [l.a, l.b].map((x) => this.sim(x)).find(Boolean);
    if (!node || (l.kind === 'wan' && !l.a.startsWith('gw'))) return bad(409, 'no_agent', 'only a link to a node\'s agent is tested from here: the internet link has no agent at its far end');
    if (this.f.host.name === node.node.alias && l.kind === 'lan') return bad(409, 'no_wire', `the controller runs on ${node.node.alias}: there is no wire between them to test`);
    if (!this.alive(node, now)) return bad(409, 'unreachable', `${node.node.alias} does not answer: not reachable`);
    const w = Math.abs(wave(this.ph(`lt:${lid}`), now));
    const test =
      l.kind === 'fabric'
        ? { gbps: Math.round((193 + 4 * w) * 10) / 10, at: now, rtt_ms: 0.011, note: 'RoCE on, both logical interfaces up · bandwidth over RDMA' }
        : l.kind === 'wan'
          ? { gbps: Math.round((0.38 + 0.1 * w) * 100) / 100, at: now, rtt_ms: Math.round((37 + 2 * w) * 10) / 10, note: 'through the SSH tunnel; bandwidth is TCP through the agent\'s mTLS: a floor of what the link carries' }
          : { gbps: Math.round((9.1 + 0.4 * w) * 100) / 100, at: now, rtt_ms: Math.round((0.38 + 0.1 * w) * 1000) / 1000, note: 'bandwidth is TCP through the agent\'s mTLS: a floor of what the link carries' };
    l.test = test;
    const peer = l.kind === 'fabric' ? (this.f.infra.find((i) => i.id === l.b)?.label ?? this.sim(l.b)?.node.alias ?? l.b) : 'controller';
    this.f.events.push({ at: now, item: { id: `e-lt-${lid}-${++this.seq}`, type: 'link', severity: null, node: node.node.id, deployment: null, text: `Link test ${node.node.alias} ↔ ${peer}: ${test.gbps} Gb/s, RTT ${test.rtt_ms} ms`, detail: `${test.note} · by admin` } });
    return { status: 200, body: { contract_version: FLEET_CONTRACT, link: l, result: { rtt: { n: 10, min_ms: test.rtt_ms * 0.9, median_ms: test.rtt_ms, p90_ms: test.rtt_ms * 1.2 }, bandwidth: { gbps: test.gbps, bytes: 67108864, streams: 4, s: Math.round((0.537 / test.gbps) * 1000) / 1000 }, notes: [test.note] } } };
  }

  // ---- the routes ----------------------------------------------------------------------------
  handle(method: string, path: string, q: URLSearchParams, body: unknown, nowMs = Date.now()): Reply {
    const now = Math.floor(nowMs / 1000);
    const parts = path.split('/').slice(3); // /v1/fleet/<parts>
    const v = FLEET_CONTRACT;
    if (parts[0] === 'discovery') return this.discovery.handle(method, parts, q, body, nowMs);
    if (parts[0] === 'add') return this.wizard.handle(method, parts, body, nowMs);
    if (parts[0] === 'update') return this.ops.handleUpdate(method, parts, q, body, now);
    if (parts[0] === 'controller') return this.ops.handleController(method, parts, q, body, now);
    if (parts[0] === 'app') return this.app.handle(method, parts, body, now);
    if (parts[0] === 'placement' || parts[0] === 'weights' || (parts[0] === 'deployments' && (parts.length > 1 || method !== 'GET'))) return this.deploy.handle(method, parts, q, body, now);
    if (method === 'GET' && parts[0] === 'nodes' && parts[2] === 'setup-confirm' && parts.length >= 4) return this.setup.plan(this, decodeURIComponent(parts[1]), parts.slice(3).map(decodeURIComponent).join('/'), q, now);
    if (method === 'GET' && parts.length === 1) {
      switch (parts[0]) {
        case 'nodes':
          return { status: 200, body: { contract_version: v, nodes: this.nodes(now) } };
        case 'links':
          return { status: 200, body: { contract_version: v, links: this.f.links } };
        case 'topology':
          return { status: 200, body: this.topology(now) };
        case 'deployments':
          return { status: 200, body: { contract_version: v, deployments: this.deployments(now) } };
        case 'memory':
          return { status: 200, body: { contract_version: v, unit: 'GiB', nodes: this.memory(now) } };
        case 'host':
          return { status: 200, body: this.host(now) };
        case 'alerts':
          return { status: 200, body: { contract_version: v, alerts: this.alerts(now) } };
        case 'rules':
          return { status: 200, body: { contract_version: v, rules: this.f.rules.map((r) => this.rule(r)) } };
        case 'logs': {
          const node = q.get('node') ?? 'all';
          const level = q.get('level') ?? 'info';
          const limit = Number(q.get('limit') ?? 200);
          if (node !== 'all' && !this.f.nodes.some((n) => n.node.id === node)) return bad(404, 'not_found', `no node ${node}`);
          if (!['debug', 'info', 'warn', 'error'].includes(level)) return bad(400, 'bad_request', `level is debug, info, warn or error, not "${level}"`);
          if (!Number.isInteger(limit) || limit < 1 || limit > 1000) return bad(400, 'bad_request', 'limit is a whole number from 1 to 1000');
          return { status: 200, body: { contract_version: v, node, lines: this.logs(node, level as LogLevel | null, q.get('q') ?? '', limit, now) } };
        }
        case 'events': {
          const f: EventFilter = {};
          for (const k of ['since', 'before'] as const) {
            const s = q.get(k);
            if (s != null && !Number.isFinite(Number(s))) return bad(400, 'bad_request', `${k} is a Unix time in seconds, not "${s}"`);
            if (s != null) f[k] = Number(s);
          }
          const types = (q.get('type') ?? '').split(',').filter(Boolean);
          const wrong = types.find((t) => !(EVENT_TYPES as readonly string[]).includes(t));
          if (wrong) return bad(400, 'bad_request', `type is one or more of ${EVENT_TYPES.join(', ')}, not "${wrong}"`);
          const limit = Number(q.get('limit') ?? 200);
          if (!Number.isInteger(limit) || limit < 1 || limit > 1000) return bad(400, 'bad_request', 'limit is a whole number from 1 to 1000');
          return { status: 200, body: { contract_version: v, events: this.events(now, { ...f, node: q.get('node') || undefined, types: types as EventType[], limit }) } };
        }
        case 'metrics': {
          const metric = q.get('metric') ?? '';
          const range = q.get('range') ?? '6h';
          const source = q.get('source') ?? 'all';
          if (!(metric in METRICS)) return bad(400, 'bad_request', `unknown metric "${metric}" (one of ${Object.keys(METRICS).join(', ')})`);
          if (!(range in RANGES)) return bad(400, 'bad_request', `unknown range "${range}" (one of ${Object.keys(RANGES).join(', ')})`);
          const m = /^(node|deployment):(.+)$/.exec(source);
          if (source !== 'all' && !m) return bad(400, 'bad_request', `source is all, node:<id> or deployment:<id>, not "${source}"`);
          if (m && m[1] === 'node' && !this.f.nodes.some((n) => n.node.id === m[2])) return bad(404, 'not_found', `no node ${m[2]}`);
          if (m && m[1] === 'deployment' && !this.f.deps.some((d) => d.dep.id === m[2])) return bad(404, 'not_found', `no deployment ${m[2]}`);
          return { status: 200, body: this.metrics(source, metric as MetricKey, range as Range, now) };
        }
      }
    }
    if (method === 'GET' && parts.length === 3 && parts[0] === 'nodes' && parts[2] === 'sample') {
      const id = decodeURIComponent(parts[1]);
      if (!this.f.nodes.some((x) => x.node.id === id && x.node.since <= now)) return bad(404, 'not_found', `no node ${id}`);
      return { status: 200, body: this.sample(id, now) };
    }
    if (method === 'GET' && parts.length === 1 && parts[0] === 'trust') {
      return { status: 200, body: { contract_version: v, controller: { id: 'c-3dmock0c0n7r', name: `TandemApp on ${this.f.host.name}`, fingerprint: '7c'.repeat(32), since: this.t0 - 60 * D }, audit: this.audit } };
    }
    if (method === 'POST' && parts[0] === 'pair') return this.pair(parts.slice(1), body, now);
    if (parts.length === 4 && parts[0] === 'nodes' && parts[2] === 'actions' && (method === 'GET' || method === 'POST')) {
      const id = decodeURIComponent(parts[1]);
      if (!this.f.nodes.some((x) => x.node.id === id && x.node.since <= now)) return bad(404, 'not_found', `no node ${id}`);
      const action = parts[3] as NodeAction;
      if (!(NODE_ACTIONS as readonly string[]).includes(action)) return bad(404, 'not_found', `no action ${action} (one of ${NODE_ACTIONS.join(', ')})`);
      return method === 'GET' ? { status: 200, body: this.plan(id, action, now, q.get('engine')) } : this.act(id, action, body, now);
    }
    if (method === 'POST' && parts.length === 3 && parts[0] === 'links' && parts[2] === 'test') return this.testLink(decodeURIComponent(parts[1]), now);
    if (method === 'DELETE' && parts.length === 2 && parts[0] === 'nodes') {
      const id = decodeURIComponent(parts[1]);
      const n = this.f.nodes.find((x) => x.node.id === id && x.node.since <= now);
      if (!n) return bad(404, 'not_found', `no node ${id}`);
      if (n.node.pairing.state === 'local') return bad(409, 'conflict', "this machine is the controller's own node; there is nothing to release");
      this.forget(id);
      this.audit.unshift({ t: now, action: 'release', node: id, who: 'dashboard session', via: n.node.pairing.via, fingerprint: n.node.pairing.fingerprint, detail: `${n.node.alias} released (node side: unpinned)` });
      return { status: 200, body: { contract_version: v, id, node: n.node.pairing.state === 'paired' ? 'unpinned' : 'not_paired' } };
    }
    if (parts.length === 2 && parts[0] === 'nodes' && (method === 'GET' || method === 'PATCH')) {
      const id = decodeURIComponent(parts[1]);
      const n = this.f.nodes.find((x) => x.node.id === id && x.node.since <= now);
      if (!n) return bad(404, 'not_found', `no node ${id}`);
      if (method === 'PATCH') {
        const others = this.f.nodes.filter((x) => x !== n).map((x) => x.node);
        const why = nodePatchError(body, others);
        if (why) return bad(400, 'bad_request', why);
        const p = body as NodePatch;
        n.node = { ...n.node, ...p, ...(p.alias != null ? { alias: p.alias.trim() } : {}), ...(p.tags ? { tags: [...new Set(p.tags.map((t) => t.trim()))] } : {}) };
      }
      return { status: 200, body: this.nodes(now).find((x) => x.id === id) };
    }
    if (method === 'POST' && parts.length === 3 && parts[0] === 'alerts' && parts[2] === 'ack') {
      const id = decodeURIComponent(parts[1]);
      if (!this.alerts(now).some((a) => a.id === id)) return bad(404, 'not_found', `no active alert ${id}`);
      if (!this.acked.has(id)) {
        this.acked.set(id, now);
        const a = this.alerts(now).find((x) => x.id === id)!;
        this.f.events.push({ at: now, item: { id: `e-ack-${id}`, type: 'alert', severity: null, node: a.node, deployment: null, text: `Acknowledged: ${a.title}`, detail: 'by admin' } });
      }
      return { status: 200, body: this.alerts(now).find((a) => a.id === id) };
    }
    if (method === 'PATCH' && parts.length === 2 && parts[0] === 'rules') {
      const id = decodeURIComponent(parts[1]);
      const r = this.f.rules.find((x) => x.id === id);
      if (!r) return bad(404, 'not_found', `no rule ${id}`);
      const why = rulePatchError(body, r, this.f.nodes.map((n) => n.node.id));
      if (why) return bad(400, 'bad_request', why);
      const p = body as RulePatch;
      this.ruleEdits.set(id, { ...this.ruleEdits.get(id), ...p, ...(p.notify ? { notify: [...new Set(p.notify)] } : {}) });
      return { status: 200, body: this.rule(r) };
    }
    if (parts.length === 2 && parts[0] === 'layouts') {
      const page = decodeURIComponent(parts[1]);
      const device = (q.get('device') ?? 'desk') as LayoutDevice;
      if (!['desk', 'tablet', 'phone'].includes(device)) return bad(400, 'bad_request', `device is desk, tablet or phone, not "${device}"`);
      const key = `${page}|${device}`;
      if (method === 'GET') return { status: 200, body: this.layouts.get(key) ?? this.defaultLayout(page, device) };
      if (method === 'PUT') {
        const why = layoutError(body, page, device);
        if (why) return bad(400, 'bad_request', why);
        const saved = { ...(body as Layout), saved_at: now };
        this.layouts.set(key, saved);
        return { status: 200, body: saved };
      }
    }
    return bad(404, 'not_found', `no route ${method} ${path}`);
  }
}

/** what is wrong with a pairing body (null: nothing), as the controller says it (proxy/fleettrust.py) */
export function pairError(b: unknown, ssh: boolean): string | null {
  const p = b as Record<string, unknown> | null;
  if (!p || typeof p !== 'object' || Array.isArray(p)) return ssh ? 'the body is {host, user, port?, password? | key?, host_key?, takeover?}' : 'the body is {address, code, port?}';
  const allowed = ssh ? ['host', 'user', 'port', 'password', 'key', 'key_file', 'jumps', 'tunnel', 'host_key', 'takeover'] : ['address', 'code', 'port'];
  if (Object.keys(p).some((k) => !allowed.includes(k))) return ssh ? 'the body is {host, user, port?, password? | key? | key_file?, jumps?, tunnel?, host_key?, takeover?}' : 'the body is {address, code, port?}';
  if ('port' in p && (typeof p.port !== 'number' || !Number.isInteger(p.port) || p.port < 1 || p.port > 65535)) return 'port is a whole number from 1 to 65535';
  if (ssh) {
    if (typeof p.host !== 'string' || !p.host || /[\s@/'"`$;|&<>]/.test(p.host) || p.host.startsWith('-')) return 'host is an address or a host name';
    if (typeof p.user !== 'string' || !/^[A-Za-z0-9._-]{1,64}$/.test(p.user) || p.user.startsWith('-')) return 'user is a login name';
    if ([p.password, p.key, p.key_file].filter((x) => x != null).length > 1) return 'a password, a key or a key file, not two';
    return null;
  }
  if (typeof p.address !== 'string' || !/^[0-9a-fA-F.:]+$/.test(p.address)) return `address is an IP address (the node's LAN or fabric address), not "${String(p.address)}"`;
  if (typeof p.code !== 'string' || !/^[0-9]{6}$/.test(p.code)) return 'the code is 6 digits';
  return null;
}

/** what is wrong with a rule's PATCH body (null: nothing), as the controller says it (proxy/fleetwatch.py) */
export function rulePatchError(b: unknown, r: FleetRule, nodes: string[]): string | null {
  const p = b as Record<string, unknown> | null;
  if (!p || typeof p !== 'object' || Array.isArray(p) || !Object.keys(p).length) return 'the body is an object with enabled, notify, threshold, for_s or scope';
  const extra = Object.keys(p).find((k) => !['enabled', 'notify', 'threshold', 'for_s', 'scope'].includes(k));
  if (extra) return `rule: unknown field '${extra}'`;
  if ('enabled' in p && typeof p.enabled !== 'boolean') return 'enabled is true or false';
  if ('notify' in p && (!Array.isArray(p.notify) || !p.notify.every((x) => x === 'app' || x === 'system'))) return 'notify is a list of app and system';
  if ('threshold' in p) {
    if (r.threshold == null) return `${r.id} has no threshold`;
    if (typeof p.threshold !== 'number' || !Number.isFinite(p.threshold)) return 'threshold is a number';
  }
  if ('for_s' in p && (typeof p.for_s !== 'number' || !(p.for_s >= 0 && p.for_s <= 86400))) return 'for_s is a number of seconds from 0 to 86400';
  if ('scope' in p) {
    if (r.scope === 'host') return `${r.id} is about the controller's machine; its scope stays`;
    if (p.scope !== 'all' && !(typeof p.scope === 'string' && p.scope.startsWith('node:') && nodes.includes(p.scope.slice(5)))) return 'scope is all or node:<id> of a node in the fleet';
  }
  return null;
}

/** what is wrong with a node's PATCH body (null: nothing), as the controller says it (proxy/fleet.py) */
export function nodePatchError(b: unknown, others: Pick<FleetNode, 'alias' | 'colour'>[]): string | null {
  const p = b as Record<string, unknown> | null;
  if (!p || typeof p !== 'object' || Array.isArray(p) || !Object.keys(p).length) return 'the body is an object with alias, colour, icon or tags';
  const extra = Object.keys(p).find((k) => !['alias', 'colour', 'icon', 'tags'].includes(k));
  if (extra) return `node: unknown field '${extra}'`;
  if ('alias' in p) {
    const a = typeof p.alias === 'string' ? p.alias.trim() : '';
    if (!a || a.length > 40 || [...a].some((ch) => ch.charCodeAt(0) < 32)) return 'alias is 1 to 40 characters';
    if (others.some((o) => o.alias.toLowerCase() === a.toLowerCase())) return `another node is called ${a}`;
  }
  if ('colour' in p) {
    const c = p.colour;
    if (typeof c !== 'number' || !Number.isInteger(c) || c < 1 || c > 8) return 'colour is a whole number from 1 to 8';
    const held = others.find((o) => o.colour === c);
    if (held) return `colour ${c} is ${held.alias}'s`;
  }
  if ('icon' in p && !['desk', 'cloud', 'rack'].includes(p.icon as string)) return 'icon is desk, cloud or rack';
  if ('tags' in p && (!Array.isArray(p.tags) || p.tags.length > 16 || !p.tags.every((t) => typeof t === 'string' && t.trim().length > 0 && t.trim().length <= 32)))
    return 'tags is a list of at most 16 words of 1 to 32 characters';
  return null;
}

/** what is wrong with a layout body (null: nothing); the schema says the rest */
export function layoutError(b: unknown, page: string, device: LayoutDevice): string | null {
  const l = b as Partial<Layout> | null;
  if (!l || typeof l !== 'object') return 'the body is a layout object';
  if (l.page !== page || l.device !== device) return `the body is the layout of ${page} on ${device}`;
  if (!l.rail || !l.ctx || !l.docks || !Array.isArray(l.windows)) return 'rail, ctx, docks and windows are required';
  for (const w of l.windows) {
    if (!(WINDOW_KINDS as readonly string[]).includes(w?.kind)) return `unknown window kind "${w?.kind}"`;
    if (!['right', 'bottom', 'float', 'min'].includes(w.at)) return `window ${w.id}: at is right, bottom, float or min`;
    if (w.prev != null && !['right', 'bottom', 'float'].includes(w.prev)) return `window ${w.id}: prev is right, bottom or float`;
  }
  return null;
}
