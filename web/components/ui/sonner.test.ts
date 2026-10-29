import { beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const sonner = vi.hoisted(() => {
  const base = vi.fn();
  return Object.assign(base, {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    loading: vi.fn(),
    dismiss: vi.fn(),
    getToasts: vi.fn((): { id: string }[] => []),
  });
});
vi.mock('sonner', () => ({ toast: sonner, Toaster: () => null }));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }));
vi.mock('next-themes', () => ({ useTheme: () => ({ theme: 'light' }) }));

import { toast } from './sonner';

describe('toast', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sonner.getToasts.mockReturnValue([]);
  });

  it('lets a caller override the duration', () => {
    toast.success('ok', { duration: 1000 });
    expect(sonner.success).toHaveBeenCalledWith('ok', expect.objectContaining({ duration: 1000 }));
  });

  it('replaces the current toast with a new one', () => {
    toast.success('first');
    expect(sonner.dismiss).toHaveBeenCalledOnce();
    expect(sonner.dismiss.mock.invocationCallOrder[0]).toBeLessThan(
      sonner.success.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it('treats an id that is not on screen as a new toast', () => {
    sonner.getToasts.mockReturnValue([{ id: 'other' }]);
    toast.success('first time', { id: 'fresh' });
    expect(sonner.dismiss).toHaveBeenCalledOnce();
  });

  it('updates a toast by id without dismissing it, and the update takes the new tone duration', () => {
    sonner.getToasts.mockReturnValue([{ id: 'import' }]);
    toast.success('imported', { id: 'import' });
    expect(sonner.dismiss).not.toHaveBeenCalled();
    expect(sonner.success).toHaveBeenCalledWith(
      'imported',
      expect.objectContaining({ id: 'import', duration: 5000 }),
    );
  });
});
