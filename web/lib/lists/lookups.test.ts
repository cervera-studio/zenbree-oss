import { describe, expect, it } from 'vite-plus/test';
import { emailView, isVerified, lookupsSchema, phoneView, rowPhoneStatusSchema } from './lookups';

describe('emailView', () => {
  it.each([
    [{ state: 'found', verdict: 'verified' }, 'emailChip.verified', null],
    [{ state: 'found', verdict: 'catch_all' }, 'emailChip.catch_all', null],
    [{ state: 'found', verdict: 'mx_ok' }, 'emailChip.unverified', null],
    [{ state: 'found', verdict: 'unknown' }, 'emailChip.unverified', null],
    [{ state: 'found', verdict: 'invalid' }, 'emailChip.invalid', null],
    [{ state: 'not_found' }, 'emailChip.missing', 'emailNotFound'],
    [{ state: 'not_searched' }, undefined, 'emailNotSearched'],
    [{ state: 'failed' }, undefined, 'emailFailed'],
    [{ state: 'refunded' }, undefined, 'emailRefunded'],
    [{ state: 'searching' }, undefined, 'emailSearching'],
  ] as const)('%j shows chip %s and text %s', (lookup, chip, text) => {
    const view = emailView(lookup);
    expect(view.chip?.labelKey).toBe(chip);
    expect(view.textKey).toBe(text);
  });

  it('pulses an email only while a running turn searches it', () => {
    expect(emailView({ state: 'searching' }).pulse).toBe(true);
    for (const state of ['not_searched', 'failed', 'not_found', 'refunded'] as const) {
      expect(emailView({ state }).pulse).toBe(false);
    }
    expect(emailView({ state: 'found', verdict: 'unknown' }).pulse).toBe(false);
  });

  it('offers to ask the chat again only for a refunded email (#531)', () => {
    expect(emailView({ state: 'refunded' }).askAgain).toBe(true);
    expect(emailView({ state: 'not_found' }).askAgain).toBe(false);
  });

  it('counts only a found address the verifier accepted as verified', () => {
    expect(isVerified({ state: 'found', verdict: 'verified' })).toBe(true);
    expect(isVerified({ state: 'found', verdict: 'catch_all' })).toBe(false);
    expect(isVerified({ state: 'not_found' })).toBe(false);
  });
});

describe('phoneView', () => {
  it('offers the search on a row never searched, failed or refunded, and not while one runs or after a miss', () => {
    expect(phoneView({ state: 'found' })).toBeNull();
    expect(phoneView({ state: 'not_searched' })).toMatchObject({ textKey: null, search: true });
    expect(phoneView({ state: 'failed' })).toMatchObject({ textKey: 'phoneFailed', search: true });
    expect(phoneView({ state: 'refunded' })).toMatchObject({
      textKey: 'phoneRefunded',
      search: true,
    });
    expect(phoneView({ state: 'searching' })).toMatchObject({
      textKey: 'phonePending',
      search: false,
    });
    expect(phoneView({ state: 'not_found' })).toEqual({
      textKey: 'phoneNotFound',
      search: false,
      statusKey: 'phoneNone',
      pulse: false,
    });
  });
});

describe('phoneView pulse', () => {
  it('pulses a phone only while a running turn searches it', () => {
    expect(phoneView({ state: 'searching' })?.pulse).toBe(true);
    for (const state of ['not_searched', 'failed', 'not_found', 'refunded'] as const) {
      expect(phoneView({ state })?.pulse).toBe(false);
    }
    expect(phoneView({ state: 'found' })).toBeNull();
  });
});

describe('lookupsSchema', () => {
  const never = { state: 'not_searched' };

  it('refuses a value it has no row for, so a new API value fails loudly, not as a blank cell', () => {
    expect(lookupsSchema.safeParse({ email: { state: 'bounced' }, phone: never }).success).toBe(
      false,
    );
    expect(
      lookupsSchema.safeParse({ email: { state: 'found', verdict: 'risky' }, phone: never })
        .success,
    ).toBe(false);
    expect(lookupsSchema.safeParse({ email: never, phone: { state: 'toString' } }).success).toBe(
      false,
    );
    expect(rowPhoneStatusSchema.safeParse('already').success).toBe(true);
    expect(rowPhoneStatusSchema.safeParse('constructor').success).toBe(false);
  });
});
