import { headers } from 'next/headers';
import { redirect } from 'next/navigation';
import { auth } from '@/lib/auth';
import { signInPath } from '../sign-in/params';

// #762: the app layout sends a dead session here because a server component cannot set cookies. Asking
// Better Auth for the session expires the dead session cookie, which cuts the ban marker to 10 minutes.
export async function GET(request: Request) {
  await auth.api.getSession({ headers: await headers() });
  redirect(signInPath(new URL(request.url).searchParams.get('callbackUrl') ?? '/', true));
}
