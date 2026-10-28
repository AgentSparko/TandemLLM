// VIS-247 (tests for VIS-246): Settings → Add Spark in the mock fleet. The four Gherkin scenarios of the
// ticket: a LAN Spark with TandemLLM 0.15.0 by its pairing code (Check passes, Install skipped, Name
// shown, the node joins); a bare box over SSH with a password (installed, paired, the password not kept);
// a rented node from its ssh line with a key and a lease end (through the tunnel, the reminder set); a
// session closed at Install and opened again (the Install step with its progress). SRV-727's three UI
// scenarios: the scan asks first, the results say what each device is, macOS's Local Network denial
// says where to turn it on and offers Add by address. Besides: Back and Next, a reload, and the phone.
// Mock tier only.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { IS_MOCK, setMode } from './helpers';

const PASSWORD = 'spark-pass';

async function fresh(request: APIRequestContext, lan: 'allow' | 'deny' = 'allow'): Promise<void> {
  expect((await request.post('/__mock/fleet', { data: { reset: true, scenario: 'fleet4', lan } })).ok()).toBeTruthy();
}

async function open(page: Page, query = ''): Promise<void> {
  await page.goto(`./?fleet=fleet4${query}#/add-spark`);
  await expect(page.locator('qse-add-spark .wz-stepper')).toBeVisible({ timeout: 10_000 });
}

const atStep = (page: Page, step: string) => expect(page.locator('qse-add-spark .wz-body')).toHaveAttribute('data-at', step);
const row = (page: Page, ip: string) => page.locator(`.wz-found[data-ip="${ip}"]`);
const next = (page: Page) => page.locator('[data-act="next"]');
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

/** Find: the Local Network question answered, the scan said yes to, the rows in */
async function findAll(page: Page): Promise<void> {
  const lan = page.locator('[data-act="lan-go"]');
  if (await lan.isVisible().catch(() => false)) await lan.click();
  await page.locator('[data-act="scan-yes"]').click();
  await expect(row(page, '192.0.2.70')).toBeVisible({ timeout: 12_000 });
}

async function pick(page: Page, ip: string): Promise<void> {
  await row(page, ip).locator('input[type="checkbox"]').check();
  await expect(row(page, ip)).toHaveClass(/is-sel/);
}

async function sshConnect(page: Page, target: ReturnType<Page['locator']>, password?: string): Promise<void> {
  await target.locator('[data-act="hostkey"]').click();
  await expect(target.locator('[data-fp]')).toContainText('SHA256:');
  await target.locator('[data-in="user"]').fill('spark');
  if (password) {
    await target.locator('[data-in="auth"]').selectOption('password');
    await target.locator('[data-in="password"]').fill(password);
  }
  await target.locator('[data-act="connect"]').click();
}

test.describe('Add Spark', () => {
  test.skip(!IS_MOCK, 'the wizard’s devices are the mock’s');
  test.beforeEach(async ({ request }) => {
    await setMode(request, 'ok');
    await fresh(request);
  });

  test('Scan asks first: nothing scanned before the yes; the page asks "Scan 192.0.2.0/24 for SSH?"', async ({ page }) => {
    await open(page);
    // macOS asks once: the page says why before the first search
    await expect(page.locator('[data-perm="lan"]')).toContainText('TandemApp needs Local Network access to find Sparks.');
    await page.locator('[data-act="lan-go"]').click();
    await expect(page.locator('[data-scan-question]')).toHaveText('Scan 192.0.2.0/24 for SSH?');
    await expect(row(page, '192.0.2.60')).toBeVisible();
    await page.waitForTimeout(1500);
    await expect(page.locator('.wz-found[data-ip="192.0.2.70"]')).toHaveCount(0);
    const d = await (await page.request.get('/v1/fleet/discovery')).json();
    expect(d.scan).toMatchObject({ state: 'ask', done: 0, total: 0 });
    await page.locator('[data-act="scan-yes"]').click();
    await expect(page.locator('[data-scan="running"]')).toBeVisible();
    await expect(row(page, '192.0.2.70')).toBeVisible({ timeout: 12_000 });
  });

  test('Classified results: the NAS is "Linux host, not a Spark", the GX10 "GB10 box, nothing installed" once SSH read it', async ({ page }) => {
    await open(page);
    await findAll(page);
    await expect(row(page, '192.0.2.70').locator('.wz-title')).toHaveText('Linux host, not a Spark');
    await expect(row(page, '192.0.2.70').locator('input')).toBeDisabled();
    await expect(row(page, '192.0.2.60').locator('.wz-title')).toHaveText('Spark with TandemLLM 0.15.0');
    await expect(row(page, '192.0.2.62').locator('.wz-title')).toHaveText('Spark owned by another TandemApp');
    await pick(page, '192.0.2.63');
    await next(page).click();
    const t = page.locator('section[data-target="t1"]');
    await sshConnect(page, t, PASSWORD);
    await expect(t.locator('[data-state="bare"]')).toBeVisible();
    await expect(t.locator('.panel-sub')).toContainText('GB10 box, nothing installed');
    await page.locator('[data-act="back"]').click();
    await expect(row(page, '192.0.2.63').locator('.wz-title')).toHaveText('GB10 box, nothing installed');
  });

  test('Local Network denied: the page says where to turn it on and offers Add by address', async ({ page, request }) => {
    await fresh(request, 'deny');
    await open(page, '&lan=deny');
    await page.locator('[data-act="lan-go"]').click();
    const card = page.locator('[data-perm="lan-denied"]');
    await expect(card).toContainText('Local Network access is off.');
    await expect(card).toContainText('System Settings → Privacy & Security → Local Network');
    await card.locator('[data-act="denied-addr"]').click();
    await expect(page.locator('[data-panel="by-address"] [data-in="address"]')).toBeVisible();
    await expect(page.locator('[data-ctx-perm="lan"]')).toContainText('denied');
  });

  test('Gherkin: a LAN Spark with TandemLLM 0.15.0 by its pairing code: Check passes, Install skipped, Name shown, it joins', async ({ page }) => {
    await open(page);
    await findAll(page);
    await expect(page.locator('[data-act="back"]')).toBeDisabled();
    await expect(next(page)).toBeDisabled();
    await expect(page.locator('[data-why]')).toHaveText('Pick at least one device');
    await pick(page, '192.0.2.60');
    await next(page).click();
    await atStep(page, 'connect');
    const t = page.locator('section[data-target="t1"]');
    await t.locator('[data-in="code"]').fill('000000');
    await t.locator('[data-act="pair"]').click();
    await expect(t.locator('[data-err="t1"]')).toContainText('the code does not match');
    await t.locator('[data-in="code"]').fill('482913');
    await t.locator('[data-act="pair"]').click();
    await expect(t.locator('[data-state="paired"]')).toBeVisible();
    await next(page).click();
    await atStep(page, 'check');
    await page.locator('[data-act="check"]').click();
    await expect(page.locator('[data-check="ready"]')).toBeVisible();
    await expect(page.locator('[data-pf="tandemllm"]')).toContainText('0.15.0 · the fleet’s version');
    await expect(page.locator('[data-why]')).toContainText('Next skips Install and Link');
    await expect(next(page)).toHaveText('Next: Name');
    await next(page).click();
    await atStep(page, 'name');
    await expect(page.locator('[data-skipped]')).toContainText('Install skipped:');
    await expect(page.locator('[data-skipped]')).toContainText('Link skipped:');
    await expect(page.locator('.wz-step[data-step="install"]')).toHaveClass(/is-skip/);
    const n = page.locator('section[data-name]');
    await n.locator('[data-in="alias"]').fill('Desk');
    await expect(page.locator('[data-why]')).toContainText('Another Spark is called Desk.');
    await expect(next(page)).toBeDisabled();
    await n.locator('[data-in="alias"]').fill('Spark Lab');
    await n.locator('[data-colour="6"]').click();
    await n.locator('[data-in="tags"]').fill('desk, lab');
    await next(page).click();
    await atStep(page, 'done');
    await expect(page.locator('[data-panel="done"]')).toContainText('1 Spark added');
    await expect(page.locator('[data-act="serve"]')).toHaveAttribute('href', '#/models/place');
    await expect(page.locator('[data-panel="topology"] qse-topology')).toBeVisible();
    // VIS-254: the Sparks that were there already show their figures, never "no figures yet"
    await expect(page.locator('[data-panel="topology"] .node-g[data-node="desk"] title')).toContainText('tok/s');
    await expect(page.locator('[data-panel="topology"] .node-g[data-node="desk"] title')).not.toContainText('no figures yet');
    const nodes = (await (await page.request.get('/v1/fleet/nodes')).json()).nodes as { alias: string; colour: number; tags: string[] }[];
    expect(nodes.find((x) => x.alias === 'Spark Lab')).toMatchObject({ colour: 6, tags: ['desk', 'lab'] });
  });

  test('Gherkin: a bare box over SSH with a password: TandemNode and the engine installed, paired, the password not kept', async ({ page }) => {
    test.setTimeout(90_000);
    await open(page);
    await findAll(page);
    await pick(page, '192.0.2.63');
    await next(page).click();
    const t = page.locator('section[data-target="t1"]');
    await sshConnect(page, t, PASSWORD);
    await expect(t.locator('[data-secret="held"]')).toContainText('never written anywhere');
    await next(page).click();
    await page.locator('[data-act="check"]').click();
    const red = page.locator('[data-pf="clock"][data-state="fail"]');
    await expect(red).toBeVisible();
    await expect(red.locator('.wz-fix')).toContainText('sudo timedatectl set-ntp true');
    await expect(page.locator('[data-why]')).toHaveText('Fix the red rows, then check again');
    await page.locator('[data-act="recheck"]').click();
    await expect(page.locator('[data-check="ready"]')).toBeVisible();
    await expect(next(page)).toHaveText('Next: Install');
    await next(page).click();
    await page.locator('[data-act="install"]').click();
    const inst = page.locator('.wz-inst-row[data-target="t1"]');
    await expect(inst).toHaveAttribute('data-install', 'running');
    await inst.locator('[data-act="log"]').click();
    await expect(inst.locator('[data-log]')).toContainText('uploaded tandemllm-0.15.0', { timeout: 10_000 });
    await expect(inst).toHaveAttribute('data-install', 'done', { timeout: 40_000 });
    await expect(inst.locator('[data-step="engine"]')).toHaveClass(/is-done/);
    const s = await (await page.request.get('/v1/fleet/add')).json();
    expect(JSON.stringify(s)).not.toContain(PASSWORD);
    expect(s.session.targets[0]).toMatchObject({ state: 'paired', secret: { held: false } });
    await next(page).click();
    await atStep(page, 'name');
    await next(page).click();
    await expect(page.locator('[data-panel="done"]')).toContainText('1 Spark added');
  });

  test('Gherkin: rented from its ssh line with a key file and a lease end: through a tunnel, the reminder before the lease ends', async ({ page }) => {
    test.setTimeout(90_000);
    await open(page);
    const lan = page.locator('[data-act="lan-go"]');
    if (await lan.isVisible().catch(() => false)) await lan.click();
    await page.locator('[data-act="manual-rent"]').click();
    const f = page.locator('[data-panel="rented"]');
    await f.locator('[data-in="ssh-line"]').fill('ssh -p 2201 spark@198.51.100.7');
    await f.locator('[data-in="key-file"]').setInputFiles({ name: 'rent_ed25519', mimeType: 'text/plain', buffer: Buffer.from('-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n-----END OPENSSH PRIVATE KEY-----\n') });
    const end = new Date(Date.now() + 6 * 86400_000);
    const day = `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, '0')}-${String(end.getDate()).padStart(2, '0')}`;
    await f.locator('[data-in="lease-end"]').fill(day);
    await f.locator('[data-in="cost"]').fill('2.40');
    await f.locator('[data-act="rent-read"]').click();
    await expect(f.locator('[data-route]')).toContainText('spark@198.51.100.7:2201');
    await f.locator('[data-act="rent-add"]').click();
    await expect(page.locator('.wz-remote [data-target="t1"]')).toContainText('Rented Spark');
    await next(page).click();
    const t = page.locator('section[data-target="t1"]');
    await t.locator('[data-act="hostkey"]').click();
    await expect(t.locator('[data-in="auth"]')).toHaveValue('key');
    await t.locator('[data-act="connect"]').click();
    await expect(t.locator('[data-state="bare"]')).toBeVisible();
    await next(page).click();
    await page.locator('[data-act="check"]').click();
    await expect(page.locator('[data-check="ready"]')).toBeVisible();
    await next(page).click();
    await page.locator('[data-act="install"]').click();
    await expect(page.locator('.wz-inst-row[data-target="t1"]')).toHaveAttribute('data-install', 'done', { timeout: 40_000 });
    await next(page).click();
    await atStep(page, 'name');
    await expect(page.locator('[data-skipped]')).toContainText('through a tunnel');
    await next(page).click();
    const rem = page.locator('[data-reminders] [data-reminder]');
    await expect(rem).toContainText('72 h before the lease of');
    const nodes = (await (await page.request.get('/v1/fleet/nodes')).json()).nodes as { transport: string; lease_end: number | null; tags: string[] }[];
    const n = nodes.find((x) => x.transport === 'tunnel' && x.tags.includes('rented') && x.lease_end && x.lease_end > Date.now() / 1000 + 5 * 86400);
    expect(n).toBeTruthy();
  });

  test('Gherkin: closed at Install, opened again: the Install step with its progress (a reload, the same session)', async ({ page }) => {
    test.setTimeout(60_000);
    await open(page);
    await findAll(page);
    await pick(page, '192.0.2.63');
    await next(page).click();
    await sshConnect(page, page.locator('section[data-target="t1"]'), PASSWORD);
    await next(page).click();
    await page.locator('[data-act="check"]').click();
    await page.locator('[data-act="recheck"]').click();
    await expect(page.locator('[data-check="ready"]')).toBeVisible();
    await next(page).click();
    await page.locator('[data-act="install"]').click();
    await expect(page.locator('.wz-inst-row[data-target="t1"]')).toHaveAttribute('data-install', 'running');
    await page.waitForTimeout(5000);
    await page.reload();
    await expect(page.locator('qse-add-spark .wz-stepper')).toBeVisible();
    await atStep(page, 'install');
    const inst = page.locator('.wz-inst-row[data-target="t1"]');
    await expect(inst).toHaveAttribute('data-install', 'running');
    const pc = Number((await inst.locator('[role="progressbar"]').getAttribute('aria-valuenow')) ?? '0');
    expect(pc).toBeGreaterThan(0);
    await expect(inst.locator('.wz-steps-mini .is-done')).not.toHaveCount(0);
    await expect(page.locator('.wz-step[data-step="install"]')).toHaveClass(/is-on/);
  });

  test('Back and Next on every step; the stepper goes back to a step that was done; Start over', async ({ page }) => {
    await open(page);
    await findAll(page);
    await pick(page, '192.0.2.60');
    await next(page).click();
    await atStep(page, 'connect');
    await page.locator('[data-act="back"]').click();
    await atStep(page, 'find');
    await expect(row(page, '192.0.2.60')).toHaveClass(/is-sel/);
    await next(page).click();
    const t = page.locator('section[data-target="t1"]');
    await t.locator('[data-in="code"]').fill('482913');
    await t.locator('[data-act="pair"]').click();
    await next(page).click();
    await atStep(page, 'check');
    await page.locator('.wz-step[data-step="connect"]').click();
    await atStep(page, 'connect');
    await expect(page.locator('.wz-step[data-step="check"]')).toBeDisabled();
    await page.locator('[data-act="restart"]').click();
    await atStep(page, 'find');
    await expect(page.locator('.wz-found.is-sel')).toHaveCount(0);
  });

  test('the phone: one column, no side scroll, the stepper and the foot on screen', async ({ page }, info) => {
    test.skip(!/phone/.test(info.project.name), 'the phone layout');
    await open(page);
    await findAll(page);
    expect(await noSideScroll(page)).toBe(true);
    await pick(page, '192.0.2.60');
    await next(page).scrollIntoViewIfNeeded();
    await expect(next(page)).toBeInViewport();
    await expect(page.locator('[data-why]')).toBeInViewport();
    await next(page).click();
    expect(await noSideScroll(page)).toBe(true);
    await expect(page.locator('.wz-step.is-on .l')).toBeVisible();
    await expect(page.locator('section[data-target="t1"] [data-in="code"]')).toBeInViewport();
  });
});
