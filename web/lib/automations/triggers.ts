import type { Schema } from '@/lib/api/schema';

export type TriggerKind = Schema<'Automation'>['trigger_kind'];

/** Every trigger of the API, each by its own key: typecheck fails here when the API adds one. */
export const TRIGGER_KINDS: { readonly [K in TriggerKind]: K } = {
  manual: 'manual',
  schedule: 'schedule',
  webhook: 'webhook',
  signal: 'signal',
};
