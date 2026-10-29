import Link from 'next/link';
import type { ComponentProps, MouseEvent } from 'react';

// A switch link changes the session and its redirect drops a fragment when the router follows it, so it is a
// full navigation: no prefetch, the `#reply` kept, and the new organization's state loaded fresh.
const SWITCH = /^\/(billing\/)?switch(\?|$)/;

const MARK_READ_WAIT_MS = 1500;

type Props = Omit<ComponentProps<'a'>, 'onClick'> & {
  href: string;
  /** May return a promise: a plain left click waits for it before navigating (a mark-read in flight). */
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => unknown;
};

export function NotificationLink({ href, onClick, ...props }: Props) {
  if (!SWITCH.test(href)) return <Link href={href} onClick={onClick} {...props} />;
  const onSwitchClick = (event: MouseEvent<HTMLAnchorElement>) => {
    const pending = onClick?.(event);
    const plain =
      event.button === 0 && !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey);
    if (!plain || !(pending instanceof Promise)) return;
    // A full navigation tears the page down before a request that is still starting goes out.
    event.preventDefault();
    const go = () => window.location.assign(href);
    // A failed or hung mark-read still navigates: the race settles once, so `go` runs once.
    void Promise.race([
      pending,
      new Promise((resolve) => setTimeout(resolve, MARK_READ_WAIT_MS)),
    ]).then(go, go);
  };
  return <a href={href} onClick={onSwitchClick} {...props} />;
}
