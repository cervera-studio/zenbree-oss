import { describe, expect, it } from 'vite-plus/test';
import { visiblePhase } from './hidden-tools';

describe('visiblePhase', () => {
  it('hides the phase of a tool the user never sees and keeps any other', () => {
    expect(visiblePhase('set_brief')).toBeNull();
    expect(visiblePhase('ask_user')).toBeNull();
    expect(visiblePhase(null)).toBeNull();
    expect(visiblePhase('search_companies')).toBe('search_companies');
  });
});
