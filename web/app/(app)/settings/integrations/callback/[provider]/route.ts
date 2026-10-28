import { type NextRequest, NextResponse } from 'next/server';
import { completeHubSpot } from '@/lib/integrations/hubspot';
import { completeMailbox } from '@/lib/integrations/mailboxes';
import { MAILBOX_PROVIDERS, type MailboxProvider } from '@/lib/integrations/schemas';

// The API's OAuth callback bounces the provider's code/state here; completing it needs this session.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ provider: string }> },
) {
  const { provider } = await params;
  const query = request.nextUrl.searchParams;
  // The public origin: behind the tunnel request.url is the container's own address.
  const back = new URL(
    '/settings/integrations',
    process.env.BETTER_AUTH_URL ?? request.nextUrl.origin,
  );
  back.searchParams.set('provider', provider);
  const isHubSpot = provider === 'hubspot';
  // ?hubspot= or ?mailbox= tells the page which card the outcome belongs to.
  const outcome = isHubSpot ? 'hubspot' : 'mailbox';
  const code = query.get('code');
  const state = query.get('state');
  const known = isHubSpot || (MAILBOX_PROVIDERS as readonly string[]).includes(provider);
  if (!known || !code || !state) {
    back.searchParams.set(outcome, 'failed');
    back.searchParams.set('reason', query.get('error') ? 'DECLINED' : 'INVALID_CALLBACK');
    return NextResponse.redirect(back);
  }
  const result = isHubSpot
    ? await completeHubSpot(code, state)
    : await completeMailbox(provider as MailboxProvider, code, state);
  back.searchParams.set(outcome, result.success ? 'connected' : 'failed');
  if (!result.success) back.searchParams.set('reason', result.error.code);
  return NextResponse.redirect(back);
}
