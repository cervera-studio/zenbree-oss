'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { type List, listsApi } from '@/lib/lists/api';
import { missingResource, otherDestinations } from '@/lib/not-found';
import { useCurrentProjectId } from '@/lib/projects';

const LINK = 'text-ui-strong text-accent-ink hover:underline';

// Client-side: a not-found page gets no props, and only the URL says what was missing.
// Shared by the root 404 (any unknown address) and the one inside the app shell.
export function NotFoundView() {
  const t = useTranslations('notFound');
  const { kind, href } = missingResource(usePathname());
  const projectId = useCurrentProjectId();
  const [recent, setRecent] = useState<List[]>([]);

  // The card keeps the fixed destinations unless the request succeeds with a list. Signed out it
  // answers 401: the zb-project cookie outlives sign-out, so `projectId` alone proves nothing.
  // ponytail: GET /v1/lists has no limit and sends up to 500 lists (with columns) to show two;
  // add a `limit` to the API if this page gets heavy.
  useEffect(() => {
    if (!projectId) return;
    let stale = false;
    void listsApi.all(projectId).then((res) => {
      // The API sends the newest first.
      if (!stale && res.success) setRecent(res.data.lists.slice(0, 2));
    });
    return () => {
      stale = true;
    };
  }, [projectId]);

  return (
    <main className="flex min-h-full flex-1 items-center justify-center p-8 phone:px-4">
      <div className="flex max-w-140 flex-col gap-4">
        <p className="text-eyebrow text-muted font-mono uppercase">{t('eyebrow')}</p>
        <h1 className="text-headline phone:text-4xl">{t(`title.${kind}`)}</h1>
        <p className="text-body text-muted">{t(`body.${kind}`)}</p>
        <div className="flex flex-wrap gap-2">
          <Button asChild className="font-semibold">
            <Link href={href}>{t(`cta.${kind}`)}</Link>
          </Button>
          {href !== '/' && (
            <Button asChild variant="outline" className="font-semibold">
              <Link href="/">{t('cta.chat')}</Link>
            </Button>
          )}
        </div>
        <nav
          aria-label={t('lookingFor')}
          className="bg-raised flex flex-col gap-1.5 rounded-lg border p-4"
        >
          <p className="text-small text-muted font-bold">{t('lookingFor')}</p>
          {recent.length > 0
            ? recent.map((list) => (
                <Link key={list.id} href={`/lists/${list.id}`} className={LINK}>
                  {t('recentList', {
                    name: list.name,
                    entity: list.entity,
                    count: list.delivered_count,
                  })}
                </Link>
              ))
            : otherDestinations(href).map((destination) => (
                <Link key={destination.kind} href={destination.href} className={LINK}>
                  {t(`destination.${destination.kind}`)}
                </Link>
              ))}
        </nav>
      </div>
    </main>
  );
}
