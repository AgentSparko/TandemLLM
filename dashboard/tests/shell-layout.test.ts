// The shell's measures (VIS-242, VIS-256, src/lib/layout.ts): the width classes at the prototype's
// breakpoints, the sidebars a first visit opens (Chat's list always, its Context from 1180 px; every
// other page from 1400 and 1700 px), and what a device saved before layouts were per page, read once
// so it keeps what it had: every field checked, a storage that throws read as nothing.
import { describe, expect, it } from 'vitest';
import { CHAT_CTX_OPEN_MIN, CTX_OPEN_MIN, CTX_W, RAIL_OPEN_MIN, RAIL_W, currentWidthClass, defaultSides, legacySides, widthClass } from '../src/lib/layout';

const mem = (o: Record<string, string> = {}) => {
  const m = new Map<string, string>(Object.entries(o));
  return { getItem: (k: string) => m.get(k) ?? null, m };
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

describe('the sidebars of a first visit', () => {
  it('open only where today’s pages keep about 1000 px', () => {
    expect([RAIL_OPEN_MIN, CTX_OPEN_MIN]).toEqual([1400, 1700]);
    const at = (w: number) => {
      const s = defaultSides(w);
      return [s.rail.open, s.ctx.open];
    };
    expect([at(1920), at(1700), at(1440), at(1400), at(1200)]).toEqual([
      [true, true],
      [true, true],
      [true, false],
      [true, false],
      [false, false],
    ]);
    // the page keeps at least the width of a 1101 px window beside the old 76 px rail
    for (const w of [1200, 1400, 1440, 1700, 1920]) {
      const s = defaultSides(w);
      const content = w - 57 - (s.rail.open ? s.rail.w : 0) - (s.ctx.open ? s.ctx.w : 44) - 48;
      expect(content, String(w)).toBeGreaterThanOrEqual(1101 - 76 - 48);
    }
  });
  it('on Chat: the list always, the Context panel from 1180 px, as before it moved into the shell', () => {
    expect(CHAT_CTX_OPEN_MIN).toBe(1180);
    expect(defaultSides(1440, true)).toEqual({ rail: { open: true, w: RAIL_W.def }, ctx: { open: true, w: CTX_W.def } });
    expect(defaultSides(1179, true).ctx.open).toBe(false);
    expect(defaultSides(1179, true).rail.open).toBe(true);
  });
  it('take the device’s widths', () => {
    expect(defaultSides(1920, false, { rail: 300, ctx: 420 })).toEqual({ rail: { open: true, w: 300 }, ctx: { open: true, w: 420 } });
  });
});

describe('what a device saved before', () => {
  it('2.2’s one layout and Chat’s own list and Context, each field checked', () => {
    expect(legacySides(mem())).toBeNull();
    expect(legacySides(null)).toBeNull();
    const s = mem({ 'tandemapp.layout': JSON.stringify({ rail: { open: false, w: 2000 }, ctx: { open: true, w: 300 }, full: { rail: true, ctx: false } }), 'qse.chat.rail': 'closed', 'qse.chat.ctx': 'open' });
    expect(legacySides(s)).toEqual({ all: { rail: { open: false, w: RAIL_W.max }, ctx: { open: true, w: 300 } }, chat: { rail: false, ctx: true } });
    expect(legacySides(mem({ 'tandemapp.layout': JSON.stringify({ rail: { open: 'yes', w: 1 }, ctx: { w: 300 } }) }))).toBeNull();
    for (const raw of ['not json', 'null', '"x"', '42']) expect(legacySides(mem({ 'tandemapp.layout': raw })), raw).toBeNull();
    expect(legacySides(mem({ 'qse.chat.rail': 'maybe' }))).toBeNull();
  });
  it('a storage that throws is read as nothing', () => {
    const throwing = {
      getItem: () => {
        throw new Error('SecurityError');
      },
    };
    expect(legacySides(throwing)).toBeNull();
  });
});
