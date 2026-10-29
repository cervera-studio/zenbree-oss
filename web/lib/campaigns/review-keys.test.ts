import { describe, expect, it } from 'vite-plus/test';
import { reviewShortcut } from './review-keys';

const press = (key: string, extra: Partial<KeyboardEvent> = {}) => ({
  key,
  repeat: false,
  isComposing: false,
  defaultPrevented: false,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  ...extra,
});
const button = { tagName: 'BUTTON', isContentEditable: false };

describe('reviewShortcut', () => {
  it('maps A, S and E, in either case', () => {
    expect(reviewShortcut(press('a'), button, false)).toBe('approve');
    expect(reviewShortcut(press('s'), null, false)).toBe('reject');
    expect(reviewShortcut(press('e'), button, false)).toBe('edit');
    // Caps Lock on.
    expect(reviewShortcut(press('A'), button, false)).toBe('approve');
    expect(reviewShortcut(press('k'), button, false)).toBeNull();
    expect(reviewShortcut(press('Enter'), button, false)).toBeNull();
  });

  it('ignores a press while an action runs or the editor is open', () => {
    expect(reviewShortcut(press('a'), button, true)).toBeNull();
  });

  it('ignores a held key, a composition and a press another handler took', () => {
    expect(reviewShortcut(press('a', { repeat: true }), button, false)).toBeNull();
    expect(reviewShortcut(press('a', { isComposing: true }), button, false)).toBeNull();
    expect(reviewShortcut(press('a', { defaultPrevented: true }), button, false)).toBeNull();
  });

  it('leaves every modifier combination to its owner', () => {
    expect(reviewShortcut(press('k', { metaKey: true }), button, false)).toBeNull();
    expect(reviewShortcut(press('a', { metaKey: true }), button, false)).toBeNull();
    expect(reviewShortcut(press('a', { ctrlKey: true }), button, false)).toBeNull();
    expect(reviewShortcut(press('a', { altKey: true }), button, false)).toBeNull();
    expect(reviewShortcut(press('A', { shiftKey: true }), button, false)).toBeNull();
  });

  it('ignores typing in a field', () => {
    expect(reviewShortcut(press('a'), { tagName: 'INPUT' }, false)).toBeNull();
    expect(reviewShortcut(press('s'), { tagName: 'TEXTAREA' }, false)).toBeNull();
    expect(reviewShortcut(press('e'), { tagName: 'SELECT' }, false)).toBeNull();
    expect(
      reviewShortcut(press('a'), { tagName: 'DIV', isContentEditable: true }, false),
    ).toBeNull();
  });

  it('ignores a letter typed in an open menu, list or dialog', () => {
    const inside = (role: string) => ({
      tagName: 'DIV',
      closest: (selector: string) => (selector.includes(`[role="${role}"]`) ? {} : null),
    });
    expect(reviewShortcut(press('a'), inside('menu'), false)).toBeNull();
    expect(reviewShortcut(press('a'), inside('listbox'), false)).toBeNull();
    expect(reviewShortcut(press('s'), inside('dialog'), false)).toBeNull();
    expect(reviewShortcut(press('a'), { ...button, closest: () => null }, false)).toBe('approve');
  });
});
