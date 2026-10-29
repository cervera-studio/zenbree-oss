// The 404 page names what was missing and points to where to go instead.
const RESOURCES = [
  ['/lists', 'list', '/lists'],
  ['/campaigns', 'campaign', '/campaigns'],
  ['/chat', 'chat', '/'],
] as const;

export type MissingResource = { kind: 'list' | 'campaign' | 'chat' | 'page'; href: string };

export function missingResource(pathname: string): MissingResource {
  for (const [prefix, kind, href] of RESOURCES) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return { kind, href };
  }
  return { kind: 'page', href: '/' };
}

// What the 404 card offers. The new chat is not here: the primary button or the secondary
// «new chat» button already goes to `/`.
const CARD_DESTINATIONS = [
  { kind: 'list', href: '/lists' },
  { kind: 'campaign', href: '/campaigns' },
] as const;

/** The card's places, minus the one the primary button already covers. */
export function otherDestinations(primaryHref: string) {
  return CARD_DESTINATIONS.filter((destination) => destination.href !== primaryHref);
}
