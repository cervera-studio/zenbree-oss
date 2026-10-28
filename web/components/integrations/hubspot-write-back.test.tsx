import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { HubSpotWriteBack } from './hubspot-write-back';

vi.mock('@/app/(app)/settings/integrations/actions', () => ({
  connectHubSpotAction: vi.fn(),
  setHubSpotWriteBackAction: vi.fn(),
}));

function render(canWrite: boolean, enabled: boolean, canWriteCompanies = canWrite) {
  return renderToStaticMarkup(
    <TestIntl>
      <HubSpotWriteBack
        canWrite={canWrite}
        canWriteCompanies={canWriteCompanies}
        enabled={enabled}
      />
    </TestIntl>,
  );
}

describe('HubSpot write-back switch (#526)', () => {
  it('is a switch, off until the user turns it on, once the grant can write', () => {
    const off = render(true, false);
    expect(off).toContain('role="switch"');
    expect(off).toContain('aria-checked="false"');
    expect(off).toContain('Scrivi su HubSpot');
    expect(off).toContain('Non sovrascrive i dati che ci sono già');
    expect(render(true, true)).toContain('aria-checked="true"');
  });

  it('offers a reconnection, not a switch, to a grant without the write permission', () => {
    const html = render(false, false);
    expect(html).not.toContain('role="switch"');
    expect(html).toContain('Ricollega');
    expect(html).toContain('serve un permesso in più');
    expect(html).toContain('spunta i permessi facoltativi'); // unticked at HubSpot, the grant comes back the same
    expect(html).not.toContain('<label'); // the button keeps its own name, «Ricollega»
  });

  it('keeps the switch with the contacts permission alone, and says the companies are left out', () => {
    const partial = render(true, false, false);
    expect(partial).toContain('role="switch"');
    expect(partial).toContain('Manca il permesso sulle aziende');
    expect(partial).toContain('Ricollega');
    expect(render(true, false, true)).not.toContain('Manca il permesso sulle aziende');
  });
});
