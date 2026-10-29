import { z } from 'zod';
import type { Tone } from '@/components/ui/badge';
import { senderTone } from '@/lib/integrations/sender-state';

const SENDER_HOLDS = [
  'send_uncertain',
  'mailbox_unknown',
  'disconnected',
  'needs_reconnect',
  'unlinked',
  'not_in_pool',
  'paused',
  'throttled',
] as const;
type SenderHold = (typeof SENDER_HOLDS)[number];
export const senderHoldSchema = z.enum(SENDER_HOLDS);

export const SENDER_HOLD_TONES = {
  send_uncertain: 'warning',
  mailbox_unknown: 'warning',
  disconnected: 'warning',
  needs_reconnect: 'warning',
  unlinked: 'warning',
  not_in_pool: 'warning',
  paused: senderTone('held', 'paused'),
  throttled: senderTone('held', 'throttled'),
} as const satisfies Record<SenderHold, Tone>;

const REPLY_HOLDS = ['mailbox_unknown', 'disconnected', 'needs_reconnect', 'unlinked'] as const;
export const replyHoldSchema = z.enum(REPLY_HOLDS);
