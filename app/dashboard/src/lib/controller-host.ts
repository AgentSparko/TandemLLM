// App settings → Controller host (OPS-234, concept A9): where the controller runs, the choices to move
// it to, and where this page goes once it moved. The page (views/fleet/app-settings.ts) and the shell's
// "moved" banner read these; the controller's answer is /v1/fleet/controller (proxy/ctlmove.py).

import type { ControllerView, MoveJob, MovePlan } from '../api/fleet';

export interface HostChoice {
  /** `here` for the machine the controller runs on now, else a node id */
  id: string;
  title: string;
  sub: string;
  current: boolean;
  allowed: boolean;
  why: string | null;
  recommended: boolean;
  warn: string | null;
}

const KIND: Record<ControllerView['host']['kind'], string> = { mac: 'This Mac', linux: 'This machine', spark: 'This Spark' };

/** The radio cards: the host it runs on, then each Spark it may move to (a Spark is recommended
 *  while a Mac that sleeps runs it: Q-1, controller default on a Spark). */
export function hostChoices(v: ControllerView): HostChoice[] {
  const here: HostChoice = {
    id: 'here', title: KIND[v.host.kind], sub: `${v.host.name} · ${v.host.address}`, current: true, allowed: true, why: null, recommended: v.host.kind === 'spark',
    warn: v.sleep_warning,
  };
  // one Spark is recommended while a Mac that sleeps runs the controller: the first that may host it
  const rec = v.host.may_sleep ? v.targets.find((t) => t.allowed && !t.virtual)?.node : undefined;
  return [
    here,
    ...v.targets.map((t) => ({
      id: t.node, title: `Spark ${t.alias}`, sub: t.allowed ? 'Always on, on the LAN. This page and your phone stay its UI.' : (t.why ?? ''), current: false, allowed: t.allowed, why: t.why,
      recommended: t.node === rec, warn: null,
    })),
  ];
}

/** "The API moves to http://…/v1" for a plan; the URL the move ends on for a job */
export const apiOf = (url: string) => `${url.replace(/\/+$/, '')}/v1`;

/** Where the page follows to once the controller moved: the new controller's page with this route. */
export function followUrl(moved: NonNullable<ControllerView['moved_to']>, hash: string): string {
  return `${moved.api_url.replace(/\/+$/, '')}/${hash.startsWith('#') ? hash : ''}`;
}

/** A move job's words: what runs now, or how it ended */
export function moveWords(j: MoveJob): string {
  if (j.state === 'done') return j.direction === 'pull' ? 'This controller runs the fleet now.' : `Moved. The API is ${apiOf(j.result?.api_url ?? j.api_url)}.`;
  if (j.state === 'failed') return j.error?.message ?? 'The move failed.';
  const s = j.steps.find((x) => x.state === 'running');
  return s ? `${s.label}…` : 'Starting…';
}

/** A plan's nodes, grouped by what the move does with them */
export function planGroups(p: MovePlan): { moved: MovePlan['nodes']; stays: MovePlan['nodes'] } {
  return { moved: p.nodes.filter((n) => n.how !== 'stays'), stays: p.nodes.filter((n) => n.how === 'stays') };
}

/** the admin password a controller starts with: as long as server/auth.py's MIN_TOKEN */
export const MIN_PASSWORD = 32;

/** what a move's form may send: a port 1024..65535, a password (MIN_PASSWORD+ characters, no spaces)
 *  when the plan needs one */
export function moveFormError(p: MovePlan | null, port: string, password: string): string | null {
  const n = Number(port);
  if (!Number.isInteger(n) || n < 1024 || n > 65535) return 'The port is a whole number from 1024 to 65535.';
  if (p?.needs_password && (password.length < MIN_PASSWORD || /\s/.test(password))) return `The new controller listens on the LAN: its admin password is ${MIN_PASSWORD} or more characters, no spaces. Generate makes one.`;
  return null;
}

/** what a take's form may send: the controller's http(s) URL */
export function takeFormError(url: string): string | null {
  return /^https?:\/\/[^\s/]+/.test(url.trim()) ? null : 'The URL of the controller that runs the fleet now, such as http://192.0.2.40:8080.';
}

/** Where the first open goes (OPS-234, SRV-749): a page opened without a route on a fleet with no
 *  member yet (no Spark, no backend) lands on Get started, which offers the ways in; a route given
 *  (a bookmark, a link) is kept. */
export function landing(startHash: string, nodes: readonly unknown[]): 'start' | null {
  const bare = !startHash || startHash === '#' || startHash === '#/';
  return bare && nodes.length === 0 ? 'start' : null;
}

/** a new admin password: MIN_PASSWORD characters from the browser's random source */
export function newPassword(rand: (n: number) => Uint8Array = (n) => crypto.getRandomValues(new Uint8Array(n))): string {
  const abc = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
  return [...rand(MIN_PASSWORD)].map((b) => abc[b % abc.length]).join('');
}
