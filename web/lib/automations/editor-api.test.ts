import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { getCampaigns } from '@/lib/campaigns/api';
import { httpClient } from '@/lib/http-client';
import { getProjects } from '@/lib/projects/server';
import { AppError, fail, succeed } from '@/lib/types';
import { getChoices, previewAutomation, updateAutomation } from './editor-api';

vi.mock('server-only', () => ({}));
vi.mock('@/lib/auth-token', () => ({ getToken: vi.fn() }));
vi.mock('@/lib/http-client', () => ({
  httpClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}));
vi.mock('@/lib/projects/server', () => ({ getProjects: vi.fn() }));
vi.mock('@/lib/campaigns/api', () => ({ getCampaigns: vi.fn() }));

const PEOPLE = { id: 'l1', name: 'Persone', entity: 'person' };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getProjects).mockResolvedValue(
    succeed({ projects: [], active: { id: 'p1' } }) as never,
  );
  vi.mocked(getCampaigns).mockResolvedValue(succeed({ campaigns: [] }) as never);
});

const WATCHLISTS = [
  { id: 'w1', name: 'Assunzioni', listId: 'l2', listName: 'Aziende in crescita', enabled: true },
  { id: 'w2', name: 'Ferma', listId: 'l2', listName: 'Aziende in crescita', enabled: false },
];

type Read = Awaited<ReturnType<typeof httpClient.get>>;

function answer(instantly: Read, watchlists: Read = succeed({ watchlists: WATCHLISTS })) {
  vi.mocked(httpClient.get).mockImplementation(async (path: string) => {
    if (path.startsWith('/v1/lists')) return succeed({ lists: [PEOPLE] });
    if (path === '/v1/watchlists') return watchlists;
    return instantly;
  });
}

describe('the choices of the automation editor', () => {
  it("offers the active project's watchlists, paused ones too", async () => {
    answer(fail(new AppError('INSTANTLY.NOT_CONNECTED', 'connect', 409)));
    const choices = await getChoices();
    expect(choices.success && choices.data.watchlists).toEqual({
      state: 'ok',
      items: [
        { id: 'w1', name: 'Assunzioni', enabled: true, listName: 'Aziende in crescita' },
        { id: 'w2', name: 'Ferma', enabled: false, listName: 'Aziende in crescita' },
      ],
    });
    expect(httpClient.get).toHaveBeenCalledWith(
      '/v1/watchlists',
      expect.objectContaining({ project: 'p1' }),
    );
  });

  it('still loads the lists when the watchlists cannot be read', async () => {
    answer(
      fail(new AppError('INSTANTLY.NOT_CONNECTED', 'connect', 409)),
      fail(new AppError('INTERNAL.SERVICE_UNAVAILABLE', 'down', 503)),
    );
    const choices = await getChoices();
    expect(choices.success).toBe(true);
    expect(choices.success && choices.data.people).toEqual([{ id: 'l1', name: 'Persone' }]);
    expect(choices.success && choices.data.watchlists).toEqual({ state: 'unreadable' });
  });

  it('offers the Instantly campaigns with their status and whether they send', async () => {
    const campaign = {
      id: 'c1',
      name: 'Verify running',
      status: 1,
      maySend: true,
      statusLabel: 'active',
    };
    answer(succeed({ campaigns: [campaign] }));
    const choices = await getChoices();
    expect(choices.success && choices.data.instantly).toEqual({
      state: 'ready',
      campaigns: [{ id: 'c1', name: 'Verify running', status: 1, maySend: true }],
    });
  });

  it.each([
    [new AppError('INSTANTLY.NOT_CONNECTED', 'connect', 409), { state: 'notConnected' }],
    [
      new AppError('INSTANTLY.DISABLED', 'off', 503),
      { state: 'unreadable', code: 'INSTANTLY.DISABLED' },
    ],
    [
      new AppError('INTERNAL.SERVICE_UNAVAILABLE', 'down', 503),
      { state: 'unreadable', code: 'INTERNAL.SERVICE_UNAVAILABLE' },
    ],
  ])('still loads the lists when Instantly answers %s', async (error, instantly) => {
    answer(fail(error));
    const choices = await getChoices();
    expect(choices.success).toBe(true);
    expect(choices.success && choices.data.people).toEqual([{ id: 'l1', name: 'Persone' }]);
    expect(choices.success && choices.data.instantly).toEqual(instantly);
  });
});

describe('saving an automation with a signal trigger', () => {
  const plan = {
    definition: { listId: 'l1', steps: [{ type: 'find_emails' as const }] },
    creditCap: 5,
  };

  const signal = { kind: 'signal', watchlistId: 'w1', minScore: 80 } as const;

  it('sends the watchlist and the minimum', async () => {
    await updateAutomation('a1', { ...plan, name: 'Segnali', trigger: signal });
    expect(httpClient.patch).toHaveBeenCalledWith(
      '/v1/automations/a1',
      { ...plan, name: 'Segnali', trigger: signal },
      expect.anything(),
    );
  });

  it('previews the trigger the draft would save', async () => {
    await previewAutomation(plan, signal, 'a1');
    expect(httpClient.post).toHaveBeenCalledWith(
      '/v1/automations/preview',
      { ...plan, trigger: signal, automationId: 'a1' },
      expect.anything(),
    );
  });
});
