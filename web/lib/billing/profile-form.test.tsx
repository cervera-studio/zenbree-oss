// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { BillingProfileForm } from '@/app/(app)/billing/profile-form';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

vi.mock('next-intl', () => ({
  useLocale: () => 'en',
  useTranslations: () => (key: string) => key,
}));
vi.mock('@/app/(app)/billing/actions', () => ({ billingProfileAction: vi.fn() }));

const countries = [
  { code: 'IT', taxIdTypes: ['eu_vat'] },
  { code: 'AQ', taxIdTypes: [] },
];

async function mount() {
  const host = document.createElement('div');
  const root = createRoot(host);
  await act(async () =>
    root.render(<BillingProfileForm data={{ profile: null, syncStatus: 'local', countries }} />),
  );
  const select = (name: string) => host.querySelector<HTMLSelectElement>(`select[name=${name}]`);
  const pick = async (name: string, value: string) => {
    const el = select(name);
    if (!el) throw new Error(name);
    el.value = value;
    await act(async () => {
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  };
  return { select, pick };
}

describe('billing profile customer type default', () => {
  it('follows the country until the user picks a type', async () => {
    const { select, pick } = await mount();
    expect(select('customerType')?.value).toBe('business');
    await pick('country', 'AQ');
    expect(select('customerType')?.value).toBe('individual');
  });

  it('keeps a type the user picked when the country changes', async () => {
    const { select, pick } = await mount();
    await pick('customerType', 'business');
    await pick('country', 'AQ');
    expect(select('customerType')?.value).toBe('business');
  });
});
