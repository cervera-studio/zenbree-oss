// @vitest-environment happy-dom
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import type { ListColumn } from '@/lib/lists/api';
import { type InstantlyEnrollment, instantlyApi } from '@/lib/lists/instantly';
import { fail, succeed } from '@/lib/types';
import { InstantlyDialog } from './instantly-dialog';

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const columns: ListColumn[] = [
  { key: 'first_name', label: 'Nome', kind: 'static', position: 0 },
  { key: 'title', label: 'Ruolo', kind: 'static', position: 1 },
  { key: 'company_domain', label: 'Sito', kind: 'static', position: 2 },
  { key: 'hunter_id', label: 'Id', kind: 'static', position: 3 },
  { key: 'email', label: 'Email', kind: 'static', position: 4 },
];
const running = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Q4 Milano',
  status: 1,
  status_label: 'active',
  may_send: true,
};
const paused = {
  ...running,
  id: '22222222-2222-4222-8222-222222222222',
  name: 'Pausa',
  status: 2,
  status_label: 'paused',
  may_send: false,
};

const preview = (may_send: boolean) => ({
  campaign: {
    id: running.id,
    name: running.name,
    may_send,
    url: 'https://app.instantly.ai/app/campaign/x',
  },
  selected: 3,
  eligible: 2,
  skipped: { excluded: 1 },
  rows: [],
  sample: [],
  custom_variables: [],
});
const enrollment = (over: Partial<InstantlyEnrollment> = {}): InstantlyEnrollment => ({
  id: '33333333-3333-4333-8333-333333333333',
  status: 'completed',
  error_code: null,
  campaign: { id: running.id, name: running.name, url: 'https://app.instantly.ai/app/campaign/x' },
  counts: { pending: 0, publishing: 0, accepted: 1, skipped: 0, failed: 0, uncertain: 1 },
  cancel_requested: false,
  can_cancel: false,
  can_retry: true,
  rows: [
    { row_id: 'a', email: 'a@x.com', outcome: 'accepted', reason: null },
    { row_id: 'b', email: 'b@x.com', outcome: 'uncertain', reason: 'INSTANTLY.UNKNOWN_OUTCOME' },
  ],
  ...over,
});

const body = () => document.body;
const radio = (label: string) => {
  const input = [...body().querySelectorAll('label')]
    .find((l) => l.textContent?.includes(label))
    ?.querySelector('input');
  if (!input) throw new Error(`no campaign ${label}`);
  return input;
};
const confirmButton = () => control(body(), 'Aggiungi 2 persone') as HTMLButtonElement;

async function open(list: (typeof running)[], cols: ListColumn[] = columns) {
  vi.spyOn(instantlyApi, 'latest').mockResolvedValue(succeed(null));
  vi.spyOn(instantlyApi, 'campaigns').mockResolvedValue(succeed({ campaigns: list }));
  await render(
    <InstantlyDialog listId="l1" rowIds={['a', 'b', 'c']} columns={cols} onClose={() => {}} />,
  );
  await click(radio(list[0]?.name ?? ''));
  await click(control(body(), 'Avanti'));
}

describe('InstantlyDialog', () => {
  it('asks for a second consent when the campaign is running, and sends the default mapping', async () => {
    vi.spyOn(instantlyApi, 'preview').mockResolvedValue(succeed(preview(true)));
    const enroll = vi.spyOn(instantlyApi, 'enroll').mockResolvedValue(succeed(enrollment()));
    await open([running]);
    await click(control(body(), 'Anteprima'));

    expect(body().textContent).toContain('Questa campagna è attiva in Instantly');
    expect(confirmButton().disabled).toBe(true);
    await click(body().querySelector<HTMLElement>('[role=checkbox]') as HTMLElement);
    expect(confirmButton().disabled).toBe(false);
    await click(confirmButton());

    const [target, acknowledged] = enroll.mock.calls[0] ?? [];
    expect(acknowledged).toBe(true);
    expect(target?.campaignId).toBe(running.id);
    expect(target?.rowIds).toEqual(['a', 'b', 'c']);
    expect(target?.mapping.fields).toMatchObject({
      first_name: 'first_name',
      job_title: 'title',
      website: 'company_domain',
    });
    expect(target?.mapping.fields).not.toHaveProperty('phone');
    expect(target?.mapping.custom).toEqual([]);
  });

  it('leaves a field to the API default when the list has no column for it', async () => {
    vi.spyOn(instantlyApi, 'preview').mockResolvedValue(succeed(preview(false)));
    const enroll = vi.spyOn(instantlyApi, 'enroll').mockResolvedValue(succeed(enrollment()));
    const onlyFullName: ListColumn[] = [
      { key: 'full_name', label: 'Nome e cognome', kind: 'static', position: 0 },
      { key: 'email', label: 'Email', kind: 'static', position: 1 },
    ];
    await open([paused], onlyFullName);
    await click(control(body(), 'Anteprima'));
    await click(confirmButton());
    const fields = enroll.mock.calls[0]?.[0].mapping.fields ?? {};
    expect(Object.keys(fields)).toEqual([]);
  });

  it('needs no consent for a paused campaign and never sends one', async () => {
    vi.spyOn(instantlyApi, 'preview').mockResolvedValue(
      succeed({ ...preview(false), campaign: { ...preview(false).campaign, id: paused.id } }),
    );
    const enroll = vi.spyOn(instantlyApi, 'enroll').mockResolvedValue(succeed(enrollment()));
    await open([paused]);
    await click(control(body(), 'Anteprima'));
    expect(body().textContent).not.toContain('Questa campagna è attiva in Instantly');
    await click(confirmButton());
    expect(enroll.mock.calls[0]?.[1]).toBe(false);
  });

  it('shows each row result, the uncertain hint and the link to the campaign', async () => {
    vi.spyOn(instantlyApi, 'preview').mockResolvedValue(succeed(preview(false)));
    vi.spyOn(instantlyApi, 'enroll').mockResolvedValue(succeed(enrollment()));
    await open([paused]);
    await click(control(body(), 'Anteprima'));
    await click(confirmButton());

    expect(body().textContent).toContain('a@x.com');
    expect(body().textContent).toContain('Non certe');
    expect(body().textContent).toContain('non aggiunge mai una persona due volte');
    const link = control(body(), 'Apri la campagna in Instantly');
    expect(link.getAttribute('href')).toBe('https://app.instantly.ai/app/campaign/x');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(control(body(), 'Riprova')).toBeTruthy();
  });

  it('keeps the preview and says why when the batch is refused', async () => {
    vi.spyOn(instantlyApi, 'preview').mockResolvedValue(succeed(preview(false)));
    vi.spyOn(instantlyApi, 'enroll').mockResolvedValue(
      fail({ name: 'AppError', code: 'INSTANTLY.BUSY', message: 'raw english', status: 409 }),
    );
    await open([paused]);
    await click(control(body(), 'Anteprima'));
    await click(confirmButton());
    expect(body().querySelector('[role=alert]')?.textContent).not.toContain('raw english');
    expect(body().querySelector('[role=alert]')?.textContent).toBeTruthy();
    expect(confirmButton().disabled).toBe(false);
  });

  it('reads a running enrollment again until it settles', async () => {
    vi.useFakeTimers();
    vi.spyOn(instantlyApi, 'preview').mockResolvedValue(succeed(preview(false)));
    vi.spyOn(instantlyApi, 'enroll').mockResolvedValue(
      succeed(enrollment({ status: 'running', can_retry: false, can_cancel: true })),
    );
    const get = vi.spyOn(instantlyApi, 'get').mockResolvedValue(succeed(enrollment()));
    await open([paused]);
    await click(control(body(), 'Anteprima'));
    await click(confirmButton());
    expect(control(body(), 'Annulla l’aggiunta')).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(get).toHaveBeenCalledTimes(1);
    expect(body().textContent).toContain('Aggiunta a «Q4 Milano» completata');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(get).toHaveBeenCalledTimes(1);
  });

  it('opens on the batch the user left, with the controls that end it', async () => {
    vi.spyOn(instantlyApi, 'campaigns').mockResolvedValue(succeed({ campaigns: [running] }));
    vi.spyOn(instantlyApi, 'latest').mockResolvedValue(
      succeed(enrollment({ status: 'running', can_cancel: true, can_retry: false })),
    );
    vi.spyOn(instantlyApi, 'get').mockResolvedValue(succeed(enrollment()));
    const cancel = vi
      .spyOn(instantlyApi, 'cancel')
      .mockResolvedValue(succeed(enrollment({ status: 'cancelled', can_cancel: false })));
    await render(
      <InstantlyDialog listId="l1" rowIds={['a']} columns={columns} onClose={() => {}} />,
    );
    expect(body().textContent).toContain('Aggiunta delle persone a');
    expect(body().textContent).not.toContain('Anteprima');
    await click(control(body(), 'Annulla l’aggiunta'));
    expect(cancel).toHaveBeenCalledWith('33333333-3333-4333-8333-333333333333');
    expect(body().textContent).toContain('Aggiunta a «Q4 Milano» annullata');
    expect(control(body(), 'Aggiungi altre persone')).toBeTruthy();
  });

  it('reads as a neutral label, not "send 0 people", when no one is left to send', async () => {
    vi.spyOn(instantlyApi, 'preview').mockResolvedValue(
      succeed({ ...preview(false), eligible: 0 }),
    );
    await open([paused]);
    await click(control(body(), 'Anteprima'));
    const button = control(body(), 'Nessuno da aggiungere') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(body().textContent).not.toContain('Aggiungi 0');
  });
});
