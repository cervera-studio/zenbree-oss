import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { MailboxRow } from './mailbox-row';

vi.mock('@/app/(app)/settings/integrations/actions', () => ({
  connectMailboxAction: vi.fn(),
  disconnectMailboxAction: vi.fn(),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

function render(canReadBounces: boolean) {
  return renderToStaticMarkup(
    <TestIntl>
      <MailboxRow
        id="00000000-0000-0000-0000-000000000001"
        provider="google"
        accountLabel="me@acme.test"
        connected
        disabled={false}
        canDisconnect
        canReadBounces={canReadBounces}
      />
    </TestIntl>,
  );
}

describe('mailbox row (#747)', () => {
  it('asks a mailbox connected before the bounce permission to reconnect, and keeps it sending', () => {
    const html = render(false);
    expect(html).toContain('far leggere i rimbalzi');
    expect(html).toContain('Ricollega');
    expect(html).toContain('Scollega');
    expect(html).toContain('Collegata');
  });
});
