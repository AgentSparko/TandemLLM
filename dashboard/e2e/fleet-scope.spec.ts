// QA VIS-255 (VIS-254): every older page in the fleet's scope, against the mock fleet. The Gherkin
// scenarios of VIS-254 ("Live per node", "Compare in Playground", "Usage by node") and its acceptance:
// each page follows the scope and the chart modes; one Spark looks as before. @mock
import { expect, test, type Page } from '@playwright/test';
import { IS_MOCK, login, noHorizontalOverflow } from './helpers';

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1440) < 700;
const num = (s: string | null) => Number((s ?? '').replace(/[^\d.]/g, ''));

test.describe('the fleet in every page (VIS-254)', () => {
  test.skip(!IS_MOCK, 'the mock fleet scenarios');

  test('Gherkin "Live per node": four Sparks decoding; the Now line shows the total and a strip per Spark', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/performance', '?fleet=quad');
    const now = page.locator('[data-fleet-now]');
    await expect(now).toBeVisible({ timeout: 10_000 });
    await expect(now.locator('qse-fleet-chart')).toBeVisible({ timeout: 10_000 });
    const strips = page.locator('[data-fleet-strips] [data-strip]');
    await expect(strips).toHaveCount(4);
    for (const id of ['gold', 'r1', 'r2', 'r3']) await expect(page.locator(`[data-strip="${id}"] [data-strip-tps]`)).toHaveText(/^[1-9][\d,]*$/, { timeout: 10_000 });
    // the total is the strips' sum (each shown rounded, and the poll may land between two reads)
    const read = async () => {
      const total = num(await now.locator('[data-fleet-total]').textContent());
      const parts = await strips.locator('[data-strip-tps]').allTextContents();
      return { total, sum: parts.reduce((a, t) => a + num(t), 0) };
    };
    await expect.poll(async () => {
      const r = await read();
      return Math.abs(r.total - r.sum) <= 3;
    }).toBe(true);
    // the tiles: decode, TTFT and in flight, each with every Spark's share
    await expect(page.locator('[data-fleet-tiles] [data-tile]')).toHaveCount(3);
    await expect(page.locator('[data-tile="decode-5m"]')).toContainText('Gold');
    // the engine's own detail stays, under its Spark's chip
    await expect(page.locator('[data-here] .node-chip')).toContainText('Gold');
    await expect(page.locator('qse-live-panel')).toBeVisible();
    // the fleet's part fits the page's width (the Live panel below is the engine's, checked by its own specs)
    expect(await page.evaluate(() => {
      const doc = document.documentElement;
      const out = [...document.querySelectorAll('[data-fleet-live] *')].filter((el) => el.getBoundingClientRect().right > doc.clientWidth + 1);
      return doc.scrollWidth <= doc.clientWidth + 1 && out.length === 0;
    })).toBe(true);
  });

  test('Live follows the scope: one Spark, one strip; another Spark than this server’s says so', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/performance?scope=node%3Ar1', '?fleet=quad');
    await expect(page.locator('[data-fleet-strips] [data-strip]')).toHaveCount(1, { timeout: 10_000 });
    await expect(page.locator('[data-strip="r1"]')).toBeVisible();
    await expect(page.locator('[data-fleet-now] .eyebrow')).toContainText('Σ 1 Spark');
    await expect(page.locator('[data-here-out]')).toContainText('leaves Gold out');
    await expect(page.locator('qse-live-panel')).toHaveCount(0);
    await page.locator('[data-here-out] button').click();
    await expect(page).toHaveURL(/scope=node%3Agold/);
    await expect(page.locator('qse-live-panel')).toBeVisible();
    // a deployment: its members
    await page.goto('./#/performance?scope=deployment%3Aqwen27');
    await expect(page.locator('[data-fleet-strips] [data-strip]')).toHaveCount(2, { timeout: 10_000 });
    await expect(page.locator('[data-fleet-now] .eyebrow')).toContainText('Qwen3.8-27B');
  });

  test('the strips and the Now line hold still while the numbers change', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/performance', '?fleet=quad');
    await expect(page.locator('[data-fleet-strips] [data-strip]')).toHaveCount(4, { timeout: 10_000 });
    await expect(page.locator('[data-fleet-now] qse-fleet-chart svg').first()).toBeVisible({ timeout: 10_000 });
    const boxes = () =>
      page.evaluate(() => [...document.querySelectorAll('[data-fleet-now], [data-strip], [data-fleet-tiles], [data-here]')].map((e) => {
        const r = e.getBoundingClientRect();
        return [Math.round(r.y + scrollY), Math.round(r.height)];
      }));
    const a = await boxes();
    await page.waitForTimeout(4500);
    expect(await boxes()).toEqual(a);
  });

  test('Requests: the lanes grouped by deployment, then Spark; the scope keeps its own', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/performance?tab=requests', '?fleet=fleet4');
    await expect(page.locator('.par')).toHaveAttribute('data-in-flight', '14', { timeout: 10_000 });
    const groups = page.locator('.par-group');
    await expect(groups.first()).toBeVisible();
    await expect(page.locator('.par-group', { hasText: 'Qwen3.8-27B' }).filter({ hasText: 'R-3' }).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.par-group', { hasText: 'on no Spark' })).toBeVisible();
    // each backend card names its Spark
    await expect(page.locator('.par-bk[data-backend="r3-engine"] .par-bk-node')).toContainText('R-3');
    // a group's lanes follow its head until the next head
    const order = await page.locator('.par-table > .par-group, .par-table > .par-row[data-lane]').evaluateAll((els) => els.map((e) => (e.classList.contains('par-group') ? `#${e.getAttribute('data-group')}` : (e as HTMLElement).dataset.backend)));
    const r3At = order.indexOf('#qwen27|r3');
    expect(r3At).toBeGreaterThanOrEqual(0);
    expect(order[r3At + 1]).toBe('r3-engine');
    // the scope: R-3 only
    await page.goto('./#/performance?tab=requests&scope=node%3Ar3');
    await expect(page.locator('.par-group')).toHaveCount(1, { timeout: 10_000 });
    await expect(page.locator('.par-row[data-lane]:not([data-backend="r3-engine"])')).toHaveCount(0);
    await expect(page.locator('[data-scope-hidden]')).toContainText('outside the scope');
  });

  test('History: per Spark with the chart modes, then this server’s ledger', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/performance?tab=history&range=24h', '?fleet=quad');
    const charts = page.locator('[data-fleet-history] [data-fleet-chart]');
    await expect(charts).toHaveCount(3, { timeout: 10_000 });
    const decode = page.locator('[data-fleet-chart="tps"] qse-fleet-chart');
    await expect(decode.locator('qse-mode-switch')).toBeVisible();
    await decode.locator('qse-mode-switch button', { hasText: 'Split' }).click();
    await expect(decode.locator('.fc-note')).toContainText('one chart per Spark');
    await decode.locator('qse-mode-switch button', { hasText: 'Table' }).click();
    await expect(decode.locator('table')).toContainText('R-2');
    // TTFT is a mean: Stacked is off with the reason
    await expect(page.locator('[data-fleet-chart="ttft"] qse-mode-switch button', { hasText: 'Stacked' })).toBeDisabled();
    await expect(page.locator('#history .history-head h2')).toHaveText("This server's ledger");
    await expect(page.locator('#history qse-ribbon')).toHaveCount(5, { timeout: 10_000 });
  });

  test('Gherkin "Usage by node": a month on 3 Sparks stacks by Spark; the table has kWh and tokens per kWh', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/usage', '?fleet=gold3');
    const by = page.locator('[data-usage-fleet]');
    await expect(by.locator('[data-usage-by-node] tbody tr')).toHaveCount(3, { timeout: 15_000 });
    for (const id of ['gold', 'r3', 'bronze']) {
      const row = by.locator(`[data-usage-by-node] tr[data-node="${id}"]`);
      await expect(row.locator('[data-kwh]')).toHaveText(/^\d+\.\d{2}/);
      await expect(row.locator('[data-tpk]')).toHaveText(/^\d/);
    }
    await expect(by.locator('[data-usage-stack] qse-fleet-chart')).toBeVisible();
    await expect(by.locator('[data-usage-stack] .fc-note')).toContainText('stacked');
    await expect(by.locator('[data-tile="u-kwh"] .share')).toBeVisible();
    // the page says which ledger; this server's by default, never added to the controller's
    await expect(page.locator('[data-ledger="here"]')).toBeVisible();
    // a Spark picked: the controller's ledger, filtered, said in the chip and the ledger line
    await page.locator('[data-filter="node"] select').selectOption('r3');
    await expect(page).toHaveURL(/scope=node%3Ar3/);
    await expect(page.locator('[data-ledger="fleet"]')).toContainText("TandemApp's controller");
    await expect(page.locator('[data-chip="node"]')).toContainText('R-3');
    await expect(by.locator('[data-usage-by-node] tbody tr')).toHaveCount(1);
    await page.locator('[data-chip="node"]').click();
    await expect(page.locator('[data-ledger="here"]')).toBeVisible();
    // the controller's ledger by choice
    await page.locator('[data-ledger] [role="radio"]', { hasText: 'TandemApp controller' }).click();
    await expect(page).toHaveURL(/ledger=fleet/);
    await expect(page.locator('[data-ledger="fleet"]')).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });

  test('Dev: one Spark’s log through the controller, all of them interleaved with chips, Spark and deployment columns', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/dev', '?fleet=fleet4');
    const pick = page.locator('[data-dev-source]');
    await expect(pick).toBeVisible({ timeout: 10_000 });
    await expect(pick.locator('[data-src="here"]')).toHaveAttribute('aria-checked', 'true');
    await expect(page.locator('#requests th', { hasText: 'Spark' })).toBeVisible();
    await expect(page.locator('#requests td[data-col="node"]').first()).toBeVisible({ timeout: 15_000 });
    await pick.locator('[data-src="all"]').click();
    await expect(page).toHaveURL(/node=all/);
    await expect(page.locator('.log-line[data-node]').first()).toBeVisible({ timeout: 10_000 });
    const nodes = await page.locator('.log-line[data-node]').evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute('data-node')))]);
    expect(nodes.length).toBeGreaterThan(1);
    await expect(page.locator('.log-line[data-node] .log-node .node-chip').first()).toBeVisible();
    await pick.locator('[data-src="r3"]').click();
    await expect.poll(async () => page.locator('.log-line[data-node]').evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute('data-node')))].join())).toBe('r3');
    await expect(page.locator('#config .panel-title')).toHaveText("R-3's configuration");
    await expect(page.locator('#config [data-node-deps]')).toContainText('Qwen3.8-27B');
    await expect(page.locator('#requests .panel-sub')).toContainText("R-3's rows");
  });

  test('Chat: the model picker names the deployment and its Sparks; the Context panel says where it runs', { tag: '@mock' }, async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem('qse.chat.last'));
    await login(page, '#/chat/c_sql', '?chat=mock&fleet=fleet4');
    await page.locator('.ch-col .ch-msg').first().waitFor();
    await page.locator('.ch-pill[title="model"]').click();
    const row = page.locator('.ch-pop-models .ch-pop-item[data-model="TandemLLM"]');
    await expect(row.locator('.place-line')).toContainText('Replicas');
    await expect(row.locator('.place-line .node-chip')).toHaveCount(2);
    // a cloud model is on no Spark: no chips
    await expect(page.locator('.ch-pop-models .ch-pop-item:not([data-model="TandemLLM"]) .place-line')).toHaveCount(0);
    if (isPhone(page)) return; // the Context panel is a drawer there; the picker is the same
    const ctx = page.locator('qse-chat-context [data-runs-on]');
    await expect(ctx).toContainText('runs on', { timeout: 10_000 });
    await expect(ctx.locator('.node-chip').first()).toContainText('Desk');
    await expect(ctx).toContainText('the conversation stays on one member');
  });

  test('Gherkin "Compare in Playground": the same prompt on Gold and R-3 side by side with their chips and timings', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/playground', '?fleet=gold3');
    await expect(page.locator('[data-pg-target] select')).toBeVisible({ timeout: 10_000 });
    await page.locator('[data-pg-compare]').click();
    await expect(page.locator('[data-cmp="a"]')).toHaveValue('pin:qwen27:gold');
    await expect(page.locator('[data-cmp="b"]')).toHaveValue('pin:qwen27:r3');
    await page.locator('#pg-composer').fill('Say hi in one line.');
    await page.locator('.pg-send').click();
    const cols = page.locator('[data-cmp-col]');
    await expect(cols).toHaveCount(2);
    await expect(cols.nth(0).locator('.pg-cmp-h .node-chip')).toContainText('Gold');
    await expect(cols.nth(1).locator('.pg-cmp-h .node-chip')).toContainText('R-3');
    for (const i of [0, 1]) {
      await expect(cols.nth(i)).toHaveClass(/is-done/, { timeout: 30_000 });
      await expect(cols.nth(i).locator('[data-fig="ttft"] dd')).toHaveText(/\d\.\d\d s/);
      await expect(cols.nth(i).locator('[data-fig="total"] dd')).toHaveText(/\d\.\d\d s/);
      await expect(cols.nth(i).locator('[data-fig="tokens"] dd')).toHaveText(/\d/);
    }
    // keep one: the turn and that answer join the chat, named by its Spark
    await cols.nth(1).locator('[data-keep]').click();
    await expect(page.locator('.pg-msg[data-role="assistant"] [data-node-on]')).toContainText('R-3');
    // a target alone: the next turn goes through the gateway to the deployment
    await page.locator('[data-pg-compare]').click();
    await page.locator('[data-pg-target] select').selectOption('dep:wren');
    await expect(page).toHaveURL(/target=dep%3Awren/);
    await page.locator('#pg-composer').fill('again');
    const sent = page.waitForRequest((r) => r.url().includes('/v1/fleet/gateway/chat/completions') && r.method() === 'POST');
    await page.locator('.pg-send').click();
    expect(JSON.parse((await sent).postData() ?? '{}').model).toBe('wren');
    await expect(page.locator('.pg-msg[data-role="assistant"]').last().locator('[data-node-on]')).toContainText('Bronze', { timeout: 30_000 });
    expect(await noHorizontalOverflow(page)).toBe(true);
  });

  test('Conductor: whose conductor, by deployment; one only other Sparks serve says why', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/conductor', '?fleet=fleet4');
    const dep = page.locator('[data-cond-dep]');
    await expect(dep).toBeVisible({ timeout: 10_000 });
    await expect(dep.locator('select')).toHaveValue('qwen27');
    await expect(dep).toContainText("Desk's engine");
    await expect(dep).toContainText('R-3 runs their own copy');
    await expect(dep.locator('option[value="dsv41"]')).toHaveAttribute('disabled', '');
    await dep.locator('select').selectOption('wren');
    await expect(page).toHaveURL(/dep=wren/);
  });

  test('a ceiling at capacity: the memory bar keeps its marks and their labels inside', { tag: '@mock' }, async ({ page }) => {
    await page.route('**/v1/fleet/memory', async (r) => {
      const res = await r.fetch();
      const j = (await res.json()) as { nodes: { node: string; ceiling: number; peak: number; usable: number }[] };
      for (const n of j.nodes) if (n.node === 'desk') Object.assign(n, { ceiling: 121.63, peak: 121.2, usable: 121.6 });
      await r.fulfill({ response: res, json: j });
    });
    for (const hash of ['#/sparks/desk', '#/hardware?scope=node%3Adesk']) {
      await login(page, hash, '?fleet=fleet4');
      const bar = page.locator('.umem:not(.is-sm)').first();
      await expect(bar.locator('.umem-mark span').first()).toBeVisible({ timeout: 10_000 });
      const out = await bar.evaluate((b) => {
        const r = b.getBoundingClientRect();
        return [...b.querySelectorAll('.umem-mark, .umem-mark span')].map((m) => {
          const q = m.getBoundingClientRect();
          return { l: q.left - r.left, r: r.right - q.right };
        });
      });
      for (const m of out) expect(Math.min(m.l, m.r), JSON.stringify(out)).toBeGreaterThanOrEqual(-0.5);
    }
  });

  test('one Spark looks as before: no strips, no ledger switch, no source picker, no target, no deployment', { tag: '@mock' }, async ({ page }) => {
    await login(page, '#/performance', '?fleet=fleet1');
    await expect(page.locator('qse-live-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-fleet-live], [data-here]')).toHaveCount(0);
    await page.goto('./#/usage');
    await expect(page.locator('.hero')).toBeVisible();
    await expect(page.locator('[data-usage-fleet], [data-ledger] [role="radio"], [data-filter="node"]')).toHaveCount(0);
    await page.goto('./#/dev');
    await expect(page.locator('#console')).toBeVisible();
    await expect(page.locator('[data-dev-source], #requests th:has-text("Spark")')).toHaveCount(0);
    await page.goto('./#/playground');
    await expect(page.locator('.pg-toolbar')).toBeVisible();
    await expect(page.locator('[data-pg-target], [data-pg-compare]')).toHaveCount(0);
    await page.goto('./#/conductor');
    await expect(page.locator('.cond-head')).toBeVisible();
    await expect(page.locator('[data-cond-dep]')).toHaveCount(0);
  });
});
