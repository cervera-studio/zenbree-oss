import { useSyncExternalStore } from 'react';

/** The phone layout's one breakpoint (#171). The number mirrors `@custom-variant phone` in `app/globals.css`. */
const PHONE = '(max-width: 759px)';

function subscribe(onChange: () => void) {
  const query = window.matchMedia(PHONE);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

/** For layouts CSS can't swap (a table vs cards). Server render and hydration assume desktop. */
export function usePhone() {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(PHONE).matches,
    () => false,
  );
}
