import { describe, expect, it } from 'vite-plus/test';
import { linkedInConnectSchema, toOutcome } from './schemas';

describe('linkedin connect', () => {
  const outcome = (body: unknown) => toOutcome(linkedInConnectSchema.parse(body));

  it('hosted link', () =>
    expect(outcome({ url: 'https://account.unipile.com/x' })).toEqual({
      kind: 'redirect',
      url: 'https://account.unipile.com/x',
    }));
  it('checkpoint', () =>
    expect(outcome({ connected: false, checkpoint: '2FA', accountId: 'acc' })).toEqual({
      kind: 'checkpoint',
      accountId: 'acc',
    }));
  it('connected', () =>
    expect(outcome({ connected: true, accountId: 'acc' })).toEqual({ kind: 'connected' }));
});
