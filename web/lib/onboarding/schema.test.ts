import { describe, expect, it } from 'vite-plus/test';
import { hasStaleSegments, type Segment, segmentSchema } from './schema';

const criteria = {
  titles: [],
  seniorities: [],
  industries: [],
  employeeRanges: [],
  locations: [],
  keywords: [],
};
const segment = (locale?: string | null): Segment => ({
  name: 'Agenzie',
  who: 'Founder',
  problem: 'p',
  outcome: 'o',
  criteria,
  locale,
});

describe('segmentSchema', () => {
  it('accepts a segment stored before its language was recorded, which the API sends as null', () => {
    expect(segmentSchema.safeParse({ ...segment(), locale: null }).success).toBe(true);
  });
});

describe('hasStaleSegments', () => {
  it('is false for segments written in the language the user sees', () => {
    expect(hasStaleSegments([segment('en')], 'en')).toBe(false);
  });

  it('is true when a segment was written in another language', () => {
    expect(hasStaleSegments([segment('it')], 'en')).toBe(true);
  });

  it('is false for segments stored before the language was recorded', () => {
    expect(hasStaleSegments([segment(null), segment()], 'en')).toBe(false);
  });
});
