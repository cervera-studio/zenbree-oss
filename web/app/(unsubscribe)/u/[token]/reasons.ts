// The page's optional «Vuoi dirci perché?» (#220); the API accepts exactly these.
export const REASONS = ['not_interested', 'wrong_person', 'too_many', 'other'] as const;
export type Reason = (typeof REASONS)[number];
