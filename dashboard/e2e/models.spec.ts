// VIS-253 (tests for VIS-252): Fleet → Models in the mock fleet (fleet4: Qwen3.8-27B as Replicas on
// Desk + R-3, DeepSeek-V4.1-Flash as a Split over R-1 + R-2, Wren a Role on Desk, a copy of DeepSeek's
// pack R-1 → Desk running). The two Gherkin scenarios of the ticket as the mock can hold them:
// "Placement shows fit" (with DeepSeek stopped through its plan, a 120B model: every Spark's memory
// bar, Single refused on Desk for memory, a Split over R-1 → R-2 allowed but not built, a Split with
// Desk refused for the fabric) and "Copy weights" (a pack on Desk only copied to R-3: the progress,
// then R-3's cell present with the same content id). Besides: the cards and the scope, a start after
// Serve, each Spark's Setup Guide through the controller with a write said before it goes, the
// delete refused while a pack serves, and the phone. Mock tier only. PW_SHOTS=<dir> keeps a
// screenshot of each page.
import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import { IS_MOCK, setMode } from './helpers';

async function fresh(request: APIRequestContext): Promise<void> {
  expect((await request.post('/__mock/fleet', { data: { scenario: 'fleet4', reset: true } })).ok()).toBeTruthy();
}
async function open(page: Page, hash: string): Promise<void> {
  await page.goto(`./?fleet=fleet4${hash}`);
  await expect(page.locator('.shell')).toBeVisible();
}
async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  if (process.env.PW_SHOTS) await page.screenshot({ path: `${process.env.PW_SHOTS}/${info.project.name}-${name}.png`, fullPage: true });
}
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const card = (page: Page, id: string) => page.locator(`.dcard[data-dep="${id}"]`);

/** a deployment's plan dialog, read */
async function dialogFor(page: Page, dep: string, act: string) {
  await card(page, dep).locator(`[data-dep-act="${act}"]`).click();
  const d = page.locator('qse-deploy-action dialog[open]');
  await expect(d).toBeVisible();
  return d;
}

test.describe('Models pages', () => {
  test.skip(!IS_MOCK, 'the fleet scenarios are the mock’s');
  test.beforeEach(async ({ request }) => {
    await setMode(request, 'ok');
    await fresh(request);
  });

  test('Deployments: a card per deployment with its type, members and their states; a Split not built; the scope narrows it', async ({ page }, info) => {
    await open(page, '#/models');
    await expect(page.locator('.page-head[data-page="models"] .page-sub')).toContainText('3 deployments behind one API');
    await expect(page.locator('.dcard')).toHaveCount(3);
    const q = card(page, 'qwen27');
    await expect(q).toHaveAttribute('data-type', 'replicas');
    await expect(q.locator('tr[data-member]')).toHaveCount(2);
    await expect(q.locator('tr[data-member="r3"] [data-member-state]')).toHaveText('serving');
    await expect(q.locator('.dcard-m')).toHaveText('managed');
    await expect(q.locator('.dcard-warn')).toContainText('Each conversation stays on one member');
    await expect(card(page, 'dsv41').locator('[data-not-built]')).toContainText('not built yet (ENG-307)');
    await expect(card(page, 'dsv41').locator('[data-dep-act="move"]')).toBeDisabled();
    await expect(page.locator('[data-tiles] [data-tile="tps"]')).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await shot(page, info, 'deployments');
    // the scope: one Spark keeps its own deployments, and its numbers carry no share bar
    await open(page, '#/models?scope=node:r1');
    await expect(page.locator('.dcard')).toHaveCount(1);
    await expect(card(page, 'dsv41')).toBeVisible();
    await expect(page.locator('[data-tile="tps"] .share')).toHaveCount(0);
    if (info.project.name === 'chromium-desktop') {
      // a card's head opens its members and last job in the right sidebar
      await card(page, 'dsv41').locator('.dcard-h').click();
      await expect(page.locator('.ctx [data-selection]')).toContainText('Split · one model across 2 or 4 Sparks');
    }
  });

  test('Placement shows fit: every Spark’s bar, Single refused on Desk for memory, a Split over R-1 → R-2 allowed but not built, one with Desk refused for the fabric; Serve creates and starts', async ({ page }, info) => {
    test.setTimeout(90_000);
    await open(page, '#/models');
    // DeepSeek's Split holds R-1 and R-2: stopped through its plan first
    const d = await dialogFor(page, 'dsv41', 'stop');
    await expect(d.locator('.act-sec.is-impact')).toContainText('takes no new request');
    await d.locator('[data-act-go]').click();
    await expect(d.locator('[data-dep-job="done"]')).toBeVisible({ timeout: 15_000 });
    await d.locator('[data-act-cancel]').click();
    // Serve a model: a 120B model, one lane of 32k
    await open(page, '#/models/place');
    await page.locator('[data-sv-model]').selectOption('nemotron-3.5-super-vl-120b-a12b');
    await expect(page.locator('[data-sv-pack]')).toHaveValue('nemotron-super-vl-exl3-4.0');
    await page.locator('[data-sv-ctx]').fill('32768');
    await page.locator('[data-sv-ctx]').dispatchEvent('change');
    await page.locator('[data-sv-slots]').fill('1');
    await page.locator('[data-sv-slots]').dispatchEvent('change');
    await shot(page, info, 'serve-model');
    await page.locator('[data-sv-next]').click();
    const nodes = page.locator('[data-place-node]');
    await expect(nodes).toHaveCount(4);
    for (const id of ['desk', 'r1', 'r2', 'r3']) await expect(page.locator(`[data-place-node="${id}"] .pbar`)).toBeVisible();
    await expect(page.locator('[data-place-node="desk"]')).toHaveAttribute('data-fits', 'false');
    await expect(page.locator('[data-place-node="desk"] [data-reason]')).toContainText(/needs \d+ GiB, Desk has \d+ free/);
    await expect(page.locator('[data-place-node="r1"]')).toHaveAttribute('data-fits', 'true');
    const split = page.locator('.ptype[data-type="split"]');
    await expect(split).toHaveAttribute('data-allowed', 'true');
    await expect(split.locator('[data-not-built]')).toHaveText('not built yet (ENG-307)');
    await expect(split).toContainText('R-1 → R-2');
    await shot(page, info, 'placement');
    // Desk and R-1, in that order: no fabric between them
    await page.locator('[data-pick="desk"]').check();
    await page.locator('[data-pick="r1"]').check();
    await expect(split).toHaveAttribute('data-allowed', 'false');
    await expect(split).toContainText('no fabric link between Desk and R-1');
    // R-1 alone: a Single there
    await page.locator('[data-pick="desk"]').uncheck();
    await expect(page.locator('[data-place-node]')).toHaveCount(4);
    await expect(page.locator('.ptype[data-type="single"]')).toContainText('R-1');
    await page.locator('.ptype[data-type="single"]').click();
    await page.locator('[data-sv-next]').click();
    await page.locator('[data-sv-api]').fill('nemotron-super');
    await shot(page, info, 'serve-review');
    await page.locator('[data-sv-go]').click();
    await expect(page.locator('[data-sv-made]')).toContainText('recorded as nemotron-super');
    await expect(page.locator('[data-sv-job]')).toBeVisible();
    await expect(page.locator('[data-sv-done]')).toBeVisible({ timeout: 20_000 });
    await page.locator('[data-sv-done]').click();
    await expect(card(page, 'nemotron-super')).toBeVisible();
    await expect(card(page, 'nemotron-super').locator('tr[data-member="r1"]')).toBeVisible();
  });

  test('each Spark’s Setup Guide through the controller: its own Spark named, a write said by the plan before it goes', async ({ page }, info) => {
    await open(page, '#/models/place?step=placement&model=qwen3.8-27b&pack=exl3-4.5&ctx=32768&slots=1');
    await page.locator('[data-place-node="r1"] [data-guide="r1"]').click();
    await expect(page).toHaveURL(/#\/models\/serve\/qwen3\.8-27b\/overview\?node=r1$/);
    const bar = page.locator('.setup-node-bar[data-setup-node="r1"]');
    await expect(bar).toContainText('Setup Guide on');
    await expect(bar.locator('.node-chip')).toContainText('R-1');
    await shot(page, info, 'guide-r1');
    // waking Wren on R-1 is a write: the controller's plan first; No sends nothing
    const wake = page.locator('qse-wren .wren-wake').first();
    if (await wake.isVisible()) {
      await wake.click();
      const c = page.locator('qse-setup-confirm dialog[open]');
      await expect(c).toBeVisible();
      await expect(c.locator('#sc-title')).toHaveText('Wake Wren on R-1');
      await c.locator('[data-act-cancel]').click();
      await expect(page.locator('qse-wren .wren-wake').first()).toBeVisible();
      await page.locator('qse-wren .wren-wake').first().click();
      await page.locator('qse-setup-confirm dialog[open] [data-act-go]').click();
      await expect(page.locator('qse-wren [data-wren-status]')).not.toHaveText('');
    }
    // another Spark from the bar: its own guide
    if (info.project.name === 'chromium-desktop') {
      await bar.locator('[data-setup-node-pick]').selectOption('r3');
      await expect(page.locator('.setup-node-bar[data-setup-node="r3"]')).toBeVisible();
    }
    // this server's own guide keeps its deep links, without the bar
    await open(page, '#/models/serve');
    await expect(page.locator('qse-setup')).toBeVisible();
    await expect(page.locator('.setup-node-bar')).toHaveCount(0);
  });

  test('Copy weights: a pack on Desk only copied to R-3 shows progress, then R-3 holds it with the same content id; a delete is refused while a pack serves', async ({ page }, info) => {
    await open(page, '#/models/weights');
    // the copy the fleet started with, R-1 → Desk through the controller
    await expect(page.locator('[data-copy][data-state="running"]').first()).toContainText('relay');
    await expect(page.locator('[data-cell="deepseek-v4.1-flash-nvfp4@desk"]:visible')).toHaveAttribute('data-kind', 'copying');
    await shot(page, info, 'weights');
    const pack = 'qwen3.5-2b-exl3-6.0';
    await expect(page.locator(`[data-cell="${pack}@r3"]:visible`)).toHaveAttribute('data-kind', 'missing');
    await page.locator(`[data-weights-copy="${pack}@r3"]:visible`).click();
    await expect(page.locator(`[data-cell="${pack}@r3"]:visible`)).toHaveAttribute('data-kind', 'copying');
    await expect(page.locator(`[data-cell="${pack}@r3"]:visible`)).toHaveAttribute('data-kind', /present|in-use/, { timeout: 20_000 });
    const desk = await page.locator(`[data-cell="${pack}@desk"]:visible .num`).getAttribute('title');
    const r3 = await page.locator(`[data-cell="${pack}@r3"]:visible .num`).getAttribute('title');
    expect(r3).toBe(desk);
    await expect(page.locator('[data-copy] [data-verified="true"]').first()).toHaveText('✓ verified');
    // a pack a deployment serves there cannot be deleted; the copy can
    await page.locator(`[data-weights-delete="${pack}@r3"]:visible`).click();
    const dlg = page.locator('dialog.wd-dialog[open]');
    await expect(dlg.locator('.act-sec.is-removes')).toContainText(`${pack} is removed from R-3`);
    await dlg.locator('[data-act-go]').click();
    await expect(page.locator(`[data-cell="${pack}@r3"]:visible`)).toHaveAttribute('data-kind', 'missing');
    // Qwen3.8-27B serves exl3-4.0 on Desk: in use, no delete there (the controller refuses it too)
    await expect(page.locator('[data-cell="exl3-4.0@desk"]:visible')).toHaveAttribute('data-kind', 'in-use');
    await expect(page.locator('[data-weights-delete="exl3-4.0@desk"]')).toHaveCount(0);
  });

  test('the phone: cards one under another, the matrix as a card per pack, the placement stacked; no side scroll', async ({ page }, info) => {
    test.skip(info.project.name !== 'webkit-phone', 'the phone’s layout');
    await open(page, '#/models');
    await expect(page.locator('.dcard')).toHaveCount(3);
    expect(await noSideScroll(page)).toBe(true);
    await open(page, '#/models/weights');
    await expect(page.locator('.wm-cards .wm-card').first()).toBeVisible();
    await expect(page.locator('.wm-table')).toBeHidden();
    expect(await noSideScroll(page)).toBe(true);
    await open(page, '#/models/place?step=placement&model=qwen3.8-27b&pack=exl3-4.5&ctx=32768&slots=1');
    await expect(page.locator('[data-place-node]').first()).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await shot(page, info, 'placement-phone');
  });
});
