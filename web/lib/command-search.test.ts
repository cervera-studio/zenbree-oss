import { describe, expect, it } from 'vite-plus/test';
import { filterItems, isSearchShortcut, type SearchItem } from './command-search';

const item = (label: string): SearchItem => ({ id: label, label, href: '/', group: 'lists' });
const key = { key: 'k', metaKey: false, ctrlKey: false, altKey: false };

describe('isSearchShortcut', () => {
  it('takes only the platform chord, in either case', () => {
    expect(isSearchShortcut({ ...key, metaKey: true }, true)).toBe(true);
    expect(isSearchShortcut({ ...key, ctrlKey: true, key: 'K' }, false)).toBe(true);
  });
  it('leaves Ctrl+K to the Mac and Cmd+K to other platforms', () => {
    expect(isSearchShortcut({ ...key, ctrlKey: true }, true)).toBe(false);
    expect(isSearchShortcut({ ...key, metaKey: true }, false)).toBe(false);
  });
  it('ignores a bare K, other keys and Alt chords', () => {
    expect(isSearchShortcut(key, true)).toBe(false);
    expect(isSearchShortcut({ ...key, metaKey: true, key: 'j' }, true)).toBe(false);
    expect(isSearchShortcut({ ...key, ctrlKey: true, altKey: true }, false)).toBe(false);
  });
});

describe('filterItems', () => {
  const items = [item('Italian SaaS CTOs'), item('Team and roles'), item('Berlin agencies')];
  it('matches every word anywhere in the label, ignoring case', () => {
    expect(filterItems(items, 'cto ital', 'en').map((i) => i.label)).toEqual(['Italian SaaS CTOs']);
  });
  it('lists a few of each group for an empty query', () => {
    const many = [
      ...Array.from({ length: 6 }, (_, n) => item(`list ${n}`)),
      { ...item('Profile'), group: 'settings' as const },
    ];
    const found = filterItems(many, '  ', 'en');
    expect(found.filter((i) => i.group === 'lists')).toHaveLength(4);
    expect(found.some((i) => i.group === 'settings')).toBe(true);
  });
  it('returns nothing when no label matches', () => {
    expect(filterItems(items, 'zzz', 'en')).toEqual([]);
  });
});
