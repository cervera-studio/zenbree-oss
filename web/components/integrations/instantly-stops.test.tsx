// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import type { InstantlyStop, InstantlyStops as Stops } from '@/lib/integrations/instantly-stops';
import { InstantlyStops } from './instantly-stops';

const move = vi.fn();
const refresh = vi.fn();
vi.mock('@/app/(app)/settings/integrations/actions', () => ({
  moveInstantlyStopAction: (...args: unknown[]) => move(...args),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }));
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
beforeEach(() => {
  move.mockReset().mockResolvedValue({ success: true, data: {} });
  refresh.mockReset();
});

const stop = (id: string, patch: Partial<InstantlyStop>): InstantlyStop => ({
  id,
  email: `${id}@client.test`,
  reason: 'human_reply',
  status: 'pending',
  attempts: 0,
  errorCode: null,
  nextAttemptAt: null,
  createdAt: '2026-10-17T09:00:00Z',
  confirmedAt: null,
  campaignUrl: 'https://app.instantly.ai/app/campaign/x',
  ...patch,
});

const stops = (items: InstantlyStop[], paused = false, mayConfirm = true): Stops => ({
  items,
  pending: items.filter((s) => s.status === 'pending').length,
  failed: items.filter((s) => s.status === 'failed').length,
  paused,
  mayConfirm,
});

const NOW = '2026-10-17T10:00:00Z';
const card = (value: Stops, connected = true) => (
  <InstantlyStops stops={value} connected={connected} now={NOW} />
);

const rowOf = (container: HTMLElement, email: string) =>
  [...container.querySelectorAll('li')].find((li) =>
    li.textContent?.includes(email),
  ) as HTMLElement;

describe('InstantlyStops', () => {
  it('shows nothing when no stop is open', async () => {
    const container = await render(card(stops([])));
    expect(container.textContent).toBe('');
  });

  it('lists each stop with its reason and state, and the error of a failed one', async () => {
    const container = await render(
      card(
        stops([
          stop('ann', { status: 'failed', errorCode: 'INSTANTLY.STOP_SCOPE', reason: 'opt_out' }),
          stop('bob', {
            attempts: 2,
            errorCode: 'INSTANTLY.RATE_LIMITED',
            nextAttemptAt: '2026-10-17T10:05:00Z',
            reason: 'hard_bounce',
          }),
        ]),
      ),
    );
    expect(container.textContent).toContain('Stop in Instantly');
    expect(container.textContent).toContain('1 in attesa di Instantly · 1 non riuscito');
    const failed = rowOf(container, 'ann@client.test');
    expect(failed.textContent).toContain('Non riuscito');
    expect(failed.textContent).toContain('Si è disiscritta');
    expect(failed.textContent).toContain('La chiave API di Instantly non può rimuovere');
    const waiting = rowOf(container, 'bob@client.test');
    expect(waiting.textContent).toContain('In corso');
    expect(waiting.textContent).toContain('Indirizzo rimbalzato');
    expect(waiting.textContent).toContain(
      'Instantly ha chiesto a Zenbree di rallentare. Zenbree riprova tra 5 minuti.',
    );
    expect(waiting.querySelector('button')).toBeNull();
    expect(container.textContent).not.toContain('Confermato');
  });

  it('retries a failed stop, or confirms the user removed the person', async () => {
    const container = await render(
      card(stops([stop('ann', { status: 'failed', errorCode: 'INSTANTLY.UNREACHABLE' })])),
    );
    await click(control(container, 'Riprova'));
    expect(move).toHaveBeenLastCalledWith('ann', 'retry');
    await click(control(container, 'Ho rimosso la persona in Instantly'));
    expect(move).toHaveBeenLastCalledWith('ann', 'confirm');
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('says why a confirm was refused, and that an admin paused Instantly', async () => {
    move.mockResolvedValueOnce({
      success: false,
      error: { code: 'INSTANTLY.NOT_ALLOWED', message: 'no', status: 403 },
    });
    const container = await render(
      card(stops([stop('ann', { status: 'failed', errorCode: 'INSTANTLY.DISCONNECTED' })], true)),
    );
    expect(container.textContent).toContain('Un admin ha messo in pausa Instantly');
    await click(control(container, 'Ho rimosso la persona in Instantly'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'Solo il membro che ha collegato Instantly',
    );
    expect(refresh).not.toHaveBeenCalled();
  });

  it('says an overdue attempt is due now, and why each code waits', async () => {
    const container = await render(
      card(
        stops([
          stop('ann', {
            attempts: 3,
            errorCode: 'INSTANTLY.UNKNOWN_OUTCOME',
            nextAttemptAt: '2026-10-17T09:58:00Z',
          }),
        ]),
      ),
    );
    const row = rowOf(container, 'ann@client.test');
    expect(row.textContent).toContain(
      'Instantly non ha risposto in tempo. Zenbree riprova entro un minuto.',
    );
    expect(row.textContent).not.toContain('minuti fa');
  });

  it('gives a failed stop its own text, not the text of an enrollment batch', async () => {
    const container = await render(
      card(stops([stop('ann', { status: 'failed', errorCode: 'INSTANTLY.UNKNOWN_OUTCOME' })])),
    );
    expect(container.textContent).toContain('Zenbree non sa se ha rimosso la persona');
    expect(container.textContent).not.toContain('controlla prima la campagna');
  });

  it('offers Retry only while Instantly is connected, and the confirm only to who may', async () => {
    const failed = [stop('ann', { status: 'failed', errorCode: 'INSTANTLY.DISCONNECTED' })];
    const offline = await render(card(stops(failed), false));
    expect(offline.textContent).not.toContain('Riprova');
    expect(offline.textContent).toContain('Ho rimosso la persona in Instantly');
    const member = await render(card(stops(failed, false, false)));
    expect(member.textContent).toContain('Riprova');
    expect(member.textContent).not.toContain('Ho rimosso la persona in Instantly');
  });
});
