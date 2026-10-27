// The node chip (VIS-242): a Spark's identity wherever it appears -- its colour swatch, its alias and a
// status glyph. Colour is identity only; the status is a glyph with its word for screen readers and
// in the tooltip, never a colour. Colours 5..8 reuse the four node hues as a hollow swatch (and a
// dashed line in charts).

import { html, nothing, type TemplateResult } from 'lit';
import type { FleetNode, NodeStatus } from '../api/fleet';

export const STATUS_GLYPH: Record<NodeStatus, string> = { up: '●', draining: '◐', off: '○', alert: '⚠' };

type ChipNode = Pick<FleetNode, 'alias' | 'colour'> & Partial<Pick<FleetNode, 'id' | 'status' | 'status_text' | 'hw'>>;

const clampColour = (c: number) => Math.min(8, Math.max(1, Math.round(c) || 1));

/** The CSS colour of a node's identity (--n1..--n4). */
export function nodeColour(colour: number): string {
  return `var(--n${((clampColour(colour) - 1) % 4) + 1})`;
}

/** Colours 5..8: a hollow swatch and a dashed line. */
export function isHollow(colour: number): boolean {
  return clampColour(colour) > 4;
}

/** The status in words: `up`, `draining`, `off`, or the alert's own text. */
export function statusWords(n: Pick<ChipNode, 'status' | 'status_text'>): string {
  if (!n.status) return '';
  if (n.status === 'alert') return n.status_text && n.status_text !== 'alert' ? `alert: ${n.status_text}` : 'alert';
  return n.status;
}

/** What a screen reader hears: the alias and, with a status, its words. */
export function chipLabel(n: ChipNode, withStatus = true): string {
  const w = withStatus ? statusWords(n) : '';
  return w ? `${n.alias}, ${w}` : n.alias;
}

export interface ChipOptions {
  /** show the status glyph (default: when the node has a status) */
  status?: boolean;
  /** a link to the node's page */
  href?: string;
  lg?: boolean;
  /** muted text after the alias (a role, a stage) */
  extra?: string;
}

export function nodeChip(n: ChipNode, o: ChipOptions = {}): TemplateResult {
  const st = o.status !== false && n.status ? n.status : null;
  const title = [n.alias, n.hw, st ? statusWords(n) : ''].filter(Boolean).join(' · ');
  const body = html`<i class="node-sw ${isHollow(n.colour) ? 'is-hollow' : ''}" style="--c:${nodeColour(n.colour)}" aria-hidden="true"></i><span class="node-alias">${n.alias}</span>${st
      ? html`<span class="node-st is-${st}" aria-hidden="true">${STATUS_GLYPH[st]}</span><span class="sr-only">, ${statusWords(n)}</span>`
      : nothing}${o.extra ? html`<span class="node-extra">${o.extra}</span>` : nothing}`;
  const cls = `node-chip ${o.lg ? 'is-lg' : ''}`;
  return o.href ? html`<a class=${cls} href=${o.href} title=${title} data-node=${n.id ?? nothing}>${body}</a>` : html`<span class=${cls} title=${title} data-node=${n.id ?? nothing}>${body}</span>`;
}
