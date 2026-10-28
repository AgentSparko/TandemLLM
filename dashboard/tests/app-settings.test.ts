// 6.3b: App settings' logic (lib/app-settings.ts: the permission rows with this browser's notification
// state, the password's state and form, the configuration file, an import's plan and the Updates
// line) and the mock's App routes (mock/appsettings.ts): the permissions per host, the export without
// secrets, the import's plan, 428 without the confirm, 409 spent or the fleet changed, applied. Every
// mock answer validates against fleet-v1.

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import { describe, expect, it } from 'vitest';
import { MockFleet } from '../mock/fleet.ts';
import type { AppPermission, AppView, FleetConfigFile, ImportPlan, ImportResult, PasswordState } from '../src/api/fleet.ts';
import { applyLabel, browserNotify, exportName, importGroups, passwordBlocked, passwordError, passwordStatus, permRows, planSummary, readConfig, resultWords, updateLine } from '../src/lib/app-settings.ts';

const FDIR = join(__dirname, '..', '..', 'docs', 'contract', 'fleet-v1');
const ajv = new Ajv2020({ allErrors: true, strict: true });
const schema: Record<string, ReturnType<typeof ajv.compile>> = {};
for (const f of readdirSync(FDIR)) if (f.endsWith('.schema.json')) schema[f.replace('.schema.json', '')] = ajv.compile(JSON.parse(readFileSync(join(FDIR, f), 'utf8')));
function valid<T>(name: string, data: T): T {
  if (!schema[name](data)) throw new Error(`${name}: ${ajv.errorsText(schema[name].errors, { separator: '\n' })}`);
  return data;
}
const T0 = Date.parse('2026-10-28T09:00:00Z');
const q = () => new URLSearchParams();
const PW_STATE: PasswordState = { set: false, password: false, token: false, set_at: null, min_length: 12, max_length: 1024, this_machine: true };
const perm = (o: Partial<AppPermission>): AppPermission => ({ id: 'local_network', title: 'Local Network', applies: true, state: 'granted', why: 'w', detail: 'd', fallback: null, action: null, ...o });

describe('the permission rows', () => {
  it('names each state, and merges this browser’s notification permission', () => {
    const rows = permRows(
      [perm({ state: 'lan_denied', fallback: 'Run the controller on a Spark', action: { kind: 'check', label: 'Check now' } }), perm({ id: 'keys', state: 'not_applicable', applies: false }), perm({ id: 'notifications', state: 'in_browser', action: { kind: 'browser', label: 'Allow notifications' } }), perm({ id: 'node_sudo', state: 'per_action' })],
      'default',
    );
    expect(rows.map((r) => [r.id, r.badge, r.tone])).toEqual([
      ['local_network', 'Denied', 'err'],
      ['keys', 'Does not apply', 'muted'],
      ['notifications', 'Not asked yet', 'warn'],
      ['node_sudo', 'Asked per action', 'muted'],
    ]);
    expect(rows[2].action?.label).toBe('Allow notifications');
    const denied = permRows([perm({ id: 'notifications', state: 'in_browser', action: { kind: 'browser', label: 'Allow' } })], 'denied')[0];
    expect([denied.badge, denied.action]).toEqual(['Blocked', null]);
    expect(denied.detail).toContain('site settings');
    const none = permRows([perm({ id: 'notifications', state: 'in_browser', action: { kind: 'browser', label: 'Allow' } })], 'unsupported')[0];
    expect(none.badge).toBe('Not offered');
    expect(none.detail).toContain('https');
  });

  it('reads the browser: no API or an http page is unsupported', () => {
    expect(browserNotify({})).toBe('unsupported');
    expect(browserNotify({ Notification: { permission: 'granted' }, isSecureContext: false })).toBe('unsupported');
    expect(browserNotify({ Notification: { permission: 'denied' }, isSecureContext: true })).toBe('denied');
  });
});

describe('the admin password', () => {
  it('says its state', () => {
    expect(passwordStatus(PW_STATE).title).toBe('No admin password');
    expect(passwordStatus({ ...PW_STATE, set: true, token: true }).title).toBe('The admin token');
    const both = passwordStatus({ ...PW_STATE, set: true, token: true, password: true, set_at: T0 / 1000 }, () => 'today');
    expect(both.title).toBe('Set since today');
    expect(both.sub).toContain('bearer for tools');
  });

  it('checks the form as the controller would', () => {
    const f = (o = {}) => ({ current: '', next: 'a long passphrase', again: 'a long passphrase', ...o });
    expect(passwordError(PW_STATE, f())).toBeNull();
    expect(passwordError(PW_STATE, f({ next: 'short', again: 'short' }))).toBe('A password is 12 to 1024 characters.');
    expect(passwordError(PW_STATE, f({ next: '            ', again: '            ' }))).toBe('A password is more than spaces.');
    expect(passwordError(PW_STATE, f({ next: 'tab\there long one', again: 'tab\there long one' }))).toBe('A password has no control characters.');
    expect(passwordError(PW_STATE, f({ again: 'something else' }))).toBe('The two new passwords differ.');
    expect(passwordError({ ...PW_STATE, set: true }, f())).toContain('current password');
    expect(passwordError({ ...PW_STATE, set: true }, f({ current: 'x' }))).toBeNull();
  });

  it('sets the first one only from the controller’s machine', () => {
    expect(passwordBlocked(PW_STATE)).toBeNull();
    expect(passwordBlocked({ ...PW_STATE, this_machine: false })).toContain('tandem-app password set');
    expect(passwordBlocked({ ...PW_STATE, set: true, this_machine: false })).toBeNull();
  });
});

describe('the configuration file', () => {
  it('names the file by its day and reads what it may send', () => {
    expect(exportName(new Date(2026, 9, 8))).toBe('tandemapp-fleet-config-2026-10-08.json');
    expect(readConfig('{', 1)).toEqual({ error: 'This is not a JSON file.' });
    expect(readConfig('[]', 2)).toEqual({ error: 'This is not a TandemApp fleet configuration.' });
    expect('error' in readConfig('{"kind":"tandemapp-export"}', 30) && readConfig('{"kind":"tandemapp-export"}', 30)).toEqual({ error: expect.stringContaining('Take the fleet') });
    expect(readConfig('{}', 5 * 1024 * 1024)).toEqual({ error: expect.stringContaining('4 MiB') });
    expect(readConfig('{"kind":"tandemapp-fleet-config","version":1}', 40)).toEqual({ doc: { kind: 'tandemapp-fleet-config', version: 1 } });
  });

  it('groups a plan with its conflicts first, and says it', () => {
    const items = [
      { kind: 'node' as const, id: 'a', label: 'A', op: 'keep' as const, detail: null, fields: [] },
      { kind: 'node' as const, id: 'b', label: 'B', op: 'add' as const, detail: null, fields: [] },
      { kind: 'rule' as const, id: 'r', label: 'r', op: 'conflict' as const, detail: 'x', fields: [] },
    ];
    expect(importGroups({ items }).map((g) => [g.op, g.items.length])).toEqual([['conflict', 1], ['add', 1], ['keep', 1]]);
    const c = { add: 2, change: 4, remove: 0, keep: 9, conflict: 3 };
    expect(planSummary(c)).toBe('2 added · 4 changed · 3 skipped');
    expect(planSummary({ add: 0, change: 0, remove: 0, keep: 1, conflict: 0 })).toBe('nothing to change');
    expect(applyLabel(c)).toBe('Import 6 changes');
    expect(applyLabel({ ...c, add: 1, change: 0 })).toBe('Import 1 change');
    expect(resultWords({ contract_version: '1.0', mode: 'merge', applied: { add: 1, change: 0, remove: 2 }, skipped: 0, items: [] })).toBe('Imported: 1 added · 2 removed.');
  });
});

describe('the Updates line', () => {
  const n = (alias: string, version: string, status: 'up' | 'off' = 'up') => ({ alias, version, status });
  it('counts the Sparks on another release', () => {
    expect(updateLine('0.15.1', []).text).toContain('No Spark');
    expect(updateLine('0.15.1', [n('A', '0.15.1'), n('B', '0.15.1+abc')]).behind).toBe(1);
    const mixed = updateLine('0.15.1', [n('A', '0.15.1'), n('B', '0.15.0'), n('C', '0.14.0', 'off')]);
    expect([mixed.behind, mixed.mixed]).toEqual([1, true]);
    expect(mixed.text).toContain('1 Spark runs another release');
    expect(updateLine(null, [n('A', '0.15.0')]).text).toContain('not known');
  });
});

describe('the mock’s App routes', () => {
  it('reports the permissions of a Spark host and of a Mac host', () => {
    const spark = new MockFleet({ scenario: 'quad', now: T0 / 1000 });
    const v = spark.handle('GET', '/v1/fleet/app', q(), null, T0).body as Omit<AppView, 'password'>;
    const withPw = valid('app', { ...v, password: PW_STATE } as AppView);
    expect(withPw.platform).toBe('linux');
    expect(withPw.permissions.map((p) => [p.id, p.state])).toEqual([['local_network', 'not_applicable'], ['keys', 'not_applicable'], ['notifications', 'in_browser'], ['node_sudo', 'per_action']]);
    const mac = new MockFleet({ scenario: 'gold3', now: T0 / 1000 });
    mac.discovery.lanMode = 'deny';
    expect((mac.handle('GET', '/v1/fleet/app', q(), null, T0).body as { permissions: AppPermission[] }).permissions[0].state).toBe('not_asked');
    const after = mac.handle('POST', '/v1/fleet/app/permissions/local-network', q(), {}, T0).body as { permissions: AppPermission[]; service: { manager: string } };
    expect(after.permissions[0].state).toBe('lan_denied');
    expect(after.permissions[0].fallback).toContain('Spark');
    expect(after.service.manager).toBe('launchd');
  });

  it('exports without secrets, plans an import, and applies it only with its confirm', () => {
    const m = new MockFleet({ scenario: 'fleet4', now: T0 / 1000 });
    const doc = valid('fleet-config', m.handle('GET', '/v1/fleet/app/export', q(), null, T0).body as FleetConfigFile);
    expect(doc.secrets).toBe('excluded');
    expect(JSON.stringify(doc)).not.toMatch(/"trust"|BEGIN|"key"/);
    const file = JSON.parse(JSON.stringify(doc)) as FleetConfigFile;
    const first = file.nodes[0];
    first.alias = 'Renamed';
    file.nodes.push({ ...first, id: 'n-new', alias: 'Newbie', colour: first.colour, address: '192.0.2.99', controller_host: false, paired: false, pin_sha256: null });
    file.rules[0].enabled = !file.rules[0].enabled;
    const plan = valid('import-plan', m.handle('POST', '/v1/fleet/app/import', q(), { config: file }, T0).body as ImportPlan);
    expect(plan.counts).toMatchObject({ add: 1, change: 2 });
    expect(plan.items.find((i) => i.id === 'n-new')?.detail).toContain('unpaired');
    expect(plan.items.find((i) => i.id === 'n-new')?.detail).toContain(`colour ${first.colour}`);
    expect(m.handle('POST', '/v1/fleet/app/import/apply', q(), { config: file }, T0).status).toBe(428);
    expect(m.handle('POST', '/v1/fleet/app/import/apply', q(), { config: doc, confirm: plan.confirm }, T0).status).toBe(409);
    const p2 = m.handle('POST', '/v1/fleet/app/import', q(), { config: file }, T0).body as ImportPlan;
    m.handle('PATCH', `/v1/fleet/nodes/${file.nodes[1].id}`, q(), { tags: ['moved'] }, T0);
    const stale = m.handle('POST', '/v1/fleet/app/import/apply', q(), { config: file, confirm: p2.confirm }, T0);
    expect([stale.status, (stale.body as { error: { message: string } }).error.message]).toEqual([409, expect.stringContaining('changed')]);
    const p3 = m.handle('POST', '/v1/fleet/app/import', q(), { config: file }, T0).body as ImportPlan;
    const done = m.handle('POST', '/v1/fleet/app/import/apply', q(), { config: file, confirm: p3.confirm }, T0);
    expect(done.status).toBe(200);
    valid('import-result', done.body as ImportResult);
    const nodes = (m.handle('GET', '/v1/fleet/nodes', q(), null, T0).body as { nodes: { id: string; alias: string; pairing: { state: string }; status: string }[] }).nodes;
    expect(nodes.find((x) => x.id === first.id)?.alias).toBe('Renamed');
    const added = nodes.find((x) => x.id === 'n-new');
    expect([added?.alias, added?.pairing.state, added?.status]).toEqual(['Newbie', 'unpaired', 'off']);
    expect(m.handle('POST', '/v1/fleet/app/import/apply', q(), { config: file, confirm: p3.confirm }, T0).status).toBe(409);
    const again = m.handle('POST', '/v1/fleet/app/import', q(), { config: file }, T0).body as ImportPlan;
    expect([again.allowed, again.confirm]).toEqual([false, null]);
    expect(m.handle('POST', '/v1/fleet/app/import', q(), { config: { kind: 'tandemapp-export' } }, T0).status).toBe(400);
  });
});
