'use client';

import { Search } from 'lucide-react';
import Link from 'next/link';
import { useLocale, useTranslations } from 'next-intl';
import {
  Fragment,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { z } from 'zod';
import { tabsFor } from '@/components/settings/settings-tabs';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { api } from '@/lib/api/browser';
import {
  filterItems,
  isSearchShortcut,
  type SearchGroup,
  type SearchItem,
} from '@/lib/command-search';
import { useNav } from '@/lib/home/nav';
import { listsApi } from '@/lib/lists/api';
import { getCurrentProjectId } from '@/lib/projects';
import { cn } from '@/lib/utils';
import { rowFocus } from './nav';

const named = z.looseObject({ id: z.string(), name: z.string() });
const campaignsSchema = z.looseObject({ campaigns: z.array(named) });

const GROUPS: readonly SearchGroup[] = ['lists', 'chats', 'campaigns', 'settings'];

const noop = () => () => {};
const isMac = () => /mac|iphone|ipad/i.test(navigator.platform);

/** What the active project holds, fetched when the search opens; a failed fetch leaves its group
 *  empty rather than blocking the rest. */
async function loadRemote(projectId: string | null): Promise<SearchItem[]> {
  const [lists, campaigns] = await Promise.all([
    projectId ? listsApi.all(projectId) : null,
    api('/v1/campaigns', { schema: campaignsSchema }),
  ]);
  return [
    ...(lists?.success ? lists.data.lists : []).map(
      (l): SearchItem => ({ id: l.id, label: l.name, href: `/lists/${l.id}`, group: 'lists' }),
    ),
    ...(campaigns.success ? campaigns.data.campaigns : []).map(
      (c): SearchItem => ({
        id: c.id,
        label: c.name,
        href: `/campaigns/${c.id}`,
        group: 'campaigns',
      }),
    ),
  ];
}

/** The sidebar's search box and the ⌘K palette it opens. Only the desktop rail (`phone` false)
 *  listens for the shortcut: the drawer's copy is mounted beside it, and two listeners would open
 *  two dialogs. */
export function CommandSearch({ orgAdmin, phone = false }: { orgAdmin: boolean; phone?: boolean }) {
  const t = useTranslations('nav');
  const ts = useTranslations('settings.tabs');
  const locale = useLocale();
  const nav = useNav();
  const mac = useSyncExternalStore(noop, isMac, () => false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [remote, setRemote] = useState<SearchItem[]>([]);

  const base = useId();
  const request = useRef(0);

  // Cleared first, and only the newest request of the current project lands: the switcher can
  // change the project while a fetch is in flight, and a reopen can overtake an older one.
  const show = () => {
    const projectId = getCurrentProjectId();
    setQuery('');
    setActive(0);
    setRemote([]);
    setOpen(true);
    const mine = ++request.current;
    void loadRemote(projectId).then((items) => {
      if (mine === request.current && getCurrentProjectId() === projectId) setRemote(items);
    });
  };
  const onShortcut = useEffectEvent((e: KeyboardEvent) => {
    if (!isSearchShortcut(e, isMac())) return;
    e.preventDefault();
    show();
  });

  useEffect(() => {
    if (phone) return;
    window.addEventListener('keydown', onShortcut);
    return () => window.removeEventListener('keydown', onShortcut);
  }, [phone]);

  const items: SearchItem[] = [
    ...remote.filter((i) => i.group === 'lists'),
    ...(nav?.sessions ?? []).map(
      (s): SearchItem => ({
        id: s.id,
        label: s.title ?? t('searches'),
        href: `/?s=${s.id}`,
        group: 'chats',
      }),
    ),
    ...remote.filter((i) => i.group === 'campaigns'),
    ...tabsFor(orgAdmin).map(
      ({ href, key }): SearchItem => ({ id: href, label: ts(key), href, group: 'settings' }),
    ),
  ];
  const found = filterItems(items, query, locale);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setActive((a) => Math.max(0, Math.min(found.length - 1, a + step)));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      // A click, so the drawer's «following a link closes it» handler sees it too.
      document.querySelector<HTMLElement>('[data-search-active]')?.click();
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={show}
        className={cn(
          'text-ui text-muted border-line-strong bg-ground flex shrink-0 items-center gap-2 rounded-md border px-2 transition-colors hover:bg-hair',
          rowFocus,
          phone ? 'min-h-12' : 'h-control',
        )}
      >
        <Search className="size-4 shrink-0" aria-hidden />
        <span className="flex-1 text-left">{t('search')}</span>
        {!phone && (
          <kbd className="text-data-sm border-line rounded-sm border px-1 font-mono">
            {mac ? '⌘K' : 'Ctrl K'}
          </kbd>
        )}
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          showCloseButton={false}
          aria-describedby={undefined}
          className="top-1/4 translate-y-0 gap-2 p-3"
        >
          <DialogTitle className="sr-only">{t('search')}</DialogTitle>
          <Input
            autoFocus
            role="combobox"
            aria-expanded
            aria-autocomplete="list"
            aria-controls={`${base}-list`}
            aria-activedescendant={found.length ? `${base}-${active}` : undefined}
            value={query}
            placeholder={t('searchPlaceholder')}
            aria-label={t('search')}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
          />
          {found.length === 0 && <p className="text-muted px-2 py-3 text-sm">{t('searchEmpty')}</p>}
          <div
            id={`${base}-list`}
            role="listbox"
            aria-label={t('search')}
            className="flex max-h-80 flex-col gap-1 overflow-y-auto"
          >
            {GROUPS.map((group) => {
              const rows = found.filter((i) => i.group === group);
              if (rows.length === 0) return null;
              return (
                <Fragment key={group}>
                  <div
                    id={`${base}-g-${group}`}
                    className="text-label text-muted px-2 pt-2 pb-1 uppercase"
                  >
                    {t(`search_${group}`)}
                  </div>
                  {rows.map((i) => (
                    <Link
                      key={`${group}-${i.id}`}
                      id={`${base}-${found.indexOf(i)}`}
                      role="option"
                      tabIndex={-1}
                      aria-describedby={`${base}-g-${group}`}
                      aria-selected={found[active] === i}
                      href={i.href}
                      data-search-active={found[active] === i ? '' : undefined}
                      onClick={() => setOpen(false)}
                      onMouseMove={() => setActive(found.indexOf(i))}
                      className={cn(
                        'text-ui flex min-h-control items-center rounded-md px-2 py-1',
                        rowFocus,
                        found[active] === i ? 'bg-hair text-ui-strong' : 'text-ink',
                      )}
                    >
                      <span className="truncate">{i.label}</span>
                    </Link>
                  ))}
                </Fragment>
              );
            })}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
