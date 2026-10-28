// The engine's requests beside the ones the proxy relays (0.11.3): the Performance and Usage
// views compare them, never pool them (a cloud p50 of 300 tok/s read as the engine's).
import { describe, expect, it } from 'vitest';
import { backendName, compareRows, relayedBackends, scatterGroup } from '../src/lib/relayed';
import { isOwnBackend } from '../src/api/client';

const OWN = 'engine';
import { SCATTER_COLORS } from '../src/charts/scatter';
import type { BackendRef, Usage } from '../src/api/types';

const usage = (over: Partial<Usage['totals']> = {}, backends: BackendRef[] = [{ id: OWN, kind: 'engine', label: 'this engine' }, { id: 'deepseek', kind: 'deepseek', label: 'DeepSeek (cloud)' }]): Usage =>
  ({ dimensions: { models: [], clients: [], backends }, totals: { requests: 10, prompt_tokens: 1000, cached_tokens: 800, completion_tokens: 300, decode_tps_p50: 35.6, ttft_ms_p50: 824, cost_micros: null, ...over } }) as unknown as Usage;

describe('relayed backends', () => {
  it('lists the backends beside the engine', () => {
    expect(relayedBackends(usage()).map((b) => b.id)).toEqual(['deepseek']);
    expect(relayedBackends(usage({}, [{ id: OWN, kind: 'engine', label: null }]))).toEqual([]);
    expect(relayedBackends(null)).toEqual([]);
    expect(backendName({ id: 'deepseek', label: null })).toBe('deepseek');
  });
  it('puts a relayed request in a scatter group of its own, never an engine cache tier', () => {
    expect(scatterGroup({ backend: { id: 'deepseek', kind: 'deepseek', label: 'DeepSeek (cloud)' } }, 'cold')).toBe('relayed');
    expect(scatterGroup({ backend: null }, 'end')).toBe('end');
    expect(SCATTER_COLORS.relayed).toBeDefined();
  });
  it('compares the engine with each relayed backend, cost on the cloud row', () => {
    const rows = compareRows(usage(), [{ ref: { id: 'deepseek', kind: 'deepseek', label: 'DeepSeek (cloud)' }, usage: usage({ requests: 16, decode_tps_p50: 268.4, ttft_ms_p50: 1388, cost_micros: 61_234 }) }]);
    expect(rows.map((r) => [r.id, r.own, r.name, r.decodeP50, r.cost])).toEqual([
      ['engine', true, 'this engine', 35.6, null],
      ['deepseek', false, 'DeepSeek (cloud)', 268.4, 61_234],
    ]);
  });
  it("knows the ledger's own rows by the server's word, never by a constant of the views", () => {
    expect(isOwnBackend({ id: 'engine' })).toBe(true);
    expect(isOwnBackend({ id: 'deepseek' })).toBe(false);
    expect(isOwnBackend(null)).toBe(false);
  });
});
