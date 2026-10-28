export type ReviewKey = 'approve' | 'reject' | 'edit';

const KEYS: Record<string, ReviewKey> = { a: 'approve', s: 'reject', e: 'edit' };
const TYPING = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

type Press = Pick<
  KeyboardEvent,
  | 'key'
  | 'repeat'
  | 'isComposing'
  | 'defaultPrevented'
  | 'metaKey'
  | 'ctrlKey'
  | 'altKey'
  | 'shiftKey'
>;
type Focus = {
  tagName?: string;
  isContentEditable?: boolean;
  closest?: (selector: string) => unknown;
} | null;
// Radix menus and selects take letters for typeahead without preventDefault.
const OVERLAY =
  '[role="menu"], [role="menubar"], [role="listbox"], [role="dialog"], [role="alertdialog"]';

/**
 * The approval screen's letter shortcut for a key press, or null. Approve sends email and has no
 * undo, so a press counts only when nothing else could have meant it: no action running and no
 * editor open, a single press (not a held key), no modifier (⌘K and the browser's own shortcuts
 * stay theirs), and no focus in a field or in an open menu, list or dialog.
 */
export function reviewShortcut(e: Press, focus: Focus, blocked: boolean): ReviewKey | null {
  if (blocked || e.repeat || e.isComposing || e.defaultPrevented) return null;
  if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return null;
  if (focus && (focus.isContentEditable || TYPING.has(focus.tagName ?? ''))) return null;
  if (focus?.closest?.(OVERLAY)) return null;
  return KEYS[e.key.toLowerCase()] ?? null;
}
