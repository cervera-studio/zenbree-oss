import 'server-only';

import { getToken } from '@/lib/auth-token';
import { httpClient } from '@/lib/http-client';
import { linkedInConnectSchema, linkedInStatusSchema } from './schemas';

const BASE = '/v1/integrations/linkedin';

export async function getLinkedIn(project?: string) {
  return httpClient.get(BASE, { token: await getToken(), project, schema: linkedInStatusSchema });
}

// `riskAccepted` → `risk_accepted`: the user ticked the ban-risk notice.
export async function connectLinkedIn(credentials?: { username: string; password: string }) {
  return httpClient.post(
    `${BASE}/connect`,
    { riskAccepted: true, ...credentials },
    { token: await getToken(), schema: linkedInConnectSchema },
  );
}

export async function solveLinkedInCheckpoint(accountId: string, code: string) {
  return httpClient.post(
    `${BASE}/checkpoint`,
    { accountId, code, riskAccepted: true },
    { token: await getToken(), schema: linkedInConnectSchema },
  );
}

export async function disconnectLinkedIn() {
  return httpClient.delete(BASE, { token: await getToken() });
}
