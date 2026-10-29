export type SearchItem = { id: string; label: string; href: string; group: SearchGroup };
export type SearchGroup = 'lists' | 'chats' | 'campaigns' | 'settings';

/** The platform's own chord: ⌘K on macOS, Ctrl+K elsewhere. Ctrl+K stays the native kill-line on a Mac. */
export function isSearchShortcut(
  e: { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean },
  mac: boolean,
) {
  const mod = mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
  return e.key.toLowerCase() === 'k' && !e.altKey && mod;
}

/** The items whose label holds every word of the query, in order, at most `perGroup` of each group:
 *  a few of each for an empty query, so no group hides the others. */
export function filterItems(items: readonly SearchItem[], query: string, locale: string) {
  const words = query.toLocaleLowerCase(locale).split(/\s+/).filter(Boolean);
  const perGroup = words.length ? 8 : 4;
  const taken = new Map<string, number>();
  return items.filter((i) => {
    const label = i.label.toLocaleLowerCase(locale);
    if (!words.every((w) => label.includes(w))) return false;
    const n = (taken.get(i.group) ?? 0) + 1;
    taken.set(i.group, n);
    return n <= perGroup;
  });
}
