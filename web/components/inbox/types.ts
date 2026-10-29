import type { getInbox, getThread } from '@/lib/inbox/api';

export type Message = Extract<
  Awaited<ReturnType<typeof getInbox>>,
  { success: true }
>['data']['messages'][number];
export type ThreadData = Extract<Awaited<ReturnType<typeof getThread>>, { success: true }>['data'];

export const personName = (m: Message) =>
  m.fromName || [m.targetFirstName, m.targetLastName].filter(Boolean).join(' ') || m.fromAddress;
