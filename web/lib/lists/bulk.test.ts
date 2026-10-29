import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { type AppErrorJSON, fail, succeed } from '@/lib/types';
import { addToCampaign, bulkApi, markOffTarget, rateRows } from './bulk';

afterEach(() => vi.restoreAllMocks());

const ids = (n: number) => Array.from({ length: n }, (_, i) => `r${i}`);
const notDraft: AppErrorJSON = {
  name: 'AppError',
  code: 'CAMPAIGN.NOT_DRAFT',
  message: 'campaign is not a draft',
  status: 409,
};

describe('addToCampaign', () => {
  it('sends 2001 ids as 1000, 1000 and 1, and sums what each request added and skipped', async () => {
    const send = vi
      .spyOn(bulkApi, 'addToCampaign')
      .mockResolvedValueOnce(succeed({ added: 990, skipped: { excluded: 4, no_contact: 6 } }))
      .mockResolvedValueOnce(succeed({ added: 0, skipped: { over_limit: 990, excluded: 10 } }))
      .mockResolvedValueOnce(succeed({ added: 0, skipped: { over_limit: 1 } }));

    const done = await addToCampaign('l1', 'c1', ids(2001));

    expect(send.mock.calls.map(([, , part]) => part.length)).toEqual([1000, 1000, 1]);
    expect(send.mock.calls[2]).toEqual(['l1', 'c1', ['r2000']]);
    expect(done).toEqual({
      result: { added: 990, skipped: { excluded: 14, no_contact: 6, over_limit: 991 } },
      error: null,
      unsent: [],
    });
  });

  it('counts a reason this web does not know under other', async () => {
    vi.spyOn(bulkApi, 'addToCampaign').mockResolvedValueOnce(
      succeed({ added: 1, skipped: { replied_before: 2, bounced: 1, duplicate: 3 } }),
    );

    const done = await addToCampaign('l1', 'c1', ids(7));

    expect(done.result).toEqual({ added: 1, skipped: { other: 3, duplicate: 3 } });
  });

  it('stops at a failed request and answers what the earlier ones did, with the error', async () => {
    const send = vi
      .spyOn(bulkApi, 'addToCampaign')
      .mockResolvedValueOnce(succeed({ added: 1000, skipped: {} }))
      .mockResolvedValueOnce(fail(notDraft));

    const done = await addToCampaign('l1', 'c1', ids(2500));

    expect(send).toHaveBeenCalledTimes(2);
    expect(done.result).toEqual({ added: 1000, skipped: {} });
    expect(done.error?.code).toBe('CAMPAIGN.NOT_DRAFT');
    expect(done.unsent).toHaveLength(1500);
    expect(done.unsent[0]).toBe('r1000');
  });
});

describe('markOffTarget', () => {
  it('sums marked and not_found across requests', async () => {
    vi.spyOn(bulkApi, 'markOffTarget')
      .mockResolvedValueOnce(succeed({ marked: 998, not_found: 2 }))
      .mockResolvedValueOnce(succeed({ marked: 5, not_found: 0 }));

    const done = await markOffTarget('l1', ids(1005));

    expect(done).toEqual({ result: { marked: 1003, not_found: 2 }, error: null, unsent: [] });
  });
});

describe('rateRows', () => {
  it('sends 1500 ids as 1000 and 500 with the one rating, and sums rated and not_found', async () => {
    const send = vi
      .spyOn(bulkApi, 'rate')
      .mockResolvedValueOnce(succeed({ rated: 1000, not_found: 0 }))
      .mockResolvedValueOnce(succeed({ rated: 497, not_found: 3 }));

    const done = await rateRows('l1', ids(1500), null);

    expect(send.mock.calls.map(([list, part, rating]) => [list, part.length, rating])).toEqual([
      ['l1', 1000, null],
      ['l1', 500, null],
    ]);
    expect(done).toEqual({ result: { rated: 1497, not_found: 3 }, error: null, unsent: [] });
  });

  it('sends nothing for no ids', async () => {
    const send = vi.spyOn(bulkApi, 'rate');

    const done = await rateRows('l1', [], 'good');

    expect(send).not.toHaveBeenCalled();
    expect(done).toEqual({ result: { rated: 0, not_found: 0 }, error: null, unsent: [] });
  });
});
