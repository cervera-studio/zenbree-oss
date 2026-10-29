import { describe, expect, it } from 'vite-plus/test';
import { regionName } from './region-name';

describe('regionName', () => {
  it('names a country the API stores in English in the UI language', () => {
    expect(['Italy', 'Spain', 'Germany'].map((v) => regionName(v, 'it'))).toEqual([
      'Italia',
      'Spagna',
      'Germania',
    ]);
    expect(regionName('Germany', 'fr')).toBe('Allemagne');
  });

  it('accepts other spellings and any case', () => {
    expect(regionName('USA', 'it')).toBe('Stati Uniti');
    expect(regionName(' united kingdom ', 'it')).toBe('Regno Unito');
  });

  it('keeps a value it does not know as written', () => {
    expect(regionName('Rome, Italy', 'it')).toBe('Rome, Italy');
    expect(regionName('DACH', 'it')).toBe('DACH');
    expect(regionName('Europe', 'it')).toBe('Europe');
  });
});
