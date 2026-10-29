import type { Metadata } from 'next';
import { getFormatter, getTranslations } from 'next-intl/server';
import { z } from 'zod';
import { httpClient } from '@/lib/http-client';
import { SenderHeader, UnsubscribeCard } from './unsubscribe-card';

// The token is the only credential: keep it out of search indexes and Referer headers. The title is
// not "Zenbree": the recipient knows the sender, not us.
export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations('public.unsubscribe');
  return {
    title: t('pageTitle'),
    robots: { index: false, follow: false },
    referrer: 'no-referrer',
  };
}

const stateSchema = z.looseObject({
  senderName: z.string(),
  senderEmail: z.string().nullish(),
  companyName: z.string().nullish(),
  email: z.string(),
  lastSubject: z.string().nullish(),
  lastSentAt: z.string().nullish(),
  status: z.enum(['subscribed', 'unsubscribed']),
  unsubscribedAt: z.string().nullish(),
  undoAvailable: z.boolean(),
});

// On the unsubscribe host every other path is a 404 (proxy.ts), so the privacy notice links the app.
const PRIVACY_URL = `${process.env.BETTER_AUTH_URL ?? ''}/privacy`;

// Reads only: a link scanner opening this page never unsubscribes anybody (#172).
export default async function UnsubscribePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const [t, format, state] = await Promise.all([
    getTranslations('public.unsubscribe'),
    getFormatter(),
    /^[\w-]{1,600}$/.test(token)
      ? httpClient.get(`/v1/unsubscribe/${token}/state`, { schema: stateSchema })
      : null,
  ]);
  const day = (iso: string | null | undefined) =>
    iso
      ? format.dateTime(new Date(iso), { day: 'numeric', month: 'long', timeZone: 'Europe/Rome' })
      : null;
  const s = state?.success ? state.data : null;
  // The company the person knows, else the name in From.
  const sender = s?.companyName || s?.senderName || t('fallbackSender');
  return (
    <>
      <section className="rounded-2xl border bg-ground p-7 phone:p-6" aria-live="polite">
        {s ? (
          <>
            <SenderHeader
              title={sender}
              detail={[s.companyName && s.senderName, s.senderEmail].filter(Boolean).join(' · ')}
            />
            <UnsubscribeCard
              token={token}
              sender={sender}
              email={s.email}
              status={s.status}
              undoAvailable={s.undoAvailable}
              lastEmail={
                s.lastSubject && s.lastSentAt
                  ? t('lastEmail', {
                      name: s.senderName || sender,
                      date: day(s.lastSentAt) ?? '',
                      subject: s.lastSubject,
                    })
                  : null
              }
              unsubscribedOn={day(s.unsubscribedAt)}
            />
          </>
        ) : (
          <div className="space-y-4">
            <h1 className="text-2xl font-bold tracking-tight">{t('invalidTitle')}</h1>
            <p className="leading-relaxed text-muted">
              {t.rich('invalidBody', {
                strong: (chunks) => <strong className="text-ink">{chunks}</strong>,
              })}
            </p>
          </div>
        )}
      </section>
      <p className="mt-6 text-center text-xs leading-relaxed text-muted">
        {s ? t('controller', { sender }) : t('controllerUnknown')}{' '}
        <a href={PRIVACY_URL} className="underline hover:text-ink">
          {t('privacy')}
        </a>
      </p>
    </>
  );
}
