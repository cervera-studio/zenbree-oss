import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { BillingProfileForm } from '@/app/(app)/billing/profile-form';
import { billingProfileResponseSchema, billingProfileSavedSchema } from './schemas';

vi.mock('next-intl', () => ({
  useLocale: () => 'en',
  useTranslations: () => (key: string) => key,
}));
vi.mock('@/app/(app)/billing/actions', () => ({ billingProfileAction: vi.fn() }));

const profile = {
  customerType: 'business' as const,
  name: 'Synthetic Customer',
  email: 'customer@example.com',
  address: {
    country: 'CH',
    state: '',
    city: 'Zurich',
    postalCode: '8001',
    line1: 'Test address',
    line2: '',
  },
  fiscalCode: '',
  taxIdType: 'ch_vat',
  taxIdValue: 'CHE-123.456.789 MWST',
  recipientCode: '',
  pec: '',
};
const countries = [{ code: 'CH', taxIdTypes: ['ch_vat', 'ch_uid'] }];

describe('customer billing profile', () => {
  it('accepts an empty profile with supported country options', () => {
    const data = { profile: null, syncStatus: 'local', countries };
    expect(billingProfileResponseSchema.parse(data)).toEqual(data);
  });

  it('preserves native tax identifier punctuation and pending provider state', () => {
    const data = { profile, syncStatus: 'pending' };
    expect(billingProfileSavedSchema.parse(data)).toEqual(data);
  });

  it('rejects unknown customer type and provider status', () => {
    expect(billingProfileSavedSchema.safeParse({ profile, syncStatus: 'success' }).success).toBe(
      false,
    );
    expect(
      billingProfileSavedSchema.safeParse({
        profile: { ...profile, customerType: 'company' },
        syncStatus: 'local',
      }).success,
    ).toBe(false);
  });

  it('renders saved native business identifiers without mutating them', () => {
    const html = renderToStaticMarkup(
      createElement(BillingProfileForm, {
        data: { profile, syncStatus: 'synced', countries },
      }),
    );
    expect(html).toContain('CHE-123.456.789 MWST');
    expect(html).toContain('value="ch_vat" selected');
    expect(html).toContain('billing-pec-note');
    expect(html).toContain('pecNote');
  });

  it('discloses unsupported business countries and disables saving', () => {
    const html = renderToStaticMarkup(
      createElement(BillingProfileForm, {
        data: {
          profile: { ...profile, address: { ...profile.address, country: 'AQ' } },
          syncStatus: 'local',
          countries: [{ code: 'AQ', taxIdTypes: [] }],
        },
      }),
    );
    expect(html).toContain('unsupported');
    expect(html).toMatch(/type="submit"[^>]*disabled/);
  });

  it('starts neutral, as a company, with countries in alphabetical order', () => {
    const html = renderToStaticMarkup(
      createElement(BillingProfileForm, {
        data: {
          profile: null,
          syncStatus: 'local',
          countries: ['AD', 'AE', 'AF', 'AT', 'IT'].map((code) => ({ code, taxIdTypes: ['x'] })),
        },
      }),
    );
    expect(html).toContain('role="status"');
    expect(html).toMatch(/role="status"[^>]*>(<svg.*?<\/svg>)?required</);
    expect(html).not.toContain('text-negative');
    expect(html).toMatch(/<option value="business" selected/);
    const order = [...html.matchAll(/<option value="(A[DEFT])"/g)].map((m) => m[1]);
    expect(order).toEqual(['AF', 'AD', 'AT', 'AE']);
  });

  it('labels EU VAT in words and defaults to individual where no tax ID is supported', () => {
    const render = (taxIdTypes: string[]) =>
      renderToStaticMarkup(
        createElement(BillingProfileForm, {
          data: { profile: null, syncStatus: 'local', countries: [{ code: 'IT', taxIdTypes }] },
        }),
      );
    expect(render(['eu_vat'])).toContain('taxIdTypes.eu_vat');
    expect(render([])).toMatch(/<option value="individual" selected/);
  });
});
