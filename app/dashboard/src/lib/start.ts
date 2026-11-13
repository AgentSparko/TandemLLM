// Get started (SRV-749): the three ways into a fleet and the connect-a-server form's rules, kept
// pure so they can be tested; the page is views/fleet/start.ts. A server is tested before it is
// saved; a failed test saves nothing until the user says to save it anyway.

import type { TestResult } from '../api/config';

export interface StartPath {
  id: 'server' | 'spark' | 'engine';
  title: string;
  text: string;
  /** where it goes; null opens the form on the page */
  href: string | null;
}

/** The ways in, in the order a newcomer needs them. `engine` is where installing the TandemLLM
 *  engine from TandemApp starts; today that is Add Spark's install step. */
export const PATHS: StartPath[] = [
  { id: 'server', title: 'Connect a server', text: 'A server you already run, or a cloud API, by its URL. Any OpenAI-compatible server works.', href: null },
  { id: 'spark', title: 'Add a Spark', text: 'Pair a Spark on your network. TandemApp then reads its hardware and manages its engine.', href: '#/add-spark' },
  { id: 'engine', title: 'Install the TandemLLM engine', text: 'On a machine with an NVIDIA GPU. Add Spark installs TandemLLM there; the Setup Guide then loads a model.', href: '#/add-spark' },
];

/** why a URL cannot be tested yet; null when it can */
export function urlError(url: string): string | null {
  const u = url.trim();
  if (!u) return 'Enter the server’s URL.';
  return /^https?:\/\/[^\s/?#]+/i.test(u) ? null : 'A URL starts with http:// or https://, such as http://192.0.2.20:8000/v1.';
}

export type TestTone = 'ok' | 'warn' | 'err';

/** A test's outcome in words, and whether it needs the user's word before a save. */
export function testWords(t: TestResult): { tone: TestTone; text: string; confirm: string | null } {
  if (t.ok) {
    const n = t.models.length;
    const list = t.models.slice(0, 4).join(', ') + (n > 4 ? ` and ${n - 4} more` : '');
    return { tone: 'ok', text: `Connected${t.latency_ms != null ? ` in ${Math.round(t.latency_ms)} ms` : ''}. ${n ? `${n} model${n === 1 ? '' : 's'}: ${list}.` : 'It lists no model yet.'}`, confirm: null };
  }
  if (t.reason === 'key_rejected' || t.key_rejected) return { tone: 'warn', text: 'The server refused the API key. Nothing is saved. Check the key and test again.', confirm: 'Save it with this key anyway' };
  if (t.reason === 'not_openai') return { tone: 'err', text: `This address answers, but not like an OpenAI-compatible server (${t.error ?? 'no model list'}). Most servers want /v1 at the end of the URL.`, confirm: 'Save it anyway' };
  return { tone: 'err', text: `No answer from this address${t.error ? ` (${t.error})` : ''}. Is the server running, and reachable from this machine?`, confirm: 'Save it anyway, it may be off right now' };
}

/** Save is allowed after a test of exactly what the form holds now: a passed one, or a failed one
 *  the user confirmed. */
export function canSave(tested: string | null, now: string, t: TestResult | null, confirmed: boolean): boolean {
  return !!t && tested === now && (t.ok || confirmed);
}

/** what a test covers: a change to any of it asks for a new test */
export const formKey = (url: string, key: string, kind: string) => `${kind}\n${url.trim()}\n${key}`;

/** The Setup Guide's simple/advanced choice (setup/simple.ts, a browser memory): the advanced
 *  fields start open for someone who works in advanced mode. */
export function advancedOpen(get: (k: string) => string | null = (k) => localStorage.getItem(k)): boolean {
  try {
    return get('tandem.setup.simple') === '0';
  } catch {
    return false;
  }
}
