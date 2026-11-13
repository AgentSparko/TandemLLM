// What the Playground sends to (VIS-254, concept §8.10): this server's own model, as before, or on a
// fleet a deployment through TandemApp's gateway (the deployment picks its member), or one member of
// it pinned (`X-Tandem-Pin`: that Spark only, for an A/B of the same prompt on two Sparks). A stopped
// deployment serves nothing and is not offered; a Split is one target (its tail answers), never pinned.

import { api } from '../api/client';
import type { FleetDeployment, FleetNode } from '../api/fleet';
import { TYPE_WORD } from '../lib/models';
import type { FleetView } from '../lib/where';

export interface Target {
  /** '' is this server's own model; `dep:<id>` a deployment; `pin:<id>:<node>` one of its members */
  key: string;
  label: string;
  deployment: FleetDeployment | null;
  pin: FleetNode | null;
}

export const HERE: Target = { key: '', label: 'This server', deployment: null, pin: null };

export function targetsOf(f: FleetView | null): Target[] {
  const out: Target[] = [HERE];
  if (!f) return out;
  for (const d of f.deployments) {
    if (d.state === 'stopped' || d.status === 'stopped') continue;
    out.push({ key: `dep:${d.id}`, label: `${d.name} · ${TYPE_WORD[d.type]}`, deployment: d, pin: null });
    if (d.members.length < 2 || d.type === 'split') continue;
    for (const id of d.members) {
      const n = f.nodes.find((x) => x.id === id);
      if (n) out.push({ key: `pin:${d.id}:${id}`, label: `${d.name} on ${n.alias}`, deployment: d, pin: n });
    }
  }
  return out;
}

/** a key's target, this server's own for one the fleet no longer has */
export function targetOf(list: Target[], key: string | null | undefined): Target {
  return list.find((t) => t.key === (key ?? '')) ?? HERE;
}

/** Compare's first pair: two members of the first deployment that has two, else the first two targets. */
export function comparePair(list: Target[]): [string, string] {
  const pins = list.filter((t) => t.pin);
  const d = pins[0]?.deployment;
  const two = d ? pins.filter((t) => t.deployment?.id === d.id) : [];
  if (two.length >= 2) return [two[0].key, two[1].key];
  return [list[0]?.key ?? '', list[1]?.key ?? list[0]?.key ?? ''];
}

/** Where a target's request goes, the model it names and its headers. */
export function sendTo(t: Target, hereModel: string): { url: string; model: string; headers: Record<string, string> } {
  if (!t.deployment) return { url: api.chatUrl(), model: hereModel, headers: {} };
  return { url: api.gatewayUrl(), model: t.deployment.api_id, headers: t.pin ? { 'X-Tandem-Pin': t.pin.id } : {} };
}
