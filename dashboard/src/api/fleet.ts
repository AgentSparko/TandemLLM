// The fleet API of TandemApp's controller (docs/contract/fleet-v1, SRV-723, SRV-725, SRV-729,
// SRV-731): the Sparks, the links between them, the deployments, per-node metric series, memory,
// events, alerts, rules and the saved window layouts. Times are Unix seconds. The controller sends
// every metric per node; the page computes Total, Stacked, Lines, Split and Table itself
// (charts/modes.ts), so one answer serves every chart mode.

import { request } from './client';

export const FLEET_CONTRACT = '1.0';

export type NodeStatus = 'up' | 'draining' | 'off' | 'alert';
export type Transport = 'lan' | 'tunnel';

/** What a node's agent read about the box. A field the probe could not read is null, never guessed. */
export interface NodeInventory {
  serial: string | null;
  os: string | null;
  kernel: string | null;
  driver: string | null;
  cuda: string | null;
  cx7: string | null;
  disk: string | null;
  nics: string | null;
}

export interface FleetNode {
  id: string;
  alias: string;
  /** identity colour 1..8: 1..4 are --n1..--n4, 5..8 the same hues hollow and dashed */
  colour: number;
  icon: 'desk' | 'cloud' | 'rack';
  tags: string[];
  class: 'spark';
  hw: string;
  dmi: string;
  owned: boolean;
  transport: Transport;
  /** how the controller reaches the node's agent (host:port) */
  address: string;
  version: string;
  status: NodeStatus;
  status_text: string;
  /** paired at */
  since: number;
  lease_end: number | null;
  cost_per_hour: number | null;
  inventory: NodeInventory;
}

export type LinkKind = 'lan' | 'wan' | 'fabric';
export type LinkState = 'up' | 'degraded' | 'down';

export interface FleetLink {
  id: string;
  /** a node id or an infra id (topology `infra[]`) */
  a: string;
  b: string;
  kind: LinkKind;
  speed: string;
  rtt_ms: number | null;
  mtu: number | null;
  state: LinkState;
  note: string | null;
  /** the last link test (B4) */
  test?: { gbps: number; at: number };
  roce?: 'on' | 'off';
  /** logical interfaces up of those the port has (a fabric port on one interface runs at half) */
  interfaces?: { up: number; total: number };
  port?: string;
}

export interface InfraItem {
  id: string;
  kind: 'host' | 'lan' | 'internet' | 'gateway' | 'switch';
  label: string;
}

export type DeploymentType = 'single' | 'replicas' | 'split' | 'role';
export type DeploymentStatus = 'serving' | 'starting' | 'degraded' | 'down' | 'stopped';

export interface DeploymentStage {
  node: string;
  layers: string;
  gib: number;
}

export interface FleetDeployment {
  id: string;
  name: string;
  model: string;
  pack: string;
  type: DeploymentType;
  members: string[];
  /** a Split's stages, in pipeline order */
  stages?: DeploymentStage[];
  /** the model id clients send to the one API */
  api_id: string;
  /** context window, tokens */
  ctx: number;
  status: DeploymentStatus;
  status_text: string;
  /** a Role's main deployment and what the role does */
  role_of?: string;
  role?: string;
  warn?: string;
}

export interface TopologyGroup {
  deployment: string;
  type: DeploymentType;
  members: string[];
}

export interface FleetTopology {
  contract_version: string;
  host: { name: string; address: string; api_url: string; may_sleep: boolean };
  infra: InfraItem[];
  nodes: FleetNode[];
  links: FleetLink[];
  /** the deployments drawn as hulls over their members */
  groups: TopologyGroup[];
}

export type Agg = 'sum' | 'mean' | 'max' | 'maxmean';
export type MetricKey = 'tps' | 'req' | 'ttft' | 'gpu' | 'power' | 'temp' | 'sm' | 'memclk' | 'mem' | 'cpu' | 'disk' | 'lan' | 'fabric' | 'rtt';
export type Range = '5m' | '1h' | '6h' | '24h' | '7d';

export interface MetricInfo {
  label: string;
  unit: string;
  /** how nodes combine in Total: tok/s and power add up, temperatures show max and mean */
  agg: Agg;
  decimals: number;
  /** a fixed axis top (percentages) */
  max?: number;
}

/** Every metric the fleet reports per node (concept §5), with how it aggregates. */
export const METRICS: Record<MetricKey, MetricInfo> = {
  tps: { label: 'Decode', unit: 'tok/s', agg: 'sum', decimals: 0 },
  req: { label: 'Active requests', unit: 'req', agg: 'sum', decimals: 0 },
  ttft: { label: 'TTFT p50', unit: 'ms', agg: 'mean', decimals: 0 },
  gpu: { label: 'GPU utilisation', unit: '%', agg: 'mean', decimals: 0, max: 100 },
  power: { label: 'Power', unit: 'W', agg: 'sum', decimals: 0 },
  temp: { label: 'GPU temperature', unit: '°C', agg: 'maxmean', decimals: 0 },
  sm: { label: 'SM clock', unit: 'MHz', agg: 'mean', decimals: 0 },
  memclk: { label: 'Memory clock', unit: 'MHz', agg: 'mean', decimals: 0 },
  mem: { label: 'Unified memory used', unit: 'GiB', agg: 'sum', decimals: 1 },
  cpu: { label: 'CPU', unit: '%', agg: 'mean', decimals: 0, max: 100 },
  disk: { label: 'Disk read + write', unit: 'MB/s', agg: 'sum', decimals: 0 },
  lan: { label: 'LAN rx + tx', unit: 'MB/s', agg: 'sum', decimals: 1 },
  fabric: { label: 'Fabric rx + tx', unit: 'Gb/s', agg: 'sum', decimals: 1 },
  rtt: { label: 'RTT from the controller', unit: 'ms', agg: 'max', decimals: 1 },
};
export const METRIC_KEYS = Object.keys(METRICS) as MetricKey[];

/** span and sample count per range; the step is span / (count - 1) */
export const RANGES: Record<Range, { span: number; count: number; label: string }> = {
  '5m': { span: 300, count: 151, label: '5 min' },
  '1h': { span: 3600, count: 121, label: '1 h' },
  '6h': { span: 21600, count: 145, label: '6 h' },
  '24h': { span: 86400, count: 145, label: '24 h' },
  '7d': { span: 604800, count: 169, label: '7 d' },
};
export const RANGE_KEYS = Object.keys(RANGES) as Range[];

export interface MetricSeries {
  contract_version: string;
  /** all | node:<id> | deployment:<id> */
  source: string;
  metric: MetricKey;
  unit: string;
  agg: Agg;
  range: Range;
  step_s: number;
  /** the first sample's time; sample i is at t0 + i * step_s */
  t0: number;
  /** per-node capacity (memory: the usable unified memory), null when the metric has none */
  cap: number | null;
  /** one series per node in the source; null = no sample (node off, not yet paired, no fabric) */
  series: { node: string; values: (number | null)[] }[];
}

export interface NodeMemory {
  node: string;
  weights: number;
  kv: number;
  cache: number;
  other: number;
  page: number;
  /** the engine's memory ceiling and its 24 h peak */
  ceiling: number;
  peak: number;
  usable: number;
}

export interface FleetMemory {
  contract_version: string;
  unit: 'GiB';
  nodes: NodeMemory[];
}

export type EventType = 'alert' | 'failover' | 'deploy' | 'link' | 'weights' | 'node' | 'update';
export type Severity = 'warn' | 'err' | 'resolved';

export interface FleetEvent {
  id: string;
  at: number;
  type: EventType;
  severity: Severity | null;
  /** a node id, `all` or `host` */
  node: string;
  deployment: string | null;
  text: string;
  detail: string;
}

export interface FleetAlert {
  id: string;
  severity: 'warn' | 'err';
  node: string;
  title: string;
  since: number;
  rule: string;
  detail: string;
  action: { label: string; route: string } | null;
  acked: boolean;
  acked_at: number | null;
}

export interface FleetRule {
  id: string;
  metric: string;
  condition: string;
  scope: string;
  notify: ('app' | 'system')[];
  enabled: boolean;
}

export type LayoutDevice = 'desk' | 'tablet' | 'phone';
export const WINDOW_KINDS = ['terminal', 'files', 'editor', 'diff', 'markdown', 'plan', 'preview', 'image', 'pdf', 'video', 'audio', 'binary', 'logs', 'chart', 'inspector', 'chat', 'playground', 'context'] as const;
export type WindowKind = (typeof WINDOW_KINDS)[number];

export interface LayoutWindow {
  id: string;
  kind: WindowKind;
  /** what the window shows (a node, a file, a metric): the kind's own keys */
  ref: Record<string, unknown>;
  at: 'right' | 'bottom' | 'float' | 'min';
  /** where a minimised window goes back to */
  prev?: 'right' | 'bottom' | 'float';
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
}

/** A page's window layout on one device class (VIS-256). Dock `f` is the dock's share of the work area. */
export interface Layout {
  page: string;
  device: LayoutDevice;
  rail: { open: boolean; w: number };
  ctx: { open: boolean; w: number };
  windows: LayoutWindow[];
  docks: { right: { f: number; act: string | null }; bottom: { f: number; act: string | null } };
  /** when it was saved; null for the default a page gets before its first save */
  saved_at?: number | null;
}

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

/** A line of a node's log (its agent and its engines), oldest first in an answer. */
export interface FleetLogLine {
  t: number;
  node: string;
  level: LogLevel;
  /** what wrote it: `agent`, `engine`, `gateway` */
  source: string;
  msg: string;
}

export interface FleetLogs {
  contract_version: string;
  node: string;
  lines: FleetLogLine[];
}

export const fleet = {
  nodes: () => request<{ contract_version: string; nodes: FleetNode[] }>('/v1/fleet/nodes'),
  links: () => request<{ contract_version: string; links: FleetLink[] }>('/v1/fleet/links'),
  topology: () => request<FleetTopology>('/v1/fleet/topology'),
  deployments: () => request<{ contract_version: string; deployments: FleetDeployment[] }>('/v1/fleet/deployments'),
  metrics: (p: { source: string; metric: MetricKey; range: Range }) => {
    const q = new URLSearchParams({ source: p.source, metric: p.metric, range: p.range });
    return request<MetricSeries>(`/v1/fleet/metrics?${q}`);
  },
  memory: () => request<FleetMemory>('/v1/fleet/memory'),
  events: (since?: number | null) => request<{ contract_version: string; events: FleetEvent[] }>(`/v1/fleet/events${since != null ? `?since=${since}` : ''}`),
  alerts: () => request<{ contract_version: string; alerts: FleetAlert[] }>('/v1/fleet/alerts'),
  ack: (id: string) => request<FleetAlert>(`/v1/fleet/alerts/${encodeURIComponent(id)}/ack`, { method: 'POST' }),
  rules: () => request<{ contract_version: string; rules: FleetRule[] }>('/v1/fleet/rules'),
  logs: (p: { node: string; level?: LogLevel | 'all'; q?: string; limit?: number }) => {
    const q = new URLSearchParams({ node: p.node });
    if (p.level && p.level !== 'all') q.set('level', p.level);
    if (p.q) q.set('q', p.q);
    if (p.limit) q.set('limit', String(p.limit));
    return request<FleetLogs>(`/v1/fleet/logs?${q}`);
  },
  layout: (page: string, device: LayoutDevice) => request<Layout>(`/v1/fleet/layouts/${encodeURIComponent(page)}?device=${device}`),
  saveLayout: (l: Layout) =>
    request<Layout>(`/v1/fleet/layouts/${encodeURIComponent(l.page)}?device=${l.device}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(l),
    }),
};

/** The sample times of a series answer. */
export function sampleTimes(m: Pick<MetricSeries, 't0' | 'step_s' | 'series'>): number[] {
  const n = Math.max(0, ...m.series.map((s) => s.values.length));
  return Array.from({ length: n }, (_, i) => m.t0 + i * m.step_s);
}
