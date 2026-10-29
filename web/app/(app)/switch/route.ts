import { redirect } from 'next/navigation';
import { safeHref } from '@/lib/notifications/group';
import { switchOrg } from '@/lib/org';

// The link of every notification (#743): `/switch?org=<id>&next=<path>` makes that organization the active
// one, then opens `next`. `safeHref` keeps `next` on this origin, so the route is no open redirect.
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  await switchOrg(request, params.get('org'));
  redirect(safeHref(params.get('next') ?? '/'));
}
