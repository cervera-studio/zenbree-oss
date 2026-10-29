import { describe, expect, it } from 'vite-plus/test';
import {
  blankFallbacks,
  type CampaignField,
  type CatalogEntry,
  entryOf,
  type FieldCatalog,
  toApiFields,
  usesToken,
  withField,
} from './fields';
import { blankStep } from './steps';

const entry = (key: string, token = key): CatalogEntry => ({
  token,
  source: { kind: 'column', key },
  label: key.toUpperCase(),
  ai: false,
  filled: 3,
});
const catalog: FieldCatalog = {
  people: 5,
  contactTokens: ['firstName'],
  columns: [entry('city')],
  signal: [
    { ...entry('title', 'signalTitle'), source: { kind: 'signal', key: 'title' }, label: null },
  ],
};

describe('withField', () => {
  it('adds a picked entry as a required field with the catalog token', () => {
    expect(withField([], entry('city'))).toEqual({
      fields: [{ token: 'city', source: { kind: 'column', key: 'city' }, fallback: null }],
      token: 'city',
    });
  });

  it('picks the same source twice into one field, with its saved token', () => {
    const saved: CampaignField[] = [
      { token: 'town', source: { kind: 'column', key: 'city' }, fallback: 'your city' },
    ];
    const again = withField(saved, entry('city'));
    expect(again.fields).toBe(saved);
    expect(again.token).toBe('town');
  });

  it('numbers a token another source already holds', () => {
    const taken: CampaignField[] = [{ token: 'city', source: { kind: 'column', key: 'town' } }];
    expect(withField(taken, entry('city')).token).toBe('city2');
  });
});

describe('a saved field', () => {
  it('finds its catalog entry by source, and has none when the list lost the column', () => {
    expect(entryOf({ source: { kind: 'signal', key: 'title' } }, catalog)?.token).toBe(
      'signalTitle',
    );
    expect(entryOf({ source: { kind: 'column', key: 'gone' } }, catalog)).toBeUndefined();
    expect(entryOf({ source: { kind: 'column', key: 'city' } }, null)).toBeUndefined();
  });

  it('is in use while a subject or a message holds its token', () => {
    const steps = [{ ...blankStep(), subject: 'From {{city}}' }];
    expect(usesToken(steps, 'city')).toBe(true);
    expect(usesToken(steps, 'cit')).toBe(false);
  });
});

describe('fields for the API', () => {
  const field = (fallback?: string | null): CampaignField => ({
    token: 'city',
    source: { kind: 'column', key: 'city', extra: 1 },
    fallback,
    extra: 'x',
  });

  it('trims the fallback, sends a required field as null and drops unknown keys', () => {
    expect(toApiFields([field('  your city '), field(undefined), field('')])).toEqual([
      { token: 'city', source: { kind: 'column', key: 'city' }, fallback: 'your city' },
      { token: 'city', source: { kind: 'column', key: 'city' }, fallback: null },
      { token: 'city', source: { kind: 'column', key: 'city' }, fallback: null },
    ]);
  });

  it('names a field set to fallback whose text is still blank', () => {
    expect(blankFallbacks([field('  '), field(null), field('x')])).toEqual(['city']);
  });
});
