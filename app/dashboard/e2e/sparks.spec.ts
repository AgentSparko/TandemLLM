// VIS-251 (tests for VIS-250): Fleet → Sparks in the mock fleet, the three Gherkin scenarios of the
// ticket and the rest of its acceptance. Rename: R-1 becomes "Rental East" with a new colour and
// every page and chart shows it. Drain: Gold (fleet4's Desk renamed) serves conversations, is
// drained, its new requests go to its other replica or wait, the ones in flight finish one by one and
// it shows drained wherever it appears. Release: the rented R-2 is released after its plan and the
// typed alias, and leaves the fleet. Besides: the list's sort and filters, the full topology with a
// link's last test and Run again, confirmations that name what goes and what stays, what today's box
// (fleet1) refuses and says why, the controller's sign-in for a write, and the phone. Mock tier only.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { IS_MOCK, TOKEN, setMode } from './helpers';

type Scenario = 'fleet1' | 'fleet4';

async function fresh(request: APIRequestContext): Promise<void> {
  expect((await request.post('/__mock/fleet', { data: { reset: true } })).ok()).toBeTruthy();
}

async function open(page: Page, s: Scenario, hash: string): Promise<void> {
  await page.goto(`./?fleet=${s}${hash}`);
  await expect(page.locator('.shell')).toBeVisible();
}

const json = async <T>(page: Page, path: string): Promise<T> => (await page.request.get(path)).json() as Promise<T>;
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
const rows = (page: Page) => page.locator('.sp-table tbody .sp-row');
const rowIds = (page: Page) => rows(page).evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.node));

async function act(page: Page, action: string): Promise<ReturnType<Page['locator']>> {
  await page.locator(`[data-panel="actions"] [data-action="${action}"]`).click();
  const d = page.locator('qse-node-action dialog[open]');
  await expect(d).toBeVisible();
  await expect(d.locator('.skeleton')).toHaveCount(0);
  return d;
}

test.describe('Sparks pages', () => {
  test.skip(!IS_MOCK, 'the fleet scenarios are the mock’s');
  test.beforeEach(async ({ request }) => {
    await setMode(request, 'ok');
    await fresh(request);
  });

  test('the list: a row per Spark with what the ticket names; every column sorts, text, status and kind filter', async ({ page }, info) => {
    await open(page, 'fleet4', '#/sparks');
    await expect(page.locator('.page-head[data-page="sparks"]')).toContainText('4 Sparks in this fleet: 1 on the LAN, 3 rented through an SSH tunnel.');
    await expect(rows(page)).toHaveCount(4);
    const r3 = page.locator('.sp-row[data-node="r3"]');
    for (const label of ['Spark', 'Status', 'Serves', 'Memory', 'GPU °C', 'Power W', 'Version', 'Link', 'Lease']) await expect(r3.locator(`td[data-label="${label}"]`)).toHaveCount(1);
    await expect(r3.locator('[data-status="alert"]')).toHaveText('⚠ alert');
    await expect(r3.locator('.sp-why')).toContainText('thermal throttle');
    await expect(r3.locator('td[data-label="Lease"]')).toContainText('ends in 2 d');
    await expect(page.locator('.sp-row[data-node="desk"] td[data-label="Lease"]')).toContainText('owned');
    await expect(page.locator('.sp-row[data-node="desk"] td[data-label="Link"]')).toContainText('LAN · 0.4 ms');
    await expect(page.locator('.sp-row[data-node="r1"] td[data-label="Serves"]')).toContainText('DeepSeek-V4.1-Flash');
    if (info.project.name === 'chromium-desktop') {
      // sort: the hottest first, then turned around; an owned Spark has no lease end and sorts last
      await page.locator('[data-sort="temp"]').click();
      expect((await rowIds(page))[0]).toBe('r3');
      await expect(page.locator('th[aria-sort="descending"] [data-sort="temp"]')).toHaveCount(1);
      await page.locator('[data-sort="temp"]').click();
      expect((await rowIds(page)).at(-1)).toBe('r3');
      await page.locator('[data-sort="lease"]').click();
      expect((await rowIds(page)).at(-1)).toBe('desk');
      await page.locator('[data-sort="alias"]').click();
      expect(await rowIds(page)).toEqual(['desk', 'r1', 'r2', 'r3']);
    }
    // filters
    await page.locator('[data-sp-q]').fill('deepseek');
    expect(await rowIds(page)).toEqual(['r1', 'r2']);
    await page.locator('[data-sp-q]').fill('');
    await page.locator('[data-filter="status:alert"]').click();
    expect(await rowIds(page)).toEqual(['r3']);
    await page.locator('[data-filter="status:all"]').click();
    await page.locator('[data-filter="kind:owned"]').click();
    expect(await rowIds(page)).toEqual(['desk']);
    await page.locator('[data-sp-q]').fill('no such spark');
    await expect(page.locator('[data-sp-none]')).toContainText('No Spark matches (4 in the fleet)');
    await page.locator('[data-sp-none] button').click();
    await expect(rows(page)).toHaveCount(4);
    // a row opens the Spark's page
    await page.locator('.sp-row[data-node="r2"] td[data-label="Power W"]').click();
    await expect(page).toHaveURL(/#\/sparks\/r2$/);
    await expect(page.locator('[data-node-alias]')).toHaveText('R-2');
    expect(await noSideScroll(page)).toBe(true);
  });

  test('Rename: R-1 becomes “Rental East” with a new colour, and every page and chart shows it', async ({ page }) => {
    await open(page, 'fleet4', '#/sparks/r1');
    await page.locator('[data-edit]').click();
    const f = page.locator('[data-sp-edit]');
    // a colour another Spark has is not on offer, and says whose it is
    await expect(f.locator('[data-colour="1"]')).toBeDisabled();
    await expect(f.locator('[data-colour="1"]')).toHaveAttribute('title', 'colour 1: Desk’s');
    await f.locator('[data-edit-alias]').fill('desk');
    await f.locator('[data-edit-save]').click();
    await expect(f.locator('[data-edit-error]')).toHaveText('Another Spark is called desk.');
    await f.locator('[data-edit-alias]').fill('Rental East');
    await f.locator('[data-colour="6"]').click();
    await f.locator('[data-edit-tags]').fill('rented, east');
    await f.locator('[data-edit-save]').click();
    await expect(page.locator('[data-sp-edit]')).toHaveCount(0);
    await expect(page.locator('[data-node-alias]')).toHaveText('Rental East');
    await expect(page.locator('.sp-node-sw')).toHaveClass(/is-hollow/);
    expect((await json<{ alias: string; colour: number; tags: string[] }>(page, '/v1/fleet/nodes/r1'))).toMatchObject({ alias: 'Rental East', colour: 6, tags: ['rented', 'east'] });
    // the list, the topology, Overview's cards and chart legend, Hardware, the top bar's readout
    await open(page, 'fleet4', '#/sparks');
    await expect(page.locator('.sp-row[data-node="r1"] .node-alias')).toHaveText('Rental East');
    await expect(page.locator('.sp-row[data-node="r1"] .node-sw')).toHaveClass(/is-hollow/);
    await open(page, 'fleet4', '#/sparks/topology');
    await expect(page.locator('qse-topology .node-g[data-node="r1"] .t-name')).toHaveText('Rental East');
    await open(page, 'fleet4', '#/overview');
    await expect(page.locator('.ncard[data-node="r1"]')).toContainText('Rental East');
    await expect(page.locator('[data-panel="decode"] qse-fleet-chart .legend')).toContainText('Rental East');
    await expect(page.locator('[data-tile="tps"] .share')).toHaveAttribute('title', /Rental East: \d+ tok\/s/);
    await open(page, 'fleet4', '#/hardware');
    await expect(page.locator('.hw-group[data-group="temp"]')).toContainText('Rental East');
  });

  test('Drain: Gold serves conversations; drained, new requests go elsewhere or wait, those in flight finish, Gold shows drained everywhere', async ({ page }) => {
    await open(page, 'fleet4', '#/sparks/desk');
    expect((await page.request.patch('/v1/fleet/nodes/desk', { data: { alias: 'Gold' } })).ok()).toBeTruthy();
    await page.reload();
    await expect(page.locator('[data-node-alias]')).toHaveText('Gold');
    const d = await act(page, 'drain');
    await expect(d.locator('h2')).toHaveText('Drain Gold');
    const impact = d.locator('.act-sec.is-impact');
    await expect(impact).toContainText(/\d+ requests? in flight on Gold finish/);
    await expect(impact).toContainText('Qwen3.8-27B: new requests go to R-3, its other replica');
    await expect(impact).toContainText('Wren: new requests wait, the gateway answers 503 with Retry-After 30 s until Gold takes them again');
    await expect(d.locator('.act-sec.is-keeps')).toContainText('only new requests through this gateway stop');
    const n = Number(/(\d+) requests? in flight/.exec((await impact.textContent()) ?? '')![1]);
    expect(n).toBeGreaterThan(0);
    await d.locator('[data-act-go]').click();
    await expect(d.locator('[data-act-result]')).toContainText(`Gold takes no new requests; ${n} request${n === 1 ? '' : 's'} in flight finish`);
    await d.locator('[data-act-cancel]').click();
    // draining: the banner, the header, the chip; then the requests end and it is drained
    await expect(page.locator('.sp-banner.is-drain')).toContainText(/Draining: \d+ requests? in flight/);
    await expect(page.locator('.sp-node-head [data-status="draining"]')).toContainText('◐');
    await expect(page.locator('[data-panel="actions"] [data-action="undrain"]')).toBeVisible();
    await expect(page.locator('.sp-banner.is-drain')).toContainText('Drained: nothing in flight', { timeout: 4000 * n + 30_000 });
    await expect(page.locator('.sp-node-head [data-status="draining"]')).toContainText('drained');
    expect((await json<{ drain: { state: string } }>(page, '/v1/fleet/nodes/desk')).drain.state).toBe('drained');
    // everywhere it appears: the list, the topology, Overview's card, Hardware's sheet
    await open(page, 'fleet4', '#/sparks');
    await expect(page.locator('.sp-row[data-node="desk"] [data-status="draining"]')).toHaveText('◐ drained');
    await expect(page.locator('.sp-row[data-node="desk"] .sp-why')).toHaveText('nothing in flight');
    await open(page, 'fleet4', '#/sparks/topology');
    await expect(page.locator('qse-topology .node-g[data-node="desk"] .t-st')).toHaveText('◐ drained');
    await open(page, 'fleet4', '#/overview');
    await expect(page.locator('.ncard[data-node="desk"]')).toContainText('drained · nothing in flight');
    await expect(page.locator('.ncard[data-node="desk"] .node-st')).toHaveText('◐');
    await open(page, 'fleet4', '#/events');
    await expect(page.locator('[data-timeline]')).toContainText('Gold is drained');
    // undrain: it takes requests again
    await open(page, 'fleet4', '#/sparks/desk');
    const u = await act(page, 'undrain');
    await expect(u.locator('.act-sec.is-impact')).toContainText('new requests go to Gold again');
    await u.locator('[data-act-go]').click();
    await expect(u.locator('[data-act-result]')).toContainText('Gold takes new requests again');
    await u.locator('[data-act-cancel]').click();
    await expect(page.locator('.sp-banner.is-drain')).toHaveCount(0);
    await expect(page.locator('.sp-node-head [data-status="up"]')).toBeVisible();
  });

  test('Release a rented Spark: R-2 after its plan and the typed alias; weights, caches, keys and the agent go; it leaves the fleet', async ({ page }) => {
    await open(page, 'fleet4', '#/sparks/r2');
    await expect(page.locator('[data-panel="actions"] [data-action="forget"]')).toHaveCount(0);
    const d = await act(page, 'release-lease');
    await expect(d).toHaveClass(/is-danger/);
    await expect(d.locator('h2')).toHaveText('Release R-2');
    const removes = d.locator('.act-sec.is-removes li');
    await expect(removes).toHaveCount(4);
    await expect(removes.nth(0)).toContainText(/the weights and caches on R-2, wiped \(\d/);
    await expect(removes.nth(1)).toHaveText("the node certificate, this controller's pin and the app's SSH key on R-2");
    await expect(removes.nth(2)).toHaveText('TandemNode, uninstalled from R-2');
    await expect(removes.nth(3)).toHaveText("R-2's record and its history here");
    await expect(d.locator('.act-sec.is-impact')).toContainText('DeepSeek-V4.1-Flash stops on R-2 for good: a Split needs every stage');
    await expect(d.locator('.act-sec.is-keeps')).toContainText('nothing of TandemApp stays on R-2');
    const go = d.locator('[data-act-go]');
    await expect(go).toHaveText('Release and wipe');
    await expect(go).toBeDisabled();
    await d.locator('[data-act-type]').fill('R-1');
    await expect(go).toBeDisabled();
    await d.locator('[data-act-type]').fill('R-2');
    await expect(go).toBeEnabled();
    await go.click();
    await expect(d.locator('[data-act-result]')).toContainText('R-2 released: weights and caches wiped, keys removed, TandemNode uninstalled; it left the fleet');
    await d.locator('[data-act-cancel]').click();
    await expect(page.locator('.page-head[data-page="spark-gone"]')).toContainText('Not in the fleet');
    await open(page, 'fleet4', '#/sparks');
    expect(await rowIds(page)).toEqual(['desk', 'r1', 'r3']);
    await open(page, 'fleet4', '#/sparks/topology');
    await expect(page.locator('qse-topology .node-g[data-node="r2"]')).toHaveCount(0);
    expect((await json<{ deployments: { id: string }[] }>(page, '/v1/fleet/deployments')).deployments.map((x) => x.id)).toEqual(['qwen27', 'wren']);
  });

  test('confirmations name what goes and what stays; a cancel changes nothing; a reboot takes the Spark off and back', async ({ page }) => {
    await open(page, 'fleet4', '#/sparks/desk');
    const f = await act(page, 'forget');
    await expect(f.locator('h2')).toHaveText('Forget Desk');
    await expect(f.locator('.act-sec.is-removes')).toContainText("this controller's pin of Desk's certificate");
    await expect(f.locator('.act-sec.is-removes')).toContainText("Desk's metric history on this controller");
    await expect(f.locator('.act-sec.is-keeps')).toContainText('TandemNode stays installed and running on Desk, without an owner');
    await expect(f.locator('.act-sec.is-keeps')).toContainText('its engines keep running: Qwen3.8-27B, Wren');
    await expect(f.locator('.act-sec.is-keeps')).toContainText("weights and caches stay on Desk's disk");
    await f.locator('[data-act-cancel]').click();
    expect((await json<{ nodes: unknown[] }>(page, '/v1/fleet/nodes')).nodes).toHaveLength(4);
    await open(page, 'fleet4', '#/sparks/r3');
    const r = await act(page, 'reboot');
    await expect(r.locator('.act-sec.is-impact')).toContainText('Qwen3.8-27B keeps serving on Desk; on R-3 it stops until it is up again');
    await expect(r.locator('.act-sec.is-impact')).toContainText('a rented node reboots through its provider');
    await r.locator('[data-act-go]').click();
    await expect(r.locator('[data-act-result]')).toContainText('R-3 reboots');
    await expect(r.locator('[data-act-job]')).toContainText('reboot running');
    await r.locator('[data-act-cancel]').click();
    await expect(page.locator('.sp-node-head [data-status="off"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.sp-banner.is-off')).toContainText('rebooting');
  });

  test('today’s box (fleet1): the engine is not managed by TandemNode, reboot and update are not set up, the controller’s own Spark cannot be forgotten, its LAN link has no wire', async ({ page }) => {
    await open(page, 'fleet1', '#/sparks/desk');
    for (const [a, why] of [
      ['restart-engine', 'this engine (engine, port 8000) is not managed by TandemNode: it was started outside TandemNode, so TandemNode never stops or starts it'],
      ['reboot', 'reboot is not set up on this node: add a reboot command to node.json'],
      ['update', 'update is not set up on this node: add an update command to node.json'],
      ['forget', 'Desk is the machine this controller runs on: it is its own node and cannot be forgotten'],
    ] as const) {
      const d = await act(page, a);
      await expect(d.locator('[data-act-blocked]')).toContainText(why);
      await expect(d.locator('[data-act-go]')).toHaveCount(0);
      await d.locator('[data-act-cancel]').click();
      await expect(page.locator('qse-node-action dialog[open]')).toHaveCount(0);
    }
    await expect(page.locator('[data-panel="links"]')).toContainText('the controller runs on this Spark: no wire between them to test');
    await expect(page.locator('[data-panel="links"] [data-run-test]')).toHaveCount(0);
    await expect(page.locator('[data-panel="inventory"]')).toContainText('CX7');
  });

  test('Topology: the full graph with its legend; a link opens its side panel with the last test, and Run again tests it', async ({ page }, info) => {
    await open(page, 'fleet4', '#/sparks/topology');
    const g = page.locator('[data-panel="topology-full"] qse-topology');
    await expect(g.locator('.topo-wrap')).not.toHaveClass(/is-compact/);
    await expect(g.locator('.topo-legend')).toContainText('● up · ◐ draining · ○ off · ⚠ alert');
    await expect(g.locator('.node-g[data-node]')).toHaveCount(4);
    const card = page.locator('.sp-link[data-link="r1-sw"]');
    await expect(card).toContainText('196 Gb/s');
    if (info.project.name === 'chromium-desktop') {
      await g.locator('.link-g[data-link="r1-sw"] .l-hit').focus();
      await page.keyboard.press('Enter');
      const side = page.locator('[data-selection]');
      await expect(side).toContainText('fabric (ConnectX)');
      await expect(side).toContainText('Last link test');
      await expect(side.locator('[data-link-test="r1-sw"]')).toContainText('196 Gb/s');
      await side.locator('[data-run-test="r1-sw"]').click();
      await expect(side.locator('[data-run-test="r1-sw"]')).toHaveText('Run again');
      await expect(side.locator('[data-link-test="r1-sw"]')).toContainText('just now');
    }
    await card.locator('[data-run-test]').click();
    await expect(card.locator('[data-run-test]')).toHaveText('Run again');
    await expect(card).toContainText('just now');
    const t = (await json<{ links: { id: string; test: { at: number } }[] }>(page, '/v1/fleet/links')).links.find((l) => l.id === 'r1-sw')!.test;
    expect(Date.now() / 1000 - t.at).toBeLessThan(30);
    await open(page, 'fleet4', '#/events');
    await expect(page.locator('[data-timeline]')).toContainText('Link test R-1 ↔ Fabric switch');
  });

  test('a controller with an admin password: a drain waits for the sign-in, then runs', async ({ page, request }) => {
    await setMode(request, 'ok', { fleetPassword: true });
    try {
      await open(page, 'fleet4', '#/sparks/r1');
      const d = await act(page, 'drain');
      await d.locator('[data-act-go]').click();
      await expect(d.locator('.mon-signin')).toContainText('Sign in to TandemApp’s controller');
      await d.locator('.mon-signin input[type="password"]').fill(TOKEN);
      await d.locator('.mon-signin button[type="submit"]').click();
      await expect(d.locator('.mon-signin')).toHaveCount(0);
      await d.locator('[data-act-go]').click();
      await expect(d.locator('[data-act-result]')).toContainText('R-1 takes no new requests');
    } finally {
      await setMode(request, 'ok');
    }
  });

  test('the phone: the list as cards, the page in one column, a confirmation as a sheet from the bottom', async ({ page }, info) => {
    test.skip(info.project.name !== 'webkit-phone', 'the phone layout');
    await open(page, 'fleet4', '#/sparks');
    await expect(rows(page)).toHaveCount(4);
    expect(await noSideScroll(page)).toBe(true);
    const box = await rows(page).first().boundingBox();
    expect(box!.width).toBeGreaterThan(300);
    await expect(page.locator('[data-sort-pick]')).toBeVisible();
    await page.locator('[data-sort-pick]').selectOption('temp');
    expect((await rowIds(page))[0]).toBe('r3');
    await open(page, 'fleet4', '#/sparks/r2');
    expect(await noSideScroll(page)).toBe(true);
    const d = await act(page, 'release-lease');
    // a sheet on the bottom edge, the full width, once its opening motion has ended
    const vh = page.viewportSize()!.height;
    await expect.poll(async () => {
      const b = await d.boundingBox();
      return Math.round(b!.y + b!.height - vh);
    }).toBe(0);
    expect((await d.boundingBox())!.width).toBeGreaterThan(380);
  });
});
