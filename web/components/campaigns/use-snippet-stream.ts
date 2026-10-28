import { useRouter } from 'next/navigation';
import { type Dispatch, useEffect } from 'react';
import { errorFrameCode, followStream } from '@/lib/api/event-stream';
import type { SnippetAction, SnippetState } from '@/lib/campaigns/snippet';
import { toEvent } from '@/lib/lists/fill-stream';

const NEW_COLUMN_REPLAY_FROM = '0-0';

export function useSnippetStream(state: SnippetState, dispatch: Dispatch<SnippetAction>) {
  const router = useRouter();
  const refreshFieldCatalog = router.refresh;
  const listId = state.phase === 'ready' ? state.setup.listId : null;
  const key = state.phase === 'ready' && state.run.status === 'filling' ? state.run.key : null;
  useEffect(() => {
    if (!listId || !key) return;
    const ctrl = new AbortController();
    void followStream(
      `/v1/lists/${encodeURIComponent(listId)}/stream`,
      NEW_COLUMN_REPLAY_FROM,
      (frame) => {
        if (frame.event === 'error') {
          return dispatch({ type: 'streamFailed', code: errorFrameCode(frame) });
        }
        const event = toEvent(frame);
        if (!event) return;
        dispatch({ type: 'frame', event });
        const ended = event.type === 'fill' && event.key === key;
        if (ended && (event.fill.status === 'done' || event.fill.status === 'failed')) {
          refreshFieldCatalog();
        }
      },
      ctrl.signal,
    );
    return () => ctrl.abort();
  }, [listId, key, dispatch, refreshFieldCatalog]);
}
