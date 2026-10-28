// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { NewCampaignDialog } from './new-campaign-dialog';

vi.mock('@/app/(app)/campaigns/actions', () => ({ createCampaignAction: vi.fn() }));

const ALPHA_PEOPLE = '1c6a9b52-7d0e-4f3a-9b8c-2e5d4f6a7b81';

describe('NewCampaignDialog', () => {
  it('drops ?list= from the URL when the user closes the dialog it opened', async () => {
    window.history.replaceState(null, '', `/campaigns?list=${ALPHA_PEOPLE}&status=draft`);
    await render(
      <NewCampaignDialog
        lists={[{ id: ALPHA_PEOPLE, name: 'Alpha people' }]}
        defaultListId={ALPHA_PEOPLE}
      />,
    );
    const select = document.querySelector<HTMLSelectElement>('[role=dialog] select');
    expect(select?.value).toBe(ALPHA_PEOPLE);
    await click(control(document.body, 'Chiudi'));
    expect(window.location.pathname + window.location.search).toBe('/campaigns?status=draft');
  });

  it('reopens from its button on the first list, without the link note', async () => {
    const OLDER = '3e8c1d74-5a2b-4c6d-8e9f-0a1b2c3d4e5f';
    window.history.replaceState(null, '', '/campaigns?list=00000000-0000-4000-8000-000000000000');
    await render(
      <NewCampaignDialog
        lists={[
          { id: ALPHA_PEOPLE, name: 'Alpha people' },
          { id: OLDER, name: 'Older people' },
        ]}
        defaultListId="00000000-0000-4000-8000-000000000000"
      />,
    );
    await click(control(document.body, 'Chiudi'));
    const open = [...document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Nuova campagna da una lista',
    );
    await click(open as HTMLElement);
    const select = document.querySelector<HTMLSelectElement>('[role=dialog] select');
    expect(select?.value).toBe(ALPHA_PEOPLE);
    expect(document.body.textContent).not.toContain('La lista del link non è disponibile');
  });
});
