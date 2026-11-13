// What the three Monitor pages share (VIS-248): the fleet from the shell's store, the scope bar's
// scope and a poll that follows it (every `every` ms while live, once when paused), the Sparks the
// scope covers, and the sidebar content each page registers while it shows.

import { state } from 'lit/decorators.js';
import type { FleetNode } from '../../api/fleet';
import { refreshContext, registerContext, type ContextContent } from '../../lib/context';
import { onFleet, sparksOnly, type FleetState } from '../../lib/fleet-state';
import { poll, type Poller } from '../../lib/poll';
import type { View } from '../../lib/router';
import { currentScope, onScope, resolveScope, type Scope } from '../../lib/scope';
import { LightElement } from '../../ui/base';

export abstract class MonitorPage extends LightElement {
  @state() protected fleetState: FleetState | null = null;
  @state() protected scope: Scope = currentScope();
  protected abstract readonly view: View;
  protected every = 10_000;
  /** a page about machines (Sparks, a Spark's sheet, weights): it never sees a backend by URL */
  protected machines = false;
  private poller: Poller | null = null;
  private unsub: (() => void)[] = [];

  connectedCallback(): void {
    super.connectedCallback();
    this.unsub.push(
      onFleet((f) => {
        const first = !this.fleetState && f;
        this.fleetState = this.machines ? sparksOnly(f) : f;
        if (first) void this.poller?.refresh();
        refreshContext();
      }),
      onScope((s) => {
        this.scope = s;
        this.scopeChanged();
        this.restart();
      }),
      registerContext(this.view, () => this.context()),
    );
    this.restart();
  }
  disconnectedCallback(): void {
    this.unsub.forEach((u) => u());
    this.unsub = [];
    this.poller?.stop();
    this.poller = null;
    super.disconnectedCallback();
  }

  /** the scope as the fleet knows it: a forgotten Spark reads as All */
  protected get resolved(): Scope {
    return this.fleetState ? resolveScope(this.scope, this.fleetState) : this.scope;
  }

  /** the Sparks the scope covers: all, one, or a deployment's members */
  protected get inScope(): FleetNode[] {
    const f = this.fleetState;
    if (!f) return [];
    const s = this.resolved;
    if (s.kind === 'node') return f.nodes.filter((n) => n.id === s.id);
    if (s.kind === 'deployment') {
      const d = f.deployments.find((x) => x.id === s.id);
      return f.nodes.filter((n) => d?.members.includes(n.id));
    }
    return f.nodes;
  }

  protected restart(): void {
    this.poller?.stop();
    this.poller = poll((cur) => this.load(cur), this.every);
    if (this.scope.live) this.poller.start();
    else void this.poller.refresh();
  }
  /** reads again now (an acknowledgement, a rule saved) */
  protected reload(): void {
    void this.poller?.refresh();
  }

  protected scopeChanged(): void {}
  protected abstract load(isCurrent: () => boolean): Promise<void>;
  protected abstract context(): ContextContent;
}
