import { useFormatter, useTranslations } from 'next-intl';
import { useBrowserTimeZone } from '@/i18n/browser-time-zone';
import { dayRelation } from '@/lib/chat/home-overview';

export function useWhen() {
  const t = useTranslations('chat.home.data.lists');
  const format = useFormatter();
  const timeZone = useBrowserTimeZone();
  const now = new Date();
  return (iso: string, { clock = false }: { clock?: boolean } = {}): string | null => {
    if (!timeZone) return null;
    const at = new Date(iso);
    const time = format.dateTime(at, { hour: '2-digit', minute: '2-digit', timeZone });
    const day = dayRelation(at, now, timeZone);
    if (day === 'today') return clock ? time : t('today', { time });
    if (day === 'yesterday') return t('yesterday', { time });
    return format.dateTime(at, { day: 'numeric', month: 'short', timeZone });
  };
}
