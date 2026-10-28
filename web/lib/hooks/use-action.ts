'use client';

import { useState, useTransition } from 'react';
import { toast } from '@/components/ui/sonner';
import type { AppErrorJSON, Result } from '@/lib/types';

interface RunOptions<T> {
  onSuccess?: (data: T) => void;
  onError?: (error: AppErrorJSON) => void;
  /** Runs after either outcome, once the toasts and the outcome callback have run. */
  onSettled?: () => void;
  successToast?: string;
  errorToast?: string | ((error: AppErrorJSON) => string);
}

/**
 * Runs a server action that returns a `Result` inside a transition. `error` holds the last
 * failure and clears when the next run starts, or on `reset`. An action may return `undefined`
 * when it redirects. `run` resolves once the action has settled, for a caller that waits on it.
 */
export function useAction() {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<AppErrorJSON | null>(null);

  function run<T>(
    action: () => Promise<Result<T> | undefined>,
    options: RunOptions<T> = {},
  ): Promise<void> {
    setError(null);
    return new Promise((resolve) =>
      startTransition(async () => {
        try {
          const result = await action();
          if (result?.success) {
            if (options.successToast) toast.success(options.successToast);
            options.onSuccess?.(result.data);
          } else if (result) {
            setError(result.error);
            const { errorToast } = options;
            if (errorToast) {
              toast.error(typeof errorToast === 'function' ? errorToast(result.error) : errorToast);
            }
            options.onError?.(result.error);
          }
          options.onSettled?.();
        } finally {
          resolve();
        }
      }),
    );
  }

  return { pending, error, run, reset: () => setError(null) };
}
