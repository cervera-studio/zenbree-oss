import { toast } from '@/components/ui/sonner';
import { advanced } from './chat-turn';
import type { ChatState, UseChatLabels } from './stream-store-types';

/**
 * «Ferma»: `POST /v1/jobs/{id}/cancel` for this turn's job. The job closes the stream itself
 * (`error` `cancelled`, `message_saved`, `done`); any other answer puts the turn back.
 */
export function askStop(
  cancelJob: (jobId: string) => Promise<string | null>,
  setChat: (updater: (prev: ChatState) => ChatState) => void,
  jobId: string,
  labels?: UseChatLabels,
): void {
  void cancelJob(jobId).then((status) => {
    if (status === 'cancelling') return;
    // failed, or the job had already ended: its own `done` closes the turn
    setChat((prev) => advanced(prev, { type: 'stopFailed', jobId }));
    if (status === null && labels) toast.error(labels.stopFailed);
  });
}
