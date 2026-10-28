// Each Spark's Setup Guide through the controller in the mock (`/v1/fleet/nodes/{id}/setup/*` and
// `/v1/fleet/nodes/{id}/setup-confirm/*`, docs/contract/fleet-v1, VIS-252), as proxy/fleetsetup.py
// answers: every node of the mock fleet has a Setup Guide of its own (a MockSetup each, made at its
// first call), an off node answers 503 `unreachable`, and a write (apply, remove, reset, a retry,
// waking Wren) goes only with the confirm of its plan, bound to the node, the route and the choice the
// plan was read for (428 without it, 409 for a spent, stale, another node's or another choice's).

import type { IncomingMessage, ServerResponse } from 'node:http';
import type { SetupConfirm } from '../src/api/fleet.ts';
import { FLEET_CONTRACT } from '../src/api/fleet.ts';
import type { MockFleet } from './fleet.ts';
import { MockSetup } from './setup.ts';

type Reply = { status: number; body: unknown };
const bad = (status: number, type: string, message: string): Reply => ({ status, body: { error: { type, message } } });
const CONFIRM_S = 300;
const WRITES = ['apply', 'remove', 'reset', 'retry', 'assistant/load'] as const;
type Write = (typeof WRITES)[number];

/** the write a request is, else null (a read, a dry run, a free POST) */
export function writeOf(method: string, sub: string[], body: unknown): Write | null {
  if (method !== 'POST') return null;
  const w = sub.length === 3 && sub[0] === 'jobs' && sub[2] === 'retry' ? 'retry' : sub.join('/');
  if (!(WRITES as readonly string[]).includes(w)) return null;
  if (body && typeof body === 'object' && (body as { dry_run?: unknown }).dry_run === true) return null;
  return w as Write;
}

/** what a confirm is for besides the node and the route */
export function bound(w: Write, src: Record<string, unknown>): string {
  if (w === 'apply') {
    const sel = (src.selection && typeof src.selection === 'object' ? src.selection : src) as Record<string, unknown>;
    return `${sel.model ?? ''}/${sel.profile ?? ''}`;
  }
  if (w === 'remove') {
    const p = src.profiles;
    const list = typeof p === 'string' ? p.split(',') : Array.isArray(p) ? p : [];
    return list.filter(Boolean).map(String).sort().join(',');
  }
  if (w === 'reset') return String(src.level ?? '');
  return '';
}

export class MockSetupRelay {
  private setups = new Map<string, MockSetup>();
  private tokens = new Map<string, { node: string; write: Write; what: string; until: number }>();
  private seq = 0;

  setupOf(node: string): MockSetup {
    let s = this.setups.get(node);
    if (!s) this.setups.set(node, (s = new MockSetup()));
    return s;
  }

  plan(fleet: MockFleet, nid: string, route: string, q: URLSearchParams, now: number): Reply {
    const n = fleet.nodes(now).find((x) => x.id === nid);
    if (!n) return bad(404, 'not_found', `no node ${nid}`);
    const sub = route.split('/').filter(Boolean);
    const w = writeOf('POST', sub, null);
    if (!w) return bad(404, 'not_found', `${route || '(none)'} is no Setup Guide write: one of apply, remove, reset, jobs/<id>/retry, assistant/load`);
    const a = n.alias;
    const what = bound(w, Object.fromEntries(q));
    const p: SetupConfirm = { contract_version: FLEET_CONTRACT, node: nid, route: sub.join('/'), title: '', allowed: true, blocked: null, impact: [], keeps: [], confirm: null, expires_at: null };
    if (n.status === 'off') Object.assign(p, { allowed: false, blocked: `${a} does not answer: ${n.status_text}` });
    const stops = () => {
      const deps = fleet.deployments(now).filter((d) => d.members.includes(nid) && d.status !== 'stopped');
      if (!deps.length) p.impact.push(`${a} serves nothing through this gateway now`);
      for (const d of deps) {
        // a Split needs every stage: no other member takes its requests
        const other = d.type === 'split' ? undefined : (d.member_states ?? []).find((m) => m.node !== nid && m.routable)?.alias;
        p.impact.push(other ? `${d.name}: new requests go to ${other}, another member` : `${d.name} does not answer until ${a}'s engine is back`);
      }
    };
    if (w === 'apply' || w === 'retry') {
      const [model, profile] = what.split('/');
      if (w === 'apply') {
        p.title = `Apply the Setup Guide on ${a}`;
        p.impact.push(`${a}'s engine restarts serving ${model || 'the chosen model'}${profile ? ` with ${profile}` : ''}: what it serves is away while it loads, a minute or more`);
      } else {
        p.title = `Run job ${sub[1]} again on ${a}`;
        p.impact.push(`the failed job ${sub[1]} runs again on ${a} from its selection: its engine restarts`);
      }
      stops();
      p.keeps.push(`the weights on ${a}'s disk; the Setup Guide's review lists every serve.env change`, "each deployment's record: a member serves again once its engine is up and warm");
    } else if (w === 'reset') {
      p.title = `Reset level ${what || '?'} on ${a}`;
      if (what === '2') {
        p.impact.push(`${a}'s engine restarts with the shipped configuration`);
        stops();
      } else p.impact.push(`what level ${what || '?'} lists is removed from ${a} (the Setup Guide shows every path and its size first)`);
      p.keeps.push(`TandemNode on ${a}, its pairing and this controller's records`);
    } else if (w === 'remove') {
      const names = what.split(',').filter(Boolean);
      p.title = `Remove weights on ${a}`;
      if (!names.length) Object.assign(p, { allowed: false, blocked: 'name the profiles to remove (?profiles=a,b)' });
      for (const x of names) p.impact.push(`${x} is removed from ${a}'s disk`);
      p.keeps.push(`${names.length > 1 ? 'these packs' : 'the pack'} on every other node, and every other pack on ${a}`);
    } else {
      p.title = `Wake Wren on ${a}`;
      p.impact.push(`Wren, the Setup Guide's helper, loads on ${a}: it takes memory there while the guide is open (the engine refuses it when too little would stay free)`);
      p.keeps.push(`what ${a} serves keeps running`);
    }
    if (p.allowed) {
      const t = `sc-${nid}-${++this.seq}`;
      this.tokens.set(t, { node: nid, write: w, what, until: now + CONFIRM_S });
      p.confirm = t;
      p.expires_at = now + CONFIRM_S;
    }
    return { status: 200, body: p };
  }

  /** the node's guide: true when it answered (a route MockSetup does not know answers 404 there) */
  async handle(req: IncomingMessage, res: ServerResponse, fleet: MockFleet, nid: string, sub: string[], q: URLSearchParams, raw: string): Promise<Reply | null> {
    const now = Math.floor(Date.now() / 1000);
    const n = fleet.nodes(now).find((x) => x.id === nid);
    if (!n) return bad(404, 'not_found', `no node ${nid}`);
    if (n.status === 'off') return bad(503, 'unreachable', `${n.alias} does not answer: ${n.status_text}`);
    const method = req.method ?? 'GET';
    if (method !== 'GET' && method !== 'POST') return { status: 405, body: { error: { type: 'method_not_allowed', code: 'method_not_allowed', message: 'the Setup Guide takes GET and POST' } } };
    let body: Record<string, unknown> = {};
    try {
      body = raw ? JSON.parse(raw) : {};
    } catch {
      return bad(400, 'bad_request', 'bad json');
    }
    const w = writeOf(method, sub, body);
    if (w) {
      const c = q.get('confirm');
      if (!c) return bad(428, 'confirm_required', `${w} on a node needs its plan's confirm: GET /v1/fleet/nodes/${nid}/setup-confirm/${w} and send its \`confirm\` as ?confirm=`);
      const t = this.tokens.get(c);
      this.tokens.delete(c);
      if (!t || t.node !== nid || t.write !== w || t.what !== bound(w, body) || t.until <= now) return bad(409, 'confirm_invalid', "that confirm is spent, too old, another node's or for another choice: read the plan again");
    }
    const rest = new URLSearchParams(q);
    rest.delete('confirm');
    const path = `/v1/setup/${sub.map(encodeURIComponent).join('/')}`;
    const s = this.setupOf(nid);
    // its engine restarts (a job): the agent answers for it, never the page's own origin
    if (s.away()) return bad(503, 'engine_down', `the engine on ${n.alias} does not answer: it restarts`);
    if (!(await s.handle(req, res, path, rest, async () => raw))) return bad(404, 'not_found', `no route ${path}`);
    if (w) fleet.addEvent(now, 'engine', nid, `Setup Guide on ${n.alias}: ${w.replace('/', ' ')}${bound(w, body) ? ` ${bound(w, body)}` : ''}`, `HTTP ${res.statusCode} · by admin`);
    return null;
  }
}
