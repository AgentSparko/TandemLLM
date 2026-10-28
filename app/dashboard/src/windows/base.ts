// What every window body shares (VIS-256): the window it draws (its id and its ref, what it shows),
// whether it sits in a phone's tray, and two ways to talk to the window manager: a change to its ref,
// which the layout saves, and a window it opens (the inspector's Terminal and Logs). A body's live
// data follows `Feed`: it runs while the body is on the page and the tab is visible, never after.

import { property } from 'lit/decorators.js';
import type { LayoutWindow, WindowKind } from '../api/fleet';
import type { Ref, Where } from '../lib/wm';
import { poll, type Poller, type PollFn } from '../lib/poll';
import { LightElement } from '../ui/base';

export class WindowBody extends LightElement {
  @property({ attribute: false }) win: LayoutWindow | null = null;
  @property({ type: Boolean }) sheet = false;

  protected get ref(): Ref {
    return this.win?.ref ?? {};
  }

  /** what the window shows changed: the layout keeps it */
  protected setRef(patch: Ref): void {
    if (!this.win) return;
    this.dispatchEvent(new CustomEvent('wm-ref', { detail: { id: this.win.id, patch }, bubbles: true }));
  }

  protected openWindow(kind: WindowKind, ref: Ref, where: Where = 'auto'): void {
    this.dispatchEvent(new CustomEvent('wm-open', { detail: { kind, ref, where }, bubbles: true }));
  }
}

/** A body's live data: polled every `ms` while it is connected (and the tab visible: lib/poll.ts),
 *  stopped when it leaves, asked again at once when what it shows changes. */
export class Feed {
  private p: Poller | null = null;
  constructor(
    private readonly fn: PollFn,
    private readonly ms: number,
  ) {}
  get running(): boolean {
    return !!this.p?.running;
  }
  start(): void {
    if (this.p?.running) return;
    this.p = poll(this.fn, this.ms);
    this.p.start();
  }
  stop(): void {
    this.p?.stop();
    this.p = null;
  }
  refresh(): void {
    void this.p?.refresh();
  }
}
