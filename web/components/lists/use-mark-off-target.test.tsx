// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import type { ListRow } from '@/lib/lists/api';
import { bulkApi } from '@/lib/lists/bulk';
import { AppError, fail, succeed } from '@/lib/types';
import { useMarkOffTarget } from './use-mark-off-target';

const { toast } = vi.hoisted(() => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/components/ui/sonner', () => ({ toast }));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }));
vi.mock('@/i18n/error-text', () => ({ useErrorText: () => () => 'error' }));

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const rows = [
  { id: 'good1', rating: 'good' },
  { id: 'none1', rating: null },
  { id: 'bad1', rating: 'bad' },
  { id: 'good2', rating: 'good' },
] as ListRow[];

function mount(marked: ListRow[] = rows) {
  const patchRow = vi.fn();
  const refetch = vi.fn();
  const ref: { current: ReturnType<typeof useMarkOffTarget> | null } = { current: null };
  function Probe() {
    ref.current = useMarkOffTarget({ listId: 'l1', patchRow, refetch, onMarked: () => {} });
    return null;
  }
  act(() => createRoot(document.createElement('div')).render(<Probe />));
  const mark = () => act(async () => void (await ref.current?.mark(marked)));
  const undo = (from: typeof toast.success = toast.success) =>
    act(async () => {
      from.mock.calls[0]?.[1].action.onClick();
      await vi.waitFor(() => expect(rate).toHaveBeenCalledTimes(2));
    });
  return { patchRow, refetch, mark, undo };
}

const rate = vi.spyOn(bulkApi, 'rate');

beforeEach(() => {
  vi.clearAllMocks();
  rate.mockReset();
  vi.spyOn(bulkApi, 'markOffTarget').mockResolvedValue(succeed({ marked: 4, not_found: 0 }));
});

describe('useMarkOffTarget undo', () => {
  it('rates the rows that were good back to good and the unrated back to null; bad rows stay', async () => {
    rate.mockImplementation(async (_l, ids) => succeed({ rated: ids.length, not_found: 0 }));
    const { patchRow, refetch, mark, undo } = mount();
    await mark();
    patchRow.mockClear();

    await undo();

    expect(rate.mock.calls).toEqual([
      ['l1', ['good1', 'good2'], 'good'],
      ['l1', ['none1'], null],
    ]);
    expect(patchRow.mock.calls).toEqual([
      ['good1', { rating: 'good' }],
      ['good2', { rating: 'good' }],
      ['none1', { rating: null, rating_note: null }],
    ]);
    expect(toast.error).not.toHaveBeenCalled();
    expect(refetch).not.toHaveBeenCalled();
  });

  it('keeps the rows of a failed request as they are, says so and reloads', async () => {
    rate
      .mockResolvedValueOnce(succeed({ rated: 2, not_found: 0 }))
      .mockResolvedValueOnce(fail(new AppError('UNAUTHORIZED', 'expired')));
    const { patchRow, refetch, mark, undo } = mount();
    await mark();
    patchRow.mockClear();

    await undo();

    expect(patchRow.mock.calls).toEqual([
      ['good1', { rating: 'good' }],
      ['good2', { rating: 'good' }],
    ]);
    expect(toast.error).toHaveBeenCalledWith('bulk.undoError');
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('after a failed request, undoes only the rows the earlier requests marked', async () => {
    // 1000 ids a request: good1, none1 and the 998 bad rows go first; good2 and none2 are in the failed one.
    const pads = Array.from({ length: 998 }, (_, i) => ({ id: `pad${i}`, rating: 'bad' }));
    const many = [
      { id: 'good1', rating: 'good' },
      { id: 'none1', rating: null },
      ...pads,
      { id: 'good2', rating: 'good' },
      { id: 'none2', rating: null },
    ] as ListRow[];
    vi.spyOn(bulkApi, 'markOffTarget')
      .mockResolvedValueOnce(succeed({ marked: 1000, not_found: 0 }))
      .mockResolvedValueOnce(fail(new AppError('UNAUTHORIZED', 'expired')));
    rate.mockImplementation(async (_l, ids) => succeed({ rated: ids.length, not_found: 0 }));
    const { patchRow, mark, undo } = mount(many);
    await mark();
    expect(toast.error.mock.calls[0]?.[1]).toMatchObject({
      description: 'bulk.marked',
      action: { label: 'undo' },
    });
    patchRow.mockClear();

    await undo(toast.error);

    expect(rate.mock.calls).toEqual([
      ['l1', ['good1'], 'good'],
      ['l1', ['none1'], null],
    ]);
    expect(patchRow.mock.calls).toEqual([
      ['good1', { rating: 'good' }],
      ['none1', { rating: null, rating_note: null }],
    ]);
  });

  it('offers no undo when no request marked a row', async () => {
    vi.spyOn(bulkApi, 'markOffTarget').mockResolvedValue(
      fail(new AppError('UNAUTHORIZED', 'expired')),
    );
    const { mark } = mount();
    await mark();

    expect(toast.error).toHaveBeenCalledWith('error', undefined);
  });
});
