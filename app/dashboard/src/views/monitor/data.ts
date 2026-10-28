// What the Monitor pages read besides the shell's fleet store (VIS-248): a fleet call that may meet a
// controller without the route yet (404 → absent, said on the page), the events a chart marks for its
// range, and whether the controller's writes (acknowledge, rule edits) need its admin password first.

import { describeError } from '../../api/client';
import { fleet, type FleetAlert, type FleetSession, type FleetEvent } from '../../api/fleet';
import type { ChartEvent } from '../../charts/fleet-chart';
import { refreshFleet } from '../../lib/fleet-state';

/** a fleet answer, or `absent` for a controller without that route (an older build) */
export async function fromFleet<T>(f: () => Promise<T>): Promise<T | 'absent'> {
  try {
    return await f();
  } catch (e) {
    if (describeError(e).status === 404) return 'absent';
    throw e;
  }
}

// ---- event markers -------------------------------------------------------------------------------

const MARK_TTL_MS = 30_000;
let marks: { since: number; at: number; list: FleetEvent[] } | null = null;

/** A chart's markers: the fleet's events since `since`, read at most every 30 s for every chart on
 *  the page at once. Acknowledgements and the controller's own stops are left out: they say nothing
 *  about the curve. */
export async function chartEvents(since: number): Promise<ChartEvent[]> {
  const now = Date.now();
  if (!marks || now - marks.at > MARK_TTL_MS || since < marks.since) {
    const r = await fromFleet(() => fleet.events({ since, limit: 200 }));
    marks = { since, at: now, list: r === 'absent' ? [] : r.events };
  }
  return marks.list.filter((e) => e.at >= since && !(e.type === 'alert' && e.severity == null)).map(markOf);
}

export function markOf(e: FleetEvent): ChartEvent {
  return { at: e.at, text: e.text, alert: e.type === 'alert' && e.severity !== 'resolved' };
}

// ---- the controller's writes ---------------------------------------------------------------------

let session: FleetSession | null = null;
let asked: Promise<FleetSession | null> | null = null;
const listeners = new Set<() => void>();

/** Whether the controller has an admin password this browser is not signed in with. A controller
 *  without the session route (or with nothing set) needs nothing. */
export function needsSignIn(): boolean {
  return !!session && session.password_set && !session.authenticated;
}

export function checkSession(force = false): Promise<FleetSession | null> {
  if (!asked || force) {
    asked = fleet
      .session()
      .then((s) => (session = s))
      .catch(() => (session = null))
      .finally(() => listeners.forEach((f) => f()));
  }
  return asked;
}

/** Hears the session change (signed in, refused); returns the unsubscribe. */
export function onSession(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

window.addEventListener('qse-signed-in', () => void checkSession(true));

/** Acknowledges an alert; a 401 means the password is needed after all: the page shows the sign-in.
 *  The fleet store reads the alerts again either way. */
export async function acknowledge(a: Pick<FleetAlert, 'id'>): Promise<'ok' | 'sign-in' | string> {
  try {
    await fleet.ack(a.id);
    return 'ok';
  } catch (e) {
    const d = describeError(e);
    if (d.status === 401) {
      await checkSession(true);
      if (!needsSignIn()) session = { contract_version: '1.0', password_set: true, authenticated: false, expires_at: null };
      listeners.forEach((f) => f());
      return 'sign-in';
    }
    return d.message;
  } finally {
    refreshFleet();
  }
}
