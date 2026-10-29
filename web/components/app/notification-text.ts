import { useTranslations } from 'next-intl';
import type { AppNotification } from '@/lib/notifications/group';

export function useText() {
  const t = useTranslations('notifications');
  const tl = useTranslations('inbox.label');
  const title = (n: AppNotification) => {
    const { name, label, people, companies, count, campaign, mailbox, provider, plan, credits } =
      n.data;
    switch (n.kind) {
      case 'reply_urgent':
        return t('kind.reply_urgent.title', { name: name || t('someone'), label: label ?? '' });
      case 'reply':
        return t('kind.reply.title', { name: name || t('someone') });
      case 'approval_needed':
        return t('kind.approval_needed.title', { count: count ?? 0 });
      case 'search_done':
        if (people && companies) return t('kind.search_done.titleBoth', { people, companies });
        if (people) return t('kind.search_done.titlePeople', { people });
        if (companies) return t('kind.search_done.titleCompanies', { companies });
        return t('kind.search_done.title');
      case 'linkedin_accepted':
        return t('kind.linkedin_accepted.title', { name: name || t('someone') });
      case 'bounces_high':
        return t('kind.bounces_high.title', { campaign: campaign ?? '' });
      case 'mailbox_error':
        return mailbox
          ? t('kind.mailbox_error.titleNamed', { mailbox, provider: provider ?? '' })
          : t('kind.mailbox_error.title', { provider: provider ?? '' });
      case 'plan_renewed':
        return t('kind.plan_renewed.title', { plan: plan ?? '', credits: credits ?? 0 });
      default:
        return t(`kind.${n.kind}.title`);
    }
  };
  // The subtitle's parts, joined by « · »; none falls back to the kind's own line.
  const details = (n: AppNotification) => {
    const {
      label,
      unverified,
      chat,
      done,
      units,
      reason,
      count,
      campaign,
      credits,
      organization,
      invalid,
      attempted,
      paused,
      provider,
      expired,
    } = n.data;
    switch (n.kind) {
      case 'reply_urgent':
      case 'reply':
        return [
          label && tl(label),
          unverified && t('kind.reply.unverified'),
          campaign && t('kind.reply.campaign', { campaign }),
        ];
      case 'search_done':
        return [chat];
      case 'search_failed':
        return [chat, units && t('kind.search_failed.progress', { done: done ?? 0, units })];
      case 'agent_question':
        return [chat, reason && t(`kind.agent_question.${reason}`)];
      case 'approval_needed':
        return [campaign, t('kind.approval_needed.body', { count: count ?? 0 })];
      case 'low_credits':
        return [organization, t('kind.low_credits.body', { credits: credits ?? 0 })];
      case 'payment_failed':
        return [organization, t('kind.payment_failed.body')];
      case 'linkedin_accepted':
        return [campaign && t('kind.linkedin_accepted.body', { campaign })];
      case 'bounces_high':
        return [
          t(paused ? 'kind.bounces_high.paused' : 'kind.bounces_high.body', {
            invalid: invalid ?? 0,
            attempted: attempted ?? 0,
          }),
        ];
      case 'mailbox_error':
        return [t('kind.mailbox_error.body', { provider: provider ?? '' })];
      case 'plan_renewed':
        return [
          t('kind.plan_renewed.body', { expired: expired ?? 0, organization: organization ?? '' }),
        ];
      default:
        return [];
    }
  };
  // Only a kind whose body takes no argument can stand alone; the others show nothing without a detail.
  const plainBody = (n: AppNotification) => {
    switch (n.kind) {
      case 'reply_urgent':
        return t('kind.reply_urgent.body');
      case 'reply':
        return t('kind.reply.body');
      case 'search_done':
        return t('kind.search_done.body');
      case 'search_failed':
        return t('kind.search_failed.body');
      case 'agent_question':
        return t('kind.agent_question.body');
      case 'payment_failed':
        return t('kind.payment_failed.body');
      default:
        return '';
    }
  };
  return (n: AppNotification) => ({
    title: title(n),
    body: details(n).filter(Boolean).join(' · ') || plainBody(n),
  });
}
