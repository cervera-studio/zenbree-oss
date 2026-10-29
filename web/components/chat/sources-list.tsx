'use client';

import { useTranslations } from 'next-intl';
import { dedupeSources, faviconUrl } from '@/lib/chat/sources';
import type { Source } from '@/lib/chat/types';

/** ponytail: a collapsed list, not the earlier implementation's 480-line side panel; grow it when citations land. */
export function SourcesList({ sources }: { sources: Source[] }) {
  const t = useTranslations('chat');
  const rows = dedupeSources(sources);
  return (
    <details className="text-sm">
      <summary className="text-muted cursor-pointer text-xs">
        {t('sources', { count: rows.length })}
      </summary>
      <ul className="mt-2 flex flex-col gap-1.5">
        {rows.map((s) => (
          <li key={s.url} className="flex items-start gap-2">
            {/* biome-ignore lint/performance/noImgElement: external favicons, no optimisation needed */}
            <img src={faviconUrl(s)} alt="" className="mt-0.5 size-4 shrink-0" />
            <a href={s.url} target="_blank" rel="noreferrer" className="min-w-0 hover:underline">
              <span className="block truncate">{s.title}</span>
              <span className="text-muted block truncate text-xs">{s.domain}</span>
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}
