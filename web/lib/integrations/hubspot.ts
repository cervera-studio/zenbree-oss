import 'server-only';

import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { hubspotStatusSchema, mailboxConnectSchema, timelineChangedSchema } from './schemas';

const BASE = '/v1/integrations/hubspot';

export async function getHubSpot() {
  return httpClient.get(BASE, { token: await getToken(), schema: hubspotStatusSchema });
}

export async function connectHubSpot() {
  return httpClient.post(`${BASE}/connect`, undefined, {
    token: await getToken(),
    schema: mailboxConnectSchema,
  });
}

export async function setHubSpotWriteBack(enabled: boolean) {
  return httpClient.put(
    `${BASE}/write-back`,
    { enabled },
    { token: await getToken(), schema: hubspotStatusSchema },
  );
}

export async function retryHubSpotTimeline(code: string) {
  return httpClient.post(
    `${BASE}/timeline/retry`,
    { code },
    { token: await getToken(), schema: timelineChangedSchema },
  );
}

export async function resolveHubSpotTimeline(code: string, as: 'log_again' | 'logged') {
  return httpClient.post(
    `${BASE}/timeline/resolve`,
    { code, as },
    { token: await getToken(), schema: timelineChangedSchema },
  );
}

export async function disconnectHubSpot() {
  return httpClient.delete(BASE, { token: await getToken() });
}

// Finishes the OAuth exchange with the user's own JWT: the API refuses a state minted for anyone else.
export async function completeHubSpot(code: string, state: string) {
  return httpClient.post(`${BASE}/complete`, { code, state }, { token: await getToken() });
}
