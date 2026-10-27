// The shell's layout (VIS-242, src/lib/layout.ts): the width classes at the prototype's breakpoints,
// the two sidebars' open state and width saved and read back, every field checked, limits kept, a
// page with its own sidebars (Chat) keeping its own open state, and a storage that throws on read,
// on write or on the property itself leaving the defaults and a working page.
import { describe, expect, it } from 'vitest';
import { BOTH_OPEN_MIN, CTX_W, DEFAULT_LAYOUT, RAIL_W, defaultLayout, isOpen, loadLayout, opened, resized, saveLayout, toggled, widthClass } from '../src/lib/layout';

const mem = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
};

describe('width class', () => {
  it('desk above 1100 px, tablet to 1100, phone to 600', () => {
    expect([1440, 1101, 1100, 768, 601, 600, 375, 320].map(widthClass)).toEqual(['desk', 'desk', 'tablet', 'tablet', 'tablet', 'phone', 'phone', 'phone']);
  });
});

describe('the sidebars', () => {
  it('open on a first visit at the prototype’s widths; Chat’s own state starts closed', () => {
    expect(loadLayout(mem())).toEqual(DEFAULT_LAYOUT);
    expect(DEFAULT_LAYOUT).toEqual({ rail: { open: true, w: 248 }, ctx: { open: true, w: 340 }, full: { rail: false, ctx: false } });
    expect(isOpen(DEFAULT_LAYOUT, 'ctx', false)).toBe(true);
    expect(isOpen(DEFAULT_LAYOUT, 'ctx', true)).toBe(false);
  });
  it('below the design’s 1440 px both start as strips, so today’s pages keep their width; saved state wins', () => {
    expect(BOTH_OPEN_MIN).toBe(1440);
    expect(defaultLayout(1440)).toEqual(DEFAULT_LAYOUT);
    expect(loadLayout(mem(), 1920)).toEqual(DEFAULT_LAYOUT);
    const narrow = loadLayout(mem(), 1200);
    expect([narrow.rail.open, narrow.ctx.open, narrow.rail.w, narrow.ctx.w]).toEqual([false, false, 248, 340]);
    const s = mem();
    saveLayout(toggled(narrow, 'ctx', false), s);
    expect(loadLayout(s, 1200).ctx.open).toBe(true);
    expect(loadLayout(s, 1200).rail.open).toBe(false);
  });
  it('a toggle and a resize are saved and read back', () => {
    const s = mem();
    let l = toggled(loadLayout(s), 'ctx', false);
    l = resized(l, 'rail', 300);
    expect(saveLayout(l, s)).toBe(true);
    expect(loadLayout(s)).toEqual({ rail: { open: true, w: 300 }, ctx: { open: false, w: 340 }, full: { rail: false, ctx: false } });
  });
  it('on a full page the toggle is that page’s own', () => {
    const l = toggled(DEFAULT_LAYOUT, 'rail', true);
    expect(l.full.rail).toBe(true);
    expect(l.rail.open).toBe(true);
    expect(isOpen(toggled(l, 'rail', false), 'rail', true)).toBe(true);
  });
  it('a selection opens the context sidebar, and leaves an open one as it is', () => {
    const closed = toggled(DEFAULT_LAYOUT, 'ctx', false);
    expect(opened(closed, 'ctx', false).ctx.open).toBe(true);
    expect(opened(DEFAULT_LAYOUT, 'ctx', false)).toBe(DEFAULT_LAYOUT);
  });
  it('widths stay within their limits', () => {
    expect(resized(DEFAULT_LAYOUT, 'rail', 10).rail.w).toBe(RAIL_W.min);
    expect(resized(DEFAULT_LAYOUT, 'rail', 9999).rail.w).toBe(RAIL_W.max);
    expect(resized(DEFAULT_LAYOUT, 'ctx', 100).ctx.w).toBe(CTX_W.min);
    expect(resized(DEFAULT_LAYOUT, 'ctx', 333.6).ctx.w).toBe(334);
  });
  it('what is saved is checked: a wrong or missing field reads as its default, a width is clamped', () => {
    const s = mem();
    s.m.set('tandemapp.layout', JSON.stringify({ rail: { open: 'yes', w: 2000 }, ctx: { w: Number.NaN }, full: { ctx: true } }));
    expect(loadLayout(s)).toEqual({ rail: { open: true, w: RAIL_W.max }, ctx: { open: true, w: 340 }, full: { rail: false, ctx: true } });
    for (const raw of ['not json', 'null', '"x"', '42']) {
      s.m.set('tandemapp.layout', raw);
      expect(loadLayout(s), raw).toEqual(DEFAULT_LAYOUT);
    }
  });
  it('a storage that throws leaves the defaults and a working page', () => {
    const throwing = {
      getItem: () => {
        throw new Error('SecurityError');
      },
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    };
    expect(loadLayout(throwing)).toEqual(DEFAULT_LAYOUT);
    expect(saveLayout(DEFAULT_LAYOUT, throwing)).toBe(false);
    expect(loadLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(saveLayout(DEFAULT_LAYOUT, null)).toBe(false);
  });
  it('a browser whose localStorage property itself throws', () => {
    const d = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError');
      },
    });
    try {
      expect(loadLayout()).toEqual(DEFAULT_LAYOUT);
      expect(saveLayout(DEFAULT_LAYOUT)).toBe(false);
    } finally {
      if (d) Object.defineProperty(globalThis, 'localStorage', d);
      else delete (globalThis as { localStorage?: unknown }).localStorage;
    }
  });
});
