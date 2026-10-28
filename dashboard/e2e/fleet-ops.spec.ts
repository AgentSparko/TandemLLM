// OPS-235 (tests for OPS-234): TandemApp alone, the controller's host and the fleet update, in the mock
// fleet. The three Gherkin scenarios of the ticket: the first open of TandemApp on a Mac with no Spark
// lands on Add Spark; the controller on the Mac with three Sparks moves to Gold (the nodes trust the
// new controller, the API URL is Gold's, the page follows to it); Qwen3.8-27B as Replicas on Gold and
// R-3 while the fleet updates (one member drains and updates at a time, the model stays served).
// Besides: a Split refuses a partial update, the mixed-version alert on the Overview and the fleet
// pill, and the phone. Each scenario leaves a screenshot in e2e-artifacts/ops234/. Mock tier only.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { IS_MOCK, setMode } from './helpers';

const SHOTS = 'e2e-artifacts/ops234';

async function fresh(request: APIRequestContext, scenario: string): Promise<void> {
  expect((await request.post('/__mock/fleet', { data: { reset: true, scenario } })).ok()).toBeTruthy();
}

async function open(page: Page, scenario: string, hash: string): Promise<void> {
  await page.goto(`./?fleet=${scenario}${hash}`);
  await expect(page.locator('.shell')).toBeVisible({ timeout: 10_000 });
}

const shot = (page: Page, name: string, el?: ReturnType<Page['locator']>) => (el ?? page).screenshot({ path: `${SHOTS}/${name}.png`, ...(el ? {} : { fullPage: false }) });
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test.describe('TandemApp alone, its controller, the fleet update', () => {
  test.skip(!IS_MOCK, 'the fleet is the mock’s');
  test.beforeEach(async ({ request }) => {
    await setMode(request, 'ok');
  });

  test('Install on a Mac: the first open with no Spark lands on Add Spark; a route given is kept', async ({ page, request }, info) => {
    await fresh(request, 'empty');
    await page.goto('./?fleet=empty');
    await expect(page).toHaveURL(/#\/add-spark$/, { timeout: 10_000 });
    await expect(page.locator('qse-add-spark .wz-stepper')).toBeVisible({ timeout: 10_000 });
    if (info.project.name === 'chromium-desktop') await shot(page, 'first-open-add-spark');
    // a link to another page is kept: no detour
    await page.goto('./?fleet=empty#/app');
    await expect(page.locator('qse-app-settings [data-panel="controller-host"]')).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/#\/app$/);
    // This Mac runs the controller; no Spark to move to; it may take a fleet
    await expect(page.locator('[data-host="here"]')).toContainText('This Mac');
    await expect(page.locator('[data-sleep-warning]')).toContainText('This Mac can sleep');
    await expect(page.locator('[data-panel="take-fleet"]')).toBeVisible();
  });

  test('Move controller to a Spark: three Sparks trust the new controller, the API URL is Gold’s, the page follows', async ({ page, request }, info) => {
    test.skip(info.project.name !== 'chromium-desktop', 'the move once, on the desk');
    test.setTimeout(90_000);
    await fresh(request, 'gold3');
    // the new controller's page, as the browser would load it after the move
    await page.route('http://192.0.2.40:8080/**', (r) => r.fulfill({ status: 200, contentType: 'text/html', body: '<title>TandemApp on Gold</title><h1 id="new">TandemApp on Gold</h1>' }));
    await open(page, 'gold3', '#/app');
    const host = page.locator('[data-panel="controller-host"]');
    await expect(host).toBeVisible();
    await expect(page.locator('[data-host="here"]')).toContainText('runs the controller');
    await expect(page.locator('[data-sleep-warning]')).toContainText('This Mac can sleep');
    await expect(page.locator('[data-host="gold"]')).toContainText('recommended');
    await expect(page.locator('[data-api-url] code')).toHaveText('http://192.0.2.10:8080/v1');
    await shot(page, 'settings-controller');
    await page.locator('[data-host="gold"] input').check();
    await page.locator('[data-act="move"]').click();
    const dlg = page.locator('dialog[data-dialog="move"]');
    await expect(dlg).toBeVisible();
    await expect(dlg.locator('[data-new-api]')).toHaveText('http://192.0.2.40:8080/v1');
    await expect(dlg.locator('[data-move-plan-nodes] [data-how]')).toHaveCount(3);
    await expect(dlg.locator('[data-how="release"]')).toContainText('Gold');
    await expect(dlg.locator('[data-how="repin"]')).toHaveCount(2);
    // the Mac has no admin password: the new controller on the LAN needs one
    await dlg.locator('[data-act="move-go"]').click();
    await expect(dlg.locator('[data-move-error]')).toContainText('32 or more characters');
    await dlg.locator('[data-act="gen-pw"]').click();
    await expect(dlg.locator('[data-in="move-pw"]')).toHaveValue(/^[A-Za-z0-9]{32}$/);
    await shot(page, 'move-confirm');
    await dlg.locator('[data-act="move-go"]').click();
    await expect(dlg).toBeHidden();
    await expect(page.locator('[data-move-job]')).toHaveAttribute('data-move-job', 'running');
    await expect(page.locator('[data-move-job]')).toHaveAttribute('data-move-job', 'done', { timeout: 30_000 });
    await expect(page.locator('[data-moved="r3"]')).toContainText('answers it');
    await expect(page.locator('[data-moved="bronze"]')).toContainText('trusts the new controller');
    await expect(page.locator('[data-moved-to]')).toContainText('http://192.0.2.40:8080/v1');
    await expect(page.locator('[data-follow-count]')).toBeVisible();
    await shot(page, 'move-done');
    // the old page follows to the new controller
    await page.waitForURL('http://192.0.2.40:8080/**', { timeout: 15_000 });
    await expect(page.locator('#new')).toHaveText('TandemApp on Gold');
  });

  test('Rolling update: Replicas on Gold and R-3, one member drains and updates at a time, the model stays served', async ({ page, request }, info) => {
    test.skip(info.project.name !== 'chromium-desktop', 'the update once, on the desk');
    test.setTimeout(150_000);
    await fresh(request, 'gold3');
    await open(page, 'gold3', '#/sparks');
    await page.locator('[data-act="update-fleet"]').click();
    const dlg = page.locator('qse-fleet-update dialog');
    await expect(dlg.locator('[data-upd-target]')).toHaveText('0.15.1');
    const steps = dlg.locator('[data-upd-order] .upd-step');
    await expect(steps).toHaveCount(3);
    await expect(steps.nth(0)).toHaveAttribute('data-kind', 'replica');
    await expect(steps.nth(0)).toContainText('Gold');
    await expect(steps.nth(0)).toContainText('no downtime');
    await expect(steps.nth(1)).toHaveAttribute('data-kind', 'replica');
    await expect(steps.nth(1)).toContainText('R-3');
    await expect(steps.nth(2)).toHaveAttribute('data-kind', 'single');
    await expect(steps.nth(2)).toContainText('Wren not served while Bronze updates');
    await shot(page, 'update-plan');
    await dlg.locator('[data-upd-go]').click();
    await expect(dlg).toBeHidden();
    const prog = page.locator('[data-panel="update-progress"]');
    await expect(prog).toHaveAttribute('data-state', 'running');
    let shotTaken = false;
    let seenGold = false;
    let seenR3 = false;
    const end = Date.now() + 70_000;
    while (Date.now() < end) {
      const nodes = (await (await page.request.get('/v1/fleet/nodes')).json()).nodes as { id: string; status: string }[];
      const deps = (await (await page.request.get('/v1/fleet/deployments')).json()).deployments as { id: string; status: string }[];
      const draining = nodes.filter((n) => ['gold', 'r3'].includes(n.id) && n.status === 'draining').map((n) => n.id);
      expect(draining.length, 'two Replicas members drained at once').toBeLessThanOrEqual(1);
      seenGold ||= draining.includes('gold');
      seenR3 ||= draining.includes('r3');
      expect(deps.find((d) => d.id === 'qwen27')!.status).not.toBe('down');
      if (!shotTaken && (await prog.locator('[data-phase="update"]').count())) {
        shotTaken = true;
        await shot(page, 'update-progress');
      }
      const job = (await (await page.request.get('/v1/fleet/update')).json()).job;
      if (job.state !== 'running') break;
      await page.waitForTimeout(700);
    }
    expect(seenGold && seenR3).toBeTruthy();
    await expect(prog).toHaveAttribute('data-state', 'done', { timeout: 10_000 });
    await expect(prog.locator('[data-upd-count]')).toHaveText('3 of 3');
    const versions = ((await (await page.request.get('/v1/fleet/nodes')).json()).nodes as { version: string }[]).map((n) => n.version);
    expect(versions).toEqual(['0.15.1', '0.15.1', '0.15.1']);
  });

  test('a Split refuses a partial update: one member left out blocks the plan with why', async ({ page, request }, info) => {
    await fresh(request, 'fleet4');
    await open(page, 'fleet4', '#/sparks');
    await page.locator('[data-act="update-fleet"]').click();
    const dlg = page.locator('qse-fleet-update dialog');
    await expect(dlg.locator('[data-upd-order] .upd-step')).toHaveCount(3);
    await expect(dlg.locator('[data-upd-order] .upd-step').nth(2)).toHaveAttribute('data-kind', 'split');
    await expect(dlg.locator('[data-upd-order] .upd-step').nth(2)).toContainText('down while its members update together');
    await dlg.locator('[data-pick="r2"] input').uncheck();
    await expect(dlg.locator('[data-upd-blocked]')).toContainText('DeepSeek-V4.1-Flash runs Split over R-1, R-2: its members update together, so R-2 must be in the update too');
    await expect(dlg.locator('[data-upd-go]')).toHaveCount(0);
    if (info.project.name === 'chromium-desktop') await shot(page, 'split-refused');
    else expect(await noSideScroll(page)).toBeTruthy();
    // the controller refuses it too
    const plan = await (await page.request.get('/v1/fleet/update/plan')).json();
    const r = await page.request.post('/v1/fleet/update', { data: { confirm: plan.confirm, nodes: ['r1', 'desk'] } });
    expect(r.status()).toBe(409);
    expect((await r.json()).error.type).toBe('partial_split');
  });

  test('mixed versions: the Overview health line and the fleet pill say so, and lead to Update fleet', async ({ page, request }, info) => {
    await fresh(request, 'mixed');
    await open(page, 'mixed', '#/overview');
    const line = page.locator('[data-mixed-versions]');
    await expect(line).toContainText('Mixed versions: 0.15.0 on Desk, R-1, R-2 · 0.15.1 on R-3', { timeout: 15_000 });
    if (info.project.name === 'chromium-desktop') {
      await expect(page.locator('[data-fleet-mixed]')).toBeVisible();
      await shot(page, 'mixed-version-alert');
    }
    await line.click();
    await expect(page.locator('qse-fleet-update dialog')).toBeVisible();
    await expect(page.locator('qse-fleet-update [data-upd-target]')).toHaveText('0.15.1');
    await expect(page.locator('qse-fleet-update [data-pick="r3"]')).toContainText('runs 0.15.1');
  });

  test('the phone: App settings in one column, the move as a sheet, the update dialog without side scroll', async ({ page, request }, info) => {
    test.skip(info.project.name !== 'webkit-phone', 'the phone layout');
    await fresh(request, 'gold3');
    await open(page, 'gold3', '#/app');
    await expect(page.locator('[data-panel="controller-host"]')).toBeVisible();
    expect(await noSideScroll(page)).toBeTruthy();
    const cards = page.locator('.app-host');
    const a = await cards.nth(0).boundingBox();
    const b = await cards.nth(1).boundingBox();
    expect(b!.y).toBeGreaterThan(a!.y + a!.height - 1);
    await shot(page, 'settings-phone');
    await page.locator('[data-host="gold"] input').check();
    await page.locator('[data-act="move"]').click();
    const dlg = page.locator('dialog[data-dialog="move"]');
    await expect(dlg.locator('[data-new-api]')).toBeVisible();
    const box = await dlg.boundingBox();
    expect(box!.width).toBeGreaterThan(380);
    await page.locator('[data-act="move-cancel"]').click();
    await page.goto('./?fleet=gold3#/sparks');
    await page.locator('[data-act="update-fleet"]').click();
    await expect(page.locator('qse-fleet-update [data-upd-order] .upd-step')).toHaveCount(3);
    expect(await noSideScroll(page)).toBeTruthy();
    await shot(page, 'update-plan-phone');
  });
});
