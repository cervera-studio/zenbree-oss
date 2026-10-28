import { revalidatePath } from 'next/cache';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { toAppError, unreachable } from '@/lib/api/error';
import { setSenderLink, setSenderPaused, setSenderWindow } from '@/lib/integrations/mailboxes';
import { fail, succeed } from '@/lib/types';
import { setSenderLinkAction, setSenderPausedAction, setSenderWindowAction } from './actions';

vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }));
vi.mock('@/lib/integrations/mailboxes', () => ({
  setSenderPaused: vi.fn(),
  setSenderLink: vi.fn(),
  setSenderWindow: vi.fn(),
}));

const SENDER = '00000000-0000-4000-8000-000000000001';
const CLIENT = '00000000-0000-4000-8000-000000000002';

const changes = [
  ['pause', () => setSenderPausedAction(SENDER, true)],
  ['window', () => setSenderWindowAction(SENDER, { startHour: 9, endHour: 18, days: [0, 1] })],
  ['link', () => setSenderLinkAction(SENDER, CLIENT, true)],
] as const;

function apiAnswers(result: ReturnType<typeof fail> | ReturnType<typeof succeed>) {
  vi.mocked(setSenderPaused).mockResolvedValue(result);
  vi.mocked(setSenderWindow).mockResolvedValue(result);
  vi.mocked(setSenderLink).mockResolvedValue(result);
}

beforeEach(() => vi.clearAllMocks());

describe('sender actions (#1042)', () => {
  it.each(changes)(
    'a %s change the API never answered leaves the page as it is',
    async (_, change) => {
      apiAnswers(fail(unreachable()));
      expect((await change()).success).toBe(false);
      expect(revalidatePath).not.toHaveBeenCalled();
    },
  );

  it.each(changes)(
    'a %s change the API refused renders the true state again',
    async (_, change) => {
      apiAnswers(fail(toAppError(404, { detail: { code: 'INTEGRATIONS.MAILBOX_NOT_FOUND' } })));
      expect((await change()).success).toBe(false);
      expect(revalidatePath).toHaveBeenCalledWith('/settings/senders');
    },
  );

  it.each(changes)(
    'a %s change that failed inside the API renders the page again',
    async (_, change) => {
      apiAnswers(fail(toAppError(500, null)));
      expect((await change()).success).toBe(false);
      expect(revalidatePath).toHaveBeenCalledWith('/settings/senders');
    },
  );

  it.each(changes)('a saved %s change renders the page again', async (_, change) => {
    apiAnswers(succeed({}));
    expect((await change()).success).toBe(true);
    expect(revalidatePath).toHaveBeenCalledWith('/settings/senders');
  });
});
