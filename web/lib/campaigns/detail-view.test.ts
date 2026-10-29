import { describe, expect, it } from 'vite-plus/test';
import type { Target } from './api';
import { activity, personState, sendDays, stepsDone } from './detail-view';

const target = (patch: Partial<Target>): Target => ({
  id: 't',
  firstName: 'A',
  lastName: 'B',
  companyName: '',
  status: 'active',
  nextStep: 1,
  pausedUntil: null,
  awaitingApproval: false,
  invalidEmail: false,
  lastSentStep: null,
  lastSentAt: null,
  messageId: null,
  messageKind: null,
  messageLabel: null,
  messageAt: null,
  signalTitle: null,
  hold: null,
  emailSender: null,
  sender: null,
  senderHold: null,
  frozenAt: null,
  missing: [],
  variant: null,
  ...patch,
});

describe('campaign detail view', () => {
  it('names a run of days by its ends and other days one by one', () => {
    expect(sendDays([4, 0, 1, 2, 3], 'it')).toBe('lun–ven');
    expect(sendDays([0, 2], 'en')).toBe('Mon, Wed');
  });

  it('puts an invalid address and a reply before the sequence state', () => {
    expect(personState(target({ invalidEmail: true, status: 'replied' }), 3)).toBe('invalidEmail');
    expect(personState(target({ status: 'replied', awaitingApproval: true }), 3)).toBe('replied');
    expect(personState(target({ awaitingApproval: true }), 3)).toBe('awaiting');
    expect(personState(target({ nextStep: 4 }), 3)).toBe('done');
    expect(personState(target({ nextStep: 2 }), 3)).toBe('waiting');
  });

  it('fills one dot per step behind the person', () => {
    expect(stepsDone(target({ nextStep: 1 }), 3)).toBe(0);
    expect(stepsDone(target({ nextStep: 3 }), 3)).toBe(2);
    expect(stepsDone(target({ nextStep: 13 }), 3)).toBe(3);
  });

  it('shows a person paused by an out-of-office with the day it restarts, linking the auto-reply', () => {
    const paused = target({
      status: 'paused',
      pausedUntil: '2026-10-08T07:00:00Z',
      messageId: 'm1',
      messageKind: 'auto_reply',
      messageAt: '2026-10-01T10:00:00Z',
    });
    expect(personState(paused, 3)).toBe('paused');
    expect(activity(paused)).toEqual({
      kind: 'paused',
      until: '2026-10-08T07:00:00Z',
      messageId: 'm1',
    });
    expect(activity({ ...paused, status: 'active', pausedUntil: null })).toMatchObject({
      kind: 'message',
      messageKind: 'auto_reply',
    });
  });

  it('shows a draft person who waits for a value by the tokens they miss', () => {
    const waiting = target({ status: 'draft', missing: ['recentNews'] });
    expect(personState(waiting, 3)).toBe('missingValue');
    expect(activity(waiting)).toEqual({ kind: 'missing', tokens: ['recentNews'] });
    expect(personState(target({ status: 'draft' }), 3)).toBe('waiting');
  });

  it('falls back from the newest message to the sequence', () => {
    expect(activity(target({ awaitingApproval: true, nextStep: 2 }))).toEqual({
      kind: 'ready',
      step: 2,
    });
    expect(activity(target({ lastSentStep: 1, lastSentAt: 'x' }))).toEqual({
      kind: 'sent',
      step: 1,
      at: 'x',
    });
    expect(activity(target({}))).toEqual({ kind: 'none' });
  });
});
