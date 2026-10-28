import { sentinelClient } from '@better-auth/infra/client';
import { adminClient, jwtClient, organizationClient } from 'better-auth/client/plugins';
import { createAuthClient } from 'better-auth/react';
import { LAST_EMAIL_KEY } from '@/app/auth/sign-in/params';
import { disablePush } from '@/lib/notifications/push';
import { removeStored } from '@/lib/storage/storage';

// Same-origin: no baseURL needed.
export const authClient = createAuthClient({
  plugins: [
    adminClient(),
    jwtClient(),
    organizationClient(),
    sentinelClient({ identifyUrl: process.env.BETTER_AUTH_IDENTIFY_URL }),
  ],
});

// Shared computers: an explicit sign-out forgets the email prefilled on the expired screen, and this
// browser's push subscription (before the session ends, so the API can remove it), or the next person
// at this computer would see the previous user's alerts.
export const signOut = () =>
  disablePush()
    .then(() => authClient.signOut())
    .then(() => removeStored(LAST_EMAIL_KEY, 'local'));
