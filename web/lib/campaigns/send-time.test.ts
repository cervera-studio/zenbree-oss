import { describe, expect, it } from 'vite-plus/test';
import { sendWhen } from './send-time';

const ROME = 'Europe/Rome';
// Monday 5 October 2026, 10:00 in Rome.
const NOW = new Date('2026-10-05T08:00:00Z');

describe('sendWhen', () => {
  it('says today for a time later the same local day', () => {
    expect(sendWhen(new Date('2026-10-05T12:30:00Z'), NOW, ROME, 'it')).toMatchObject({
      day: 'today',
      time: '14:30',
    });
  });

  it('says tomorrow across local midnight, not UTC midnight', () => {
    const late = new Date('2026-10-05T21:30:00Z'); // 23:30 Rome, still Monday
    expect(sendWhen(late, new Date('2026-10-05T10:00:00Z'), ROME, 'it').day).toBe('today');
    expect(sendWhen(new Date('2026-10-06T07:00:00Z'), NOW, ROME, 'it')).toMatchObject({
      day: 'tomorrow',
      time: '9:00',
    });
  });

  it('names the weekday from two days out', () => {
    expect(sendWhen(new Date('2026-10-12T07:00:00Z'), NOW, ROME, 'it')).toEqual({
      day: 'weekday',
      weekday: 'lunedì',
      time: '9:00',
    });
  });
});
