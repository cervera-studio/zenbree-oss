import { redirect } from 'next/navigation';
import { switchOrg } from '@/lib/org';

// The link of a low-credit or payment notice (#323): `/billing/switch?org=<id>` makes that organization the
// active one, as the account menu's switcher does, then opens its billing. Only one the user belongs to.
export async function GET(request: Request) {
  await switchOrg(request, new URL(request.url).searchParams.get('org'));
  redirect('/billing');
}
