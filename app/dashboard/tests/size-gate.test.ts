// OPS-246: the size budget is a CI gate on the built output. scripts/check-size.mjs, the step CI runs
// after the build, on fixture folders: a total over the budget exits 1 and prints the size and the
// budget; under it exits 0; the bundle's manifest (bundle.json) is not measured; no folder exits 2.
import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { BUDGET, MANIFEST } from '../scripts/gzip-size.mjs';

const SCRIPT = join(__dirname, '..', 'scripts', 'check-size.mjs');
const ROOT = mkdtempSync(join(tmpdir(), 'size-gate-'));
afterAll(() => rmSync(ROOT, { recursive: true, force: true }));

const fixture = (name: string, files: Record<string, Uint8Array | string>) => {
  const dir = join(ROOT, name);
  for (const [path, body] of Object.entries(files)) {
    const p = join(dir, path);
    mkdirSync(join(p, '..'), { recursive: true });
    writeFileSync(p, body);
  }
  return dir;
};
const check = (dir: string) => spawnSync(process.execPath, [SCRIPT, dir], { encoding: 'utf8' });

describe('check-size.mjs (the CI size gate)', () => {
  it('fails over the budget and prints the size and the budget', () => {
    // random bytes do not compress: their gzip size is above their length
    const dir = fixture('over', { 'index.html': '<!doctype html>', 'assets/app-x.js': randomBytes(BUDGET + 4096) });
    const r = check(dir);
    expect(r.status).toBe(1);
    expect(r.stdout).toMatch(new RegExp(`total gzip \\(budget ${BUDGET / 1024} KB, -\\d+ bytes left\\)`));
    expect(r.stderr).toMatch(new RegExp(`over budget: \\d+ bytes gzip \\(\\d+\\.\\d KB\\) > budget ${BUDGET} bytes`));
  });
  it('passes under the budget', () => {
    const dir = fixture('under', { 'index.html': '<!doctype html>', 'assets/app-x.js': 'console.log(1)'.repeat(1000) });
    const r = check(dir);
    expect(r.status).toBe(0);
    expect(r.stdout).toMatch(/assets\/app-x\.js/);
  });
  it('does not measure the manifest', () => {
    const dir = fixture('manifest', { 'index.html': '<!doctype html>', [MANIFEST]: randomBytes(BUDGET + 4096) });
    const r = check(dir);
    expect(r.status).toBe(0);
    expect(r.stdout).not.toMatch(/bundle\.json/);
  });
  it('says to build first when there is no folder', () => {
    const r = check(join(ROOT, 'missing'));
    expect(r.status).toBe(2);
    expect(r.stderr).toMatch(/build the page first/);
  });
});
