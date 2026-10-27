// The workspace (VIS-256, QA VIS-257) against the mock fleet: both sidebars on every page with the
// page's own context, Chat with one right column (its Context panel in the shell's sidebar, its list
// in the left panel, its panes as windows), windows added on Hardware on a Spark and on R-3, docked
// beside and below, still there after a reload with the same targets; tabs that move, float, maximise,
// minimise and dock again by dragging; Reset layout; a phone's tray of sheets on Chat; a storage that
// throws. Spark only (Chromium; the phone project runs Chromium with a phone's viewport there):
//   npx playwright test e2e/workspace.spec.ts
import { expect, test, type Locator, type Page } from '@playwright/test';
import { IS_MOCK, login, setMode } from './helpers';

const SHOTS = process.env.WM_SHOTS;

/** each page and the title of its right sidebar */
const PAGES: [section: string, title: string][] = [
  ['chat', 'Context'],
  ['overview', 'Fleet now'],
  ['performance', 'Now, per Spark'],
  ['usage', 'Today'],
  ['hardware', 'Spark status'],
  ['events', 'Firing now'],
  ['sparks', 'Sparks'],
  ['models', 'Deployments'],
  ['api', 'Gateway'],
  ['conductor', 'Conductor'],
  ['playground', 'Targets'],
  ['dev', 'This box'],
  ['add-spark', 'Add Spark'],
  ['app', 'This device'],
];

const bottom = (page: Page) => page.locator('.wm-dock-bottom');
const right = (page: Page) => page.locator('.wm-dock-right');
const tabs = (dock: Locator) => dock.locator('.wm-tab .wm-tab-b');
const shown = (dock: Locator) => dock.locator('.wm-body:not([hidden])');

/** the left panel and its content, the page and the bottom dock, as laid out (left edges, widths) */
const columns = (page: Page) =>
  page.evaluate(() => {
    const r = (sel: string) => document.querySelector(sel)?.getBoundingClientRect() ?? null;
    const panel = r('.lpanel')!;
    const inner = r('.lpanel-in')!;
    const main = r('main.content')!;
    const dock = r('.wm-dock-bottom');
    const neu = document.querySelector<HTMLElement>('.lpanel .ch-new');
    return { panel: Math.round(panel.width), inner: Math.round(inner.width), main: Math.round(main.left), mainRight: Math.round(main.right), dock: dock ? Math.round(dock.left) : null, dockRight: dock ? Math.round(dock.right) : null, newClipped: neu ? neu.scrollWidth > neu.clientWidth + 1 || neu.getBoundingClientRect().right > panel.right + 1 : null };
  });

async function addWindow(page: Page, kind: string, target?: 'Right' | 'Bottom' | 'Float'): Promise<void> {
  await page.locator('.btn-win').click();
  const menu = page.locator('.wm-menu');
  await expect(menu).toBeVisible();
  if (target) await menu.locator('.wm-target .seg-btn', { hasText: target }).click();
  await menu.locator(`.wm-mi[data-kind="${kind}"]`).click();
  await expect(menu).toHaveCount(0);
}

test.describe('workspace on a desk (VIS-256)', () => {
  test.beforeEach(async ({ request, page }, info) => {
    test.skip(!IS_MOCK, 'the mock fleet');
    test.skip(info.project.name === 'webkit-phone', 'the phone has its own tests below');
    await setMode(request, 'ok');
    // every test starts from the defaults: the mock controller's saved layouts and this browser's
    await request.post('/__mock/fleet', { data: { reset: true } });
    await page.addInitScript(() => {
      if (sessionStorage.getItem('wm-cleared')) return;
      for (const k of ['tandemapp.layouts', 'tandemapp.widths', 'tandemapp.layout', 'qse.chat.last']) localStorage.removeItem(k);
      sessionStorage.setItem('wm-cleared', '1');
    });
  });

  test('both sidebars on every page over 1100 px, each page with its own context', async ({ page }) => {
    await login(page, '#/overview', '?chat=mock');
    for (const [id, title] of PAGES) {
      await page.locator(`.istrip a.strip-i[data-section="${id}"]`).click();
      await expect(page.locator('.istrip'), id).toBeVisible();
      await expect(page.locator('aside.ctx'), id).toBeVisible();
      if (await page.locator('.shell.ctx-closed').count()) {
        await expect(page.locator('aside.ctx .ctx-vt'), id).toHaveText(title);
        await page.locator('aside.ctx .ctx-vt').click();
      }
      await expect(page.locator('aside.ctx .ctx-t'), id).toHaveText(title);
      await expect(page.locator('aside.ctx .ctx-b > *').first(), id).toBeVisible();
      // the left panel: the menu, or Chat's list of chats
      if (id === 'chat') await expect(page.locator('.lpanel qse-chat-rail')).toBeVisible();
      else await expect(page.locator('.lpanel .nav-i.is-on'), id).toHaveAttribute('data-section', id);
    }
  });

  test('Chat has one right column: its Context panel, with its eight sections, is the shell’s sidebar', async ({ page }) => {
    await login(page, '#/chat/c_sync', '?chat=mock');
    await expect(page.locator('.ch-now')).toBeVisible();
    await expect(page.locator('.shell')).not.toHaveClass(/ctx-closed/);
    await expect(page.locator('aside.ctx .ctx-t')).toHaveText('Context');
    await expect(page.locator('aside.ctx qse-chat-context')).toBeVisible();
    await expect(page.locator('aside.ctx [data-sec]')).not.toHaveCount(0);
    await expect(page.locator('qse-chat .ch-ctx, qse-chat .ch-rail, aside.ch-ctx')).toHaveCount(0);
    // the header's Context button closes and opens the shell's sidebar
    await page.getByRole('button', { name: 'Context panel', exact: true }).click();
    await expect(page.locator('.shell')).toHaveClass(/ctx-closed/);
    await page.getByRole('button', { name: 'Context panel', exact: true }).click();
    await expect(page.locator('.shell')).not.toHaveClass(/ctx-closed/);
    // the list of chats is the left panel; its hamburger hides and shows it
    await expect(page.locator('.lpanel qse-chat-rail .ch-new')).toBeVisible();
    await page.getByRole('button', { name: 'Hide the chats' }).click();
    await expect(page.locator('.shell')).toHaveClass(/rail-closed/);
    await page.getByRole('button', { name: 'Show the chats' }).click();
    // the panel settles at Chat's list width before the shot
    await expect.poll(async () => (await columns(page)).panel).toBeGreaterThanOrEqual(272);
    // the chat's terminal pane is a window below the thread
    await expect(tabs(bottom(page))).toHaveText(['Terminal']);
    await expect(bottom(page).locator('qse-chat-pane .xterm-rows')).toContainText('sandbox', { timeout: 15_000 });
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/chat-desk.png` });
  });

  test('Chat’s list and its windows keep to the page’s column: laid out to the panel’s width, the dock under the thread, open, collapsed and on a tablet', async ({ page }) => {
    await login(page, '#/chat/c_sync', '?chat=mock');
    await expect(tabs(bottom(page))).toHaveText(['Terminal']);
    const check = async (open: boolean) => {
      await expect.poll(async () => {
        const c = await columns(page);
        return open ? c.panel === c.inner && c.panel >= 272 : c.panel === 0;
      }).toBe(true);
      const c = await columns(page);
      expect(c.dock, JSON.stringify(c)).toBe(c.main);
      expect(c.dockRight, JSON.stringify(c)).toBe(c.mainRight);
      if (open) expect(c.newClipped, JSON.stringify(c)).toBe(false);
    };
    await check(true);
    // collapsed: the thread and the dock start at the strip's edge
    await page.getByRole('button', { name: 'Hide the chats' }).click();
    await check(false);
    await page.getByRole('button', { name: 'Show the chats' }).click();
    await check(true);
    // the context sidebar closed: the dock follows the page's right edge too
    await page.locator('.side-btn[data-side="right"]').click();
    await expect.poll(async () => (await columns(page)).dockRight).toBe((await columns(page)).mainRight);
    // a tablet: the panel is a drawer over the page, its content at the drawer's width
    await page.setViewportSize({ width: 768, height: 1024 });
    await expect(page.locator('.shell')).toHaveAttribute('data-w', 'tablet');
    await expect.poll(async () => {
      const c = await columns(page);
      return c.dock === c.main && c.dockRight === c.mainRight;
    }).toBe(true);
    await page.getByRole('button', { name: 'Show the chats' }).click();
    await expect(page.locator('.lpanel qse-chat-rail .ch-new')).toBeInViewport();
    await expect.poll(async () => {
      const c = await columns(page);
      // the drawer's own right rule is the 1 px between them
      return c.panel - c.inner <= 1 && c.panel >= 272 && !c.newClipped;
    }).toBe(true);
  });

  test('Hardware: a Terminal on R-1 and Logs on R-3 side by side, the same places and targets after a reload', async ({ page }) => {
    await login(page, '#/hardware');
    // the page's default: a terminal on a Spark, below the page, which says honestly it is not reachable yet
    await expect(tabs(bottom(page))).toHaveCount(1);
    await expect(shown(bottom(page)).locator('[data-unreachable]')).toContainText('TandemNode agent');
    await addWindow(page, 'terminal', 'Bottom');
    await shown(bottom(page)).locator('select[aria-label="Terminal on"]').selectOption('r1');
    await expect(tabs(bottom(page))).toHaveText(['Terminal · Desk', 'Terminal · R-1']);
    await expect(shown(bottom(page)).locator('[data-unreachable="r1"]')).toBeVisible();
    await addWindow(page, 'logs', 'Right');
    await shown(right(page)).locator('select[aria-label="Spark"]').selectOption('r3');
    await expect(tabs(right(page))).toHaveText(['Logs · R-3']);
    await expect(shown(right(page)).locator('.wm-log-l').first()).toBeVisible();
    await expect(shown(right(page)).locator('.wm-log-l .node-alias').first()).toHaveText('R-3');
    // side by side: the page and the logs share the width, the terminal runs below both
    const r = (await right(page).boundingBox())!;
    const b = (await bottom(page).boundingBox())!;
    const main = (await page.locator('main.content').boundingBox())!;
    expect(r.x).toBeGreaterThan(main.x);
    expect(r.y + r.height).toBeLessThanOrEqual(b.y + 2);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/hardware-windows-desk.png` });
    await page.waitForTimeout(600);
    await page.reload();
    await expect(tabs(bottom(page))).toHaveText(['Terminal · Desk', 'Terminal · R-1']);
    await expect(tabs(right(page))).toHaveText(['Logs · R-3']);
    await expect(shown(bottom(page)).locator('select[aria-label="Terminal on"]')).toHaveValue('r1');
    await expect(shown(right(page)).locator('select[aria-label="Spark"]')).toHaveValue('r3');
    // another page keeps its own
    await page.locator('.istrip a.strip-i[data-section="usage"]').click();
    await expect(page.locator('.wm-dock')).toHaveCount(0);
  });

  test('a tab moves to the other dock, floats, maximises, minimises and comes back; dragged near the right edge it docks there', async ({ page }) => {
    await login(page, '#/overview');
    await expect(tabs(bottom(page))).toHaveText(['Logs · all Sparks', 'Chart · power']);
    await expect(shown(bottom(page)).locator('.wm-log-l').first()).toBeVisible();
    // the chart to the right dock
    await bottom(page).locator('.wm-tab-b', { hasText: 'Chart' }).click();
    await bottom(page).getByRole('button', { name: 'Move to the right dock' }).click();
    await expect(tabs(right(page))).toHaveText(['Chart · power']);
    await expect(shown(right(page)).locator('qse-fleet-chart')).toBeVisible();
    // the logs float, and are dragged back by their title bar to the right dock
    await bottom(page).getByRole('button', { name: 'Float' }).click();
    await expect(page.locator('.wm-dock-bottom')).toHaveCount(0);
    const win = page.locator('.wm-win');
    await expect(win).toHaveCount(1);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/overview-floating.png` });
    // maximise and back
    await win.getByRole('button', { name: 'Maximise' }).click();
    await expect(win).toHaveClass(/is-max/);
    await page.keyboard.press('Escape');
    await expect(win).not.toHaveClass(/is-max/);
    // minimise to a chip, and back
    await win.getByRole('button', { name: 'Minimise' }).click();
    await expect(page.locator('.wm-min')).toHaveText(['Logs · all Sparks']);
    await page.locator('.wm-min').click();
    await expect(page.locator('.wm-win')).toHaveCount(1);
    const h = (await win.locator('.wm-win-h').boundingBox())!;
    await page.mouse.move(h.x + 60, h.y + h.height / 2);
    await page.mouse.down();
    await page.mouse.move(h.x + 120, h.y + 40, { steps: 4 });
    await expect(page.locator('.wm-drop')).toBeVisible();
    await page.mouse.move(1180, 400, { steps: 8 });
    await expect(page.locator('.wm-drop .z-r')).toHaveClass(/is-hot/);
    await page.mouse.up();
    await expect(tabs(right(page))).toHaveText(['Chart · power', 'Logs · all Sparks']);
    await expect(page.locator('.wm-win')).toHaveCount(0);
    // a dock resizes by its edge, with the keyboard too
    const sep = right(page).locator('.wm-rz');
    const before = Number(await sep.getAttribute('aria-valuenow'));
    await sep.focus();
    await page.keyboard.press('ArrowLeft');
    await expect(sep).toHaveAttribute('aria-valuenow', String(before + 5));
  });

  test('Reset layout brings the page’s default windows back, and they stay after a reload', async ({ page }) => {
    await login(page, '#/overview');
    await bottom(page).getByRole('button', { name: 'Close Chart · power' }).click();
    await addWindow(page, 'inspector', 'Right');
    await expect(shown(right(page)).locator('.wm-insp .node-alias').first()).toHaveText('Desk');
    await expect(tabs(bottom(page))).toHaveText(['Logs · all Sparks']);
    await page.locator('.btn-layout').click();
    await expect(page.locator('.wm-menu [data-saved]')).toContainText('2 windows');
    await page.locator('.wm-mi[data-reset="page"]').click();
    await expect(tabs(bottom(page))).toHaveText(['Logs · all Sparks', 'Chart · power']);
    await expect(page.locator('.wm-dock-right')).toHaveCount(0);
    await page.waitForTimeout(600);
    await page.reload();
    await expect(tabs(bottom(page))).toHaveText(['Logs · all Sparks', 'Chart · power']);
    await expect(page.locator('.wm-dock-right')).toHaveCount(0);
  });

  test('the shots: Overview in the light theme, and a tablet at 768 px', async ({ page }) => {
    test.skip(!SHOTS, 'screenshots only when WM_SHOTS names a folder');
    await login(page, '#/overview');
    await page.locator('.btn-theme').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(shown(bottom(page)).locator('.wm-log-l').first()).toBeVisible();
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${SHOTS}/overview-light.png` });
    await page.locator('.btn-theme').click();
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.reload();
    await expect(page.locator('.shell')).toHaveAttribute('data-w', 'tablet');
    await expect(tabs(bottom(page))).toHaveCount(2);
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${SHOTS}/tablet-768.png` });
  });

  test('a storage that throws: windows still open, move and close', async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', {
        get() {
          throw new DOMException('denied', 'SecurityError');
        },
      });
    });
    await login(page, '#/overview');
    await expect(tabs(bottom(page))).toHaveCount(2);
    await addWindow(page, 'inspector', 'Right');
    await expect(tabs(right(page))).toHaveCount(1);
    await right(page).getByRole('button', { name: /^Close Inspector/ }).click();
    await expect(page.locator('.wm-dock-right')).toHaveCount(0);
  });
});

test.describe('workspace on a phone (VIS-256)', () => {
  test.beforeEach(async ({ request, page }) => {
    test.skip(!IS_MOCK, 'the mock fleet');
    await setMode(request, 'ok');
    await request.post('/__mock/fleet', { data: { reset: true } });
    await page.setViewportSize({ width: 375, height: 812 });
  });

  test('375 px: Chat’s windows are a tray of full-width sheets, one at a time, opened from the bottom bar', async ({ page }) => {
    await login(page, '#/chat/c_sync', '?chat=mock');
    await expect(page.locator('.ch-now')).toBeVisible();
    await expect(page.locator('.wm-dock')).toHaveCount(0);
    await expect(page.locator('.bb-win .bb-cnt')).toHaveText('1');
    await page.locator('.bb-win').click();
    const tray = page.locator('.wm-tray');
    await expect(tray).toBeVisible();
    await expect(tray.locator('.wm-tray-tabs button')).toHaveText(['Terminal']);
    await expect(tray.locator('.ch-term-keys button')).toHaveCount(9);
    // a second window: this chat's files
    await tray.getByRole('button', { name: 'Add a window' }).click();
    await page.locator('.wm-menu .wm-mi[data-kind="files"]').click();
    await expect(tray.locator('.wm-tray-tabs button')).toHaveText(['Terminal', 'Files']);
    await expect(tray.locator('.wm-body:not([hidden])')).toHaveCount(1);
    await expect(tray.locator('.wm-body:not([hidden]) .ch-files')).toBeVisible();
    const box = (await tray.boundingBox())!;
    expect(box.width).toBe(375);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/phone-tray.png` });
    await tray.locator('.wm-tray-tabs button', { hasText: 'Terminal' }).click();
    await expect(tray.locator('.wm-body:not([hidden]) .ch-term')).toBeVisible();
    // the bottom bar's Windows hides the tray; Escape too
    await page.locator('.bb-win').click();
    await expect(tray).toHaveCount(0);
    await page.locator('.bb-win').click();
    await expect(page.locator('.wm-tray')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.wm-tray')).toHaveCount(0);
  });

  test('375 px: a page’s default windows sit in the tray; the sidebars stay drawers', async ({ page }) => {
    await login(page, '#/overview');
    await expect(page.locator('.bb-win .bb-cnt')).toHaveText('2');
    await page.locator('.bb-win').click();
    await expect(page.locator('.wm-tray .wm-tray-tabs button')).toHaveText(['Logs · all Sparks', 'Chart · power']);
    await page.locator('.side-btn[data-side="right"]').click();
    await expect(page.locator('.wm-tray')).toHaveCount(0);
    await expect(page.locator('aside.ctx')).toBeInViewport();
    await expect(page.locator('aside.ctx .ctx-t')).toHaveText('Fleet now');
  });
});
