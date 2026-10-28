import 'server-only';

import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import {
  type MailboxProvider,
  mailboxConnectSchema,
  mailboxesSchema,
  senderAccountsSchema,
} from './schemas';
import { senderStateSchema } from './sender-state';

const BASE = '/v1/integrations/mailboxes';

export async function getMailboxes(project?: string) {
  return httpClient.get(BASE, { token: await getToken(), project, schema: mailboxesSchema });
}

export async function connectMailbox(provider: MailboxProvider) {
  return httpClient.post(`${BASE}/${provider}/connect`, undefined, {
    token: await getToken(),
    schema: mailboxConnectSchema,
  });
}

export async function disconnectMailbox(senderId: string) {
  return httpClient.delete(`${BASE}/${encodeURIComponent(senderId)}`, { token: await getToken() });
}

// Finishes the OAuth exchange with the user's own JWT: the API refuses a state minted for anyone else.
export async function completeMailbox(provider: MailboxProvider, code: string, state: string) {
  return httpClient.post(
    `${BASE}/${provider}/complete`,
    { code, state },
    { token: await getToken() },
  );
}

export async function getSenders() {
  return httpClient.get('/v1/integrations/senders', {
    token: await getToken(),
    schema: senderStateSchema,
  });
}

export async function setSenderPaused(senderId: string, paused: boolean) {
  return httpClient.patch(
    `/v1/integrations/senders/${encodeURIComponent(senderId)}`,
    { paused },
    { token: await getToken() },
  );
}

export async function setSenderWindow(
  senderId: string,
  window: { startHour: number; endHour: number; days: number[] },
) {
  return httpClient.put(
    `/v1/integrations/senders/${encodeURIComponent(senderId)}/window`,
    {
      sendWindowStartHour: window.startHour,
      sendWindowEndHour: window.endHour,
      sendWindowDays: window.days,
    },
    { token: await getToken() },
  );
}

export async function getSenderAccounts() {
  return httpClient.get('/v1/integrations/senders/accounts', {
    token: await getToken(),
    schema: senderAccountsSchema,
  });
}

/** Link (or unlink) one of the organization's accounts to a client (#327). */
export async function setSenderLink(senderId: string, projectId: string, linked: boolean) {
  const path = `/v1/integrations/senders/accounts/${encodeURIComponent(senderId)}/projects/${encodeURIComponent(projectId)}`;
  const token = await getToken();
  return linked ? httpClient.put(path, undefined, { token }) : httpClient.delete(path, { token });
}
