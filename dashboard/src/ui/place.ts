// Where a deployment runs, as one line (VIS-254): its type and its Sparks' chips, a Split's stages
// joined in pipeline order. The Chat tab's model picker and its Context panel show it beside a model
// the fleet serves; Playground's targets name the same.

import { html, nothing, type TemplateResult } from 'lit';
import type { FleetDeployment, FleetNode } from '../api/fleet';
import { nodeChip } from './node-chip';
import { typeBadge } from './scope-bar';

export function placeLine(d: FleetDeployment, nodes: Pick<FleetNode, 'id' | 'alias' | 'colour' | 'status' | 'status_text'>[]): TemplateResult {
  const chips = d.members.map((id, i) => {
    const n = nodes.find((x) => x.id === id);
    return html`${i && d.type === 'split' ? html`<span class="muted" aria-label="then">→</span>` : nothing}${n ? nodeChip(n, { status: false }) : html`<span class="muted small">${id}</span>`}`;
  });
  return html`<span class="place-line" data-place=${d.id}>${typeBadge(d)}${chips}</span>`;
}
