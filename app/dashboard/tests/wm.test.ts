// The window manager's model (VIS-256, QA VIS-257, src/lib/wm.ts): each page's default windows, opening
// (and bringing back the same window), closing with the neighbour tab shown, docking, floating,
// minimising and restoring to where it was, the docks' shares and the float's size within limits,
// everything to one place, tiling, a page's own windows (Chat's panes) placed and kept, and a saved
// layout read back with every field checked. Every layout it makes validates against the contract.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { describe, expect, it } from 'vitest';
import type { Layout } from '../src/api/fleet';
import { defaultSides } from '../src/lib/layout';
import {
  CHAT_DEFAULTS,
  DOCK_F,
  FLOAT_MIN,
  PAGE_WINDOWS,
  SOURCE_KEEP,
  activate,
  allTo,
  autoDock,
  close,
  defaultLayout,
  dockOf,
  move,
  normalise,
  open,
  place,
  resizeSide,
  restore,
  sameWindow,
  setDockF,
  setRef,
  setSide,
  tile,
  withSource,
  withoutSource,
} from '../src/lib/wm';

const ajv = new Ajv2020({ allErrors: true, strict: true });
const schema = ajv.compile(JSON.parse(readFileSync(join(__dirname, '..', '..', 'contracts', 'fleet-v1', 'layout.schema.json'), 'utf8')));
const valid = (l: Layout) => {
  if (!schema(l)) throw new Error(ajv.errorsText(schema.errors));
  return true;
};
const fresh = (page: string, device: Layout['device'] = 'desk') => defaultLayout(page, device, defaultSides(1440, page === 'chat'));

describe('the default windows of each page', () => {
  it('Overview a log and a chart below, Hardware a terminal on a Spark, Dev a terminal and a diff beside; the rest none', () => {
    const kinds = (p: string) => fresh(p).windows.map((w) => `${w.kind}@${w.at}`);
    expect(kinds('overview')).toEqual(['logs@bottom', 'chart@bottom']);
    expect(kinds('hardware')).toEqual(['terminal@bottom']);
    expect(fresh('hardware').windows[0].ref).toEqual({ node: null });
    expect(kinds('dev')).toEqual(['terminal@right', 'diff@right']);
    for (const p of ['chat', 'performance', 'usage', 'events', 'sparks', 'models', 'api', 'conductor', 'playground', 'add-spark', 'app']) expect(kinds(p), p).toEqual([]);
    expect(CHAT_DEFAULTS).toEqual(['terminal', 'files', 'preview']);
  });
  it('the first window of each dock shows; the shares are the page’s; nothing saved yet', () => {
    const o = fresh('overview');
    expect(o.docks.bottom).toEqual({ f: 0.3, act: o.windows[0].id });
    expect(o.docks.right).toEqual({ f: DOCK_F.right.def, act: null });
    expect(fresh('dev').docks.right.f).toBe(0.36);
    expect(o.saved_at).toBeNull();
    for (const p of [...Object.keys(PAGE_WINDOWS), 'usage']) for (const d of ['desk', 'tablet', 'phone'] as const) expect(valid(fresh(p, d))).toBe(true);
  });
  it('a phone and a tablet get the same windows, each layout its own device', () => {
    expect(fresh('overview', 'phone').windows.map((w) => w.kind)).toEqual(['logs', 'chart']);
    expect(fresh('overview', 'phone').device).toBe('phone');
  });
});

describe('opening and closing', () => {
  it('opens where asked, or where the page’s windows are', () => {
    const base = fresh('usage');
    expect(autoDock(base, 'usage')).toBe('right');
    expect(autoDock(base, 'chat')).toBe('bottom');
    expect(autoDock(fresh('overview'), 'usage')).toBe('bottom');
    const { layout, id } = open(base, 'logs', { node: 'r3' }, 'auto', 'usage');
    expect(layout.windows).toHaveLength(1);
    expect(layout.windows[0]).toMatchObject({ id, kind: 'logs', at: 'right', ref: { node: 'r3' } });
    expect(layout.docks.right.act).toBe(id);
    const f = open(layout, 'chart', {}, 'float', 'usage');
    expect(f.layout.windows.find((w) => w.id === f.id)).toMatchObject({ at: 'float' });
    expect(f.layout.windows.find((w) => w.id === f.id)!.z).toBeGreaterThan(layout.windows[0].z);
    expect(valid(f.layout)).toBe(true);
  });
  it('the same window comes back (its ref updated, from the corner too); another target is another window', () => {
    let l = open(fresh('hardware'), 'terminal', { node: 'r3' }, 'bottom', 'hardware').layout;
    const r3 = l.windows.find((w) => w.ref.node === 'r3')!;
    l = move(l, r3.id, 'min');
    const again = open(l, 'terminal', { node: 'r3', extra: 1 }, 'right', 'hardware');
    expect(again.id).toBe(r3.id);
    expect(again.layout.windows.find((w) => w.id === r3.id)).toMatchObject({ at: 'bottom', ref: { node: 'r3', extra: 1 } });
    expect(open(l, 'terminal', { node: 'r1' }, 'auto', 'hardware').id).not.toBe(r3.id);
    expect(sameWindow('chat', {}, {})).toBe(false);
    expect(sameWindow('logs', { node: 'a', level: 'warn' }, { node: 'a' })).toBe(true);
  });
  it('a closed tab hands its dock to the tab before it, else the one after', () => {
    let l = fresh('usage');
    const ids = ['logs', 'chart', 'inspector'].map((k) => {
      const r = open(l, k as 'logs', {}, 'bottom', 'usage');
      l = r.layout;
      return r.id;
    });
    l = activate(l, ids[1]);
    expect(close(l, ids[1]).docks.bottom.act).toBe(ids[0]);
    l = activate(l, ids[0]);
    expect(close(l, ids[0]).docks.bottom.act).toBe(ids[1]);
    // closing a tab that does not show leaves the shown one
    expect(close(l, ids[2]).docks.bottom.act).toBe(ids[0]);
    expect(close(close(close(l, ids[0]), ids[1]), ids[2]).docks.bottom.act).toBeNull();
    expect(close(l, 'nope')).toBe(l);
  });
});

describe('docking, floating, minimising', () => {
  const two = () => {
    const a = open(fresh('usage'), 'logs', {}, 'bottom', 'usage');
    const b = open(a.layout, 'chart', {}, 'bottom', 'usage');
    return { l: b.layout, a: a.id, b: b.id };
  };
  it('moves a tab to the other dock, where it shows', () => {
    const { l, a, b } = two();
    const m = move(l, b, 'right');
    expect(dockOf(m, 'right')).toMatchObject({ act: { id: b } });
    expect(dockOf(m, 'bottom').act?.id).toBe(a);
    expect(m.docks.bottom.act).toBe(a);
  });
  it('a window docked anew is its dock’s last tab; one already there keeps its place', () => {
    const { l, a, b } = two();
    const c = open(l, 'inspector', {}, 'right', 'usage');
    const back = move(move(c.layout, a, 'right'), a, 'bottom');
    expect(dockOf(back, 'bottom').wins.map((w) => w.id)).toEqual([b, a]);
    expect(move(l, a, 'bottom').windows.map((w) => w.id)).toEqual([a, b]);
  });
  it('a minimised window remembers its dock and returns there; one that floated returns to float', () => {
    const { l, b } = two();
    const min = move(l, b, 'min');
    expect(min.windows.find((w) => w.id === b)).toMatchObject({ at: 'min', prev: 'bottom' });
    expect(valid(min)).toBe(true);
    expect(restore(min, b).windows.find((w) => w.id === b)).toMatchObject({ at: 'bottom' });
    expect(restore(min, b).windows.find((w) => w.id === b)!.prev).toBeUndefined();
    const fl = move(move(l, b, 'float'), b, 'min');
    expect(activate(fl, b).windows.find((w) => w.id === b)!.at).toBe('float');
    // a second minimise keeps the first place
    expect(move(min, b, 'min').windows.find((w) => w.id === b)!.prev).toBe('bottom');
  });
  it('the float pressed last comes on top', () => {
    let l = fresh('usage');
    const a = open(l, 'logs', {}, 'float', 'usage');
    const b = open(a.layout, 'chart', {}, 'float', 'usage');
    l = activate(b.layout, a.id);
    expect(Math.max(...l.windows.map((w) => w.z))).toBe(l.windows.find((w) => w.id === a.id)!.z);
    expect(activate(l, a.id)).toBe(l);
  });
  it('a float is never smaller than its minimum nor above the top; a dock stays within its share', () => {
    const { l, a } = two();
    const p = place(move(l, a, 'float'), a, { x: -50.4, y: -20, w: 10, h: 10 });
    expect(p.windows.find((w) => w.id === a)).toMatchObject({ x: -50, y: 0, w: FLOAT_MIN.w, h: FLOAT_MIN.h });
    expect(setDockF(l, 'right', 0.05).docks.right.f).toBe(DOCK_F.right.min);
    expect(setDockF(l, 'bottom', 2).docks.bottom.f).toBe(DOCK_F.bottom.max);
    expect(setDockF(l, 'bottom', 0.4444).docks.bottom.f).toBe(0.444);
  });
  it('everything to one dock, floating (cascaded) or minimised; the floats tiled', () => {
    const { l } = two();
    expect(allTo(l, 'right').windows.every((w) => w.at === 'right')).toBe(true);
    const fl = allTo(l, 'float');
    expect(new Set(fl.windows.map((w) => `${w.x},${w.y}`)).size).toBe(2);
    expect(allTo(l, 'min').windows.every((w) => w.at === 'min' && w.prev === 'bottom')).toBe(true);
    const t = tile(fl, 1000, 600);
    const [x, y] = t.windows;
    expect(x.y).toBe(y.y);
    expect(x.x + x.w).toBeLessThanOrEqual(y.x);
    expect(tile(l, 1000, 600)).toBe(l);
  });
  it('a window’s ref changes; the sidebars open, close and size within their limits', () => {
    const { l, a } = two();
    expect(setRef(l, a, { node: 'r3' }).windows.find((w) => w.id === a)!.ref).toMatchObject({ node: 'r3' });
    expect(setSide(l, 'ctx', !l.ctx.open).ctx.open).toBe(!l.ctx.open);
    expect(setSide(l, 'ctx', l.ctx.open)).toBe(l);
    expect(resizeSide(l, 'rail', 9).rail.w).toBe(200);
    expect(resizeSide(l, 'ctx', 333.6).ctx.w).toBe(334);
  });
});

describe('a page’s own windows (Chat’s panes)', () => {
  const src = [
    { id: 'chat:p1', kind: 'terminal' as const },
    { id: 'chat:p2', kind: 'files' as const },
  ];
  it('show in the page’s dock where they never sat, beside the page’s windows, in the page’s order', () => {
    const base = open(fresh('chat'), 'logs', {}, 'right', 'chat').layout;
    const shown = withSource(base, src, 'bottom');
    expect(shown.windows.map((w) => `${w.id}@${w.at}`)).toEqual(['w1@right', 'chat:p1@bottom', 'chat:p2@bottom']);
    expect(shown.windows[1].ref).toEqual({ source: true });
    expect(valid(shown)).toBe(true);
  });
  it('keep where they were moved, and the places of panes not shown now (another chat’s), up to a limit', () => {
    const shown = withSource(fresh('chat'), src, 'bottom');
    const moved = move(shown, 'chat:p2', 'float');
    const kept = withoutSource(moved, fresh('chat'));
    // another chat without p2: it is not shown, its place stays
    const other = withSource(kept, [src[0]], 'bottom');
    expect(other.windows.map((w) => w.id)).toEqual(['chat:p1']);
    const back = withoutSource(other, kept);
    expect(back.windows.find((w) => w.id === 'chat:p2')!.at).toBe('float');
    expect(withSource(back, src, 'bottom').windows.find((w) => w.id === 'chat:p2')!.at).toBe('float');
    let many = fresh('chat');
    for (let i = 0; i < SOURCE_KEEP + 10; i++) many = withoutSource(withSource(many, [{ id: `chat:x${i}`, kind: 'diff' }], 'bottom'), many);
    expect(many.windows.filter((w) => w.ref.source)).toHaveLength(SOURCE_KEEP);
    expect(many.windows.some((w) => w.id === `chat:x${SOURCE_KEEP + 9}`)).toBe(true);
  });
});

describe('a saved layout read back', () => {
  it('as saved, when it is this page on this device', () => {
    const l = { ...fresh('overview'), saved_at: 1_800_000_000 };
    expect(normalise(JSON.parse(JSON.stringify(l)), 'overview', 'desk')).toEqual(l);
    expect(normalise(l, 'usage', 'desk')).toBeNull();
    expect(normalise(l, 'overview', 'phone')).toBeNull();
    for (const raw of [null, 42, 'x', {}, { page: 'overview', device: 'desk' }]) expect(normalise(raw, 'overview', 'desk')).toBeNull();
  });
  it('every field checked: unknown kinds and places dropped, numbers brought within limits, a stray tab forgotten', () => {
    const l = fresh('overview');
    const raw = {
      ...l,
      rail: { open: 'yes', w: 9999 },
      ctx: { w: Number.NaN },
      windows: [
        { ...l.windows[0], w: 10, h: -5, y: -40, z: 'top', prev: 'right' },
        { ...l.windows[1], kind: 'clock' },
        { ...l.windows[1], id: 'w9', at: 'left' },
        { ...l.windows[0] },
        { id: 'w5', kind: 'logs', ref: [1, 2], at: 'min', prev: 'bottom', x: 1, y: 2, w: 300, h: 200, z: 3 },
      ],
      docks: { right: { f: 9, act: 'w1' }, bottom: { f: 'x', act: 'nope' } },
      saved_at: 'yesterday',
    };
    const n = normalise(raw, 'overview', 'desk')!;
    expect(n.rail).toEqual({ open: true, w: 420 });
    expect(n.ctx).toEqual({ open: true, w: 340 });
    expect(n.windows.map((w) => w.id)).toEqual([l.windows[0].id, 'w5']);
    expect(n.windows[0]).toMatchObject({ w: FLOAT_MIN.w, h: FLOAT_MIN.h, y: 0, z: 10 });
    expect(n.windows[0].prev).toBeUndefined();
    expect(n.windows[1]).toMatchObject({ ref: {}, prev: 'bottom' });
    expect(n.docks).toEqual({ right: { f: DOCK_F.right.max, act: null }, bottom: { f: DOCK_F.bottom.def, act: null } });
    expect(n.saved_at).toBeNull();
    expect(valid(n)).toBe(true);
  });
});
