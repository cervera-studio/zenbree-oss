// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import type { HubSpotStatus, HubSpotTimeline } from '@/lib/integrations/schemas';
import { fail, succeed } from '@/lib/types';
import { HubSpotTimelineStatus } from './hubspot-timeline';
import { showsTimeline } from './hubspot-timeline-shows';

const { retry, resolve, toast } = vi.hoisted(() => ({
  retry: vi.fn(),
  resolve: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('@/app/(app)/settings/integrations/actions', () => ({
  retryHubSpotTimelineAction: retry,
  resolveHubSpotTimelineAction: resolve,
}));
vi.mock('@/components/ui/sonner', () => ({ toast }));

afterEach(() => vi.clearAllMocks());

const issue = (
  code: string,
  state: HubSpotTimeline['issues'][number]['state'],
  action: HubSpotTimeline['issues'][number]['action'] = null,
  count = 3,
) => ({ code, state, count, action, latestAt: '2026-10-15T09:30:00Z' });

const timeline = (
  issues: HubSpotTimeline['issues'],
  logged = 1234,
  pending = 1,
): HubSpotTimeline => ({
  logged,
  pending,
  issues,
});

const text = () => document.body.textContent ?? '';

const status = (over: Partial<HubSpotStatus>): HubSpotStatus => ({
  connected: false,
  status: 'error',
  configured: true,
  canWrite: true,
  canWriteCompanies: true,
  writeBack: false,
  timeline: timeline([], 0, 0),
  ...over,
});

describe('the timeline block of the card (#932)', () => {
  it('shows with the switch on, and for a grant in error whose rows wait on it', () => {
    expect(showsTimeline(status({ writeBack: true }))).toBe(true);
    expect(showsTimeline(status({}))).toBe(false);
    const waiting = timeline([issue('CRM_TIMELINE.GRANT_REVOKED', 'waiting')], 0, 3);
    expect(showsTimeline(status({ connected: false, timeline: waiting }))).toBe(true);
    expect(showsTimeline(status({ timeline: timeline([], 5, 0) }))).toBe(true);
  });
});

describe('HubSpotTimelineStatus (#932)', () => {
  it('counts what was logged and what waits, with the grouped number format', async () => {
    await render(<HubSpotTimelineStatus timeline={timeline([])} />);
    expect(text()).toContain('1.234 eventi registrati');
    expect(text()).toContain('1 evento in attesa');
  });

  it('says there is nothing yet when nothing was recorded', async () => {
    await render(<HubSpotTimelineStatus timeline={timeline([], 0, 0)} />);
    expect(text()).toContain('Ancora niente');
    expect(text()).not.toContain('eventi registrati');
  });

  it('gives every code its own text, and a code it does not know a plain one', async () => {
    const codes = [
      ['NO_OWNER', 'skipped'],
      ['OUTSIDE_WRITE_BACK', 'skipped'],
      ['DUPLICATE', 'skipped'],
      ['NATIVE_DUPLICATE', 'skipped'],
      ['REFUSED', 'failed'],
      ['CONTACT_REFUSED', 'failed'],
      ['ANSWER_LOST', 'uncertain'],
      ['OWNER_LEFT', 'waiting'],
      ['GRANT_REVOKED', 'waiting'],
      ['HELD', 'waiting'],
      ['VENDOR_DISABLED', 'waiting'],
    ] as const;
    await render(
      <HubSpotTimelineStatus
        timeline={timeline(codes.map(([code, state]) => issue(`CRM_TIMELINE.${code}`, state)))}
      />,
    );
    expect(document.querySelectorAll('li')).toHaveLength(codes.length);
    const lines = [...document.querySelectorAll('li p:first-of-type')].map((p) => p.textContent);
    expect(new Set(lines).size).toBe(codes.length);
    expect(text()).not.toContain('non conosce');

    await render(
      <HubSpotTimelineStatus timeline={timeline([issue('CRM_TIMELINE.NEW', 'waiting')])} />,
    );
    expect(text()).toContain('3 eventi hanno uno stato che questa pagina non conosce');
  });

  it('tells a waiting reason what to do, and offers no button for it', async () => {
    await render(
      <HubSpotTimelineStatus
        timeline={timeline([issue('CRM_TIMELINE.GRANT_REVOKED', 'waiting')])}
      />,
    );
    expect(text()).toContain('Ricollega HubSpot');
    expect(document.querySelector('button')).toBeNull();
  });

  it('retries the failed code and says how many events it took back', async () => {
    retry.mockResolvedValue(succeed(2));
    await render(
      <HubSpotTimelineStatus
        timeline={timeline([issue('CRM_TIMELINE.REFUSED', 'failed', 'retry', 2)])}
      />,
    );
    await click(control(document.body, 'Riprova'));
    expect(retry).toHaveBeenCalledWith('CRM_TIMELINE.REFUSED');
    expect(toast.success).toHaveBeenCalledWith('2 eventi saranno scritti di nuovo.');
  });

  it('says so when a retry found nothing, and says why it failed by the error code', async () => {
    retry.mockResolvedValueOnce(succeed(0));
    await render(
      <HubSpotTimelineStatus
        timeline={timeline([issue('CRM_TIMELINE.REFUSED', 'failed', 'retry')])}
      />,
    );
    await click(control(document.body, 'Riprova'));
    expect(toast.success).toHaveBeenCalledWith('Niente da cambiare.');

    retry.mockResolvedValueOnce(
      fail({ name: 'AppError', code: 'REQUEST.INVALID_INPUT', message: 'x', status: 422 }),
    );
    await click(control(document.body, 'Riprova'));
    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error.mock.calls[0]?.[0]).not.toBe('x');
  });

  it('leaves an unsure note to the user, with the duplicate risk said before the choice', async () => {
    resolve.mockResolvedValue(succeed(1));
    await render(
      <HubSpotTimelineStatus
        timeline={timeline([issue('CRM_TIMELINE.ANSWER_LOST', 'uncertain', 'resolve', 1)])}
      />,
    );
    expect(text()).toContain('può aggiungere una seconda nota');
    expect(text()).toContain('la nota potrebbe mancare');

    await click(control(document.body, 'Registra di nuovo'));
    expect(resolve).toHaveBeenLastCalledWith('CRM_TIMELINE.ANSWER_LOST', 'log_again');
    await click(control(document.body, 'Segna come registrato'));
    expect(resolve).toHaveBeenLastCalledWith('CRM_TIMELINE.ANSWER_LOST', 'logged');
    expect(toast.success).toHaveBeenLastCalledWith('1 evento segnato come registrato.');
  });
});
