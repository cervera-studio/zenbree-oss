import { describe, expect, it } from 'vite-plus/test';
import { toCamelCase, toSnakeCase } from './case-transform';

describe('snakeToCamel (toCamelCase)', () => {
  it('handles nested objects', () => {
    const result = toCamelCase<Record<string, unknown>>({
      user_data: { first_name: 'John', last_name: 'Doe' },
    });
    expect(result).toEqual({ userData: { firstName: 'John', lastName: 'Doe' } });
  });

  it('handles arrays of objects', () => {
    const result = toCamelCase<Record<string, unknown>[]>([
      { credit_id: '1', balance_after: 100 },
      { credit_id: '2', balance_after: 90 },
    ]);
    expect(result).toEqual([
      { creditId: '1', balanceAfter: 100 },
      { creditId: '2', balanceAfter: 90 },
    ]);
  });

  it('handles keys with digits', () => {
    const result = toCamelCase<Record<string, unknown>>({
      user_2fa: true,
      error_code_404: 'not found',
    });
    expect(result).toEqual({ user2fa: true, errorCode404: 'not found' });
  });

  it('preserves null and undefined values', () => {
    const result = toCamelCase<Record<string, unknown>>({
      error_message: null,
      completed_at: undefined,
    });
    expect(result).toEqual({ errorMessage: null, completedAt: undefined });
  });

  it('leaves the camelCase keys the campaign action preview emits untouched', () => {
    // Not every backend response is uniformly snake_case:
    // `/action-preview` hand-builds its envelope and ships these three keys
    // already in camelCase. `snakeToCamel` only rewrites `_x` sequences, so
    // they pass through — but a future "normalise everything" rewrite of this
    // transform would blank the refusal and two counts a confirm dialog is
    // built on, with no type error to catch it.
    const result = toCamelCase<Record<string, unknown>>({
      refusalDetail: 'cannot pause from draft',
      irreversible: false,
      campaign: { targetStatus: 'paused' },
      counts: { inConversation: 4, queued: 26 },
      effects: [{ code: 'queued_leads_held', count: 26 }],
    });

    expect(result).toEqual({
      refusalDetail: 'cannot pause from draft',
      irreversible: false,
      campaign: { targetStatus: 'paused' },
      counts: { inConversation: 4, queued: 26 },
      // The effect CODE is a value, not a key: it must stay snake_case, since
      // it is the i18n message key the frontend looks the copy up by.
      effects: [{ code: 'queued_leads_held', count: 26 }],
    });
  });
});

describe('camelToSnake (toSnakeCase)', () => {
  it('handles consecutive uppercase (HTMLParser → html_parser)', () => {
    const result = toSnakeCase({ HTMLParser: 'v2' });
    expect(result).toEqual({ html_parser: 'v2' });
  });

  it('handles nested objects', () => {
    const result = toSnakeCase({ userData: { firstName: 'John' } });
    expect(result).toEqual({ user_data: { first_name: 'John' } });
  });

  it('handles keys already in snake_case', () => {
    const result = toSnakeCase({ user_id: '123' });
    expect(result).toEqual({ user_id: '123' });
  });

  it('preserves Date objects (does not recurse into them)', () => {
    const date = new Date('2026-01-01');
    const result = toSnakeCase({ createdAt: date }) as Record<string, unknown>;
    expect(result.created_at).toBe(date);
  });
});

describe('round-trip', () => {
  it('snake → camel → snake preserves data', () => {
    const original = { credits_remaining: 50, created_at: '2026-01-01', error_message: null };
    const camel = toCamelCase<Record<string, unknown>>(original);
    const back = toSnakeCase(camel);
    expect(back).toEqual(original);
  });
});
