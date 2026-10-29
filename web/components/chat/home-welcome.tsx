'use client';

import { Check, Plus } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useLocale, useTranslations } from 'next-intl';
import type { ReactNode, RefObject } from 'react';
import { dismissWelcomeAction } from '@/app/(app)/actions';
import { AppPage } from '@/components/app/page';
import { Button } from '@/components/ui/button';
import type { StepKind, Welcome } from '@/lib/api/home';
import { chatTitle } from '@/lib/chat/chat-view';
import { chatActionFor, DEFAULT_MODE } from '@/lib/chat/home-modes';
import type { SendOptions } from '@/lib/chat/stream-store-types';
import { useAction } from '@/lib/hooks/use-action';
import { employeeRange } from '@/lib/onboarding/criteria';
import { hasStaleSegments } from '@/lib/onboarding/schema';
import { cn } from '@/lib/utils';
import { HomeComposer } from './home-composer';
import { HomePanel, ROW } from './home-data/home-panel';

export type WelcomeView = Welcome & { firstName: string | null };

const STEPS = {
  profile: {
    href: '/settings/client',
    hint: null,
    pending: 'steps.profile.link',
    done: 'steps.profile.link',
  },
  list: { href: '/lists', hint: 'steps.list.hint', pending: null, done: 'steps.list.link' },
  mailbox: {
    href: '/settings/integrations',
    hint: 'steps.mailbox.hint',
    pending: 'steps.mailbox.link',
    done: 'steps.mailbox.manage',
  },
} as const satisfies Record<
  StepKind,
  { href: string; hint: string | null; pending: string | null; done: string }
>;

export function HomeWelcome({
  welcome,
  text,
  onText,
  composer,
  onSend,
  disabled,
  todo,
}: {
  welcome: WelcomeView;
  text: string;
  onText: (text: string) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  onSend: (text: string, opts?: SendOptions) => void;
  disabled: boolean;
  todo?: ReactNode;
}) {
  const t = useTranslations('chat.welcome');
  const tc = useTranslations('chat');
  const tn = useTranslations('nav');
  const ts = useTranslations('onboarding.segments');
  const format = useFormatter();
  const locale = useLocale();
  const { pending, run } = useAction();
  const goal = chatActionFor(DEFAULT_MODE).goal;
  const known = welcome.companyName && welcome.knowsFrom !== 'none';
  const done = welcome.steps.filter((s) => s.done).length;
  const current = welcome.steps.find((s) => !s.done)?.kind;

  const dismiss = () => run(dismissWelcomeAction, { errorToast: t('dismissFailed') });

  return (
    <AppPage
      crumbs={[{ label: tn('overview') }]}
      inProject
      crumbsLabel={tc('breadcrumb')}
      topbarActions={
        <Button
          type="button"
          size="sm"
          disabled={disabled}
          onClick={() => composer.current?.focus()}
        >
          <Plus aria-hidden />
          {tc('newChat')}
        </Button>
      }
    >
      <div className="m-auto flex w-full max-w-190 flex-col gap-7 pb-8">
        <div className="flex flex-col gap-3 desktop:text-center">
          <span className="text-eyebrow text-muted font-mono tabular-nums uppercase">
            {t('eyebrow', { name: welcome.firstName ?? 'none' })}
          </span>
          <h1 className="text-title desktop:text-headline">{t('title')}</h1>
          <p className="text-ui text-muted desktop:text-lead">
            {t('lede', {
              from: known ? welcome.knowsFrom : 'none',
              company: welcome.companyName ?? '',
            })}
          </p>
        </div>

        <HomeComposer
          text={text}
          onText={onText}
          composer={composer}
          onSubmit={(brief) => {
            onText('');
            onSend(brief, { goal });
          }}
          disabled={disabled}
          placeholder={t('placeholder')}
        />

        {welcome.segments.length > 0 && (
          <div className="flex flex-col gap-2.5">
            <span className="text-label text-muted uppercase">{t('segments')}</span>
            {hasStaleSegments(welcome.segments, locale) && (
              <p className="text-small text-muted">{t('otherLanguage')}</p>
            )}
            <div className="grid grid-cols-3 gap-2.5 phone:grid-cols-1">
              {welcome.segments.map((s, i) => (
                <button
                  key={i}
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    onSend(s.message, { goal, segment: s.key, title: chatTitle(s.name) })
                  }
                  className="border-line bg-ground hover:border-accent-ink hover:bg-raised focus-visible:ring-accent/50 flex flex-col items-start gap-1 rounded-lg border p-3.5 text-left transition-colors outline-none focus-visible:ring-3 disabled:opacity-45"
                >
                  <span className="text-ui-strong">{s.name}</span>
                  <span className="text-small text-muted">
                    {[
                      s.titles.join(', '),
                      s.employeeRange &&
                        employeeRange(s.employeeRange, ts('solo'), (n) =>
                          format.number(n, 'credits'),
                        ),
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        <HomePanel
          title={t('steps.title')}
          meta={t('steps.progress', { done, total: welcome.steps.length })}
          action={
            <Button type="button" variant="ghost" size="xs" disabled={pending} onClick={dismiss}>
              {t('dismiss')}
            </Button>
          }
        >
          <ol className="flex flex-col">
            {welcome.steps.map((s) => {
              const step = STEPS[s.kind];
              const linkKey = s.done ? step.done : step.pending;
              return (
                <li key={s.kind} className={cn(ROW, 'py-2.5')}>
                  <span
                    aria-hidden="true"
                    className={cn(
                      'flex size-5 shrink-0 items-center justify-center rounded-full border-2',
                      s.done
                        ? 'bg-accent text-on-accent border-accent'
                        : s.kind === current
                          ? 'border-accent'
                          : 'border-line-strong',
                    )}
                  >
                    {s.done && <Check className="size-3" strokeWidth={3} />}
                  </span>
                  <span className="flex grow flex-col">
                    <span
                      className={cn(s.done ? 'text-ui text-muted line-through' : 'text-ui-strong')}
                    >
                      {t(`steps.${s.kind}.label`)}
                    </span>
                    {!s.done && step.hint && (
                      <span className="text-small text-muted">{t(step.hint)}</span>
                    )}
                  </span>
                  {linkKey ? (
                    <Link
                      href={step.href}
                      className="text-accent-ink text-small font-bold hover:underline"
                    >
                      {t(linkKey)}
                    </Link>
                  ) : (
                    <Button
                      type="button"
                      size="sm"
                      disabled={disabled}
                      onClick={() => composer.current?.focus()}
                    >
                      {t('steps.list.action')}
                    </Button>
                  )}
                </li>
              );
            })}
          </ol>
        </HomePanel>

        <div className="desktop:hidden">{todo}</div>
      </div>
    </AppPage>
  );
}
