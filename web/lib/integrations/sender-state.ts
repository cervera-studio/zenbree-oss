import { z } from 'zod';
import { sendersSchema } from './schemas';

/** Held: a person or an outside event must clear it. Waiting: the clock clears it (see `sender_state.py`). */
const SENDER_HOLDS = [
  'disconnected',
  'needs_reconnect',
  'paused',
  'channel_disabled',
  'vendor_disabled',
  'throttled',
] as const;
const SENDER_WAITS = ['capped', 'outside_mailbox_window', 'paced'] as const;

export const SENDER_REASONS = [...SENDER_HOLDS, ...SENDER_WAITS] as const;
export type SenderReason = (typeof SENDER_REASONS)[number];

export const SENDER_STATES = ['ready', 'waiting', 'held'] as const;
export type SenderState = (typeof SENDER_STATES)[number];

export const senderStateSchema = z.object({
  senders: z.array(
    sendersSchema.shape.senders.element.extend({
      id: z.uuid(),
      state: z.enum(SENDER_STATES),
      reason: z.enum(SENDER_REASONS).nullish(),
      resumesAt: z.iso.datetime({ offset: true }).nullish(),
      held: z.number(),
    }),
  ),
});

const TONES = { ready: 'positive', waiting: 'neutral', held: 'warning' } as const;

/** A held account is the one that needs attention; a paused one is the user's own choice. */
export function senderTone(state: SenderState, reason: SenderReason | null | undefined) {
  return reason === 'paused' ? 'neutral' : TONES[state];
}
