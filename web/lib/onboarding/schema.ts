import { z } from 'zod';

const lines = z.array(z.string());

export const profileSchema = z.looseObject({
  companyName: z.string(),
  summary: z.string(),
  personas: lines,
  markets: lines,
  painPoints: lines,
  solution: z.string(),
  buyingTriggers: lines,
  exclusions: lines,
  proof: z.string(),
});

export const segmentSchema = z.looseObject({
  name: z.string(),
  who: z.string(),
  problem: z.string(),
  outcome: z.string(),
  signalHint: z.string().optional(),
  criteria: z.looseObject({
    titles: lines,
    seniorities: lines,
    industries: lines,
    employeeRanges: lines,
    locations: lines,
    keywords: lines,
  }),
  // The language the API wrote it in; null on segments stored before it was recorded.
  locale: z.string().nullish(),
});

export const stateSchema = z.looseObject({
  needsOnboarding: z.boolean(),
  website: z.string().nullable(),
  description: z.string().nullable(),
  profile: profileSchema.nullable(),
  segments: z.array(segmentSchema),
});

export type BusinessProfile = z.infer<typeof profileSchema>;
export type Segment = z.infer<typeof segmentSchema>;
export type OnboardingState = z.infer<typeof stateSchema>;

/** True when a segment was written in another language than the one the user sees (#587). */
export function hasStaleSegments(segments: { locale?: string | null }[], locale: string): boolean {
  return segments.some((s) => s.locale && s.locale !== locale);
}
