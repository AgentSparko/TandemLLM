// dist/ hygiene: no mock code, relative asset paths, size budget. Runs against the built page, which
// is a build output and not in git (OPS-246): with no dist/ these checks skip, except where
// DASHBOARD_DIST_REQUIRED=1 (CI, after its build), where a missing dist/ fails.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { BUDGET, distSizes, gzipSize } from '../scripts/gzip-size.mjs';

const DIST = join(__dirname, '..', 'dist');
// the fixed text below, its length and its gzip size (pako 2.1.0, level 6)
const TEXT_BYTES = 84455;
const TEXT_GZIP = 27480;

describe('dist/', () => {
  const present = existsSync(join(DIST, 'index.html'));
  it.runIf(process.env.DASHBOARD_DIST_REQUIRED === '1')('is there where the build is required (CI)', () => {
    expect(present, `${DIST} has no index.html: run the build first`).toBe(true);
  });
  it.skipIf(!present)('contains no generator or mock server code', () => {
    const files = readdirSync(join(DIST, 'assets')).filter((f) => f.endsWith('.js'));
    expect(files.length).toBeGreaterThan(0);
    for (const f of files) {
      const src = readFileSync(join(DIST, 'assets', f), 'utf8');
      for (const marker of ['createMockMiddleware', 'generateYear', 'renderMetrics', 'qse_mock', 'mulberry', 'LogRing', '__mock/mode', 'MockFleet', 'qse_fleet']) {
        expect(src.includes(marker), `${f} contains ${marker}`).toBe(false);
      }
    }
  });
  it.skipIf(!present)('0.8.0: the Chat tab carries a Beta pill in the menu and in the chat header', () => {
    const files = readdirSync(join(DIST, 'assets'));
    const js = files.filter((f) => f.endsWith('.js')).map((f) => readFileSync(join(DIST, 'assets', f), 'utf8'));
    const css = files.filter((f) => f.endsWith('.css')).map((f) => readFileSync(join(DIST, 'assets', f), 'utf8')).join('');
    const pills = js.filter((src) => src.includes('beta-pill') && src.includes('The Chat tab is in beta'));
    expect(pills.length).toBeGreaterThanOrEqual(2);
    expect(css).toMatch(/\.beta-pill\{/);
    // VIS-242: in the menu panel beside its label, on the phone's bar and in its menu sheet
    expect(css).toMatch(/\.bb-i \.beta-pill\{/);
    expect(css).toMatch(/\.more-item \.beta-pill\{/);
  });
  it.skipIf(!present)('index.html uses relative asset paths and no external origin', () => {
    const html = readFileSync(join(DIST, 'index.html'), 'utf8');
    expect(html).toMatch(/src="\.\/assets\//);
    expect(html).toMatch(/href="\.\/assets\//);
    expect(html).not.toMatch(/https?:\/\//);
  });
  // 615 KB since the rebase onto main's 0.15.4: its simple Setup Guide mode and the Log panel's export button on top of TandemApp's pages (SRV-747, 2026-11-12: 613.9 KB, 628,681 bytes); 611 KB since the data plane through the controller: the page asks whether its /v1 reaches the controller, and Usage reads the controller's ledger by default then, one Spark too (SRV-733, 2026-10-28); 610 KB since the fleet in every page: Performance's Now line and strips, History and Usage per Spark, the grouped lanes, Dev's sources, Playground's Compare, Conductor's deployment (VIS-254, 2026-10-28); 590 KB since the Models pages: the deployment cards, Serve a model with its placement step, the weights matrix, each Spark's Setup Guide through the controller (VIS-252, 2026-10-28); 572 KB since the controller's host and the fleet update: App settings' move and take, Update fleet's dialog and progress (OPS-234, 2026-10-28); 563 KB since the Add Spark wizard: Find, Connect, Check, Install, Link, Name and Done in a chunk of its own (VIS-246, 2026-10-28); 547 KB since the Sparks pages: the list, the full topology with each link's test, a Spark's page with its edit and the action dialog (VIS-250, 2026-10-28); 534 KB since the Monitor pages: Overview with the topology, Hardware with every metric group and the host, Events & Alerts with the rule editor (VIS-248, 2026-10-28); 508 KB since the window manager: docks, floats, the tray, the menus, each page's layout and the window kinds' chunks (VIS-256, 2026-10-27); 485 KB since TandemApp's shell (VIS-242, 2026-10-27); 465 KB since the Log panel's export button (SRV-803, 2026-11-10: 464.4 KB, 475,542 bytes: the fetch of the engine's file and its time limit, the save step, the panel's error block and the line after a save); 463 KB since the guide learned a simple mode (#103, 2026-11-03: 462.2 KB, 473,274 bytes, on the pako measure, rebuilt against main: the mode's own chunk (the three-page flow, the folded settings page, the card tiles and their note-line trim) and the Finish gate's plan-check round trip); 458 KB since each running worker's live figures in the Context panel, the Now dock and its card (SRV-579, VIS-219, 2026-10-17); 456 KB since a main agent's workers: the Task card, the worker's context, the Agents switch and their mock (VIS-217, VIS-219, 2026-10-17); 445 KB since the Setup Guide's restart card, finished screen, Retry and diagnostics and its weights list (VIS-209, VIS-211, VIS-213, VIS-215, 2026-10-17); 435 KB since the verify phase's check card and Checked block (SRV-532, 2026-10-17); 434 KB since the Setup Guide's window steppers (VIS-199, 2026-10-16); 433 KB since its parallel-chats stepper (VIS-199, 2026-10-16); 431 KB since the Chat tab's design pass (VIS-185, 2026-10-16); 430 KB since the preview's target, waiting state, page report and new-server watch (SRV-511, 2026-10-16); 428 KB since a live run's reasons on the Now figures (VIS-181) and the Chat tab's panel for a box whose chatd is down (VIS-179, 2026-10-16); 425 KB since the sandbox's reason on a call and the attachment chip's tokens (VIS-167, VIS-169, 2026-10-15); 424 KB since the Setup Guide's job log and change list merged with the media players (2026-10-14); 423 KB since the media players and the Context panel's memory notes (2026-10-14); 420 since 0.9.0; 415 since the community weights section (2026-10-13); 410 since 0.8.0's phone bar, composer and Beta pill; 405 before (scripts/check-size.mjs)
  // the budget itself is set once, in scripts/gzip-size.mjs (OPS-246)
  it.skipIf(!present)(`total gzip size is under the budget (${BUDGET / 1024} KB)`, () => {
    const { total } = distSizes(DIST);
    expect(total).toBeLessThan(BUDGET);
  });
});

// The measurement itself (#130): pako writes the same bytes on every Node, OS and CPU, so these sizes
// are fixed. Node's own zlib gave main's dist/ 470,034 bytes with stock zlib and 471,208 on the
// official Linux builds.
describe('gzipSize', () => {
  const text = new TextEncoder().encode(
    Array.from({ length: 4000 }, (_, i) => `row ${i} ${(i * 2654435761) % 1000003} ${'abcdefgh'.slice(i % 8)}`).join('\n'),
  );
  it('gives a fixed text the same size everywhere', () => {
    expect(text.length).toBe(TEXT_BYTES);
    expect(gzipSize(text)).toBe(TEXT_GZIP);
  });
  it('grows with the input', () => {
    expect(gzipSize(new Uint8Array(0))).toBe(20);
    expect(gzipSize(text.slice(0, text.length / 2))).toBeLessThan(gzipSize(text));
  });
});

