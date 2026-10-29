import { describe, expect, it } from 'vite-plus/test';
import {
  autoTopupSavedSchema,
  autoTopupSchema,
  formatEurCents,
  formatInvoiceAmount,
  formatPricePer1000,
  invoicesSchema,
  signedAmount,
  transactionsSchema,
} from './schemas';

const TX = {
  id: 'a',
  type: 'purchase',
  status: 'committed',
  amount: 1000,
  balanceAfter: 1000,
  description: null,
  toolName: null,
  referenceType: null,
  createdAt: '2026-09-30T10:00:00Z',
  sessionId: null,
  listId: null,
};

describe('billing schemas', () => {
  it('shows removed plan credits as an expiry, not as usage', () => {
    const r = transactionsSchema.parse({
      items: [
        { ...TX, type: 'deduction', referenceType: 'plan_expiry' },
        { ...TX, type: 'deduction' },
      ],
      nextOffset: null,
    });
    expect(r.items.map((tx) => tx.type)).toEqual(['expiry', 'deduction']);
  });

  it('parses the auto top-up of a GET, which has no setup url, and of a PUT, which has', () => {
    const state = {
      enabled: false,
      packId: null,
      threshold: 100,
      thresholds: [100],
      lastError: null,
    };
    expect(autoTopupSchema.safeParse(state).success).toBe(true);
    expect(autoTopupSavedSchema.safeParse(state).success).toBe(false);
    expect(autoTopupSavedSchema.safeParse({ ...state, setupUrl: null }).success).toBe(true);
  });
});

describe('signedAmount', () => {
  it('debits are negative', () => expect(signedAmount({ type: 'deduction', amount: 5 })).toBe(-5));
  it('an expiry removes credits', () =>
    expect(signedAmount({ type: 'expiry', amount: 5 })).toBe(-5));
  it('credits are positive', () => expect(signedAmount({ type: 'purchase', amount: 5 })).toBe(5));
});

describe('formatEurCents', () => {
  it('treats the input as cents', () => expect(formatEurCents(1900, 'en')).toBe('€19'));
  it('keeps the cents when there are any', () => expect(formatEurCents(1950, 'en')).toBe('€19.50'));
});

describe('formatPricePer1000', () => {
  it('prices a pack per 1,000 credits, always with two decimals', () => {
    expect(formatPricePer1000(1900, 1000, 'en')).toBe('€19.00');
    expect(formatPricePer1000(4900, 3000, 'en')).toBe('€16.33');
    expect(formatPricePer1000(14900, 10000, 'en')).toBe('€14.90');
  });

  it('rounds to cents first, so a float just under a whole euro matches the exact one', () => {
    expect(formatPricePer1000(1699.9999, 1000, 'en')).toBe('€17.00');
    expect(formatPricePer1000(1700, 1000, 'en')).toBe('€17.00');
  });

  it('has no rate for a pack without credits', () => {
    expect(formatPricePer1000(1900, 0, 'en')).toBeNull();
    expect(formatPricePer1000(1900, -5, 'en')).toBeNull();
  });
});

describe('Stripe documents', () => {
  it('formats charge amounts in currency minor units', () => {
    expect(formatInvoiceAmount(1220, 'eur', 'en')).toBe('€12.20');
    expect(formatInvoiceAmount(1200, 'jpy', 'en')).toBe('¥1,200');
    expect(formatInvoiceAmount(120000, 'isk', 'en')).toBe('ISK\u00a01,200');
    expect(formatInvoiceAmount(120000, 'ugx', 'en')).toBe('UGX\u00a01,200');
  });
  it('preserves unavailable tax amounts and draft documents', () => {
    const page = invoicesSchema.parse({
      items: [
        {
          id: 'in_1',
          number: null,
          createdAt: '2026-10-06T00:00:00+00:00',
          status: 'draft',
          currency: 'eur',
          netAmount: null,
          taxAmount: null,
          totalAmount: 1220,
          pdfUrl: null,
          hostedUrl: null,
        },
      ],
      nextCursor: null,
      hasCustomer: true,
      portalAvailable: true,
    });
    expect(page.items[0]?.taxAmount).toBeNull();
    expect(page.items[0]?.pdfUrl).toBeNull();
  });
});
