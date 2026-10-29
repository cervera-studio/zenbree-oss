'use client';

import { Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { type FormEvent, type ReactNode, useState } from 'react';
import { saveMemoryAction } from '@/app/(app)/settings/memory/actions';
import { SettingsCard } from '@/components/settings/settings-card';
import { Button } from '@/components/ui/button';
import { Panel } from '@/components/ui/panel';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useErrorText } from '@/i18n/error-text';
import { useAction } from '@/lib/hooks/use-action';
import type { Memory } from '@/lib/memory';
import { cn } from '@/lib/utils';

export function MemoryEditor({ initial, header }: { initial: Memory; header: ReactNode }) {
  const t = useTranslations('memory');
  const [saved, setSaved] = useState(initial);
  const [body, setBody] = useState(initial.body);
  const [locked, setLocked] = useState(initial.locked);
  const errorText = useErrorText();
  const { pending, error, run } = useAction();
  const dirty = body !== saved.body || locked !== saved.locked;

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    run(() => saveMemoryAction(body, locked), {
      onSuccess: (data) => {
        setSaved(data);
        setBody(data.body);
        setLocked(data.locked);
      },
    });
  }

  return (
    <form className="flex flex-col items-start gap-4 desktop:flex-row" onSubmit={save}>
      <div className="flex w-full min-w-0 flex-col gap-4 desktop:flex-1">
        {header}
        <Panel
          title={t('bodyLabel')}
          meta={t('format')}
          action={
            <span className="text-data-sm text-muted font-mono tabular-nums">
              {t('chars', { count: body.length, max: saved.maxChars })}
            </span>
          }
          flush
        >
          <Textarea
            aria-label={t('bodyLabel')}
            className="text-data text-ink min-h-80 rounded-t-none rounded-b-lg border-0 bg-transparent px-3 py-3 font-mono shadow-none md:text-data desktop:min-h-130"
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder={t('placeholder')}
            maxLength={saved.maxChars}
          />
        </Panel>
        {saved.truncated && (
          <p role="status" className="text-warning text-ui">
            {t('truncated', { max: saved.promptMaxChars })}
          </p>
        )}
        <div className="flex items-center gap-3">
          <Button type="submit" className="phone:h-11" disabled={pending || !dirty}>
            {pending && <Loader2 className="animate-spin" />}
            {t('save')}
          </Button>
          {error ? (
            <p role="alert" className="text-negative text-small">
              {errorText(error, t('saveError'))}
            </p>
          ) : (
            <p role="status" className={cn('text-small', dirty ? 'text-warning' : 'text-positive')}>
              {dirty ? t('unsaved') : t('saved')}
            </p>
          )}
        </div>
      </div>
      <aside className="flex w-full flex-col gap-4 desktop:w-80 desktop:shrink-0">
        <SettingsCard className="gap-2.5 p-4">
          <div className="flex items-center gap-2">
            <label htmlFor="memory-lock" className="text-ui-strong grow">
              {t('lock')}
            </label>
            <Switch
              id="memory-lock"
              checked={locked}
              onCheckedChange={setLocked}
              aria-describedby="memory-lock-help"
            />
          </div>
          <p id="memory-lock-help" className="text-soft text-small">
            {t('lockHelp')}
          </p>
        </SettingsCard>
        <SettingsCard className="bg-raised gap-2.5 p-4">
          <h3 className="text-ui-strong">{t('sources.title')}</h3>
          <p className="text-soft text-small">
            {t.rich('sources.profile', { b: (chunks) => <b className="text-ink">{chunks}</b> })}
          </p>
          <p className="text-soft text-small">
            {t.rich('sources.preferences', { b: (chunks) => <b className="text-ink">{chunks}</b> })}
          </p>
          <Link href="/onboarding?redo=1" className="text-ui-strong text-accent-ink">
            {t('sources.redo')}
          </Link>
        </SettingsCard>
      </aside>
    </form>
  );
}
