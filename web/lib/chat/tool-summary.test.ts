import { describe, expect, it } from 'vite-plus/test';
import summaryFound from '@/lib/api/fixtures/summary-email-found.json';
import summaryNoRowMatch from '@/lib/api/fixtures/summary-email-no-row-match.json';
import summaryRefunded from '@/lib/api/fixtures/summary-email-refunded.json';
import summaryPhoneNothing from '@/lib/api/fixtures/summary-phone-nothing-to-search.json';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { parseToolSummary, summaryView, type ToolSummary } from './tool-summary';

/** What the web reads: the API's JSON, camelCased as every response is. */
function read(wire: unknown): ToolSummary {
  const summary = parseToolSummary(toCamelCase(wire));
  if (!summary) throw new Error('a fixture of the API does not parse');
  return summary;
}

const key = (wire: unknown) => summaryView(read(wire)).parts.map((p) => p.key);

describe('summaryView state', () => {
  // The kinds no chip test reaches; tool-summary-card.test.tsx owns the state of the others.
  const rows: { kind: string; wire: unknown; state: string }[] = [
    { kind: 'count', wire: { kind: 'count', noun: 'company', count: 3 }, state: 'done' },
    { kind: 'all_found', wire: { ...summaryFound, kind: 'all_found' }, state: 'done' },
    {
      kind: 'legacy',
      wire: { kind: 'legacy', contact: 'email', found: 4, failed: 0 },
      state: 'done',
    },
  ];

  it.each(rows)('reads $kind as $state', ({ kind, wire, state }) => {
    const view = summaryView(read(wire));
    expect(read(wire).kind).toBe(kind);
    expect(view.state).toBe(state);
  });
});

describe('summaryView parts', () => {
  it('appends the rows the turn took back, for a searched batch and an old row', () => {
    expect(key(summaryRefunded).at(-1)).toBe('email.refunded');
    expect(
      summaryView(read({ kind: 'legacy', contact: 'email', found: 2, failed: 1, refunded: 1 }))
        .parts,
    ).toEqual([
      { key: 'email.found', n: 2 },
      { key: 'email.failed', n: 1 },
      { key: 'email.refunded', n: 1 },
    ]);
  });

  it('reads nothing to search and no matching row without a count', () => {
    expect(summaryView(read(summaryPhoneNothing)).parts).toEqual([
      { key: 'nothingToSearch' },
      { key: 'phone.unmatched', n: 1 },
    ]);
    expect(summaryView(read(summaryNoRowMatch)).parts).toEqual([{ key: 'noRowMatch' }]);
  });
});

describe('parseToolSummary', () => {
  it('defaults the counts an older worker leaves out', () => {
    expect(parseToolSummary({ kind: 'nothing_to_search', contact: 'email' })).toEqual({
      kind: 'nothing_to_search',
      contact: 'email',
      unmatched: 0,
    });
  });
});
