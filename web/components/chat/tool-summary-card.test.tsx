import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import summaryAllFailed from '@/lib/api/fixtures/summary-email-all-failed.json';
import summaryFoundAndFailed from '@/lib/api/fixtures/summary-email-found-and-failed.json';
import summaryNoRowMatch from '@/lib/api/fixtures/summary-email-no-row-match.json';
import summaryNoSource from '@/lib/api/fixtures/summary-email-no-source.json';
import summaryNoneFound from '@/lib/api/fixtures/summary-email-none-found.json';
import summaryNothingToSearch from '@/lib/api/fixtures/summary-email-nothing-to-search.json';
import summarySomeFailed from '@/lib/api/fixtures/summary-email-some-failed.json';
import { parseToolSummary } from '@/lib/chat/tool-summary';
import type { ToolCallStatus } from '@/lib/chat/types';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { SummaryLine } from './summary-line';
import { ToolSummaryCard } from './tool-summary-card';

function render(summary: unknown, status: ToolCallStatus = 'done') {
  return renderToStaticMarkup(
    <TestIntl>
      <ToolSummaryCard
        call={{
          id: 't1',
          name: 'enrich_email',
          input: {},
          status,
          summary: parseToolSummary(toCamelCase(summary)),
        }}
      />
    </TestIntl>,
  );
}

const name = (html: string) => /aria-label="([^"]*)"/.exec(html)?.[1];

describe('ToolSummaryCard', () => {
  it('reads a batch whose lookups all failed as lookups to retry, not «0 email»', () => {
    const html = render(summaryAllFailed);
    expect(html).toContain('2 ricerche non riuscite');
    expect(html).not.toContain('0 email');
    expect(name(html)).toBe('Da riprovare');
    expect(html).not.toContain('lucide-check');
  });

  it('reads a batch with no active source as a warning with its own text, never «0 email» (#530)', () => {
    const html = render(summaryNoSource);
    expect(name(html)).toBe('Da riprovare');
    expect(html).toContain('2 ricerche non riuscite · nessuna fonte attiva');
    expect(html).not.toContain('0 email');
    expect(html).not.toContain('da riprovare');
  });

  it('names both what it found and what failed in a partial batch, with the check', () => {
    const html = render(summaryFoundAndFailed);
    expect(html).toContain('2 email · 1 non valida · 1 non trovata · 1 da riprovare');
    expect(name(html)).toBe('Fatto');
  });

  it('keeps «0 email» for a batch that searched and found none, in the neutral state', () => {
    const html = render(summaryNoneFound);
    expect(html).toContain('0 email · 1 non trovata');
    expect(name(html)).toBe('Nessun risultato');
    expect(render(summarySomeFailed)).toContain('0 email · 1 non trovata · 1 da riprovare');
  });

  it('reads a row saved before #530 as «N email», and never as «not found»', () => {
    const legacy = { kind: 'legacy', contact: 'email', found: 4 };
    expect(render(legacy)).toContain('4 email');
    expect(render({ ...legacy, contact: 'phone', found: 1 })).not.toContain('non trovat');
  });

  it('adds the rows to retry that an old row stored', () => {
    const text = render({ kind: 'legacy', contact: 'email', found: 1, failed: 2 });
    expect(text).toContain('1 email');
    expect(text).toContain('2 da riprovare');
  });

  it('says «Nessuno da cercare» in the neutral state', () => {
    const html = render(summaryNothingToSearch);
    expect(html).toContain('Nessuno da cercare');
    expect(name(html)).toBe('Nessun risultato');
  });

  it('reads ids that match no row as an error with its own text, although it is no search (#550)', () => {
    const html = render(summaryNoRowMatch, 'error');
    expect(name(html)).toBe('Errore');
    expect(html).toContain('Nessuna riga corrisponde');
    expect(html).not.toContain('0 email');
  });

  it('keeps the plain states of a call with no summary', () => {
    expect(name(render(undefined, 'running'))).toBe('In corso');
    expect(name(render(undefined, 'done'))).toBe('Fatto');
    expect(name(render(undefined, 'error'))).toBe('Errore');
  });
});

describe('the admin transcript line', () => {
  function line(summary: unknown) {
    const parsed = parseToolSummary(toCamelCase(summary));
    if (!parsed) throw new Error('not a summary');
    return renderToStaticMarkup(
      <TestIntl>
        <SummaryLine summary={parsed} />
      </TestIntl>,
    );
  }

  it('shows the retry state of a batch whose lookups all failed, as the chip does (#548 #549)', () => {
    const html = line(summaryAllFailed);
    expect(name(html)).toBe('Da riprovare');
    expect(html).toContain('2 ricerche non riuscite');
    expect(html).not.toContain('lucide-check');
  });

  it('shows the same state and text as the chip for every kind', () => {
    for (const wire of [
      summaryNoSource,
      summaryNoneFound,
      summaryNoRowMatch,
      summaryFoundAndFailed,
    ]) {
      const chip = render(wire);
      const admin = line(wire);
      expect(name(admin)).toBe(name(chip));
      const text = admin.replace(/<[^>]*>/g, '');
      expect(text).not.toBe('');
      expect(chip.replace(/<[^>]*>/g, '')).toContain(text);
    }
  });
});
