// The fleet in the mock (`/v1/fleet/*`, docs/contract/fleet-v1, VIS-244): Sparks, links, deployments,
// per-node metric series, memory, events, alerts, rules, each node's log and window layouts, as TandemApp's
// controller will answer them. Every metric is a function of (seed, node, time): the same seed gives
// the same answer at the same instant, and the values move as the clock moves. Ranges are what a
// GB10 shows: up to ~180 W a box, 54-86 °C, 121.6 GiB of usable unified memory, SM clock to 2.4 GHz.
//
// Scenarios (mock/server.ts picks one per request: the `qse_fleet` cookie that `?fleet=<name>` on the
// page sets, else `POST /__mock/fleet {scenario}`, else TANDEM_MOCK_FLEET, else fleet4):
//   fleet1        one Spark serving the main model and Wren, the controller on it: today's box
//   fleet4        Desk on the LAN (Replicas with R-3, and Wren as a Role), R-1 + R-2 rented and Split
//                 over a fabric switch, R-3 rented and throttling (a thermal alert since 14 min)
//   fleet2-split  two Sparks on the LAN with one direct fabric cable, one model Split over both
//   node-down     fleet4, and R-3 stops answering 30 s after the scenario starts
//   alert-thermal fleet4 without the old alert: R-3 heats up and the alert fires a minute in

import type {
  DeploymentStatus,
  FleetAlert,
  FleetDeployment,
  FleetEvent,
  FleetLogLine,
  FleetLink,
  FleetNode,
  FleetRule,
  EventType,
  FleetTopology,
  InfraItem,
  Layout,
  LayoutDevice,
  LogLevel,
  MetricKey,
  MetricSeries,
  NodeMemory,
  NodePairing,
  NodePatch,
  NodeStatus,
  PairBySsh,
  Range,
  RulePatch,
  TrustAudit,
} from '../src/api/fleet.ts';
import { EVENT_TYPES, FLEET_CONTRACT, METRICS, RANGES, WINDOW_KINDS } from '../src/api/fleet.ts';

export const FLEET_SCENARIOS = ['fleet1', 'fleet4', 'fleet2-split', 'node-down', 'alert-thermal'] as const;
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
  node: Omit<FleetNode, 'status' | 'status_text'>;
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
  const dsvFrom = t0 - 3.2 * H;
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
    // the ramp reaches 84 °C 1,125 s in: as the scenario starts; the clock throttles, and the rule
    // (60 s above 84 °C) fires a minute later
    r3.heatFrom = t0 - 1125;
    r3.throttleFrom = t0;
  }
  if (scenario === 'node-down') r3.offFrom = t0 + 30;

  const qwen = (members: Member[], type: 'single' | 'replicas'): DepSim => ({
    dep: {
      id: 'qwen27', name: 'Qwen3.8-27B', model: 'Qwen3.8-27B', pack: 'EXL3 4.0', type, members: members.map((m) => m.node), api_id: 'qwen3.8-27b', ctx: 262144,
      ...(type === 'replicas' ? { warn: 'Desk is on the LAN, R-3 is remote: 38 ms between them. Each conversation stays on one member; a failover moves it across the internet.' } : {}),
    },
    members,
  });
  const wren: DepSim = {
    dep: { id: 'wren', name: 'Wren', model: 'Qwen3.5-2B', pack: 'EXL3 6.0', type: 'role', members: ['desk'], api_id: 'wren', ctx: 65536, role_of: 'qwen27', role: 'assistant · setup and fleet help' },
    members: [{ node: 'desk', from: t0 - 4.2 * D, base: 4, span: 14, curve: 'wren', share: 1 }],
  };
  const dsv = (a: string, b: string): DepSim => ({
    dep: {
      id: 'dsv41', name: 'DeepSeek-V4.1-Flash', model: 'DeepSeek-V4.1-Flash', pack: 'NVFP4', type: 'split', members: [a, b], api_id: 'deepseek-v4.1-flash', ctx: 131072,
      stages: [{ node: a, layers: 'layers 0–30', gib: 78.2 }, { node: b, layers: 'layers 31–60 + head', gib: 76.5 }],
    },
    members: [a, b].map((node) => ({ node, from: dsvFrom, base: 80, span: 110, curve: 'dsv', share: 0.5 })),
  });
  const rules: FleetRule[] = [
    { id: 'gpu-temp', name: 'GPU temperature', metric: 'temp', condition: '> 84 °C for 60 s', scope: 'all', notify: ['app', 'system'], enabled: true, threshold: 84, unit: '°C', for_s: 60, severity: 'err' },
    { id: 'mem-avail', name: 'Memory headroom', metric: 'mem_avail', condition: 'MemAvailable < 4 GiB for 30 s', scope: 'all', notify: ['app', 'system'], enabled: true, threshold: 4, unit: 'GiB', for_s: 30, severity: 'warn' },
    { id: 'reachable', name: 'Node reachable', metric: 'reachable', condition: 'no answer for 30 s', scope: 'all', notify: ['app', 'system'], enabled: true, threshold: null, unit: null, for_s: 30, severity: 'err' },
    { id: 'fabric', name: 'Fabric link', metric: 'fabric', condition: '< 150 Gb/s or one logical interface', scope: 'fabric', notify: ['app'], enabled: true, threshold: 150, unit: 'Gb/s', for_s: 0, severity: 'warn' },
    { id: 'disk', name: 'Disk almost full', metric: 'disk_free', condition: '< 10 %', scope: 'all', notify: ['app'], enabled: true, threshold: 10, unit: '%', for_s: 300, severity: 'warn' },
    { id: 'version', name: 'Version mismatch', metric: 'version', condition: 'members differ', scope: 'deployments', notify: ['app', 'system'], enabled: true, threshold: null, unit: null, for_s: 0, severity: 'warn' },
    { id: 'lease', name: 'Lease ends', metric: 'lease_end', condition: '< 3 days', scope: 'rented', notify: ['app', 'system'], enabled: true, threshold: 3, unit: 'days', for_s: 0, severity: 'warn' },
    { id: 'host-sleep', name: 'Controller host may sleep', metric: 'host_sleep', condition: 'sleep allowed', scope: 'host', notify: ['app'], enabled: true, threshold: null, unit: null, for_s: 0, severity: 'warn' },
    { id: 'power', name: 'Power draw', metric: 'power', condition: '> 170 W for 5 min', scope: 'node:r3', notify: ['app'], enabled: false, threshold: 170, unit: 'W', for_s: 300, severity: 'warn' },
  ];

  if (scenario === 'fleet1') {
    const one = { ...desk, node: { ...desk.node, pairing: LOCAL }, rtt: [0.05, 0.03] as [number, number] };
    return {
      host: { name: 'Desk', address: '192.0.2.40', api_url: 'http://192.0.2.40:8000', may_sleep: false },
      infra: [],
      nodes: [one],
      deps: [qwen([{ node: 'desk', from: t0 - 60 * D, base: 48, span: 150, curve: 'desk', share: 1 }], 'single'), wren],
      links: [],
      events: [
        { at: t0 - 26 * H, item: { id: 'e-update', type: 'update', severity: null, node: 'desk', deployment: null, text: 'Desk updated to TandemLLM 0.15.0', detail: 'The engine restarted with the new build; 41 s without the API.' } },
        { at: t0 - 4.2 * D, item: { id: 'e-wren', type: 'deploy', severity: null, node: 'desk', deployment: 'wren', text: 'Wren (Role) started on Desk', detail: 'Qwen3.5-2B EXL3 6.0 beside the main model.' } },
      ],
      alerts: [],
      rules: rules.filter((r) => !['fabric', 'lease', 'host-sleep', 'power'].includes(r.id)),
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
        { at: t0 - 3.2 * H, item: { id: 'e-dsv', type: 'deploy', severity: null, node: 's1', deployment: 'dsv41', text: 'DeepSeek-V4.1-Flash started as Split on Spark 1 + Spark 2', detail: 'Stage 1: layers 0–30 on Spark 1. Stage 2: layers 31–60 + head on Spark 2.' } },
        { at: t0 - 3.5 * H, item: { id: 'e-link', type: 'link', severity: null, node: 's2', deployment: null, text: 'Link test Spark 1 ↔ Spark 2: 197 Gb/s, RTT 0.009 ms, MTU 9000', detail: 'RoCE on, both logical interfaces up.' } },
      ],
      alerts: [],
      rules: rules.filter((r) => !['lease', 'host-sleep', 'power'].includes(r.id)),
    };
  }

  // fleet4 and its variants
  const r3Up = (t: number) => r3.offFrom == null || t < r3.offFrom;
  const events: Fleet['events'] = [
    { at: t0 - 42 * MIN, item: { id: 'e-scale', type: 'deploy', severity: null, node: 'r3', deployment: 'qwen27', text: 'Qwen3.8-27B scaled Single → Replicas: R-3 joined', detail: 'Identity check passed (same pack, same engine 0.15.0). Output canary matched.' } },
    { at: t0 - 58 * MIN, item: { id: 'e-link-r3', type: 'link', severity: null, node: 'r3', deployment: null, text: 'Link test R-3 ↔ switch: 194 Gb/s, RTT 0.011 ms, MTU 9000', detail: 'RoCE on, both logical interfaces up.' } },
    { at: t0 - 2.4 * H, item: { id: 'e-lease', type: 'alert', severity: 'warn', node: 'all', deployment: null, text: 'Leases end in 2 days: R-1, R-2, R-3', detail: 'Release wipes weights, caches, keys and the node agent.' } },
    { at: t0 - 3.2 * H, item: { id: 'e-dsv', type: 'deploy', severity: null, node: 'r1', deployment: 'dsv41', text: 'DeepSeek-V4.1-Flash started as Split on R-1 + R-2', detail: 'Stage 1: layers 0–30 on R-1. Stage 2: layers 31–60 + head on R-2.' } },
    { at: t0 - 3.7 * H, item: { id: 'e-weights', type: 'weights', severity: null, node: 'r2', deployment: null, text: 'Weights copied R-1 → R-2 over the fabric', detail: '76.5 GiB in 4 min 12 s (2.4 Gb/s average, disk-bound).' } },
    { at: t0 - 5.1 * H, item: { id: 'e-pair-r3', type: 'node', severity: null, node: 'r3', deployment: null, text: 'R-3 paired (rental, SSH tunnel)', detail: 'Host key confirmed. Certificate pinned.' } },
    { at: t0 - 6.4 * H, item: { id: 'e-half', type: 'alert', severity: 'resolved', node: 'r2', deployment: null, text: 'Resolved: R-2 fabric on one logical interface (half bandwidth)', detail: 'Cable re-seated by the provider. Link test 196 Gb/s.' } },
    { at: t0 - 26 * H, item: { id: 'e-update', type: 'update', severity: null, node: 'all', deployment: null, text: 'Fleet updated to TandemLLM 0.15.0 (rolling)', detail: 'Desk first, then R-1 + R-2 together (Split), 6 min of DeepSeek downtime.' } },
    { at: t0 - 27.5 * H, item: { id: 'e-mem', type: 'alert', severity: 'resolved', node: 'desk', deployment: null, text: 'Resolved: Desk memory pressure (MemAvailable 3.1 GiB)', detail: 'KV pool shrunk from 52 to 48 GiB by the memory gate.' } },
    { at: t0 - 4.2 * D, item: { id: 'e-wren', type: 'deploy', severity: null, node: 'desk', deployment: 'wren', text: 'Wren (Role) started on Desk', detail: 'Qwen3.5-2B EXL3 6.0 beside the main model.' } },
    { at: rentStart + 600, item: { id: 'e-pair-r12', type: 'node', severity: null, node: 'r1', deployment: null, text: 'R-1 and R-2 paired (rental, SSH tunnel)', detail: 'Lease 7 days, 2.40 per hour per node.' } },
  ];
  const alerts: Fleet['alerts'] = [
    { at: t0 - 2.4 * H, item: { id: 'a-lease', severity: 'warn', node: 'all', title: 'Leases end in 2 days: R-1, R-2, R-3', rule: 'Lease ends in < 3 days', detail: 'DeepSeek-V4.1-Flash and the second Qwen replica stop when the leases end.', action: { label: 'Open Sparks', route: '#/sparks' } } },
    { at: t0 - 60 * D, item: { id: 'a-sleep', severity: 'warn', node: 'host', title: 'The controller runs on a Mac that can sleep', rule: 'Controller host may sleep', detail: 'If the Mac sleeps, the API and all metrics stop. Keep it awake or move the controller to Desk.', action: { label: 'App settings', route: '#/app' } } },
  ];
  const therm = r3.throttleFrom as number;
  events.push({ at: therm + MIN, item: { id: 'e-therm', type: 'alert', severity: 'warn', node: 'r3', deployment: null, text: 'R-3 GPU above 84 °C, thermal throttle', detail: 'SM clock down to 1.74 GHz. Decode on R-3 is 26 % lower while it lasts.' } });
  if (scenario !== 'alert-thermal') {
    events.push({ at: therm - 4 * MIN, item: { id: 'e-failover', type: 'failover', severity: null, node: 'r3', deployment: 'qwen27', text: '1 conversation moved R-3 → Desk', detail: 'R-3 answered a health check after 4.8 s. The replica set moved the conversation; it keeps its context.' } });
  }
  alerts.unshift({
    at: therm + MIN,
    when: r3Up,
    item: { id: 'a-therm', severity: 'err', node: 'r3', title: 'R-3 GPU above 84 °C, thermal throttle', rule: 'GPU temperature > 84 °C for 60 s', detail: 'Decode on R-3 down 26 %. Conversations stay; new ones go to Desk first while this is active.', action: { label: 'Open R-3', route: '#/sparks/r3' } },
  });
  if (r3.offFrom != null) {
    const down = r3.offFrom + 30;
    events.push({ at: down, item: { id: 'e-down', type: 'node', severity: 'err', node: 'r3', deployment: 'qwen27', text: 'R-3 not reachable for 30 s; its conversations moved to Desk', detail: 'The SSH tunnel is up, the node agent does not answer. Qwen3.8-27B serves on Desk alone.' } });
    alerts.unshift({ at: down, item: { id: 'a-down', severity: 'err', node: 'r3', title: 'R-3 not reachable', rule: 'Node reachable: no answer for 30 s', detail: 'Qwen3.8-27B runs on 1 of 2 members until R-3 answers again.', action: { label: 'Open R-3', route: '#/sparks/r3' } } });
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

  /** `now` in Unix seconds: the scenario's start */
  constructor(o: { seed?: number; scenario?: FleetScenario; now?: number } = {}) {
    this.scenario = o.scenario ?? 'fleet4';
    this.t0 = Math.floor(o.now ?? Date.now() / 1000);
    this.f = build(this.scenario, this.t0);
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
    return t >= n.node.since && (n.offFrom == null || t < n.offFrom);
  }
  private throttled(n: NodeSim, t: number): boolean {
    return n.throttleFrom != null && t >= n.throttleFrom;
  }
  private nodeLoad(n: NodeSim, t: number): number {
    return t < n.busyFrom ? 0.02 : this.load(n.curve, t);
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
    return v;
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
      case 'req':
        return Math.max(0, Math.round(this.tps(n, t, dep) / 31 + 0.4 * w('q')));
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
        return n.rtt[0] + n.rtt[1] * Math.abs(w('r', 3));
    }
  }

  // ---- state at a time ---------------------------------------------------------------------
  private status(n: NodeSim, now: number): { status: NodeStatus; text: string } {
    if (!this.alive(n, now)) return { status: 'off', text: 'not reachable' };
    if (this.throttled(n, now)) return { status: 'alert', text: `thermal throttle, GPU ${Math.round(this.value('temp', n.node.id, now) ?? 0)} °C` };
    return { status: 'up', text: 'up' };
  }

  nodes(now: number): FleetNode[] {
    return this.f.nodes.filter((n) => n.node.since <= now).map((n) => {
      const s = this.status(n, now);
      return { ...n.node, status: s.status, status_text: s.text };
    });
  }

  deployments(now: number): FleetDeployment[] {
    return this.f.deps.map((d) => {
      const up = d.members.filter((m) => m.from <= now && this.alive(this.f.nodes.find((n) => n.node.id === m.node)!, now)).length;
      const all = d.members.length;
      let status: DeploymentStatus = 'serving';
      let text = d.dep.type === 'split' ? `serving · ${all} stages` : d.dep.type === 'replicas' ? `serving · ${up} of ${all} members` : 'serving';
      if (up < all) {
        status = d.dep.type === 'split' ? 'down' : 'degraded';
        text = d.dep.type === 'split' ? `down · a stage does not answer` : `degraded · ${up} of ${all} members`;
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
      groups: this.f.deps.map((d) => ({ deployment: d.dep.id, type: d.dep.type, members: d.dep.members })),
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
    let condition = r.condition;
    if (e.threshold != null && r.threshold != null) condition = condition.replace(String(r.threshold), String(e.threshold));
    if (e.for_s != null) condition = condition.replace(/ for \d+ (s|min)$/, '') + (e.for_s ? ` for ${e.for_s} s` : '');
    return { ...out, condition };
  }

  alerts(now: number): FleetAlert[] {
    return this.f.alerts
      .filter((a) => a.at <= now && (!a.when || a.when(now)))
      .map((a) => ({ ...a.item, since: a.at, acked: this.acked.has(a.item.id), acked_at: this.acked.get(a.item.id) ?? null }));
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
      if (p.host !== MOCK_PAIR.ssh && p.host !== MOCK_PAIR.bare) return bad(502, 'unreachable', `no SSH server answers at ${p.host}:${p.port ?? 22}`);
      const known = this.f.nodes.some((n) => n.node.address.startsWith(`${p.host}:`)) ? 'same' : 'new';
      return { status: 200, body: { contract_version: v, host: p.host, port: (p.port as number) ?? 22, type: 'ssh-ed25519', fingerprint: MOCK_HOST_KEY, known, stored: known === 'same' ? MOCK_HOST_KEY : null } };
    }
    if (rest.length > (ssh ? 1 : 0)) return bad(404, 'not_found', `no route POST /v1/fleet/pair/${rest.join('/')}`);
    const why = pairError(body, ssh);
    if (why) return bad(400, 'bad_request', why);
    if (ssh) {
      const p = body as PairBySsh;
      if (p.host !== MOCK_PAIR.ssh && p.host !== MOCK_PAIR.bare) return bad(502, 'unreachable', `no SSH server answers at ${p.host}:${p.port ?? 22}`);
      if (p.host_key != null && p.host_key !== MOCK_HOST_KEY) return bad(409, 'host_key_changed', `the host key of ${p.host} is ${MOCK_HOST_KEY}, not the confirmed ${p.host_key}; refused`);
      if (p.key == null && p.password !== MOCK_PAIR.password) return bad(502, 'ssh_failed', `ssh to ${p.host} failed: ${p.user}@${p.host}: Permission denied (publickey,password).`);
      if (p.host === MOCK_PAIR.bare) return bad(409, 'not_installed', `TandemNode is not installed on ${p.host}: the Add Spark wizard's Install step installs it`);
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
  private join(address: string, via: 'code' | 'ssh', now: number): Reply {
    const used = new Set(this.f.nodes.map((n) => n.node.colour));
    const colour = [1, 2, 3, 4, 5, 6, 7, 8].find((c) => !used.has(c)) ?? 1;
    const k = this.f.nodes.length + 1;
    const id = `n${k}-${address.split('.').pop()}`;
    const tmpl = this.f.nodes[0];
    const sim: NodeSim = {
      ...tmpl,
      node: { ...tmpl.node, id, alias: `Spark ${k}`, colour, icon: 'desk', tags: [], owned: true, transport: 'lan', address: `${address}:7700`, since: now, lease_end: null, cost_per_hour: null, inventory: { ...tmpl.node.inventory, serial: `MOCK-${address}` }, pairing: paired(via, now, (k * 29).toString(16).padStart(2, '0')) },
      curve: id, busyFrom: Number.POSITIVE_INFINITY, heatFrom: null, throttleFrom: null, offFrom: null, fabric: false,
    };
    this.f.nodes.push(sim);
    this.audit.unshift({ t: now, action: 'pair', node: id, who: 'dashboard session', via, fingerprint: sim.node.pairing.fingerprint, detail: `paired by ${via} at ${address}` });
    return { status: 200, body: this.nodes(now).find((x) => x.id === id) };
  }

  /** a released node leaves the fleet, its deployments' members and its links */
  private forget(id: string): void {
    this.f.nodes = this.f.nodes.filter((n) => n.node.id !== id);
    for (const d of this.f.deps) {
      d.members = d.members.filter((m) => m.node !== id);
      d.dep = { ...d.dep, members: d.dep.members.filter((m) => m !== id), ...(d.dep.stages ? { stages: d.dep.stages.filter((s) => s.node !== id) } : {}) };
    }
    this.f.deps = this.f.deps.filter((d) => d.members.length > 0);
    this.f.links = this.f.links.filter((l) => l.a !== id && l.b !== id);
  }

  // ---- the routes ----------------------------------------------------------------------------
  handle(method: string, path: string, q: URLSearchParams, body: unknown, nowMs = Date.now()): Reply {
    const now = Math.floor(nowMs / 1000);
    const parts = path.split('/').slice(3); // /v1/fleet/<parts>
    const v = FLEET_CONTRACT;
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
    if (method === 'GET' && parts.length === 1 && parts[0] === 'trust') {
      return { status: 200, body: { contract_version: v, controller: { id: 'c-3dmock0c0n7r', name: `TandemApp on ${this.f.host.name}`, fingerprint: '7c'.repeat(32), since: this.t0 - 60 * D }, audit: this.audit } };
    }
    if (method === 'POST' && parts[0] === 'pair') return this.pair(parts.slice(1), body, now);
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
      if (!this.acked.has(id)) this.acked.set(id, now);
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
  const allowed = ssh ? ['host', 'user', 'port', 'password', 'key', 'host_key', 'takeover'] : ['address', 'code', 'port'];
  if (Object.keys(p).some((k) => !allowed.includes(k))) return ssh ? 'the body is {host, user, port?, password? | key?, host_key?, takeover?}' : 'the body is {address, code, port?}';
  if ('port' in p && (typeof p.port !== 'number' || !Number.isInteger(p.port) || p.port < 1 || p.port > 65535)) return 'port is a whole number from 1 to 65535';
  if (ssh) {
    if (typeof p.host !== 'string' || !p.host || /[\s@/'"`$;|&<>]/.test(p.host) || p.host.startsWith('-')) return 'host is an address or a host name';
    if (typeof p.user !== 'string' || !/^[A-Za-z0-9._-]{1,64}$/.test(p.user) || p.user.startsWith('-')) return 'user is a login name';
    if (p.password != null && p.key != null) return 'a password or a key, not both';
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
