// The fleet API of TandemApp's controller (docs/contract/fleet-v1, SRV-723, SRV-725, SRV-727,
// SRV-729, SRV-731, VIS-246, VIS-248, VIS-250): the Sparks, the links between them, the deployments,
// per-node metric series, each node's newest sample, the controller's host, memory, events, alerts,
// rules, the node actions, the saved window layouts, discovery and the Add Spark wizard's session. Times are Unix seconds. The controller sends every metric per node; the page
// computes Total, Stacked, Lines, Split and Table itself (charts/modes.ts), so one answer serves
// every chart mode.

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
  pairing: NodePairing;
  /** maintenance (VIS-250): no new requests through the gateway; null when the node takes them */
  drain: NodeDrain | null;
}

/** A drained node: `draining` while requests are in flight, `drained` once none are. */
export interface NodeDrain {
  state: 'draining' | 'drained';
  since: number;
  by: string | null;
  inflight: number;
}

export const NODE_ACTIONS = ['drain', 'undrain', 'restart-engine', 'reboot', 'update', 'forget', 'release-lease'] as const;
export type NodeAction = (typeof NODE_ACTIONS)[number];

/** A job the node's agent runs for an action (an engine's restart, a reboot, an update). */
export interface AgentJob {
  id?: string;
  action?: string;
  state?: 'running' | 'done' | 'failed';
  simulated?: boolean;
  started_at?: number;
  ended_at?: number | null;
  message?: string | null;
  [k: string]: unknown;
}

/** What an action would do, said before it is done (GET /v1/fleet/nodes/{id}/actions/{action}). */
export interface ActionPlan {
  contract_version: string;
  node: string;
  action: NodeAction;
  title: string;
  allowed: boolean;
  /** why it may not run here; null when it may */
  blocked: string | null;
  /** it stops or removes something: the POST needs `confirm` */
  destructive: boolean;
  impact: string[];
  removes: string[];
  keeps: string[];
  /** restart-engine: the node's engines, the one to restart first */
  engines: { id: string; kind: string | null; port: number | null; managed: boolean; state: string | null; job: AgentJob | null }[];
  job: AgentJob | null;
  /** single use until `expires_at` */
  confirm: string | null;
  expires_at: number | null;
}

export interface ActionResult {
  contract_version: string;
  node: string;
  action: NodeAction;
  state: 'done' | 'running';
  text: string;
  job: AgentJob | null;
  at: number;
}

export interface LinkTest {
  contract_version: string;
  link: FleetLink;
  result: {
    rtt: { n: number; min_ms: number; median_ms: number; p90_ms: number } | null;
    bandwidth: { gbps?: number; bytes?: number; streams?: number; s?: number; error?: string };
    notes: string[];
  };
}

/** How the controller trusts a node (SEC-4): its own machine, a pinned certificate from pairing by
 *  code or SSH once, or a record not paired yet. */
export interface NodePairing {
  state: 'local' | 'paired' | 'unpaired';
  via: 'code' | 'ssh' | null;
  /** SHA-256 of the node's pinned certificate, hex */
  fingerprint: string | null;
  paired_at: number | null;
  /** the SSH host key (SHA256:...) an SSH pairing confirmed */
  host_key: string | null;
}

/** POST /v1/fleet/pair: the 6-digit code `tandem node pair` shows on the node. */
export interface PairByCode {
  address: string;
  code: string;
  /** the pairing window's port, default the agent's port + 1 */
  port?: number;
}

/** POST /v1/fleet/pair/ssh: a password or a key for this call only; neither uses the controller
 *  host's ssh agent and keys. */
export interface PairBySsh {
  host: string;
  user: string;
  port?: number;
  password?: string;
  key?: string;
  /** a private key file on the controller's machine (the ssh line's -i) */
  key_file?: string;
  /** the jump hosts in order (the ssh line's -J) */
  jumps?: { user: string | null; host: string; port: number }[];
  /** a rented or remote node: reached through an SSH tunnel to its loopback */
  tunnel?: boolean;
  /** the host key fingerprint the user confirmed */
  host_key?: string;
  takeover?: boolean;
}

export interface HostKey {
  contract_version: string;
  host: string;
  port: number;
  type: string;
  fingerprint: string;
  known: 'new' | 'same' | 'changed';
  stored: string | null;
}

export interface Released {
  contract_version: string;
  id: string;
  node: 'unpinned' | 'unreachable' | 'refused' | 'not_paired';
}

export interface TrustAudit {
  t: number;
  action: 'pair' | 'takeover' | 'release' | 'forget';
  node: string;
  who: string;
  via: string | null;
  fingerprint: string | null;
  detail: string;
}

export interface FleetTrust {
  contract_version: string;
  controller: { id: string; name: string; fingerprint: string; since: number };
  audit: TrustAudit[];
}

export interface FleetSession {
  contract_version: string;
  password_set: boolean;
  authenticated: boolean;
  expires_at: string | null;
}

/** What the user sets on a node (PATCH /v1/fleet/nodes/{id}): any of these; an alias or a colour
 *  another node has is refused. */
export interface NodePatch {
  alias?: string;
  colour?: number;
  icon?: FleetNode['icon'];
  tags?: string[];
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
  /** the last link test (B4): `gbps` null when the bandwidth part failed (`note` says why) */
  test?: { gbps: number | null; at: number; rtt_ms?: number | null; note?: string | null };
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

/** One GPU in a node's sample; a field the board does not expose is absent (a GB10 has no power
 *  cap and no memory clock of its own). */
export interface SampleGpu {
  index?: number;
  name?: string;
  util?: number;
  sm_mhz?: number;
  mem_mhz?: number;
  power_w?: number;
  power_cap_w?: number;
  temp_c?: number;
  /** the driver answered for its clock event reasons; `throttle` names the active ones */
  throttled?: boolean;
  throttle?: string[];
  /** the video encoder and decoder, % */
  enc?: number;
  dec?: number;
}

export interface SampleCore {
  cpu: number;
  util?: number;
  mhz?: number;
  kind?: 'performance' | 'efficiency';
}

/** A node's newest one-second sample (GET /v1/fleet/nodes/{id}/sample): every block its agent reads,
 *  each present only when the board exposes it. */
export interface NodeSample {
  node?: Partial<Record<MetricKey, number>>;
  gpus?: SampleGpu[];
  memory?: Partial<Record<'total' | 'available' | 'used' | 'swap_total' | 'swap_used' | 'weights' | 'kv' | 'cache' | 'other' | 'ceiling' | 'page', number>> & { unified?: boolean };
  cpu?: { util?: number; load?: number[]; temp_c?: number; cores?: SampleCore[] };
  disks?: {
    devices?: { name: string; nvme?: boolean; read_mbps?: number; write_mbps?: number; busy?: number }[];
    fs?: { path: string; total_gb?: number; free_gb?: number }[];
    nvme?: { name: string; temp_c: number }[];
  };
  net?: {
    lan?: { iface: string; rx_mbps?: number; tx_mbps?: number; speed_mbps?: number; state?: string }[];
    fabric?: {
      iface: string;
      state?: string;
      speed_mbps?: number;
      mtu?: number;
      rx_gbps?: number;
      tx_gbps?: number;
      rdma?: { device?: string; port?: string; state?: string; link_layer?: string; rx_gbps?: number; tx_gbps?: number };
    }[];
  };
  board?: { zones?: { zone: string; type?: string; temp_c: number }[]; fans?: { name: string; rpm: number }[]; uptime_s?: number };
}

export interface FleetSample {
  contract_version: string;
  node: string;
  /** agent: the node's TandemNode stream; process: this machine without its agent (the fleet's keys,
   *  GPUs and memory only); none: no source, `note` says why */
  via: 'agent' | 'process' | 'none';
  t: number | null;
  age_s: number | null;
  sample: NodeSample | null;
  note: string | null;
}

/** The machine the controller runs on and the controller's own process (GET /v1/fleet/host). */
export interface FleetHost {
  contract_version: string;
  name: string;
  os: string | null;
  address: string;
  /** this machine's node id when it is a node, else null */
  node: string | null;
  may_sleep: boolean;
  cpu: { util: number | null; cores: number | null };
  memory: { used_gib: number | null; total_gib: number | null };
  controller: { rss_mib: number | null; cpu: number | null; threads: number; uptime_s: number };
}

export const EVENT_TYPES = ['alert', 'failover', 'deploy', 'link', 'weights', 'node', 'update', 'engine', 'controller'] as const;
export type EventType = (typeof EVENT_TYPES)[number];
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
  name: string;
  metric: string;
  condition: string;
  /** `all`, `node:<id>`, `host`, or a mock-only group */
  scope: string;
  notify: ('app' | 'system')[];
  enabled: boolean;
  /** null for a rule without one (a node that does not answer) */
  threshold: number | null;
  unit: string | null;
  /** how long the condition holds before it fires */
  for_s: number;
  severity: 'warn' | 'err';
}

/** the body of PATCH /v1/fleet/rules/{id}, any of these */
export type RulePatch = Partial<Pick<FleetRule, 'enabled' | 'notify' | 'threshold' | 'for_s' | 'scope'>>;

export interface EventQuery {
  since?: number;
  before?: number;
  /** a node id, `all` or `host` */
  node?: string;
  type?: EventType[];
  limit?: number;
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

/** Discovery (SRV-727): what a found device is. */
export type DiscoveryKind = 'spark' | 'gb10' | 'host' | 'owned' | 'unknown';
export type DiscoverySource = 'mdns' | 'scan' | 'ssh_config' | 'address' | 'login' | 'pair';

/** One box, however many sources found it (merged by node id, SSH host key, address or name). */
export interface DiscoveryRow {
  id: string;
  kind: DiscoveryKind;
  /** the kind in words: "Spark with TandemLLM 0.15.0", "GB10 box, nothing installed", "Linux host, not a Spark", … */
  title: string;
  /** the box and its state: "ASUS Ascent GX10 · TandemLLM 0.15.0 · not paired" */
  detail: string;
  name: string | null;
  addresses: string[];
  sources: DiscoverySource[];
  /** what its TandemNode says over mDNS */
  node: {
    id: string | null;
    class: string;
    version: string | null;
    hw: string | null;
    paired: boolean;
    owner: 'this' | 'other' | null;
    listen: string;
    /** the pairing window's port while `tandem node pair` shows a code */
    window: number | null;
    port: number | null;
    virtual: boolean;
  } | null;
  hw: { label: string; spark: boolean; source: 'mdns' | 'dmi' } | null;
  ssh: { port: number; banner: string | null; os: string | null; host_key: string | null } | null;
  ssh_config: { alias: string; hostname: string; user: string | null; port: number; identity_file: string | null; proxy_jump: string | null } | null;
  /** the fleet's node id when the box is in the fleet */
  fleet: string | null;
  /** what the Connect step can do */
  connect: ('code' | 'ssh' | 'takeover')[];
  evidence: string[];
  seen_at: number;
  seq: number;
}

export type LocalNetwork = 'not_needed' | 'not_asked' | 'granted' | 'denied';

/** GET /v1/fleet/discovery: the list and each source's state; with `since` only what changed. */
export interface Discovery {
  contract_version: string;
  run: number;
  cursor: number;
  /** `rows` is the whole list; else only the rows changed after `since` */
  full: boolean;
  rows: DiscoveryRow[];
  gone: string[];
  mdns: { state: 'idle' | 'browsing' | 'done' | 'denied' | 'failed'; types: string[]; started_at: number | null; ended_at: number | null; found: number; note: string | null };
  scan: {
    state: 'none' | 'ask' | 'running' | 'done' | 'stopped' | 'denied';
    subnet: string | null;
    interface: string | null;
    /** "Scan 192.0.2.0/24 for SSH?": nothing goes out until POST /scan names this subnet */
    question: string | null;
    total: number;
    done: number;
    open: number;
    started_at: number | null;
    ended_at: number | null;
    rate_per_s: number;
    timeout_ms: number;
    by: string | null;
  };
  ssh_config: { state: 'idle' | 'read' | 'none' | 'failed'; hosts: number; note: string | null };
  local_network: { state: LocalNetwork; text: string; settings: string | null };
  interfaces: { name: string; address: string; subnet: string }[];
}

/** POST /v1/fleet/discovery/target: a pasted ssh line, read. */
export interface SshTarget {
  user: string | null;
  host: string;
  port: number;
  identity_file: string | null;
  jumps: { user: string | null; host: string; port: number }[];
  /** the hops in order, the node last */
  route: { kind: 'jump' | 'node'; user: string | null; host: string; port: number }[];
  remote: boolean;
  warnings: string[];
}

/** The Add Spark wizard (VIS-246): its steps, in order. */
export const ADD_STEPS = ['find', 'connect', 'check', 'install', 'link', 'name', 'done'] as const;
export type AddStep = (typeof ADD_STEPS)[number];

/** One preflight check: its value and, for anything but ok, the fix in words. */
export interface PreflightRow {
  id: string;
  label: string;
  state: 'ok' | 'warn' | 'fail';
  value: string;
  fix: string | null;
}

export interface InstallStep {
  id: 'check' | 'upload' | 'agent' | 'engine' | 'unit' | 'pair';
  label: string;
  state: 'todo' | 'running' | 'done' | 'failed' | 'skip';
  detail: string | null;
  started_at: number | null;
  ended_at: number | null;
}

/** A target's install: skipped (TandemLLM at the fleet's version runs there), or its job. */
export interface InstallView {
  state: 'skip' | 'queued' | 'running' | 'done' | 'failed';
  why?: string;
  version?: string;
  node?: string | null;
  error?: { type: string; message: string } | null;
  started_at?: number | null;
  ended_at?: number | null;
  options?: { prefix: string; port: number; unit: string; engine: boolean };
  steps?: InstallStep[];
  log?: { t: number; step: string; level: 'info' | 'warn'; text: string }[];
}

/** A device picked in Find, as the controller keeps it for the rest of the wizard. */
export interface AddTarget {
  id: string;
  /** the discovery row it came from (null: an ssh line) */
  row: string | null;
  label: string;
  kind: DiscoveryKind | 'rented' | 'remote';
  title: string;
  detail: string;
  address: string;
  connect: ('code' | 'ssh' | 'takeover')[];
  hw: string | null;
  version: string | null;
  /** the pairing window's port while the node shows a code */
  window: number | null;
  ssh: { host: string; port: number; user: string | null; jumps: { user: string | null; host: string; port: number }[]; key_file: string | null; auth: 'password' | 'key' | 'key_file' | 'agent' | null };
  rented: { lease_end: number; cost_per_hour: number | null } | null;
  transport: Transport;
  /** paired; bare: reached over SSH, nothing installed (Install installs it) */
  state: 'todo' | 'paired' | 'bare';
  via: 'code' | 'ssh' | 'takeover' | null;
  host_key: { type: string; fingerprint: string; known: 'new' | 'same' | 'changed'; stored: string | null; confirmed: boolean } | null;
  error: { type: string; message: string } | null;
  node: string | null;
  preflight: { at: number; via: 'agent' | 'ssh' | null; rows: PreflightRow[]; verdict: { fails: number; warns: number; install: 'needed' | 'skip' | 'refused'; why: string } | null; error: { type: string; message: string } | null } | null;
  install: InstallView | null;
  name: { alias?: string; colour?: number; icon?: FleetNode['icon']; tags?: string[] } | null;
  /** whether this controller's memory holds the password or key (never the secret itself) */
  secret: { held: boolean; until: number | null };
}

/** An Add Spark session: kept on the controller, so it resumes after a reload or on another device. */
export interface AddSession {
  contract_version: string;
  id: string;
  state: 'active' | 'done' | 'abandoned';
  step: AddStep;
  created_at: number;
  updated_at: number;
  by: string;
  fleet_version: string;
  targets: AddTarget[];
  link: { state: 'todo' | 'not_needed' | 'skipped' | 'testing' | 'done'; why: string | null; results: { node: string; link: string; test: FleetLink['test'] | null; error: { type: string; message: string } | null }[] };
  done_at: number | null;
  seq: number;
  /** when the lease rule reminds of each rented node's lease */
  reminders: { node: string; target: string; lease_end: number; at: number; hours: number; rule: 'lease'; enabled: boolean }[];
}

export interface AddConnect {
  method: 'code' | 'ssh' | 'takeover';
  code?: string;
  user?: string;
  password?: string;
  key?: string;
  key_file?: string;
  agent?: boolean;
  host_key?: string;
}

export interface AddRun {
  secrets?: Record<string, { password?: string; key?: string }>;
  targets?: string[];
  options?: Record<string, { prefix?: string; state_dir?: string; port?: number; unit?: string; virtual?: string; engine?: boolean }>;
}

/** A poll answer applied to the list the page holds: a full answer replaces it, a partial one
 *  updates its changed rows and drops the gone ones. */
export function mergeDiscovery(prev: Discovery | null, next: Discovery): Discovery {
  if (next.full || !prev || prev.run !== next.run) return next;
  const rows = new Map(prev.rows.map((r) => [r.id, r]));
  for (const id of next.gone) rows.delete(id);
  for (const r of next.rows) rows.set(r.id, r);
  const order = (r: DiscoveryRow) => ['spark', 'gb10', 'host', 'owned', 'unknown'].indexOf(r.kind);
  const ip = (r: DiscoveryRow) => (r.addresses[0] ?? '999').split('.').map((x) => x.padStart(3, '0')).join('.');
  const all = [...rows.values()].sort((a, b) => order(a) - order(b) || ip(a).localeCompare(ip(b)) || (a.name ?? '').localeCompare(b.name ?? '') || a.id.localeCompare(b.id));
  return { ...next, full: true, rows: all, gone: [] };
}

export const fleet = {
  nodes: () => request<{ contract_version: string; nodes: FleetNode[] }>('/v1/fleet/nodes'),
  node: (id: string) => request<FleetNode>(`/v1/fleet/nodes/${encodeURIComponent(id)}`),
  patchNode: (id: string, p: NodePatch) =>
    request<FleetNode>(`/v1/fleet/nodes/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    }),
  links: () => request<{ contract_version: string; links: FleetLink[] }>('/v1/fleet/links'),
  topology: () => request<FleetTopology>('/v1/fleet/topology'),
  deployments: () => request<{ contract_version: string; deployments: FleetDeployment[] }>('/v1/fleet/deployments'),
  metrics: (p: { source: string; metric: MetricKey; range: Range }) => {
    const q = new URLSearchParams({ source: p.source, metric: p.metric, range: p.range });
    return request<MetricSeries>(`/v1/fleet/metrics?${q}`);
  },
  memory: () => request<FleetMemory>('/v1/fleet/memory'),
  sample: (id: string) => request<FleetSample>(`/v1/fleet/nodes/${encodeURIComponent(id)}/sample`),
  host: () => request<FleetHost>('/v1/fleet/host'),
  events: (p: EventQuery = {}) => {
    const q = new URLSearchParams();
    for (const k of ['since', 'before', 'node', 'limit'] as const) if (p[k] != null) q.set(k, String(p[k]));
    if (p.type?.length) q.set('type', p.type.join(','));
    return request<{ contract_version: string; events: FleetEvent[] }>(`/v1/fleet/events${q.size ? `?${q}` : ''}`);
  },
  alerts: () => request<{ contract_version: string; alerts: FleetAlert[] }>('/v1/fleet/alerts'),
  ack: (id: string) => request<FleetAlert>(`/v1/fleet/alerts/${encodeURIComponent(id)}/ack`, { method: 'POST' }),
  rules: () => request<{ contract_version: string; rules: FleetRule[] }>('/v1/fleet/rules'),
  editRule: (id: string, p: RulePatch) =>
    request<FleetRule>(`/v1/fleet/rules/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) }),
  /** `info` and up unless `level` says otherwise; HTTP access lines only with `access` */
  logs: (p: { node: string; level?: LogLevel; q?: string; limit?: number; access?: boolean }) => {
    const q = new URLSearchParams({ node: p.node });
    if (p.level) q.set('level', p.level);
    if (p.access) q.set('access', '1');
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
  // node trust (SEC-4): pairing, release and take-over need the admin password's session (or a
  // caller on the controller's machine); signIn sets it for the controller's routes
  pair: (p: PairByCode) => request<FleetNode>('/v1/fleet/pair', post(p)),
  pairSsh: (p: PairBySsh) => request<FleetNode>('/v1/fleet/pair/ssh', post(p)),
  hostKey: (host: string, port?: number) => request<HostKey>('/v1/fleet/pair/ssh/hostkey', post(port == null ? { host } : { host, port })),
  release: (id: string, force = false) => request<Released>(`/v1/fleet/nodes/${encodeURIComponent(id)}${force ? '?force=1' : ''}`, { method: 'DELETE' }),
  trust: () => request<FleetTrust>('/v1/fleet/trust'),
  // node actions (VIS-250): read the plan, then run it with the plan's confirm (destructive ones)
  plan: (id: string, action: NodeAction, engine?: string) =>
    request<ActionPlan>(`/v1/fleet/nodes/${encodeURIComponent(id)}/actions/${action}${engine ? `?engine=${encodeURIComponent(engine)}` : ''}`),
  act: (id: string, action: NodeAction, body: { confirm?: string; engine?: string } = {}) =>
    request<ActionResult>(`/v1/fleet/nodes/${encodeURIComponent(id)}/actions/${action}`, post(body)),
  testLink: (id: string) => request<LinkTest>(`/v1/fleet/links/${encodeURIComponent(id)}/test`, { method: 'POST' }),
  // discovery (SRV-727): start a run, poll with the cursor, say yes to the scan's question
  discovery: (since?: number) => request<Discovery>(`/v1/fleet/discovery${since != null ? `?since=${since}` : ''}`),
  discover: (iface?: string) => request<Discovery>('/v1/fleet/discovery', post(iface ? { interface: iface } : {})),
  scan: (subnet: string) => request<Discovery>('/v1/fleet/discovery/scan', post({ subnet })),
  stopScan: () => request<Discovery>('/v1/fleet/discovery/scan', { method: 'DELETE' }),
  discoverAddress: (address: string, port?: number) =>
    request<{ contract_version: string; row: DiscoveryRow }>('/v1/fleet/discovery/address', post(port == null ? { address } : { address, port })),
  sshTarget: (line: string) => request<{ contract_version: string; target: SshTarget }>('/v1/fleet/discovery/target', post({ line })),
  // the Add Spark wizard (VIS-246): the session on the controller, resumable from any device
  addSession: () => request<{ contract_version: string; fleet_version: string; session: AddSession | null }>('/v1/fleet/add'),
  startAdd: (replace = false) => request<AddSession>('/v1/fleet/add', post(replace ? { replace: true } : {})),
  patchAdd: (id: string, p: { step?: AddStep; names?: Record<string, NonNullable<AddTarget['name']>> }) =>
    request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) }),
  endAdd: (id: string) => request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  addTarget: (id: string, body: { row: string } | { ssh: SshTarget | string; rented?: { lease_end: number; cost_per_hour?: number | null }; key?: string }) =>
    request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}/targets`, post(body)),
  dropTarget: (id: string, tid: string) => request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}/targets/${encodeURIComponent(tid)}`, { method: 'DELETE' }),
  targetHostKey: (id: string, tid: string) => request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}/targets/${encodeURIComponent(tid)}/hostkey`, post({})),
  connectTarget: (id: string, tid: string, body: AddConnect) => request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}/targets/${encodeURIComponent(tid)}/connect`, post(body)),
  checkAdd: (id: string, body: AddRun = {}) => request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}/check`, post(body)),
  installAdd: (id: string, body: AddRun = {}) => request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}/install`, post(body)),
  linkAdd: (id: string, action: 'test' | 'skip' | 'configure') => request<AddSession>(`/v1/fleet/add/${encodeURIComponent(id)}/link`, post({ action })),
  session: () => request<FleetSession>('/v1/fleet/session'),
  signIn: (password: string) => request<void>('/v1/fleet/session', post({ token: password }), 'none'),
  signOut: () => request<void>('/v1/fleet/session', { method: 'DELETE' }, 'none'),
};

function post(body: unknown): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

/** The sample times of a series answer. */
export function sampleTimes(m: Pick<MetricSeries, 't0' | 'step_s' | 'series'>): number[] {
  const n = Math.max(0, ...m.series.map((s) => s.values.length));
  return Array.from({ length: n }, (_, i) => m.t0 + i * m.step_s);
}
