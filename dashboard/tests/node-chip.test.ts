// The node chip (VIS-242): identity colours 1..8 (5..8 hollow), and a status that is a glyph with its
// words for screen readers and the tooltip, never a colour alone.
import { describe, expect, it } from 'vitest';
import { nothing, type TemplateResult } from 'lit';
import { chipLabel, isHollow, nodeChip, nodeColour, statusWords, STATUS_GLYPH } from '../src/ui/node-chip';

/** the text and attribute values a template renders, flattened (no DOM in these tests) */
function text(t: unknown): string {
  if (t == null || t === nothing) return '';
  if (Array.isArray(t)) return t.map(text).join('');
  const r = t as TemplateResult;
  if (r && Array.isArray(r.strings)) return r.strings.map((s, i) => s + (i < r.values.length ? text(r.values[i]) : '')).join('');
  return String(t);
}

describe('node chip', () => {
  it('colours 1..4 are the node hues, 5..8 the same hues hollow; out of range is clamped', () => {
    expect([1, 2, 3, 4, 5, 6, 7, 8].map(nodeColour)).toEqual(['var(--n1)', 'var(--n2)', 'var(--n3)', 'var(--n4)', 'var(--n1)', 'var(--n2)', 'var(--n3)', 'var(--n4)']);
    expect([1, 4, 5, 8].map(isHollow)).toEqual([false, false, true, true]);
    expect([nodeColour(0), nodeColour(12), isHollow(12)]).toEqual(['var(--n1)', 'var(--n4)', true]);
  });
  it('every status has a glyph and words; an alert says what it is', () => {
    expect(STATUS_GLYPH).toEqual({ up: '●', draining: '◐', off: '○', alert: '⚠' });
    expect(statusWords({ status: 'up', status_text: 'up' })).toBe('up');
    expect(statusWords({ status: 'draining', status_text: 'draining' })).toBe('draining');
    expect(statusWords({ status: 'off', status_text: 'not reachable' })).toBe('off');
    expect(statusWords({ status: 'alert', status_text: 'thermal throttle, GPU 86 °C' })).toBe('alert: thermal throttle, GPU 86 °C');
    expect(statusWords({ status: 'alert', status_text: '' })).toBe('alert');
    expect(chipLabel({ alias: 'R-3', colour: 4, status: 'alert', status_text: 'thermal throttle' })).toBe('R-3, alert: thermal throttle');
    expect(chipLabel({ alias: 'R-3', colour: 4, status: 'off' }, false)).toBe('R-3');
  });
  it('renders the glyph hidden from screen readers and the words for them; no status, no glyph', () => {
    const r3 = { id: 'r3', alias: 'R-3', colour: 4, status: 'alert' as const, status_text: 'thermal throttle', hw: 'DGX Spark' };
    const t = text(nodeChip(r3));
    expect(t).toContain('aria-hidden="true">⚠</span>');
    expect(t).toContain('<span class="sr-only">, alert: thermal throttle</span>');
    expect(t).toContain('R-3 · DGX Spark · alert: thermal throttle');
    expect(t).toContain('--c:var(--n4)');
    const quiet = text(nodeChip(r3, { status: false }));
    expect(quiet).not.toContain('⚠');
    expect(quiet).not.toContain('sr-only');
    expect(text(nodeChip({ alias: 'N-5', colour: 5 }))).toContain('is-hollow');
    expect(text(nodeChip(r3, { href: '#/sparks/r3' }))).toMatch(/^<a class=.*href=#\/sparks\/r3/);
  });
});
