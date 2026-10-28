// The fleet the shell shows (VIS-242): its Sparks, deployments and alerts every 15 s, the decode rate
// of each Spark every 5 s, read from the controller's fleet API (api/fleet.ts). One store for the top
// bar's readout, the scope bar, the icon strip's swatches and the context sidebar's health list. A
// server without the fleet API (a box before TandemApp's controller, SRV-723) answers 404 once: the
// store stops asking and the shell shows the box as it does today, a fleet of none. Whether the
// page's own /v1 (the data plane) reaches the controller is asked once (SRV-733, `gateway`).

import type { FleetAlert, FleetDeployment, FleetNode } from '../api/fleet';
import { fleet, gatewayHere } from '../api/fleet';
import { describeError } from '../api/client';
import { poll, type Poller } from './poll';

export interface FleetState {
  nodes: FleetNode[];
  deployments: FleetDeployment[];
  alerts: FleetAlert[];
  /** each node's decode rate now, tok/s; absent before the first answer */
  tps: Record<string, number>;
  /** SRV-733: this page's /v1 reaches the controller (clients' requests are in its ledger) */
  gateway: boolean;
}

/** null until the first answer, and for good on a server without the fleet API */
let state: FleetState | null = null;
let absent = false;
const listeners = new Set<(s: FleetState | null) => void>();
let slow: Poller | null = null;
let fast: Poller | null = null;

const emit = () => listeners.forEach((f) => f(state));

async function loadFleet(): Promise<void> {
  try {
    const [n, d, a, gw] = await Promise.all([fleet.nodes(), fleet.deployments(), fleet.alerts(), state ? state.gateway : gatewayHere()]);
    const first = !state;
    state = { nodes: n.nodes, deployments: d.deployments, alerts: a.alerts, tps: state?.tps ?? {}, gateway: gw };
    emit();
    // the rates wait for the Sparks they belong to
    if (first) void fast?.refresh();
  } catch (e) {
    if (describeError(e).status === 404) stop(true);
  }
}

async function loadRates(): Promise<void> {
  if (!state) return;
  const m = await fleet.metrics({ source: 'all', metric: 'tps', range: '5m' });
  const tps: Record<string, number> = {};
  for (const s of m.series) {
    const v = [...s.values].reverse().find((x) => x != null);
    if (v != null) tps[s.node] = v;
  }
  state = { ...state, tps };
  emit();
}

function stop(gone: boolean): void {
  absent = gone;
  slow?.stop();
  fast?.stop();
  slow = fast = null;
  if (gone) {
    state = null;
    emit();
  }
}

/** Hears the fleet (now, and on every change); the polls run while anyone listens. */
export function onFleet(fn: (s: FleetState | null) => void): () => void {
  listeners.add(fn);
  fn(state);
  if (!slow && !absent) {
    slow = poll(() => loadFleet(), 15_000);
    fast = poll(() => loadRates(), 5_000);
    slow.start();
    fast.start();
  }
  return () => {
    listeners.delete(fn);
    if (!listeners.size) stop(false);
  };
}

/** Asks the controller again now (an alert acknowledged). */
export function refreshFleet(): void {
  void slow?.refresh();
}

export function fleetNow(): FleetState | null {
  return state;
}

export const activeAlerts = (s: Pick<FleetState, 'alerts'>) => s.alerts.filter((a) => !a.acked);

export interface Readout {
  swatches: { id: string; alias: string; colour: number; alert: boolean; drain: boolean; title: string }[];
  /** the fleet's decode rate, tok/s; null before the first reading */
  tps: number | null;
  alerts: number;
  /** the mixed-version alert's title while the Sparks run more than one release (OPS-234), else null */
  mixed: string | null;
  /** what a screen reader hears */
  label: string;
}

/** The top bar's readout: a swatch per Spark in its colour (ringed when it alerts, faint when it
 *  drains), the fleet's decode rate and the active alerts. */
export function readout(s: FleetState): Readout {
  const alerts = activeAlerts(s);
  const rates = s.nodes.map((n) => s.tps[n.id]).filter((v): v is number => v != null);
  const tps = rates.length ? rates.reduce((a, b) => a + b, 0) : null;
  const sparks = `${s.nodes.length} Spark${s.nodes.length === 1 ? '' : 's'}`;
  const al = alerts.length ? `${alerts.length} active alert${alerts.length === 1 ? '' : 's'}` : 'no alerts';
  const mixed = s.alerts.find((a) => a.id === 'version-mismatch:all')?.title ?? null;
  return {
    swatches: s.nodes.map((n) => ({
      id: n.id,
      alias: n.alias,
      colour: n.colour,
      alert: n.status === 'alert' || alerts.some((a) => a.node === n.id),
      drain: n.status === 'draining' || n.status === 'off',
      title: `${n.alias} · ${n.status === 'alert' ? n.status_text || 'alert' : n.status}`,
    })),
    tps,
    alerts: alerts.length,
    mixed,
    label: `Fleet: ${sparks}, ${tps == null ? 'no rate yet' : `${Math.round(tps)} tok/s`}, ${al}${mixed ? `, ${mixed}` : ''}. Open Events & Alerts`,
  };
}
