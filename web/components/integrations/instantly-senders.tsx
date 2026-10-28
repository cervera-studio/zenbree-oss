import Link from 'next/link';
import { getTranslations } from 'next-intl/server';
import { SettingsSection } from '@/components/settings/settings-section';
import { Tag, type Tone } from '@/components/ui/badge';
import { getInstantlyAccounts } from '@/lib/integrations/instantly';
import type { InstantlyAccount } from '@/lib/integrations/schemas';

const STATUS_TONE: Record<InstantlyAccount['status'], Tone> = {
  active: 'positive',
  paused: 'neutral',
  maintenance: 'neutral',
  unknown: 'neutral',
  connection_error: 'negative',
  soft_bounce_error: 'negative',
  sending_error: 'negative',
};

const WARMUP_TONE: Record<InstantlyAccount['warmup'], Tone> = {
  active: 'positive',
  paused: 'neutral',
  unknown: 'neutral',
  banned: 'negative',
  spam_folder_unknown: 'negative',
  permanent_suspension: 'negative',
};

const normalized = (email: string) => email.trim().toLowerCase();

/** The workspace's Instantly mailboxes: what Instantly reports, with no control, since Zenbree changes none of it. */
export async function InstantlyManagedSenders() {
  const [t, result] = await Promise.all([
    getTranslations('senders.instantly'),
    getInstantlyAccounts(),
  ]);
  const state = result.success ? result.data.state : 'unavailable';
  if (state === 'not_connected') return null;
  if (state === 'no_permission') {
    return (
      <p className="text-small text-muted max-w-225">
        {t.rich('noPermission', {
          link: (chunks) => (
            <Link href="/settings/integrations" className="text-accent-ink underline">
              {chunks}
            </Link>
          ),
        })}
      </p>
    );
  }
  if (!result.success || state === 'unavailable') {
    return <p className="text-small text-muted">{t('unavailable')}</p>;
  }
  const { accounts } = result.data;
  return (
    <SettingsSection title={t('title')} description={t('description')}>
      {accounts.length === 0 ? (
        <p className="text-small text-muted">{t('empty')}</p>
      ) : (
        <ul aria-label={t('list')} className="flex flex-col">
          {accounts.map((a) => (
            <li
              key={a.email}
              className="border-hair flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b py-2.5 first:pt-0 last:border-0 last:pb-0"
            >
              <span className="text-ui-strong min-w-0 grow break-all">{a.email}</span>
              <Tag tone={STATUS_TONE[a.status]}>{t(`status.${a.status}`)}</Tag>
              <Tag tone={WARMUP_TONE[a.warmup]}>{t(`warmup.${a.warmup}`)}</Tag>
              <span className="text-small text-soft">
                {a.dailyLimit == null ? t('noLimit') : t('dailyLimit', { limit: a.dailyLimit })}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SettingsSection>
  );
}

/** On a native mailbox card: Instantly also sends from this address, and Zenbree does not count those sends. */
export async function InstantlyOverlap({ address }: { address: string }) {
  const [t, result] = await Promise.all([
    getTranslations('senders.instantly'),
    getInstantlyAccounts(),
  ]);
  if (!result.success || result.data.state !== 'ok') return null;
  const wanted = normalized(address);
  if (!result.data.accounts.some((a) => normalized(a.email) === wanted)) return null;
  return (
    <div className="flex flex-col items-start gap-1.5">
      <Tag tone="warning">{t('overlapTag')}</Tag>
      <span className="text-small text-soft">{t('overlap')}</span>
    </div>
  );
}
