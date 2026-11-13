// the shell: served, no password prompt anywhere (AU1, 2026-10-06: no sign-in screen, no sign-out,
// no token field), a 401 from a server with a password set (a banner, still no prompt), navigation +
// reload, theme, mobile, engine offline. Then TandemApp's shell (VIS-242, QA in VIS-243): every menu
// item opens its page, the old hashes redirect, the scope survives a reload, the context sidebar's
// collapse and width persist, the tablet's drawers, the phone at 375 px (drawers, bottom bar, menu
// sheet, no horizontal scroll), a fleet of one against a fleet of four. (Static path traversal is the engine's static handler; it is covered in
// the fake-engine tier, not against the mock.)
import { expect, test, type Page } from '@playwright/test';
import { IS_MOCK, goSection, login, setMode } from './helpers';

test.describe('shell', () => {
  test.beforeEach(async ({ request }) => {
    if (IS_MOCK) await setMode(request, 'ok');
  });

  test('served: /dashboard redirects to /dashboard/ and the shell loads from one origin', async ({ page, baseURL }) => {
    const origin = new URL(baseURL!).origin;
    const foreign: string[] = [];
    page.on('request', (r) => {
      if (!r.url().startsWith(origin)) foreign.push(r.url());
    });
    const res = await page.goto(new URL('/dashboard', baseURL).toString());
    expect(res?.ok()).toBeTruthy();
    expect(page.url()).toMatch(/\/dashboard\/(\?.*)?(#.*)?$/);
    await expect(page.locator('qse-app')).toBeVisible();
    expect(foreign).toEqual([]);
  });

  test('AU1: no password set, the dashboard opens straight into Overview: no sign-in screen, no token field, no sign-out', async ({ page, context }) => {
    await context.clearCookies();
    await page.goto('./');
    await expect(page.locator('.shell')).toBeVisible();
    await expect(page.locator('.istrip a.strip-i.is-on')).toHaveAttribute('data-section', 'overview');
    await expect(page.locator('.page-head[data-page="overview"]')).toBeVisible();
    await expect(page.locator('#token, #cond-token, .login-card')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Sign out' })).toHaveCount(0);
    await expect(page.locator('[data-refused]')).toHaveCount(0);
    const s = await (await page.request.get('/v1/dashboard/session')).json();
    expect(s).toMatchObject({ login: false, password_set: false });
  });

  test('AU1: a server with a password set answers 401; the page shows a banner, never a sign-in, and keeps the tab', { tag: '@mock' }, async ({ page, request }) => {
    test.skip(!IS_MOCK, 'needs the mock password switch');
    await login(page, '#/system');
    await expect(page.locator('qse-system')).toBeVisible();
    await setMode(request, 'ok', { password: true });
    await page.reload();
    await expect(page.locator('[data-refused]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-refused]')).toContainText('admin password is set');
    expect(page.url()).toContain('#/hardware/system');
    await expect(page.locator('.shell')).toBeVisible();
    await expect(page.locator('#token, #cond-token, .login-card, input[type="password"]')).toHaveCount(0);
    await setMode(request, 'ok');
  });

  test('navigation and reload keep the Dev page', async ({ page }) => {
    await login(page);
    await goSection(page, 'Dev');
    await expect(page.locator('qse-dev')).toBeVisible();
    await page.reload();
    await expect(page.locator('qse-dev')).toBeVisible();
    await expect(page.locator('.istrip a.strip-i.is-on')).toHaveAttribute('data-section', 'dev');
    // on the phone Dev is behind More and the More slot shows it
    if (await page.locator('.bb-more').isVisible()) await expect(page.locator('.bb-more')).toHaveText('Dev');
    else await expect(page.locator('.crumb')).toHaveText(/Tune\s*\/\s*Dev/);
  });

  test('theme: follows a light OS preference, and a toggle to dark is remembered', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.addInitScript(() => {
      // forget a remembered theme once, before the first load only
      if (!sessionStorage.getItem('pw-theme-cleared')) {
        localStorage.removeItem('qse.theme');
        sessionStorage.setItem('pw-theme-cleared', '1');
      }
    });
    await login(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    // the phone has the theme in the menu sheet
    if (await page.locator('.bb-more').isVisible()) await page.locator('.bb-more').click();
    await page.getByRole('button', { name: /Switch to dark theme/ }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });

  test('engine offline: the pill says offline and panels name the endpoint', { tag: '@mock' }, async ({ page, request }) => {
    test.skip(!IS_MOCK, 'needs the mock failure switch');
    await login(page, '#/system');
    await expect(page.locator('.pill[data-status]')).toHaveText(/ok|busy/);
    await setMode(request, 'offline');
    await expect(page.locator('.pill[data-status="offline"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('.state-error').first()).toContainText('/v1/dashboard/system');
    await setMode(request, 'ok');
    await expect(page.locator('.pill[data-status="ok"], .pill[data-status="busy"]')).toBeVisible({ timeout: 15_000 });
  });

  test('status pill shows the engine state; the menu panel’s foot the version and uptime', async ({ page }) => {
    await login(page);
    await expect(page.locator('.pill[data-status]')).toBeVisible();
    await expect(page.locator('.lp-foot')).toContainText(/up \d/);
    await expect(page.locator('.more-foot')).toContainText(/up \d/);
  });

  test('phone: the bottom bar is Chat, Overview, Performance, Windows and More, every label whole at 390 px', async ({ page }, info) => {
    test.skip(info.project.name !== 'webkit-phone', 'the bottom bar is the phone layout');
    // NP1's night pass (2026-10-04, issue 5): "Backends" showed as "Backen…"; since 2026-10-13 the
    // bar holds a few slots and the other pages sit behind More (VIS-242: the grouped menu)
    await login(page, '#/overview');
    await expect(page.locator('.bottombar')).toBeVisible();
    const shown = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('.bottombar .bb-i')].map((tab) => {
        const el = tab.querySelector('.nav-label') as HTMLElement;
        return { text: el.textContent, over: el.scrollWidth - el.clientWidth };
      }));
    expect(shown.map((x) => x.text)).toEqual(['Chat', 'Overview', 'Performance', 'Windows', 'More']);
    expect(shown.filter((x) => x.over > 0)).toEqual([]);
    // the More sheet holds the grouped menu; a page behind More takes the More slot
    await page.locator('.bb-more').click();
    await expect(page.locator('.more-sheet')).toBeVisible();
    await expect(page.locator('.more-sheet .more-g')).toHaveText(['Monitor', 'Fleet', 'Tune', 'Settings']);
    await expect(page.locator('.more-item .nav-label')).toHaveText(['Chat', 'Overview', 'Performance', 'Usage', 'Hardware', 'Events & Alerts', 'Sparks', 'Models', 'API', 'Conductor', 'Playground', 'Dev', 'Get started', 'Add Spark', 'App']);
    await page.locator('.more-item', { hasText: 'Dev' }).click();
    await expect(page).toHaveURL(/#\/dev/);
    await expect(page.locator('.more-sheet')).toBeHidden();
    await expect(page.locator('.bb-more')).toHaveText('Dev');
    await expect(page.locator('.bb-more')).toHaveClass(/is-on/); // the button's own state — on purpose, not the view's
  });

  test('AU1: no password set, Performance reads /v1/dashboard/metrics with no session and System opens; no cookie is set', { tag: '@mock' }, async ({ page, context }) => {
    test.skip(!IS_MOCK, 'reads the mock strip');
    await context.clearCookies();
    await page.goto('./#/performance');
    await expect(page.locator('.shell')).toBeVisible();
    await expect(page.locator('qse-performance')).toBeVisible();
    await expect(page.locator('.live-metrics .stat')).toHaveCount(3);
    await expect(page.locator('.live-metrics .state-error')).toHaveCount(0);
    await page.goto('./#/hardware/system');
    await expect(page.locator('qse-system')).toBeVisible();
    const cookies = await context.cookies();
    expect(cookies.find((c) => c.name === 'qse_dash')).toBeUndefined();
  });
});

// ---- TandemApp's shell (VIS-242, QA VIS-243) ---------------------------------------------------------
const PAGES: [section: string, selector: string, crumb: RegExp][] = [
  ['chat', 'qse-chat', /^Chat$/],
  ['overview', '.page-head[data-page="overview"]', /Monitor\s*\/\s*Overview/],
  ['performance', 'qse-performance', /Monitor\s*\/\s*Performance/],
  ['usage', 'qse-usage', /Monitor\s*\/\s*Usage/],
  ['hardware', '.page-head[data-page="hardware"]', /Monitor\s*\/\s*Hardware/],
  ['events', '.page-head[data-page="events"]', /Monitor\s*\/\s*Events & Alerts/],
  ['sparks', '.page-head[data-page="sparks"]', /Fleet\s*\/\s*Sparks/],
  ['models', '.page-head[data-page="models"]', /Fleet\s*\/\s*Models/],
  ['api', '.page-head[data-page="api"]', /Fleet\s*\/\s*API/],
  ['conductor', 'qse-conductor', /Tune\s*\/\s*Conductor/],
  ['playground', 'qse-playground', /Tune\s*\/\s*Playground/],
  ['dev', 'qse-dev', /Tune\s*\/\s*Dev/],
  ['start', '.page-head[data-page="start"]', /Settings\s*\/\s*Get started/],
  ['add-spark', '.page-head[data-page="add-spark"]', /Settings\s*\/\s*Add Spark/],
  ['app', '.page-head[data-page="app"]', /Settings\s*\/\s*App/],
];

/** nothing on the page is wider than the window */
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

test.describe('TandemApp shell (VIS-242)', () => {
  test.beforeEach(async ({ request }, info) => {
    test.skip(!IS_MOCK, 'the mock fleet');
    test.skip(info.project.name === 'webkit-phone', 'desk and tablet; the phone has its own tests below');
    await setMode(request, 'ok');
    // a page's sidebars are saved on the controller too (VIS-256): each test starts from none
    await request.post('/__mock/fleet', { data: { reset: true } });
  });

  test('every item of the icon strip opens its page, marked in the strip, the panel and the crumb', async ({ page }) => {
    await login(page, '#/overview');
    for (const [id, sel, crumb] of PAGES) {
      await page.locator(`.istrip a.strip-i[data-section="${id}"]`).click();
      await expect(page.locator(sel).first(), id).toBeVisible();
      await expect(page.locator('.istrip a.strip-i.is-on')).toHaveAttribute('data-section', id);
      // on Chat the panel holds the list of chats (VIS-256), elsewhere the menu
      if (id === 'chat') await expect(page.locator('.lpanel qse-chat-rail')).toHaveCount(1);
      else await expect(page.locator('.lpanel .nav-i.is-on')).toHaveAttribute('data-section', id);
      await expect(page.locator('.crumb')).toHaveText(crumb);
    }
    // the indicator stands on the item that is on
    const off = () => page.evaluate(() => Math.abs(document.querySelector('.istrip .ind')!.getBoundingClientRect().top - document.querySelector('.istrip a.is-on')!.getBoundingClientRect().top));
    await expect.poll(off).toBeLessThan(2);
  });

  test('the sub-pages: Performance → Requests, Hardware → System, Models → Serve a model and the Setup Guide, API → Backends, a Spark', async ({ page }) => {
    await login(page, '#/performance');
    const sub = (label: string) => page.locator('.lpanel .nav-sub a', { hasText: label });
    await sub('Requests').click();
    await expect(page.locator('qse-parallel')).toBeVisible();
    await expect(page).toHaveURL(/#\/performance\?tab=requests$/);
    await page.locator('.lpanel .nav-i[data-section="hardware"]').click();
    await sub('System').click();
    await expect(page.locator('qse-system')).toBeVisible();
    await expect(page).toHaveURL(/#\/hardware\/system$/);
    await page.locator('.lpanel .nav-i[data-section="models"]').click();
    await sub('Serve a model').click();
    await expect(page.locator('qse-serve')).toBeVisible();
    await expect(page).toHaveURL(/#\/models\/place/);
    await sub('Setup Guide').click();
    await expect(page.locator('qse-setup')).toBeVisible();
    await expect(page).toHaveURL(/#\/models\/serve$/);
    await page.locator('.lpanel .nav-i[data-section="api"]').click();
    await sub('Backends').click();
    await expect(page.locator('qse-backends')).toBeVisible();
    await page.locator('.lpanel .nav-i[data-section="sparks"]').click();
    await sub('R-3').click();
    await expect(page.locator('.page-head h1')).toHaveText('R-3');
    await expect(sub('R-3')).toHaveAttribute('aria-current', 'page');
  });

  test('the old hashes redirect; deep links keep working', async ({ page }) => {
    const cases: [string, RegExp, string][] = [
      ['#/system', /#\/hardware\/system$/, 'qse-system'],
      ['#/backends?backend=deepseek', /#\/api\/backends\?backend=deepseek$/, 'qse-backends'],
      ['#/setup', /#\/models\/serve$/, 'qse-setup'],
      ['#/setup/qwen3.8-27b/weights', /#\/models\/serve\/qwen3\.8-27b\/weights$/, 'qse-setup'],
      ['#/performance?tab=parallel', /#\/performance\?tab=requests$/, 'qse-parallel'],
      ['#/usage', /#\/usage$/, 'qse-usage'],
      ['#/no-such-page', /#\/no-such-page$/, '.page-head[data-page="overview"]'],
    ];
    for (const [from, to, sel] of cases) {
      await page.goto(`./${from}`);
      await expect(page, from).toHaveURL(to);
      await expect(page.locator(sel).first(), from).toBeVisible();
    }
    // the setup guide's deep link lands on its step: a new browser opens the simple guide, which
    // has no rail and names its step in the page; the advanced guide's rail marks the step
    await page.goto('./#/setup/qwen3.8-27b/weights');
    await expect(page.locator('qse-setup .setup-nav-mid')).toHaveText('Step 2 of 3');
    await page.evaluate(() => localStorage.setItem('tandem.setup.simple', '0'));
    await page.reload();
    await expect(page).toHaveURL(/#\/models\/serve\/qwen3\.8-27b\/weights$/);
    await expect(page.locator('qse-setup [aria-current="step"]').first()).toBeVisible();
    await expect(page.locator('qse-setup [data-rail="weights"] [aria-current="step"]')).toBeVisible();
    // a chat's deep link
    await page.goto('./?chat=mock#/chat/c_sync');
    await expect(page.locator('.ch-now')).toBeVisible();
    await expect(page).toHaveURL(/#\/chat\/c_sync$/);
    // an old link inside a page follows the same way
    await page.goto('./#/overview');
    await page.evaluate(() => (location.hash = '#/system'));
    await expect(page).toHaveURL(/#\/hardware\/system$/);
    await expect(page.locator('qse-system')).toBeVisible();
  });

  test('the scope: picked in the bar, in the URL, kept on reload and across the Monitor pages', async ({ page }) => {
    await login(page, '#/overview');
    const bar = page.locator('qse-scope-bar');
    await expect(bar.locator('.scope-dd-btn')).toContainText('All Sparks');
    await bar.locator('.scope-dd-btn').click();
    await expect(bar.locator('.scope-opt')).toHaveCount(1 + 4 + 3);
    await bar.locator('.scope-opt[data-scope="r3"]').click();
    await bar.getByRole('button', { name: '1h' }).click();
    await bar.locator('.live-btn').click();
    await expect(page).toHaveURL(/scope=node%3Ar3/);
    await expect(page).toHaveURL(/range=1h/);
    await expect(page).toHaveURL(/live=0/);
    await page.reload();
    await expect(bar.locator('.scope-dd-btn')).toContainText('R-3');
    await expect(bar.getByRole('button', { name: '1h' })).toHaveAttribute('aria-pressed', 'true');
    await expect(bar.locator('.live-btn')).toHaveText(/Paused/);
    // the chart follows the scope: one Spark, one series
    await expect(page.locator('[data-panel="decode"] qse-fleet-chart')).toBeVisible();
    await page.locator('.lpanel .nav-i[data-section="performance"]').click();
    await expect(page).toHaveURL(/#\/performance\?scope=node%3Ar3&range=1h&live=0$/);
    await expect(bar.locator('.scope-dd-btn')).toContainText('R-3');
    // Escape closes the dropdown and gives the button its focus back
    await bar.locator('.scope-dd-btn').click();
    await page.keyboard.press('Escape');
    await expect(bar.locator('.scope-menu')).toHaveCount(0);
    await expect(bar.locator('.scope-dd-btn')).toBeFocused();
    // a page outside Monitor has no scope bar
    await page.locator('.lpanel .nav-i[data-section="dev"]').click();
    await expect(page.locator('qse-scope-bar')).toHaveCount(0);
  });

  test('the context sidebar: on every page, collapses to a strip with its title, resizes, and both persist', async ({ page }) => {
    await login(page, '#/overview');
    const ctx = page.locator('aside.ctx');
    // at 1440 px a first visit keeps today's pages their width: the context starts as its strip
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    await expect(ctx.locator('.ctx-vt')).toHaveText('Fleet now');
    await expect.poll(() => ctx.evaluate((el) => el.getBoundingClientRect().width)).toBe(44);
    for (const id of ['usage', 'dev', 'conductor', 'events']) {
      await page.locator(`.istrip a.strip-i[data-section="${id}"]`).click();
      await expect(ctx.locator('.ctx-vt'), id).toBeVisible();
    }
    // each page keeps its own open state (VIS-256): Overview's
    await page.locator('.istrip a.strip-i[data-section="overview"]').click();
    await ctx.locator('.ctx-vt').click();
    await expect(page.locator('.shell')).not.toHaveClass(/ctx-closed/);
    await expect(ctx.locator('.ctx-t')).toHaveText('Fleet now');
    await expect(ctx.locator('.mini-now')).toHaveCount(4);
    await expect.poll(() => ctx.evaluate((el) => el.getBoundingClientRect().width)).toBe(340);
    await page.reload();
    await expect(page.locator('.shell')).not.toHaveClass(/ctx-closed/);
    await page.locator('.side-btn[data-side="right"]').click();
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    await page.reload();
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    await page.locator('.side-btn[data-side="right"]').click();
    await expect.poll(() => ctx.evaluate((el) => el.getBoundingClientRect().width)).toBe(340);
    // drag its edge 60 px to the left: 60 px wider
    const rz = ctx.locator('.rz');
    const box = (await rz.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + 200);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 - 60, box.y + 200, { steps: 6 });
    await page.mouse.up();
    await expect.poll(() => ctx.evaluate((el) => el.getBoundingClientRect().width)).toBe(400);
    // the keyboard: an arrow moves the edge 16 px
    await rz.focus();
    await page.keyboard.press('ArrowLeft');
    await expect(rz).toHaveAttribute('aria-valuenow', '416');
    await page.reload();
    await expect.poll(() => ctx.evaluate((el) => el.getBoundingClientRect().width)).toBe(416);
    // the left panel collapses to the strip and comes back the same way
    await page.locator('.side-btn[data-side="left"]').click();
    await expect.poll(() => page.locator('.lpanel').evaluate((el) => el.getBoundingClientRect().width)).toBe(0);
    await page.reload();
    await expect(page.locator('.shell')).toHaveClass(/rail-closed/);
    await expect(page.locator('.istrip')).toBeVisible();
    await page.locator('.side-btn[data-side="left"]').click();
    await expect(page.locator('.shell')).not.toHaveClass(/rail-closed/);
  });

  test('Chat’s sidebars are the shell’s: its list on the left, its Context on the right, open; each page keeps its own', async ({ page }) => {
    await login(page, '#/chat/c_sync', '?chat=mock');
    await expect(page.locator('.shell')).not.toHaveClass(/rail-closed/);
    await expect(page.locator('.shell')).not.toHaveClass(/ctx-closed/);
    await expect(page.locator('.lpanel qse-chat-rail')).toBeVisible();
    await expect(page.locator('aside.ctx qse-chat-context')).toBeVisible();
    await page.locator('.side-btn[data-side="right"]').click();
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    // the other pages keep theirs (at 1440 px: the panel open, the context a strip)
    await page.locator('.istrip a.strip-i[data-section="overview"]').click();
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    await page.locator('.side-btn[data-side="right"]').click();
    await expect(page.locator('.shell')).not.toHaveClass(/ctx-closed/);
    await page.locator('.istrip a.strip-i[data-section="chat"]').click();
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    await expect(page.locator('.shell')).not.toHaveClass(/rail-closed/);
  });

  test('a storage that throws: the shell still opens and its sidebars still toggle', async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', {
        get() {
          throw new DOMException('denied', 'SecurityError');
        },
      });
    });
    await login(page, '#/overview');
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    await page.locator('.side-btn[data-side="right"]').click();
    await expect(page.locator('.shell')).not.toHaveClass(/ctx-closed/);
    await page.locator('.side-btn[data-side="left"]').click();
    await expect(page.locator('.shell')).toHaveClass(/rail-closed/);
  });

  test('tablet (820 px): the panel and the context are drawers over the page, Escape and the scrim close them', async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1000 });
    await login(page, '#/overview');
    await expect(page.locator('.shell')).toHaveAttribute('data-w', 'tablet');
    await expect(page.locator('.istrip')).toBeVisible();
    await expect(page.locator('.lpanel')).not.toBeInViewport();
    await expect(page.locator('aside.ctx')).not.toBeInViewport();
    const left = page.locator('.side-btn[data-side="left"]');
    await left.click();
    await expect(page.locator('.lpanel')).toBeInViewport();
    await expect(page.locator('.drw-scrim')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.lpanel')).not.toBeInViewport();
    await expect(left).toBeFocused();
    await page.locator('.side-btn[data-side="right"]').click();
    await expect(page.locator('aside.ctx')).toBeInViewport();
    await page.locator('.drw-scrim').click({ position: { x: 20, y: 300 } });
    await expect(page.locator('aside.ctx')).not.toBeInViewport();
    // a link in the drawer closes it
    await left.click();
    await page.locator('.lpanel .nav-i[data-section="usage"]').click();
    await expect(page.locator('qse-usage')).toBeVisible();
    await expect(page.locator('.lpanel')).not.toBeInViewport();
    expect(await noSideScroll(page)).toBe(true);
  });

  test('a fleet of one feels like one box; a fleet of four gets the Spark menu', async ({ page }) => {
    await page.goto('./?fleet=fleet1#/overview');
    await expect(page.locator('.fleet-pill .fdot')).toHaveCount(1);
    await expect(page.locator('qse-scope-bar')).toBeVisible();
    await expect(page.locator('qse-scope-bar .scope-dd')).toHaveCount(0);
    await expect(page.locator('qse-scope-bar .scope-range')).toBeVisible();
    await expect(page.locator('.fleet-health')).toContainText('1 Spark');
    await expect(page.locator('.istrip .strip-nodes b')).toHaveCount(1);
    await expect(page.locator('aside.ctx .mini-now')).toHaveCount(1);
    await page.goto('./?fleet=fleet4#/overview');
    await expect(page.locator('.fleet-pill .fdot')).toHaveCount(4);
    await expect(page.locator('.fleet-pill .fdot.is-alert')).toHaveCount(1);
    await expect(page.locator('.fleet-pill')).toHaveAttribute('aria-label', /^Fleet: 4 Sparks, .* active alerts?\. Open Events & Alerts$/);
    await expect(page.locator('qse-scope-bar .scope-dd')).toHaveCount(1);
    await expect(page.locator('.istrip .strip-nodes b')).toHaveCount(4);
    await page.locator('.fleet-pill').click();
    await expect(page.locator('.page-head[data-page="events"]')).toBeVisible();
  });
});

test.describe('TandemApp shell on a phone (VIS-242)', () => {
  test.beforeEach(async ({ page, request }) => {
    test.skip(!IS_MOCK, 'the mock fleet');
    await setMode(request, 'ok');
    await request.post('/__mock/fleet', { data: { reset: true } });
    await page.setViewportSize({ width: 375, height: 812 });
  });

  test('375 px: the bottom bar, the left drawer (strip and panel), the right drawer, the menu sheet, no horizontal scroll', async ({ page }) => {
    await login(page, '#/overview');
    await expect(page.locator('.shell')).toHaveAttribute('data-w', 'phone');
    await expect(page.locator('.bottombar')).toBeVisible();
    await expect(page.locator('.bb-i.is-on')).toHaveAttribute('data-section', 'overview');
    await expect(page.locator('.istrip')).not.toBeInViewport();
    // the left drawer: the strip and the panel
    const menu = page.locator('.side-btn[data-side="left"]');
    await menu.click();
    await expect(page.locator('.istrip')).toBeInViewport();
    await expect(page.locator('.lpanel')).toBeInViewport();
    await page.keyboard.press('Escape');
    await expect(page.locator('.istrip')).not.toBeInViewport();
    await expect(menu).toBeFocused();
    // the right drawer
    await page.locator('.side-btn[data-side="right"]').click();
    await expect(page.locator('aside.ctx')).toBeInViewport();
    await expect(page.locator('aside.ctx .ctx-t')).toHaveText('Fleet now');
    await page.keyboard.press('Escape');
    await expect(page.locator('aside.ctx')).not.toBeInViewport();
    // the scope is a chip that opens a sheet
    await page.locator('.scope-chip').click();
    await expect(page.locator('.scope-sheet')).toBeVisible();
    await page.locator('.scope-sheet .scope-opt[data-scope="desk"]').click();
    await expect(page).toHaveURL(/scope=node%3Adesk/);
    await expect(page.locator('.scope-sheet')).toHaveCount(0);
    // the menu sheet, then a page behind More
    await page.locator('.bb-more').click();
    await expect(page.locator('.more-sheet')).toBeVisible();
    await expect(page.locator('.more-item:not(.is-off)').first()).toBeFocused();
    await page.locator('.more-item', { hasText: 'Hardware' }).click();
    await expect(page.locator('.page-head[data-page="hardware"]')).toBeVisible();
    await expect(page.locator('.bb-more')).toHaveText('Hardware');
    for (const h of ['#/overview', '#/performance', '#/usage', '#/hardware', '#/hardware/system', '#/events', '#/sparks', '#/api/backends', '#/dev']) {
      await page.goto(`./${h}`);
      await page.waitForTimeout(600);
      expect(await noSideScroll(page), h).toBe(true);
    }
  });

  test('375 px: the bottom bar steps aside while a field has focus', async ({ page }) => {
    await login(page, '#/dev');
    await page.getByPlaceholder('search (server-side)').click();
    await expect(page.locator('.shell')).toHaveClass(/is-typing/);
  });
});
