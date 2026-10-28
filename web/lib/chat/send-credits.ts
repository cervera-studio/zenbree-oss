import { toast } from '@/components/ui/sonner';
import type { GlobalState, UseChatLabels } from './stream-store-types';

/** False (with a toast, and `onRejected` so the caller gets the text back) when the balance is
 * spent; warns when it runs low. */
export function mayStartTurn(
  { credits, maxCredits }: GlobalState,
  labels: UseChatLabels,
  onRejected?: () => void,
): boolean {
  if (credits !== null && credits <= 0) {
    toast.error(labels.creditsExhausted);
    onRejected?.();
    return false;
  }
  const lowThreshold = maxCredits !== null ? Math.max(3, Math.ceil(maxCredits * 0.2)) : 3;
  if (credits !== null && credits <= lowThreshold) toast.warning(labels.creditsLow);
  return true;
}
