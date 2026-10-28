import { z } from 'zod';
import { LABELS } from '@/lib/inbox/api';

export type Filters = {
  label?: string;
  channel?: string;
  unread?: string;
  kind?: string;
  campaign?: string;
  sort?: string;
  offset?: string;
  m?: string;
};
export type Clean = ReturnType<typeof clean>;

// Unknown values are dropped rather than sent: the API would answer 422.
export function clean(sp: Filters) {
  const label = sp.label === 'none' || LABELS.some((l) => l === sp.label) ? sp.label : undefined;
  const channel = sp.channel === 'email' || sp.channel === 'linkedin' ? sp.channel : undefined;
  return {
    label,
    channel,
    unread: sp.unread === '1' ? '1' : undefined,
    kind: sp.kind === 'unsubscribe' ? sp.kind : undefined,
    campaign: z.uuid().safeParse(sp.campaign).success ? sp.campaign : undefined,
    // Newest first is the API's default, so the URL names only the other order.
    sort: sp.sort === 'oldest' ? sp.sort : undefined,
    offset: Math.max(0, Number.parseInt(sp.offset ?? '', 10) || 0),
  };
}

export function href(filters: {
  label?: string;
  channel?: string;
  unread?: string;
  kind?: string;
  campaign?: string;
  sort?: string;
  offset?: number;
  m?: string;
}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) q.set(k, String(v));
  const s = q.toString();
  return s ? `/inbox?${s}` : '/inbox';
}
