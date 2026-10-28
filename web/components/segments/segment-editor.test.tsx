// @vitest-environment happy-dom
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import {
  createSegmentAction,
  editSegmentAction,
  previewAction,
  publishSegmentAction,
} from '@/app/(app)/segments/actions';
import { click, control, field, render } from '@/app/auth/testing';
import { toast } from '@/components/ui/sonner';
import type { Segment } from '@/lib/segments/api';
import type { RuleGroup } from '@/lib/segments/rule';
import type { AppErrorJSON } from '@/lib/types';
import { SegmentEditor } from './segment-editor';

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));
vi.mock('@/components/ui/sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/app/(app)/segments/actions', () => ({
  createSegmentAction: vi.fn(),
  editSegmentAction: vi.fn(),
  publishSegmentAction: vi.fn(),
  listColumnsAction: vi.fn(),
  whyAction: vi.fn(),
  previewAction: vi.fn(),
}));

const RULE: RuleGroup = {
  type: 'group',
  op: 'and',
  children: [
    { type: 'text', field: { kind: 'canonical', key: 'title' }, op: 'contains', values: ['ceo'] },
    { type: 'fit', op: 'gte', low: 80 },
  ],
};
const DRAFT: Segment = {
  id: '0b5a3f0e-6a51-4d3c-9a0e-1f2e3d4c5b6a',
  name: 'CEO',
  entity: 'person',
  rule: RULE,
  version: 1,
  published_at: null,
  created_at: '2026-10-21T10:00:00Z',
  created_by: null,
  members: 0,
  problems: [],
};
const refusal = (code: string, data?: Record<string, unknown>) => ({
  success: false as const,
  error: { name: 'AppError', code, message: 'english', status: 422, data } as AppErrorJSON,
});

beforeEach(() => {
  vi.mocked(previewAction).mockResolvedValue({
    success: true,
    data: { count: 2, pending: 0, unidentified_rows: 0, linking_rows: 0, sample: [] },
  });
  vi.mocked(createSegmentAction).mockReset();
  vi.mocked(editSegmentAction).mockReset();
  vi.mocked(publishSegmentAction).mockReset();
  vi.mocked(toast.success).mockClear();
  replace.mockClear();
});

async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const marked = (c: HTMLElement) => c.querySelectorAll('[data-invalid]').length;

describe('SegmentEditor', () => {
  it('marks a refused condition only while the rule is the one that was refused', async () => {
    vi.mocked(editSegmentAction).mockResolvedValue(
      refusal('SEGMENT.INVALID_RULE', { path: '/children/0/values/0' }),
    );
    const c = await render(<SegmentEditor saved={DRAFT} lists={[]} columns={{}} />);
    await type(field(c, 'Nome'), 'CEO 2');
    await click(control(c, 'Salva'));
    expect(marked(c)).toBe(1);
    await click(control(c, 'Rimuovi condizione'));
    expect(marked(c)).toBe(0);
  });

  it('keeps a stale version alert and its reload button through a rule edit', async () => {
    vi.mocked(editSegmentAction).mockResolvedValue(refusal('SEGMENT.STALE_VERSION'));
    const c = await render(<SegmentEditor saved={DRAFT} lists={[]} columns={{}} />);
    await type(field(c, 'Nome'), 'CEO 2');
    await click(control(c, 'Salva'));
    expect(c.querySelector('[role="alert"]')).not.toBeNull();
    await click(control(c, 'Rimuovi condizione'));
    const alert = c.querySelector<HTMLElement>('[role="alert"]');
    expect(alert?.textContent).toContain(
      'Qualcuno ha cambiato il segmento mentre lo modificavi. Ricaricalo e salva di nuovo.',
    );
    expect(alert && control(alert, 'Ricarica')).toBeInstanceOf(HTMLButtonElement);
  });

  it('marks the condition a preview refuses while a stale version alert stays', async () => {
    vi.mocked(editSegmentAction).mockResolvedValue(refusal('SEGMENT.STALE_VERSION'));
    const c = await render(<SegmentEditor saved={DRAFT} lists={[]} columns={{}} />);
    await type(field(c, 'Nome'), 'CEO 2');
    await click(control(c, 'Salva'));
    vi.mocked(previewAction).mockResolvedValue(
      refusal('SEGMENT.INVALID_RULE', { path: '/children/0/low' }),
    );
    await click(control(c, 'Rimuovi condizione'));
    await act(() => new Promise((resolve) => setTimeout(resolve, 400)));
    expect(marked(c)).toBe(1);
    expect(control(c, 'Ricarica')).toBeInstanceOf(HTMLButtonElement);
  });

  it('shows a save failure that returns after an edit made during the save', async () => {
    let answer: (result: ReturnType<typeof refusal>) => void = () => {};
    vi.mocked(editSegmentAction).mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const c = await render(<SegmentEditor saved={DRAFT} lists={[]} columns={{}} />);
    await type(field(c, 'Nome'), 'CEO 2');
    await click(control(c, 'Salva'));
    await click(control(c, 'Rimuovi condizione'));
    await act(async () => answer(refusal('SEGMENT.EVALUATION_PAUSED')));
    expect(c.querySelector('[role="alert"]')?.textContent).toBe(
      'Gli aggiornamenti dei segmenti sono sospesi per manutenzione. Riprova tra qualche minuto.',
    );
    vi.mocked(editSegmentAction).mockResolvedValue({
      success: true,
      data: { segment: { ...DRAFT, version: 2 }, joining: 0, leaving: 0, max_credits: 0 },
    });
    await type(field(c, 'Nome'), 'CEO 3');
    await click(control(c, 'Salva'));
    expect(c.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows a publish refusal that follows the save of the edits', async () => {
    vi.mocked(editSegmentAction).mockResolvedValue({
      success: true,
      data: {
        segment: { ...DRAFT, name: 'CEO 2', version: 2, rule: structuredClone(RULE) },
        joining: 0,
        leaving: 0,
        max_credits: 0,
      },
    });
    vi.mocked(publishSegmentAction).mockResolvedValue(refusal('SEGMENT.EVALUATION_PAUSED'));
    const c = await render(<SegmentEditor saved={DRAFT} lists={[]} columns={{}} />);
    await type(field(c, 'Nome'), 'CEO 2');
    await click(control(c, 'Pubblica…'));
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
    await click(control(document.body, 'Pubblica'));
    expect(publishSegmentAction).toHaveBeenCalledWith(DRAFT.id, false);
    expect(c.querySelector('[role="alert"]')?.textContent).toBe(
      'Gli aggiornamenti dei segmenti sono sospesi per manutenzione. Riprova tra qualche minuto.',
    );
  });

  it('keeps the edits and shows the failure when the save before a publish gets no answer', async () => {
    vi.mocked(editSegmentAction).mockRejectedValue(new TypeError('fetch failed'));
    const c = await render(<SegmentEditor saved={DRAFT} lists={[]} columns={{}} />);
    await type(field(c, 'Nome'), 'CEO 2');
    await click(control(c, 'Pubblica…'));
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
    await click(control(document.body, 'Pubblica'));
    expect(publishSegmentAction).not.toHaveBeenCalled();
    expect(c.querySelector('[role="alert"]')?.textContent).toBe(
      'Il servizio non è raggiungibile in questo momento. Riprova tra poco.',
    );
    expect(field(c, 'Nome').value).toBe('CEO 2');
  });

  it('confirms a new segment and opens it as a draft', async () => {
    vi.mocked(createSegmentAction).mockResolvedValue({ success: true, data: DRAFT });
    const c = await render(<SegmentEditor lists={[]} columns={{}} />);
    await type(field(c, 'Nome'), 'CEO');
    const add = c.querySelector<HTMLSelectElement>('[aria-label="Aggiungi una condizione…"]');
    await act(async () => {
      if (!add) return;
      add.value = 'fit';
      add.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const low = c.querySelector<HTMLInputElement>('[aria-label="Da"]');
    if (low) await type(low, '80');
    await click(control(c, 'Salva come bozza'));
    expect(createSegmentAction).toHaveBeenCalledWith({
      name: 'CEO',
      entity: 'person',
      rule: { type: 'group', op: 'and', children: [{ type: 'fit', op: 'gte', low: 80 }] },
    });
    expect(toast.success).toHaveBeenCalledWith('Segmento salvato.');
    expect(replace).toHaveBeenCalledWith(`/segments/${DRAFT.id}`);
  });

  it("says on a draft's page that a draft has no members", async () => {
    const c = await render(<SegmentEditor saved={DRAFT} lists={[]} columns={{}} />);
    expect(c.textContent).toContain('Una bozza non ha membri.');
  });
});
