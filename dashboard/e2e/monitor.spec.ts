// VIS-249 + VIS-245 (tests for VIS-248 and the mock fleet's scenarios): Overview, Hardware and Events &
// Alerts in each mock scenario -- fleet1, fleet4, fleet2-split, node-down, alert-thermal -- and on a
// phone. The share bar under a total; the topology drawn from /v1/fleet/topology; a Hardware cell that
// opens a Spark's sheet at its group; the host; what a board does not expose; the timeline's filters;
// acknowledge with who and when; the rule editor and what it cannot change; the controller's sign-in
// for its writes. Mock tier only (the scenarios are the mock's).
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { IS_MOCK, TOKEN, setMode } from './helpers';

const SCENARIOS = ['fleet1', 'fleet4', 'fleet2-split', 'node-down', 'alert-thermal'] as const;
type Scenario = (typeof SCENARIOS)[number];

/** every scenario's clock starts again with its next request */
async function fresh(request: APIRequestContext): Promise<void> {
  const r = await request.post('/__mock/fleet', { data: { reset: true } });
  expect(r.ok()).toBeTruthy();
}

async function open(page: Page, s: Scenario, hash: string): Promise<void> {
  await page.goto(`./?fleet=${s}${hash}`);
  await expect(page.locator('.shell')).toBeVisible();
}

const json = async <T>(page: Page, path: string): Promise<T> => (await page.request.get(path)).json() as Promise<T>;
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);

test.describe('Monitor pages', () => {
  test.skip(!IS_MOCK, 'the fleet scenarios are the mock’s');
  test.beforeEach(async ({ request }) => {
    await setMode(request, 'ok');
    await fresh(request);
  });

  for (const s of SCENARIOS) {
    test(`${s}: Overview: health, six totals with each Spark’s share, the topology of /v1/fleet/topology, a card per Spark, the deployments`, async ({ page }) => {
      await open(page, s, '#/overview');
      const topo = await json<{ nodes: { id: string }[]; infra: { id: string; kind: string }[]; links: { id: string }[] }>(page, '/v1/fleet/topology');
      const deps = await json<{ deployments: { id: string }[] }>(page, '/v1/fleet/deployments');
      await expect(page.locator('[data-fleet-health]')).toContainText(`${topo.nodes.length} Spark${topo.nodes.length === 1 ? '' : 's'} up`);
      await expect(page.locator('[data-tiles] .mtile')).toHaveCount(6);
      await expect(page.locator('[data-tile="kwh"]')).toContainText('computed from power samples');
      // the share bar splits by node, and its tooltip lists every node's value
      const share = page.locator('[data-tile="tps"] .share');
      await expect(share.locator('i')).toHaveCount(topo.nodes.length);
      for (const n of topo.nodes) {
        const alias = (await json<{ alias: string }>(page, `/v1/fleet/nodes/${n.id}`)).alias;
        await expect(share).toHaveAttribute('title', new RegExp(`${alias}: \\d+ tok/s`));
      }
      // the graph: every Spark, every infra item, every link
      const g = page.locator('qse-topology');
      await expect(g.locator('.node-g[data-node]')).toHaveCount(topo.nodes.length);
      for (const n of topo.nodes) await expect(g.locator(`.node-g[data-node="${n.id}"]`)).toBeVisible();
      for (const i of topo.infra) await expect(g.locator(i.kind === 'host' ? '[data-host]' : `[data-infra="${i.id}"]`)).toHaveCount(1);
      for (const l of topo.links) await expect(g.locator(`.link-g[data-link="${l.id}"]`)).toHaveCount(1);
      await expect(page.locator('.ncard')).toHaveCount(topo.nodes.length);
      await expect(page.locator('.dep-row')).toHaveCount(deps.deployments.length);
      await expect(page.locator('[data-panel="decode"] qse-fleet-chart')).toBeVisible();
      expect(await noSideScroll(page)).toBe(true);
    });

    test(`${s}: Hardware and Events & Alerts render`, async ({ page }) => {
      await open(page, s, '#/hardware');
      await expect(page.locator('.page-head[data-page="hardware"]')).toBeVisible();
      await expect(page.locator('#hw-host')).toBeVisible();
      if (s === 'fleet1') await expect(page.locator('[data-sheet="desk"]')).toBeVisible();
      else await expect(page.locator('.hw-group[data-group="temp"] .hw-cell')).toHaveCount((await json<{ nodes: unknown[] }>(page, '/v1/fleet/nodes')).nodes.length);
      await open(page, s, '#/events');
      await expect(page.locator('[data-timeline] .ev-item').first()).toBeVisible();
      await expect(page.locator('[data-panel="rules"] .rule-row[data-rule]').first()).toBeVisible();
      const alerts = (await json<{ alerts: unknown[] }>(page, '/v1/fleet/alerts')).alerts.length;
      if (alerts) await expect(page.locator('qse-events [data-alerts] .alert-card')).toHaveCount(alerts);
      else await expect(page.locator('qse-events [data-no-alerts]')).toBeVisible();
    });
  }

  test('VIS-245 fleet4: four Sparks, three deployments and the thermal alert', async ({ page }) => {
    await open(page, 'fleet4', '#/overview');
    await expect(page.locator('.ncard')).toHaveCount(4);
    await expect(page.locator('.dep-row')).toHaveCount(3);
    await expect(page.locator('.ncard[data-node="r3"]')).toHaveClass(/is-alert/);
    await expect(page.locator('qse-topology [data-hull="dsv41"] .hull')).toHaveCount(1);
    await expect(page.locator('qse-topology [data-hull="qwen27"] .rep')).toHaveCount(1);
    await open(page, 'fleet4', '#/events');
    await expect(page.locator('qse-events .alert-card[data-alert="gpu-temp:r3"]')).toContainText('R-3 GPU 86 °C');
    await expect(page.locator('qse-events .alert-card[data-alert="gpu-temp:r3"] a')).toHaveAttribute('href', '#/hardware?scope=node:r3');
  });

  test('VIS-245 node-down: after 30 s R-3 turns off, the Replicas runs on one member, the alert fires', async ({ page }) => {
    test.setTimeout(120_000);
    await open(page, 'node-down', '#/overview');
    await expect(page.locator('.ncard[data-node="r3"]')).not.toHaveClass(/is-off/);
    await expect(page.locator('.ncard[data-node="r3"]')).toHaveClass(/is-off/, { timeout: 60_000 });
    await expect(page.locator('qse-topology .node-g[data-node="r3"]')).toHaveClass(/is-off/, { timeout: 20_000 });
    await expect(page.locator('.dep-row[data-dep="qwen27"]')).toContainText('degraded · 1 of 2 members', { timeout: 20_000 });
    await expect(page.locator('[data-fleet-health]')).toContainText('R-3 not reachable', { timeout: 20_000 });
    await open(page, 'node-down', '#/events');
    await expect(page.locator('qse-events .alert-card[data-alert="node-down:r3"]')).toBeVisible({ timeout: 45_000 });
  });

  test('VIS-245 alert-thermal: R-3 heats up and the alert fires a minute in; Acknowledge says who and when and keeps it listed', async ({ page }) => {
    test.setTimeout(150_000);
    await open(page, 'alert-thermal', '#/events');
    await expect(page.locator('[data-panel="rules"] .rule-row[data-rule="gpu-temp"]')).toBeVisible();
    await expect(page.locator('qse-events .alert-card[data-alert="gpu-temp:r3"]')).toHaveCount(0);
    const card = page.locator('qse-events [data-alerts] .alert-card[data-alert="gpu-temp:r3"]');
    await expect(card).toBeVisible({ timeout: 100_000 });
    await card.getByRole('button', { name: 'Acknowledge' }).click();
    await expect(card).toHaveClass(/is-acked/, { timeout: 20_000 });
    await expect(card.locator('[data-acked]')).toContainText(/acknowledged at \d\d:\d\d by admin/, { timeout: 20_000 });
    await expect(page.locator('[data-timeline]')).toContainText('Acknowledged: R-3 GPU', { timeout: 20_000 });
  });

  test('Hardware matrix to node sheet: R-3’s temperature cell scopes the page to R-3 and opens its sheet at the temperature group', async ({ page }) => {
    await open(page, 'fleet4', '#/hardware');
    await page.locator('#hw-temp .hw-cell[data-node="r3"]').click();
    await expect(page).toHaveURL(/scope=node%3Ar3/);
    await expect(page.locator('[data-sheet="r3"]')).toBeVisible();
    await expect(page.locator('#hw-temp')).toBeInViewport();
    // the sheet: its engines, its limits read from the Spark, every chart
    await expect(page.locator('[data-engine="qwen27"]')).toBeVisible();
    await expect(page.locator('[data-panel="limits"]')).toContainText('power cap');
    await expect(page.locator('[data-panel="limits"]')).toContainText('not exposed by this board');
    await expect(page.locator('[data-panel="limits"]')).toContainText(/hw slowdown/);
    await expect(page.locator('.hw-group[data-group]')).toHaveCount(14);
  });

  test('a GX10 says what it does not expose: no memory clock, no fan, no ConnectX cable; never a zero line', async ({ page }) => {
    await open(page, 'fleet4', '#/hardware?scope=node:desk');
    await expect(page.locator('[data-sheet="desk"]')).toBeVisible();
    await expect(page.locator('#hw-memclk .hw-absent')).toContainText('not exposed by this board');
    await expect(page.locator('#hw-memclk qse-fleet-chart')).toHaveCount(0);
    await expect(page.locator('#hw-fabric .hw-absent')).toContainText('ConnectX ports without a cable');
    await expect(page.locator('#hw-board')).toContainText('fan not exposed by this board');
  });

  test('Host section: the Mac’s CPU, memory and the controller, and the RTT to each Spark', async ({ page }) => {
    await open(page, 'fleet4', '#/hardware?focus=host');
    const host = page.locator('#hw-host');
    await expect(host).toContainText('Mac');
    await expect(host).toContainText('This machine may sleep');
    await expect(host.locator('.mtile')).toHaveCount(4);
    await expect(host.locator('[data-panel="rtt"] qse-fleet-chart')).toBeVisible();
    await expect(host).toBeInViewport({ timeout: 10_000 });
    // a fleet of one: the controller on its own Spark has no network to time
    await open(page, 'fleet1', '#/hardware');
    await expect(page.locator('#hw-host [data-panel="rtt"] .hw-absent')).toContainText('runs on Desk itself');
  });

  test('the topology: a Spark opens its inspector in the right sidebar, a link its details', async ({ page }) => {
    await open(page, 'fleet4', '#/overview');
    await page.locator('qse-topology .node-g[data-node="r3"]').click();
    await expect(page.locator('[data-selection] [data-inspector="r3"]')).toBeVisible();
    await expect(page.locator('[data-selection]')).toContainText('paired by ssh');
    // a link by the keyboard (the bottom dock may cover the graph's lower half)
    await page.locator('qse-topology .link-g[data-link="r1-sw"] .l-hit').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-selection]')).toContainText('fabric (ConnectX)');
    await expect(page.locator('[data-selection]')).toContainText('196 Gb/s');
  });

  test('the timeline: by Spark and by type', async ({ page }) => {
    await open(page, 'fleet4', '#/events');
    const rows = page.locator('[data-timeline] .ev-item');
    await expect(rows.first()).toBeVisible();
    await page.locator('[data-ev-node="r3"]').click();
    await expect(rows.first()).toBeVisible();
    for (const r of await rows.all()) await expect(r.locator('.node-chip')).toContainText('R-3');
    await page.locator('[data-ev-node="all"]').click();
    await page.locator('[data-ev-type]').selectOption('deploy');
    await expect(rows.first()).toBeVisible();
    for (const r of await rows.all()) await expect(r).toHaveAttribute('data-type', 'deploy');
  });

  test('the rule editor: a threshold and its hold saved; a rule without a threshold and the host’s scope cannot change', async ({ page }) => {
    await open(page, 'fleet4', '#/events');
    await page.locator('.rule-row[data-rule="node-down"]').getByRole('button', { name: 'Edit' }).click();
    await expect(page.locator('[data-rule-form="node-down"] input[name="threshold"]')).toBeDisabled();
    await page.locator('.rule-row[data-rule="host-sleep"]').getByRole('button', { name: 'Edit' }).click();
    await expect(page.locator('[data-rule-form="host-sleep"] select[name="scope"]')).toBeDisabled();
    await page.locator('.rule-row[data-rule="gpu-temp"]').getByRole('button', { name: 'Edit' }).click();
    const f = page.locator('[data-rule-form="gpu-temp"]');
    await f.locator('input[name="threshold"]').fill('hot');
    await f.getByRole('button', { name: 'Save' }).click();
    await expect(f).toContainText('The threshold is a number.');
    await f.locator('input[name="threshold"]').fill('80');
    await f.locator('input[name="for_s"]').fill('120');
    await f.getByRole('button', { name: 'Save' }).click();
    await expect(page.locator('.rule-row[data-rule="gpu-temp"]')).toContainText('≥ 80 °C for 2 min');
    expect((await json<{ rules: { id: string; threshold: number; for_s: number }[] }>(page, '/v1/fleet/rules')).rules.find((r) => r.id === 'gpu-temp')).toMatchObject({ threshold: 80, for_s: 120 });
  });

  test('a controller with an admin password: the writes wait for the sign-in, the reads do not', async ({ page, request }) => {
    await setMode(request, 'ok', { fleetPassword: true });
    try {
      await open(page, 'fleet4', '#/events');
      await expect(page.locator('[data-timeline] .ev-item').first()).toBeVisible();
      const card = page.locator('qse-events .alert-card[data-alert="gpu-temp:r3"]');
      await expect(card.getByRole('button', { name: 'Acknowledge' })).toBeDisabled();
      await expect(page.locator('qse-events .mon-signin')).toContainText('Sign in to TandemApp’s controller');
      await page.locator('qse-events .mon-signin input[type="password"]').fill(TOKEN);
      await page.locator('qse-events .mon-signin button[type="submit"]').click();
      await expect(page.locator('qse-events .mon-signin')).toHaveCount(0);
      await card.getByRole('button', { name: 'Acknowledge' }).click();
      await expect(card).toHaveClass(/is-acked/, { timeout: 20_000 });
    } finally {
      await setMode(request, 'ok');
    }
  });

  test('system notifications: off until turned on; the permission is asked then, in place', async ({ page }) => {
    // headless Chromium denies notifications outright: a stand-in that has not asked yet
    await page.addInitScript(() => {
      let perm: NotificationPermission = 'default';
      class N {
        static get permission() {
          return perm;
        }
        static async requestPermission() {
          (window as unknown as { asked: number }).asked = ((window as unknown as { asked?: number }).asked ?? 0) + 1;
          perm = 'granted';
          return perm;
        }
      }
      Object.defineProperty(window, 'Notification', { value: N, configurable: true });
    });
    await open(page, 'fleet4', '#/events');
    const box = page.locator('[data-panel="notify"] input[data-notify]');
    await expect(page.locator('[data-panel="notify"] [data-perm]')).toHaveAttribute('data-perm', 'default');
    await expect(box).not.toBeChecked();
    expect(await page.evaluate(() => (window as unknown as { asked?: number }).asked ?? 0)).toBe(0);
    await box.check();
    await expect(box).toBeChecked();
    await expect(page.locator('[data-panel="notify"] [data-perm]')).toHaveAttribute('data-perm', 'granted');
    expect(await page.evaluate(() => [(window as unknown as { asked?: number }).asked, localStorage.getItem('qse.fleet.notify')])).toEqual([1, '1']);
    await expect(page.locator('[data-panel="notify"]')).toContainText('GPU temperature');
  });

  test('phone: the three pages in one column, nothing scrolls sideways', async ({ page }, info) => {
    test.skip(info.project.name !== 'webkit-phone', 'phone only');
    for (const h of ['#/overview', '#/hardware', '#/events']) {
      await open(page, 'fleet4', h);
      await expect(page.locator(`.page-head[data-page="${h.slice(2)}"]`)).toBeVisible();
      await page.waitForTimeout(800);
      expect(await noSideScroll(page), h).toBe(true);
    }
    await open(page, 'fleet4', '#/overview');
    const tiles = page.locator('[data-tiles] .mtile');
    const [a, b, c] = await Promise.all([0, 1, 2].map((i) => tiles.nth(i).boundingBox()));
    expect(a!.y).toBe(b!.y);
    expect(c!.y).toBeGreaterThan(a!.y);
  });
});
