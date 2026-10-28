import { describe, expect, it } from 'vite-plus/test';
import { isUuid, safeRandomUUID } from './uuid';

describe('isUuid', () => {
  it('accepts a generated and an upper-case uuid', () => {
    expect(isUuid(safeRandomUUID())).toBe(true);
    expect(isUuid('6F1C2B3A-0D4E-4F5A-8B6C-7D8E9F0A1B2C')).toBe(true);
  });

  it('rejects what the API would answer 422 for', () => {
    for (const id of [
      'nope',
      '',
      '6f1c2b3a-0d4e-4f5a-8b6c-7d8e9f0a1b2',
      '6f1c2b3a-0d4e-4f5a-8b6c-7d8e9f0a1b2c/x',
    ]) {
      expect(isUuid(id)).toBe(false);
    }
  });
});
