// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, render } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import type { Target } from '@/lib/campaigns/api';
import type { Step } from '@/lib/campaigns/steps';
import { CampaignPeople } from './campaign-people';

const regenerate = vi.fn();
vi.mock('@/app/(app)/campaigns/actions', () => ({
  regenerateValuesAction: (...args: unknown[]) => regenerate(...args),
}));
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const person = (firstName: string, patch: Partial<Target>): Target => ({
  id: firstName,
  firstName,
  lastName: 'Z',
  companyName: '',
  status: 'active',
  nextStep: 1,
  pausedUntil: null,
  awaitingApproval: false,
  invalidEmail: false,
  lastSentStep: null,
  lastSentAt: null,
  messageId: null,
  messageKind: null,
  messageLabel: null,
  messageAt: null,
  signalTitle: null,
  hold: null,
  emailSender: 'native',
  sender: null,
  senderHold: null,
  frozenAt: null,
  missing: [],
  variant: null,
  ...patch,
});

const live = { id: 'c1', status: 'active' } as const;

const steps: Step[] = [
  { step: 1, channel: 'email', subject: 'S', body: 'B', sendAfterDays: 0, threaded: false },
];

const desktopRowOf = (container: HTMLElement, name: string) =>
  [...container.querySelectorAll('tbody tr')].find((r) => r.textContent?.includes(name));

describe('CampaignPeople hold badges', () => {
  it('says why a person is held and who sends their email', async () => {
    const container = await render(
      <CampaignPeople
        campaign={live}
        steps={steps}
        targets={[
          person('Ann', {}),
          person('Bob', { hold: 'stop_pending', emailSender: 'instantly' }),
          person('Cat', { hold: 'stop_failed', emailSender: 'instantly' }),
          person('Dan', { hold: 'stopped', emailSender: null }),
          person('Eve', { hold: 'held_by_instantly', emailSender: 'instantly' }),
          person('Fay', { hold: null, emailSender: 'instantly' }),
        ]}
      />,
    );
    expect(desktopRowOf(container, 'Ann')?.textContent).not.toContain('Instantly');
    expect(desktopRowOf(container, 'Bob')?.textContent).toContain('Stop in corso in Instantly');
    expect(desktopRowOf(container, 'Dan')?.textContent).toContain('Stop confermato in Instantly');
    expect(desktopRowOf(container, 'Eve')?.textContent).toContain('Email da Instantly');
    expect(desktopRowOf(container, 'Fay')?.textContent).toContain('Email da Instantly');
    const failed = desktopRowOf(container, 'Cat')?.querySelector('a');
    expect(failed?.textContent).toBe('Stop non riuscito in Instantly');
    expect(failed?.getAttribute('href')).toBe('/settings/integrations');
    const bob = desktopRowOf(container, 'Bob');
    expect(bob?.textContent).toContain('finché Instantly non conferma lo stop');
    expect(bob?.querySelector('[title]')).toBeNull();
  });
});

describe('CampaignPeople held conversations', () => {
  it('says which account holds a conversation and why, after what holds the person', async () => {
    const container = await render(
      <CampaignPeople
        campaign={live}
        steps={steps}
        targets={[
          person('Ann', { sender: 'ale@ritmosales.it', senderHold: 'paused' }),
          person('Bob', { senderHold: 'mailbox_unknown' }),
          person('Cat', {
            hold: 'stop_pending',
            emailSender: 'instantly',
            sender: 'ale@ritmosales.it',
            senderHold: 'paused',
          }),
          person('Dan', { sender: 'ale@ritmosales.it' }),
        ]}
      />,
    );
    const ann = desktopRowOf(container, 'Ann')?.textContent;
    expect(ann).toContain('Trattenuta');
    expect(ann).toContain('La conversazione è su ale@ritmosales.it, che è in pausa.');
    expect(desktopRowOf(container, 'Bob')?.textContent).toContain(
      'La conversazione è partita da una casella che Zenbree non conosce più.',
    );
    const cat = desktopRowOf(container, 'Cat')?.textContent;
    expect(cat).toContain('Stop in corso in Instantly');
    expect(cat).not.toContain('Trattenuta');
    expect(desktopRowOf(container, 'Dan')?.textContent).not.toContain('Trattenuta');
  });

  it('gives a held account the tone the senders page gives it', async () => {
    const container = await render(
      <CampaignPeople
        campaign={live}
        steps={steps}
        targets={[
          person('Ann', { sender: 'a@x.it', senderHold: 'paused' }),
          person('Bob', { sender: 'a@x.it', senderHold: 'throttled' }),
        ]}
      />,
    );
    const badge = (name: string) =>
      [...(desktopRowOf(container, name)?.querySelectorAll('span') ?? [])].find(
        (e) => e.textContent === 'Trattenuta',
      );
    expect(badge('Ann')?.className).toContain('text-soft');
    expect(badge('Bob')?.className).toContain('text-warning');
  });
});

describe('CampaignPeople frozen values (#933)', () => {
  it('shows who waits for a value and when the others got theirs', async () => {
    const container = await render(
      <CampaignPeople
        campaign={live}
        steps={steps}
        targets={[
          person('Ann', { frozenAt: '2026-10-19T08:42:00Z' }),
          person('Bob', {
            status: 'draft',
            frozenAt: '2026-10-19T08:42:00Z',
            missing: ['recentNews'],
          }),
        ]}
      />,
    );
    expect(desktopRowOf(container, 'Ann')?.textContent).toContain('Valori del 19 ott, 10:42');
    const bob = desktopRowOf(container, 'Bob')?.textContent;
    expect(bob).toContain('Manca un valore');
    expect(bob).toContain(
      'Nessun valore per {{recentNews}}. Tiene aperta la campagna finché non ha un valore e rigeneri i valori.',
    );
    expect(bob).not.toContain('Valori del');
  });

  it('regenerates after a confirm and says what changed', async () => {
    regenerate.mockResolvedValue({
      success: true,
      data: { active: 1, renewed: 2, waiting: 0, kept: 1 },
    });
    const container = await render(<CampaignPeople campaign={live} steps={steps} targets={[]} />);
    const button = [...container.querySelectorAll('button')].find(
      (b) => b.textContent === 'Rigenera i valori',
    );
    if (!button) throw new Error('no regenerate button');
    await click(button);
    const confirm = [...document.querySelectorAll('[role="alertdialog"] button')].find(
      (b) => b.textContent === 'Rigenera i valori',
    );
    if (!(confirm instanceof HTMLElement)) throw new Error('no confirm button');
    expect(document.body.textContent).toContain(
      'I messaggi da approvare e quelli approvati ma non ancora partiti vengono riscritti con i nuovi valori. Le tue modifiche e approvazioni di quei messaggi si perdono.',
    );
    await click(confirm);
    expect(regenerate).toHaveBeenCalledWith('c1');
    expect(vi.mocked(toast.success)).toHaveBeenCalledWith(
      '2 persone aggiornate · 1 partita · 0 ancora in attesa · 1 tiene i valori di prima',
    );
  });

  it('offers no regenerate on a draft campaign', async () => {
    const container = await render(
      <CampaignPeople campaign={{ id: 'c1', status: 'draft' }} steps={steps} targets={[]} />,
    );
    expect(container.textContent).not.toContain('Rigenera i valori');
  });
});

describe('CampaignPeople variant tags (#934)', () => {
  it('tags each person with their copy, and nobody outside a test', async () => {
    const c = await render(
      <CampaignPeople
        campaign={live}
        steps={steps}
        targets={[
          person('Ann', { variant: 'a' }),
          person('Bob', { variant: 'b' }),
          person('Cat', { variant: null }),
        ]}
      />,
    );
    expect(desktopRowOf(c, 'Ann')?.textContent).toContain('Variante A');
    expect(desktopRowOf(c, 'Bob')?.textContent).toContain('Variante B');
    expect(desktopRowOf(c, 'Cat')?.textContent).not.toContain('Variante');
  });
});
