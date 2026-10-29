'use client';

import { useSearchParams } from 'next/navigation';
import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from 'react';
import { DEFAULT_MODE, type HomeModeId } from '@/lib/chat/home-modes';

/** `?draft=` (the CSV import's "find missing emails"): prefill, never send; dropped from the URL.
 * The page stays mounted while `?s=` and `?draft=` change (`/` to `/?s=…&draft=…`, «Nuova chat»,
 * a sidebar click), so both are read during render: another chat starts from its own draft link
 * or empty, never with the text of the chat before. The same session on screen is the same chat
 * while the URL catches up: the first send from `/` adds `?s=`, and «Nuova chat» during a stream
 * returns to it. The home's mode follows the text: another chat starts in the default mode. */
function useComposerState(sessionId: string | null) {
  const params = useSearchParams();
  const draftParam = params.get('draft') ?? '';
  const chat = params.get('s');
  const [draft, setDraft] = useState(draftParam);
  const [mode, setMode] = useState<HomeModeId>(DEFAULT_MODE);
  const [seen, setSeen] = useState({ chat, draftParam, sessionId, arrivals: draftParam ? 1 : 0 });
  if (chat !== seen.chat || draftParam !== seen.draftParam || sessionId !== seen.sessionId) {
    const arrived = draftParam !== '' && draftParam !== seen.draftParam;
    setSeen({ chat, draftParam, sessionId, arrivals: seen.arrivals + (arrived ? 1 : 0) });
    const sameChat = chat === seen.chat || (sessionId !== null && sessionId === seen.sessionId);
    if (draftParam || !sameChat) setDraft(draftParam);
    if (!sameChat) setMode(DEFAULT_MODE);
  }
  const shown = useRef(seen);
  useEffect(() => {
    shown.current = seen;
  });
  /** A refused send's text goes back to the chat that sent it, never over newer text. */
  const giveBack = (text: string) => {
    const from = shown.current;
    return () => {
      const at = shown.current;
      if (at.chat === from.chat && at.sessionId === from.sessionId)
        setDraft((now) => (now === '' ? text : now));
    };
  };
  return { draft, setDraft, mode, setMode, draftParam, draftArrivals: seen.arrivals, giveBack };
}

export type ComposerText = ReturnType<typeof useComposerState>;

const Ctx = createContext<ComposerText | null>(null);

/** Above `ChatScroll`, whose thread remounts when it empties (a refused first send): the text and
 * the mode outlive that. A keystroke renders only this provider's readers, not the chat around. */
export function ComposerTextProvider({
  sessionId,
  children,
}: {
  sessionId: string | null;
  children: ReactNode;
}) {
  return <Ctx.Provider value={useComposerState(sessionId)}>{children}</Ctx.Provider>;
}

export function useComposerText(): ComposerText {
  const value = useContext(Ctx);
  if (!value) throw new Error('useComposerText outside ComposerTextProvider');
  return value;
}
