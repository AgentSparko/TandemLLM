// The chart modes (VIS-242): the aggregate per operation with gaps, Stacked refused for values that do
// not add up, the bands, the Table's rows, the capacity line, the axis top, and the mode kept per chart.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { aggregate, capacity, chartModel, effectiveMode, modeNote, modeRefusal, saveMode, savedMode, stackLayers, stats, tableRows, type NodeSeries } from '../src/charts/modes';

const S = (id: string, colour: number, values: (number | null)[]): NodeSeries => ({ id, alias: id.toUpperCase(), colour, values });
const a = [10, 20, null, 40];
const b = [30, null, null, 0];
const c = [5, 5, null, null];

describe('aggregate', () => {
  it('sum, mean and max over the nodes that have a sample; none has one: null', () => {
    expect(aggregate([a, b, c], 'sum')).toEqual({ total: [45, 25, null, 40], mean: null });
    expect(aggregate([a, b, c], 'mean').total).toEqual([15, 12.5, null, 20]);
    expect(aggregate([a, b, c], 'max').total).toEqual([30, 20, null, 40]);
  });
  it('maxmean: the max, with the mean beside it for the dashed line', () => {
    expect(aggregate([a, b, c], 'maxmean')).toEqual({ total: [30, 20, null, 40], mean: [15, 12.5, null, 20] });
  });
  it('series of different lengths, and no series at all', () => {
    expect(aggregate([[1, 2, 3], [4]], 'sum').total).toEqual([5, 2, 3]);
    expect(aggregate([], 'sum')).toEqual({ total: [], mean: null });
  });
});

describe('modes', () => {
  it('Stacked only for values that add up, with the reason', () => {
    expect(modeRefusal('stacked', 'sum')).toBeNull();
    for (const agg of ['mean', 'max', 'maxmean'] as const) expect(modeRefusal('stacked', agg)).toMatch(/^Only values that add up can stack/);
    expect(modeRefusal('stacked', 'maxmean')).toMatch(/a max and a mean$/);
    for (const m of ['total', 'lines', 'split', 'table'] as const) expect(modeRefusal(m, 'mean')).toBeNull();
  });
  it('the drawn mode: a refused one falls back to Lines; one node has no aggregate but keeps its Table', () => {
    expect(effectiveMode('stacked', 'mean', 4)).toBe('lines');
    expect(effectiveMode('stacked', 'sum', 4)).toBe('stacked');
    expect(effectiveMode('total', 'sum', 1)).toBe('lines');
    expect(effectiveMode('split', 'sum', 1)).toBe('lines');
    expect(effectiveMode('table', 'sum', 1)).toBe('table');
  });
  it('the note says what the chart shows', () => {
    expect(modeNote('total', 'sum', 4)).toBe('sum over 4 Sparks');
    expect(modeNote('total', 'maxmean', 3)).toBe('max and mean of 3 Sparks');
    expect(modeNote('lines', 'sum', 1)).toBe('one Spark in scope');
  });
});

describe('stacked bands', () => {
  it('each band starts where the one below ends; a node without a sample has no band there', () => {
    const l = stackLayers([S('a', 1, a), S('b', 2, b)]);
    expect(l[0]).toEqual({ id: 'a', lo: [0, 0, null, 0], hi: [10, 20, null, 40] });
    expect(l[1]).toEqual({ id: 'b', lo: [10, null, null, 40], hi: [40, null, null, 40] });
  });
});

describe('the Table', () => {
  it('now is the newest sample; min, mean, max skip the gaps; an empty series is all null', () => {
    expect(stats([3, null, 1, 5, null])).toEqual({ now: 5, min: 1, mean: 3, max: 5 });
    expect(stats([null, null])).toEqual({ now: null, min: null, mean: null, max: null });
  });
  it('a row per node and the aggregate row labelled by its operation', () => {
    const t = tableRows([S('a', 1, a), S('b', 2, b)], 'sum');
    expect(t.rows.map((r) => [r.id, r.stats.now])).toEqual([['a', 40], ['b', 0]]);
    expect(t.total).toEqual({ label: 'Σ', now: 40, min: 20, mean: (40 + 20 + 40) / 3, max: 40 });
    expect(tableRows([S('a', 1, a)], 'maxmean').total.label).toBe('max');
    expect(tableRows([S('a', 1, a)], 'mean').total.label).toBe('mean');
  });
});

describe('capacity and the axis', () => {
  it('the fleet capacity for Total and Stacked of a sum, one node’s for Lines and Split, none else', () => {
    expect(capacity('total', 'sum', 121.6, 4)).toEqual({ v: 486.4, perNode: false });
    expect(capacity('stacked', 'sum', 121.6, 4)).toEqual({ v: 486.4, perNode: false });
    expect(capacity('lines', 'sum', 121.6, 4)).toEqual({ v: 121.6, perNode: true });
    expect(capacity('split', 'sum', 121.6, 4)).toEqual({ v: 121.6, perNode: true });
    expect(capacity('total', 'mean', 100, 4)).toBeNull();
    expect(capacity('lines', 'sum', null, 4)).toBeNull();
  });
  it('the top covers what is drawn: the total, the stack, the highest node, the capacity', () => {
    const ser = [S('a', 1, [100, 120]), S('b', 2, [80, 90])];
    expect(chartModel({ series: ser, agg: 'sum', mode: 'total' }).top).toBeGreaterThanOrEqual(210);
    expect(chartModel({ series: ser, agg: 'sum', mode: 'stacked' }).top).toBeGreaterThanOrEqual(210);
    const lines = chartModel({ series: ser, agg: 'sum', mode: 'lines' });
    expect(lines.top).toBeGreaterThanOrEqual(120);
    expect(lines.top).toBeLessThan(210);
    expect(chartModel({ series: ser, agg: 'sum', mode: 'lines', cap: 150 }).top).toBeGreaterThanOrEqual(156);
  });
  it('a fixed top (percentages) holds for per-node and mean charts, not for a summed Total', () => {
    const ser = [S('a', 1, [60, 70]), S('b', 2, [50, 90])];
    expect(chartModel({ series: ser, agg: 'mean', mode: 'total', max: 100 }).top).toBe(100);
    expect(chartModel({ series: ser, agg: 'mean', mode: 'split', max: 100 }).top).toBe(100);
    expect(chartModel({ series: ser, agg: 'sum', mode: 'total', max: 100 }).top).toBeGreaterThan(100);
  });
  it('the model draws the effective mode, the stack only when stacking, and counts the samples', () => {
    const ser = [S('a', 1, [1, 2, 3]), S('b', 6, [1, 1, 1])];
    const m = chartModel({ series: ser, agg: 'maxmean', mode: 'stacked' });
    expect([m.mode, m.layers.length, m.count, m.mean]).toEqual(['lines', 0, 3, [1, 1.5, 2]]);
    expect(chartModel({ series: ser, agg: 'sum', mode: 'stacked' }).layers).toHaveLength(2);
    expect(chartModel({ series: [], agg: 'sum', mode: 'total' })).toMatchObject({ mode: 'lines', count: 0, total: [] });
  });
});

describe('the chosen mode per chart', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('is kept for the session without storage (no localStorage in private mode)', () => {
    expect(savedMode('mem-test-1')).toBeNull();
    saveMode('mem-test-1', 'split');
    expect(savedMode('mem-test-1')).toBe('split');
  });
  it('is kept on the device; a broken or unknown entry is ignored and replaced', () => {
    const store: Record<string, string> = { 'qse.chart-modes': '{not json' };
    vi.stubGlobal('localStorage', { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => (store[k] = v) });
    expect(savedMode('dev-1')).toBeNull();
    saveMode('dev-1', 'table');
    expect(JSON.parse(store['qse.chart-modes'])).toEqual({ 'dev-1': 'table' });
    store['qse.chart-modes'] = JSON.stringify({ 'dev-2': 'stacked', 'dev-3': 'pie' });
    expect(savedMode('dev-2')).toBe('stacked');
    expect(savedMode('dev-3')).toBeNull();
    saveMode('dev-4', 'total');
    expect(JSON.parse(store['qse.chart-modes'])).toEqual({ 'dev-2': 'stacked', 'dev-3': 'pie', 'dev-4': 'total' });
  });
  it('a storage that throws changes nothing but the session copy', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } });
    expect(savedMode('dev-5')).toBeNull();
    saveMode('dev-5', 'lines');
    expect(savedMode('dev-5')).toBe('lines');
  });
});
