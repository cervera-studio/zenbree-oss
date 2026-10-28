// The campaign builder's activation check (C2b, #217), apart from the component so it can be tested.
import type { CampaignSender } from './senders';
import { hasCopyB, hasLinkedIn, type Step, toApiSteps } from './steps';

type Blocker = 'noSteps' | 'noTargets' | 'noMailbox' | 'pickMailbox' | 'noLinkedIn' | 'noCopyB';

/** What `/activate` would refuse, said before the click (C2b). The API still checks all of it. */
export function blockers(
  steps: Step[],
  senders: CampaignSender[] | null,
  linkedIn: boolean,
  targetCount: number,
  abTest = false,
): Blocker[] {
  const out: Blocker[] = [];
  if (steps.length === 0) out.push('noSteps');
  if (targetCount === 0) out.push('noTargets');
  const emails = steps.length === 0 || steps.some((s) => s.channel === 'email');
  // Unknown (the list failed to load): leave it to the API rather than block on a guess.
  if (senders && emails) {
    if (!senders.some((s) => s.status === 'connected')) out.push('noMailbox');
    else if (steps.length && !senders.some((s) => s.picked && s.blocker === null)) {
      out.push('pickMailbox');
    }
  }
  if (hasLinkedIn(steps) && !linkedIn) out.push('noLinkedIn');
  // What the save sends: the API would launch it without a test, and the switch says one was meant.
  if (abTest && steps.length > 0 && !hasCopyB(toApiSteps(steps))) out.push('noCopyB');
  return out;
}

export function rewriteHref(chatSessionId: string, prompt: string): string {
  return `/?s=${encodeURIComponent(chatSessionId)}&draft=${encodeURIComponent(prompt)}`;
}
