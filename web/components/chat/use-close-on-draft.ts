import { useSearchParams } from 'next/navigation';
import { useState } from 'react';

/** Where the list panel sits beside the chat: `lg:` on the panel in `chat-frame.tsx`. */
const BESIDE_CHAT = '(min-width: 1024px)';

/** A `?draft=` (the panel's «Chiedi in chat», or a link from another page) fills the composer. Narrower than `lg` the
 * panel covers it, so `close` runs, also for a draft already in the URL at mount. */
export function useCloseOnDraft(close: () => void) {
  const draft = useSearchParams().get('draft') ?? '';
  const [seen, setSeen] = useState('');
  if (draft !== seen) {
    setSeen(draft);
    // the server render has no screen to measure; the client render runs this again
    if (draft && typeof window !== 'undefined' && !window.matchMedia(BESIDE_CHAT).matches) close();
  }
}
