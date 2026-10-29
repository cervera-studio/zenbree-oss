'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { regenerateDraft } from '@/lib/inbox/draft';
import type { AppErrorJSON } from '@/lib/types';

/** What the box holds: its text, and the agent's draft it started from (`null` when the user wrote it). */
type Body = { text: string; agent: string | null };

/**
 * The answer box's text and «Rigenera» (#865). A new draft replaces the text and keeps the replaced
 * one as a single undo step; `restore` swaps the two, so neither is ever lost.
 * The call runs outside a transition, unlike the send: a pending transition holds every link to
 * another thread until the model call ends.
 */
export function useDraftText(id: string, draft: string | null) {
  const [body, setBody] = useState<Body>({ text: draft ?? '', agent: draft });
  const [previous, setPrevious] = useState<Body | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppErrorJSON | null>(null);
  const router = useRouter();

  async function regenerate() {
    const replaced = body;
    setPending(true);
    setError(null);
    const result = await regenerateDraft(id);
    setPending(false);
    if (!result.success) {
      setError(result.error);
      return;
    }
    const next = result.data.draft;
    // Back to this thread would show the cached page, with the draft from before the call.
    router.refresh();
    setBody({ text: next, agent: next });
    setPrevious(replaced.text.trim() && replaced.text !== next ? replaced : null);
  }

  return {
    ...body,
    edit: (text: string) => setBody((b) => ({ ...b, text })),
    canRestore: previous !== null,
    restore: () => {
      if (!previous) return;
      setPrevious(body);
      setBody(previous);
    },
    regenerate,
    pending,
    error,
    clearError: () => setError(null),
  };
}
