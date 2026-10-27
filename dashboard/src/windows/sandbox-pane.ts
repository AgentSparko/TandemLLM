// Which pane a sandbox window shows for a chat (VIS-256, windows/sandbox.ts), kept apart from the
// element so it can be tested.

import type { WindowKind } from '../api/fleet';
import { latestPage } from '../chat/file-panes';
import type { Conversation, PaneRef } from '../chat/types';
import type { Ref } from '../lib/wm';

const str = (v: unknown) => (typeof v === 'string' ? v : '');

/** The pane a window shows for a chat, or why it shows none: what it was opened on (a file, a run's
 *  changes), else the kind's own (the shell, the tree, the plan, the page the chat made last). */
export function paneFor(kind: WindowKind, ref: Ref, conv: Pick<Conversation, 'messages'> | null): Omit<PaneRef, 'id'> | { none: string } {
  switch (kind) {
    case 'terminal':
      return { kind: 'terminal', ref: 'shell', title: 'Terminal' };
    case 'files':
      return { kind: 'files', ref: str(ref.path), title: 'Files' };
    case 'editor':
      return str(ref.path) ? { kind: 'editor', ref: str(ref.path), title: str(ref.path).split('/').pop() } : { none: 'Open a file in a Files window and press Edit.' };
    case 'diff':
      return { kind: 'diff', ref: str(ref.ref) || 'diff:', title: str(ref.title) || 'Diff' };
    case 'plan':
      return { kind: 'plan', ref: 'md:plan', title: 'Plan' };
    case 'context':
      return { kind: 'context', ref: '', title: 'Context' };
    case 'preview': {
      if (str(ref.ref)) return { kind: 'preview', ref: str(ref.ref), title: str(ref.title) || 'Preview' };
      const page = conv ? latestPage(conv.messages) : null;
      return page ? { kind: 'preview', ref: `file:${page}`, title: page.split('/').pop() } : { none: 'This chat has made no page yet. A page it writes, or a server it starts, shows here.' };
    }
    case 'markdown':
    case 'image':
    case 'pdf':
    case 'video':
    case 'audio':
    case 'binary':
      return str(ref.ref) ? { kind, ref: str(ref.ref), title: str(ref.title) || undefined } : { none: 'Open a file from a Files window, or from a tool card in the chat.' };
    default:
      return { none: 'This window shows a chat’s sandbox.' };
  }
}

