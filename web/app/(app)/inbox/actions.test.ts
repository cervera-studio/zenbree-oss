import { revalidatePath } from 'next/cache';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { toAppError, unreachable } from '@/lib/api/error';
import { sendReply } from '@/lib/inbox/api';
import { fail, succeed } from '@/lib/types';
import { sendReplyAction } from './actions';

vi.mock('server-only', () => ({}));
vi.mock('next/cache', () => ({ revalidatePath: vi.fn() }));
vi.mock('@/lib/inbox/api', async (original) => ({
  ...(await original<typeof import('@/lib/inbox/api')>()),
  sendReply: vi.fn(),
}));

const MESSAGE = '00000000-0000-4000-8000-000000000001';
const refused = (status: number, code: string) =>
  fail(toAppError(status, { detail: { code, message: 'english' } }));

beforeEach(() => vi.clearAllMocks());

describe('sendReplyAction (#1053)', () => {
  it.each([
    ['a mailbox that refused the token', refused(409, 'INBOX.MAILBOX_ERROR')],
    ['a mailbox that is no longer connected', refused(409, 'INBOX.NO_MAILBOX')],
    ['a LinkedIn account that is no longer connected', refused(409, 'INBOX.NO_LINKEDIN')],
  ])('leaves the page as it is after %s: the box keeps the text', async (_, result) => {
    vi.mocked(sendReply).mockResolvedValue(result);
    expect((await sendReplyAction(MESSAGE, 'Ciao')).success).toBe(false);
    expect(revalidatePath).not.toHaveBeenCalled();
  });

  it.each([
    ['a sent answer', succeed({})],
    ['an answer stored as failed', refused(502, 'INBOX.SEND_FAILED')],
    ['an answer stored as unknown', refused(502, 'INBOX.SEND_UNKNOWN')],
    ['another answer being sent', refused(409, 'INBOX.REPLY_IN_PROGRESS')],
    ['an API that never answered and may have sent it', fail(unreachable())],
    ['a mailbox at its daily limit', refused(429, 'INBOX.MAILBOX_CAPPED')],
  ])('renders the thread again after %s', async (_, result) => {
    vi.mocked(sendReply).mockResolvedValue(result as Awaited<ReturnType<typeof sendReply>>);
    await sendReplyAction(MESSAGE, 'Ciao');
    expect(revalidatePath).toHaveBeenCalledWith('/inbox');
  });
});
