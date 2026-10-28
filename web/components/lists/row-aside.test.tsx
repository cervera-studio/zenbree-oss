// @vitest-environment happy-dom
import { NextIntlClientProvider } from 'next-intl';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import phoneFound from '@/lib/api/fixtures/phone-found.json';
import { fixtureRow, strayStatus } from '@/lib/lists/testing';
import { MESSAGES } from '@/messages';
import { RowAside } from './row-aside';

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

describe('RowAside', () => {
  it('shows the phone but not the raw phone status', async () => {
    const container = document.createElement('div');
    document.body.replaceChildren(container);
    await act(async () => {
      createRoot(container).render(
        <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
          <RowAside
            listId="l1"
            entity="person"
            specs={[
              { key: 'phone', label: 'Telefono', render: 'text' },
              { key: 'phone_status', label: 'Phone status', render: 'text' },
            ]}
            row={fixtureRow(phoneFound.row)}
            onClose={() => {}}
            onToggleExcluded={() => {}}
            onRowChange={() => {}}
            onRate={() => {}}
            phoneCredits={8}
          />
        </NextIntlClientProvider>,
      );
    });
    expect(container.textContent).toContain(phoneFound.row.cells.phone);
    expect(container.textContent).not.toContain('Phone status');
    expect(container.textContent).not.toContain(strayStatus('phone_status'));
  });
});
