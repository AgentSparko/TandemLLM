// The fleet's alerts as system notifications on this device (VIS-248, concept D7, the rules' `system`
// target): the controller makes no outbound call; the page shows a browser notification when an alert
// whose rule says `system` starts to fire while TandemApp is open in a tab. Off until the user turns it
// on in Events & Alerts, which is where the browser's permission is asked, once, in place. The alerts
// already firing when the page opens make no notification; an acknowledged one none either.

import { fleet, type FleetAlert, type FleetRule } from '../api/fleet';
import { onFleet } from './fleet-state';

const KEY = 'qse.fleet.notify';
const RULES_TTL_MS = 60_000;

export function systemOn(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function setSystemOn(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? '1' : '0');
  } catch {
    /* private mode: it stays off */
  }
}

/** 'granted', 'denied', 'default', or 'unsupported' (plain http, iOS outside the installed app) */
export function permission(): NotificationPermission | 'unsupported' {
  return typeof Notification === 'undefined' || !window.isSecureContext ? 'unsupported' : Notification.permission;
}

/** The alerts that start to fire now and want a system notification: not seen before (`seen` null is
 *  the first look: nothing), not acknowledged, their rule (`<rule>:<subject>`) with `system`. */
export function toNotify(seen: Set<string> | null, alerts: FleetAlert[], system: Set<string>): FleetAlert[] {
  if (!seen) return [];
  return alerts.filter((a) => !seen.has(a.id) && !a.acked && system.has(a.id.split(':')[0]));
}

let rules: { at: number; ids: Set<string> } | null = null;

async function systemRules(): Promise<Set<string>> {
  if (!rules || Date.now() - rules.at > RULES_TTL_MS) {
    const r = await fleet.rules().catch(() => null);
    rules = { at: Date.now(), ids: new Set((r?.rules ?? []).filter((x: FleetRule) => x.enabled && x.notify.includes('system')).map((x) => x.id)) };
  }
  return rules.ids;
}

/** the rules changed (an edit saved): read them again at the next alert */
export function forgetRules(): void {
  rules = null;
}

async function show(a: FleetAlert): Promise<void> {
  const opts: NotificationOptions = { body: a.detail, tag: `fleet:${a.id}`, icon: './favicon.svg', data: { url: `./${a.action?.route ?? '#/events'}` } };
  const reg = 'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistration().catch(() => undefined) : undefined;
  if (reg) await reg.showNotification(a.title, opts);
  else new Notification(a.title, opts);
}

/** Watches the fleet's alerts for the whole app; returns the stop. */
export function watchAlerts(): () => void {
  let seen: Set<string> | null = null;
  return onFleet((f) => {
    if (!f) return;
    const prev = seen;
    seen = new Set(f.alerts.map((a) => a.id));
    if (!prev || !systemOn() || permission() !== 'granted') return;
    const fresh = f.alerts.filter((a) => !prev.has(a.id) && !a.acked);
    if (!fresh.length) return;
    void systemRules().then((sys) => toNotify(prev, fresh, sys).forEach((a) => void show(a).catch(() => undefined)));
  });
}
