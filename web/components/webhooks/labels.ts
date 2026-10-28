import type { useTranslations } from 'next-intl';

export type WebhookT = ReturnType<typeof useTranslations<'webhooks'>>;

// An event the API adds later has no text yet: its own name stands in until a catalog names it.
const EVENT_KEYS = {
  'list.completed': 'listCompleted',
  'contact.replied': 'contactReplied',
  'campaign.finished': 'campaignFinished',
  'sequence.stopped': 'sequenceStopped',
  'webhook.test': 'test',
} as const;

export function eventLabel(t: WebhookT, name: string): string {
  const key = EVENT_KEYS[name as keyof typeof EVENT_KEYS];
  return key ? t(`events.${key}`) : name;
}
