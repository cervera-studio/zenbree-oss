import type { useTranslations } from 'next-intl';
import type { sendWhen } from '@/lib/campaigns/send-time';

export function whenText(
  t: ReturnType<typeof useTranslations<'campaigns.approvals'>>,
  { day, weekday, time }: ReturnType<typeof sendWhen>,
  linkedin: boolean,
) {
  switch (day) {
    case 'today':
      return linkedin ? t('when.todayWork') : t('when.today', { time });
    case 'tomorrow':
      return linkedin ? t('when.tomorrowWork') : t('when.tomorrow', { time });
    default:
      return linkedin ? t('when.weekdayWork', { weekday }) : t('when.weekday', { weekday, time });
  }
}
