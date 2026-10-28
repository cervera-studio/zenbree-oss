import { describe, expect, it } from 'vite-plus/test';
import { senderStateSchema, senderTone } from './sender-state';

const sender = {
  id: '3033effc-9b30-4b33-9bbd-6e12e443d38d',
  provider: 'google',
  accountLabel: 'ale@ritmosales.it',
  status: 'connected',
  paused: false,
  state: 'ready',
  reason: null,
  resumesAt: null,
  sentToday: 0,
  dailyCap: 10,
  warmingUp: true,
  domain: 'ritmosales.it',
  dns: null,
  sendWindowStartHour: 9,
  sendWindowEndHour: 18,
  sendWindowDays: [0, 1, 2, 3, 4],
  sendTimezone: 'Europe/Rome',
  held: 0,
};

describe('senderStateSchema', () => {
  it('keeps the conversations held on an account', () => {
    const parsed = senderStateSchema.parse({ senders: [{ ...sender, held: 3 }] });
    expect(parsed.senders[0]?.held).toBe(3);
  });
});

describe('senderTone', () => {
  it('flags a held account, but not one the user paused', () => {
    expect(senderTone('ready', null)).toBe('positive');
    expect(senderTone('waiting', 'capped')).toBe('neutral');
    expect(senderTone('held', 'needs_reconnect')).toBe('warning');
    expect(senderTone('held', 'paused')).toBe('neutral');
  });
});
