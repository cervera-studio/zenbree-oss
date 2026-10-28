import fc from 'fast-check';
import { describe, expect, it } from 'vite-plus/test';
import { toMinFit, toRowTab, viewHref } from './href';

describe('viewHref', () => {
  it('carries the tab and the search text', () => {
    expect(viewHref({ listId: 'l1', tab: 'excluded', query: 'a b&c', minFit: null })).toBe(
      '/lists/l1?tab=excluded&q=a+b%26c',
    );
  });

  it('carries the fit floor, even a floor of 0', () => {
    expect(viewHref({ listId: 'l1', tab: 'all', query: '', minFit: 80 })).toBe('/lists/l1?fit=80');
    expect(viewHref({ listId: 'l1', tab: 'all', query: '', minFit: 0 })).toBe('/lists/l1?fit=0');
  });
});

describe('toMinFit', () => {
  it('reads a whole floor from 0 to 100 and nothing else', () => {
    expect(toMinFit('80')).toBe(80);
    expect(toMinFit('0')).toBe(0);
    expect(toMinFit('100')).toBe(100);
    expect(toMinFit(['50', '80'])).toBe(50);
    for (const bad of ['abc', '101', '8.5', '-1', '', ' 80', undefined])
      expect(toMinFit(bad)).toBeNull();
  });

  it('reads back the floor that viewHref writes', () => {
    const minFit = fc.option(fc.integer({ min: 0, max: 100 }), { nil: null });
    fc.assert(
      fc.property(minFit, (floor) => {
        const href = viewHref({ listId: 'l1', tab: 'all', query: '', minFit: floor });
        const fit = new URL(href, 'http://x').searchParams.get('fit') ?? undefined;
        expect(toMinFit(fit)).toBe(floor);
      }),
    );
  });
});

describe('toRowTab', () => {
  it('falls back to all', () => {
    expect(toRowTab('pending')).toBe('pending');
    expect(toRowTab('nope')).toBe('all');
    expect(toRowTab(undefined)).toBe('all');
  });
});
