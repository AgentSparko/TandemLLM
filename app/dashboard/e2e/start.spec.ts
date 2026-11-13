// SRV-750 (tests for SRV-749): Get started and a backend in the fleet, in the mock tier. The first
// open of a fleet with no member offers the three ways in; a server is connected by its URL (a
// refused key says so and saves nothing; a passed test saves, and the key is in no later answer);
// the backend then stands beside the Sparks in the scope bar and on Overview, its Hardware says no
// host figures and offers TandemNode, and the Sparks page keeps to Sparks. Screenshots in
// e2e-artifacts/srv749/.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { IS_MOCK, setMode } from './helpers';

const SHOTS = 'e2e-artifacts/srv749';
const URL_OK = 'http://192.0.2.20:8000/v1';
const KEY = 'sk-fixture-START00001234';

async function fresh(request: APIRequestContext, scenario: string): Promise<void> {
  expect((await request.post('/__mock/fleet', { data: { reset: true, scenario } })).ok()).toBeTruthy();
  expect((await request.post('/__mock/backends', { data: { reset: true } })).ok()).toBeTruthy();
}
/** a picture once the page's blocks have risen in */
async function shot(page: Page, name: string, project: string): Promise<void> {
  await expect(page.locator('main.content')).not.toHaveClass(/is-entering/, { timeout: 5_000 });
  await page.screenshot({ path: `${SHOTS}/${name}-${project === 'chromium-desktop' ? 'desktop' : 'phone'}.png` });
}
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test.describe('Get started and backends in the fleet', () => {
  test.skip(!IS_MOCK, 'the fleet and the backends are the mock’s');
  test.beforeEach(async ({ request }) => {
    await setMode(request, 'ok');
  });
  test.afterEach(async ({ request }) => {
    await fresh(request, 'fleet4');
  });

  test('First run without a Spark: it offers connect a server, add a Spark and install the engine', async ({ page, request }, info) => {
    await fresh(request, 'empty');
    await page.goto('./?fleet=empty');
    await expect(page).toHaveURL(/#\/start$/, { timeout: 10_000 });
    const head = page.locator('.page-head[data-page="start"]');
    await expect(head).toHaveAttribute('data-first-run', 'true');
    await expect(head.locator('h1')).toHaveText('Get started');
    const paths = page.locator('qse-start .start-paths .ncard');
    await expect(paths).toHaveCount(3);
    await expect(page.locator('[data-path="server"]')).toContainText('Connect a server');
    await expect(page.locator('[data-path="spark"]')).toContainText('Add a Spark');
    await expect(page.locator('[data-path="engine"]')).toContainText('Install the TandemLLM engine');
    await expect(page.locator('[data-path="spark"]')).toHaveAttribute('href', '#/add-spark');
    await expect(page.locator('[data-path="engine"]')).toHaveAttribute('href', '#/add-spark');
    await expect(page.locator('[data-connect]')).toHaveCount(0);
    expect(await noSideScroll(page)).toBe(true);
    await shot(page, 'first-run', info.project.name);
    // the keyboard opens the form and lands in its first field
    await page.locator('[data-path="server"]').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-connect]')).toBeVisible();
    await expect(page.locator('[data-connect] input[name="url"]')).toBeFocused();
    await expect(page.locator('[data-act="save"]')).toBeDisabled();
    // a route given is kept: no detour through Get started
    await page.goto('./?fleet=empty#/app');
    await expect(page).toHaveURL(/#\/app$/);
    // and the menu keeps the page reachable
    await page.goto('./?fleet=empty#/start');
    await expect(page.locator('.crumb')).toContainText('Get started');
  });

  test('Connect a server: a refused key saves nothing; a passed test saves and the key is not answered again', async ({ page, request }, info) => {
    await fresh(request, 'empty');
    const seen: string[] = [];
    page.on('response', (r) => {
      if (r.url().includes('/v1/')) void r.text().then((t) => seen.push(t)).catch(() => undefined);
    });
    await page.goto('./?fleet=empty#/start');
    await page.locator('[data-path="server"]').click();
    const form = page.locator('[data-connect]');
    // no URL: said in words, nothing asked
    await form.locator('[data-act="test"]').click();
    await expect(form.locator('[data-error]')).toContainText('Enter the server');
    await form.locator('input[name="url"]').fill(URL_OK);
    await form.locator('input[name="key"]').fill('wrong-key-00000000');
    await form.locator('[data-act="test"]').click();
    await expect(form.locator('[data-test="key_rejected"]')).toContainText('refused the API key');
    await expect(form.locator('[data-test="key_rejected"]')).toContainText('Nothing is saved');
    await expect(form.locator('[data-act="save"]')).toBeDisabled();
    const before = await (await request.get('/v1/config')).json();
    expect(before.backends.some((b: { url: string }) => b.url === URL_OK)).toBe(false);
    // only the user's word unlocks a save of a refused key
    await form.locator('input[name="confirm"]').check();
    await expect(form.locator('[data-act="save"]')).toBeEnabled();
    await form.locator('input[name="confirm"]').uncheck();
    await expect(form.locator('[data-act="save"]')).toBeDisabled();
    // a changed form needs a new test
    await form.locator('input[name="key"]').fill(KEY);
    await expect(form.locator('[data-test="stale"]')).toBeVisible();
    await expect(form.locator('[data-act="save"]')).toBeDisabled();
    await form.locator('[data-act="test"]').click();
    await expect(form.locator('[data-test="ok"]')).toContainText('2 models: tiny-a, tiny-b');
    if (info.project.name === 'chromium-desktop') {
      await form.locator('details.start-adv summary').click();
      await form.locator('input[name="label"]').fill('Bench');
    }
    expect(await noSideScroll(page)).toBe(true);
    await shot(page, 'connect-tested', info.project.name);
    await form.locator('[data-act="save"]').click();
    const saved = page.locator('[data-saved]');
    await expect(saved).toContainText('is in your fleet');
    await expect(saved).toContainText('2 models ready');
    await expect(page.locator('[data-connect]')).toHaveCount(0);
    await expect(page.locator('.page-head[data-page="start"] h1')).toHaveText('Add to your fleet', { timeout: 20_000 });
    // its models are deployments
    await saved.locator('a', { hasText: 'See its models' }).click();
    await expect(page).toHaveURL(/#\/models/);
    await expect(page.locator('qse-models')).toContainText('tiny-a', { timeout: 10_000 });
    await expect(page.locator('qse-models')).toContainText('tiny-b');
    // the key went out once and came back in no answer
    for (const path of ['/v1/config', '/v1/fleet/nodes', '/v1/fleet/deployments']) seen.push(await (await request.get(path)).text());
    expect(seen.length).toBeGreaterThan(3);
    for (const t of seen) expect(t.includes(KEY)).toBe(false);
  });

  test('A backend beside the Sparks: the scope bar and Overview list it, Hardware says no host figures and offers TandemNode', async ({ page, request }, info) => {
    await fresh(request, 'fleet4');
    const r = await request.post('/v1/config/backends', { data: { url: URL_OK, label: 'Bench', verify: true } });
    expect(r.status()).toBe(201);
    const id = 'backend.bench';
    await page.goto('./?fleet=fleet4#/overview');
    const card = page.locator(`.ncard[data-node="${id}"]`);
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card.locator('[data-no-figures]')).toContainText('No host figures');
    await expect(card).toHaveAttribute('href', '#/api/backends');
    await expect(page.locator('[data-fleet-health]')).toContainText(/\d members up/);
    if (info.project.name === 'chromium-desktop') {
      await page.locator('.scope-dd-btn').click();
      const menu = page.locator('.scope-menu');
      await expect(menu.locator('.scope-sec', { hasText: 'Backends' })).toBeVisible();
      await menu.locator(`[data-scope="${id}"]`).click();
      await expect(page).toHaveURL(/scope=node(:|%3A)backend\.bench/);
      await expect(page.locator('.ncards .ncard')).toHaveCount(1);
    }
    await page.goto(`./?fleet=fleet4#/hardware?scope=node:${id}`);
    const none = page.locator('qse-hardware [data-no-figures]');
    await expect(none).toContainText('No host figures', { timeout: 15_000 });
    await expect(none).toContainText('no host figures are available');
    await expect(none.locator('[data-install-node]')).toHaveAttribute('href', '#/add-spark');
    await expect(none.locator('[data-install-node]')).toContainText('Install TandemNode');
    await expect(page.locator('qse-hardware .hw-grid')).toHaveCount(0);
    await expect(page.locator('.page-head[data-page="hardware"] .page-sub')).toContainText('Bench is a backend reached by its URL');
    expect(await noSideScroll(page)).toBe(true);
    await shot(page, 'hardware-no-figures', info.project.name);
    // every Spark still has its figures, and the backend is named beside them
    await page.goto('./?fleet=fleet4#/hardware');
    await expect(page.locator('qse-hardware .hw-grid').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('qse-hardware [data-no-figures]')).toContainText('Bench');
    await expect(page.locator(`qse-hardware .hw-cell[data-node="${id}"]`)).toHaveCount(0);
    // Performance and Usage list it beside the Sparks
    await page.goto('./?fleet=fleet4#/performance');
    const strip = page.locator(`[data-fleet-strips] [data-strip="${id}"]`);
    await expect(strip).toBeVisible({ timeout: 15_000 });
    await expect(strip).toContainText('tiny-a');
    await expect(strip.locator('a.node-chip')).toHaveAttribute('href', '#/api/backends');
    await expect(page.locator('[data-fleet-strips] [data-strip]')).toHaveCount(5);
    await page.goto('./?fleet=fleet4#/usage');
    await expect(page.locator(`qse-usage-fleet tr[data-node="${id}"]`)).toContainText('Bench', { timeout: 15_000 });
    await expect(page.locator('qse-usage-fleet tbody tr[data-node]')).toHaveCount(5);
    // the Sparks page is about machines
    await page.goto('./?fleet=fleet4#/sparks');
    await expect(page.locator('qse-sparks')).toContainText('4 Sparks in this fleet', { timeout: 15_000 });
    await expect(page.locator(`qse-sparks [data-node="${id}"]`)).toHaveCount(0);
  });
});
