// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { type InstantlyEnrollment, instantlyApi } from '@/lib/lists/instantly';
import { fail, succeed } from '@/lib/types';
import { InstantlyBatch } from './instantly-batch';

afterEach(() => vi.restoreAllMocks());

const batch = (over: Partial<InstantlyEnrollment> = {}): InstantlyEnrollment => ({
  id: '44444444-4444-4444-8444-444444444444',
  status: 'cancelled',
  error_code: null,
  campaign: { id: 'c', name: 'Q4', url: 'https://app.instantly.ai/app/campaign/c' },
  counts: { pending: 0, publishing: 0, accepted: 0, skipped: 1, failed: 0, uncertain: 2 },
  cancel_requested: false,
  can_cancel: false,
  can_retry: true,
  rows: [],
  ...over,
});

describe('InstantlyBatch', () => {
  it('shows nothing when no batch needs a decision', async () => {
    vi.spyOn(instantlyApi, 'latest').mockResolvedValue(succeed(null));
    await render(<InstantlyBatch connected />);
    expect(document.body.textContent).toBe('');
  });

  it('lets the user retry a cancelled batch whose people are in doubt', async () => {
    vi.spyOn(instantlyApi, 'latest').mockResolvedValue(succeed(batch()));
    const retry = vi
      .spyOn(instantlyApi, 'retry')
      .mockResolvedValue(succeed(batch({ status: 'queued', can_retry: false, can_cancel: true })));
    vi.spyOn(instantlyApi, 'get').mockResolvedValue(succeed(batch()));
    await render(<InstantlyBatch connected />);
    expect(document.body.textContent).toContain('Ultima aggiunta a Instantly');
    await click(control(document.body, 'Riprova'));
    expect(retry).toHaveBeenCalledWith('44444444-4444-4444-8444-444444444444');
    expect(control(document.body, 'Annulla l’aggiunta')).toBeTruthy();
  });

  it('says why an action failed', async () => {
    vi.spyOn(instantlyApi, 'latest').mockResolvedValue(succeed(batch()));
    vi.spyOn(instantlyApi, 'retry').mockResolvedValue(
      fail({ name: 'AppError', code: 'INSTANTLY.RECONCILE_TOO_SOON', message: 'x', status: 409 }),
    );
    await render(<InstantlyBatch connected />);
    await click(control(document.body, 'Riprova'));
    expect(document.body.querySelector('[role=alert]')).toBeTruthy();
  });

  it('can dismiss a failed batch, and does not say "not found" on a person no lookup has judged', async () => {
    vi.spyOn(instantlyApi, 'latest').mockResolvedValue(
      succeed(
        batch({
          status: 'failed',
          can_cancel: true,
          can_retry: true,
          counts: { pending: 0, publishing: 0, accepted: 0, skipped: 0, failed: 0, uncertain: 1 },
          rows: [
            { row_id: 'a', email: 'a@x.com', outcome: 'uncertain', reason: 'cancelled_in_flight' },
          ],
        }),
      ),
    );
    await render(<InstantlyBatch connected />);
    expect(control(document.body, 'Annulla l’aggiunta')).toBeTruthy();
    expect(document.body.textContent).toContain('Annullata mentre veniva aggiunta');
    expect(document.body.textContent).not.toContain('non trovata');
  });

  it('still shows a batch in doubt after a disconnect, with a note and its actions', async () => {
    vi.spyOn(instantlyApi, 'latest').mockResolvedValue(succeed(batch()));
    await render(<InstantlyBatch connected={false} />);
    expect(document.body.textContent).toContain('Ricollega Instantly');
    expect(control(document.body, 'Riprova')).toBeTruthy();
  });

  it('hides the old error of a batch that was cancelled after it failed', async () => {
    vi.spyOn(instantlyApi, 'latest').mockResolvedValue(
      succeed(batch({ status: 'cancelled', error_code: 'INSTANTLY.REJECTED' })),
    );
    await render(<InstantlyBatch connected />);
    expect(document.body.querySelector('[role=alert]')).toBeNull();
  });
});
