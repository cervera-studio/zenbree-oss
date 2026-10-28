import type { HubSpotStatus } from '@/lib/integrations/schemas';

/** The block shows with the switch on, and whenever there is something to read: a grant in error or a
 * disconnect leaves rows waiting, and the line that says to reconnect is for exactly that user. A plain module,
 * not the client one: the server card calls it, and a server component cannot call a client export. */
export function showsTimeline(status: HubSpotStatus): boolean {
  const { logged, pending, issues } = status.timeline;
  return status.writeBack || logged > 0 || pending > 0 || issues.length > 0;
}
