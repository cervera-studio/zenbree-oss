import { describe, expect, it } from 'vite-plus/test';
import { parseCampaign } from './campaign';

describe('parseCampaign', () => {
  it.each([
    undefined,
    null,
    'x',
    {},
    { id: 'c1', name: 'n' },
    { id: '', name: 'n', targetCount: 1 },
  ])('drops a malformed value: %j', (raw) => {
    expect(parseCampaign(raw)).toBeUndefined();
  });
});
