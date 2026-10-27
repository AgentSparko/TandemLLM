// The shell's layout (VIS-242, src/lib/layout.ts): the width classes at the prototype's breakpoints,
// the two sidebars' open state and width saved and read back, every field checked, limits kept, a
// page with its own sidebars (Chat) keeping its own open state, and a storage that throws on read,
// on write or on the property itself leaving the defaults and a working page.
import { describe, expect, it } from 'vitest';
import { CTX_OPEN_MIN, CTX_W, DEFAULT_LAYOUT, RAIL_OPEN_MIN, RAIL_W, currentWidthClass, defaultLayout, isOpen, loadLayout, opened, resized, saveLayout, toggled, widthClass } from '../src/lib/layout';

const mem = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
};

describe('width class', () => {
  it('desk above 1100 px, tablet to 1100, phone to 600', () => {
    expect([1440, 1101, 1100, 768, 601, 600, 375, 320].map(widthClass)).toEqual(['desk', 'desk', 'tablet', 'tablet', 'tablet', 'phone', 'phone', 'phone']);
  });
  it('the window’s class is read with media queries, as the CSS reads it', () => {
    const at = (w: number) => (q: string) => w <= Number(/max-width: (\d+)px/.exec(q)![1]);
    expect([1440, 1100, 600, 375].map((w) => currentWidthClass(at(w)))).toEqual(['desk', 'tablet', 'phone', 'phone']);
  });
});

describe('the sidebars', () => {
  it('open on a first visit at the prototype’s widths; Chat’s own state starts closed', () => {
    expect(loadLayout(mem())).toEqual(DEFAULT_LAYOUT);
    expect(DEFAULT_LAYOUT).toEqual({ rail: { open: true, w: 248 }, ctx: { open: true, w: 340 }, full: { rail: false, ctx: false } });
    expect(isOpen(DEFAULT_LAYOUT, 'ctx', false)).toBe(true);
    expect(isOpen(DEFAULT_LAYOUT, 'ctx', true)).toBe(false);
  });
  it('a first visit opens a sidebar only where today’s pages keep about 1000 px; saved state wins', () => {
    expect([RAIL_OPEN_MIN, CTX_OPEN_MIN]).toEqual([1400, 1700]);
    expect(defaultLayout(1920)).toEqual(DEFAULT_LAYOUT);
    expect(loadLayout(mem(), 1920)).toEqual(DEFAULT_LAYOUT);
    const at = (w: number) => {
      const l = loadLayout(mem(), w);
      return [l.rail.open, l.ctx.open];
    };
    expect([at(1700), at(1440), at(1400), at(1200)]).toEqual([
      [true, true],
      [true, false],
      [true, false],
      [false, false],
    ]);
    // the page keeps at least the width of a 1101 px window beside the old 76 px rail
    for (const w of [1200, 1400, 1440, 1700, 1920]) {
      const l = loadLayout(mem(), w);
      const content = w - 57 - (l.rail.open ? l.rail.w : 0) - (l.ctx.open ? l.ctx.w : 44) - 48;
      expect(content, String(w)).toBeGreaterThanOrEqual(1101 - 76 - 48);
    }
    const s = mem();
    const narrow = loadLayout(s, 1200);
    saveLayout(toggled(narrow, 'ctx', false), s);
    expect(loadLayout(s, 1200).ctx.open).toBe(true);
    expect(loadLayout(s, 1920).rail.open).toBe(false);
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
