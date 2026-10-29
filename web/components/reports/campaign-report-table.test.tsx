import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { CampaignReport, ReportRow, ReportVariant } from '@/lib/reports/api';
import { CampaignReportTable } from './campaign-report-table';
import { ReportNotes } from './report-notes';

const line = (extra: Partial<ReportRow['lineage'][number]>): ReportRow['lineage'][number] => ({
  list: { id: 'l1', name: 'Fintech' },
  source: 'csv',
  signal: null,
  contacted: 2,
  humanReplies: 1,
  positiveReplies: 0,
  ...extra,
});

const row = (extra: Partial<ReportRow>): ReportRow => ({
  sender: 'native',
  id: 'c1',
  name: 'Spring',
  status: 'active',
  list: { id: 'l1', name: 'Fintech' },
  counts: {
    enrolled: 10,
    contacted: 1200,
    sent: { email: 1500, linkedinConnect: 0, linkedinMessage: 0 },
    humanReplies: 1,
    positiveReplies: 1,
    bounced: null,
    meetings: null,
    deals: null,
  },
  lineage: [],
  variants: [],
  ...extra,
});

const variant = (group: ReportVariant['group'], contacted: number): ReportVariant => ({
  group,
  enrolled: 60,
  contacted,
  sent: { email: contacted, linkedinConnect: 0, linkedinMessage: 0 },
  humanReplies: 3,
  positiveReplies: 1,
  bounced: null,
});

const report = (rows: ReportRow[], completeUntil: string | null): CampaignReport => ({
  window: { start: '2026-05-01T00:00:00Z', end: '2026-06-01T00:00:00Z', timeZone: 'Europe/Rome' },
  campaigns: rows,
  instantly: { mode: 'polled', completeUntil, failingSince: null, lastErrorCode: null },
});

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const table = (rows: ReportRow[]) =>
  text(
    renderToStaticMarkup(
      <TestIntl>
        <CampaignReportTable rows={rows} />
      </TestIntl>,
    ),
  );
const notes = (r: CampaignReport, to: string) =>
  text(
    renderToStaticMarkup(
      <TestIntl>
        <ReportNotes report={r} range={{ from: '2026-05-01', to }} timeZone="Europe/Rome" />
      </TestIntl>,
    ),
  );

describe('campaign report table', () => {
  it('shows an unmeasured count as unknown, never as 0', () => {
    const html = table([row({})]);
    expect(html.match(/Non noto/g)).toHaveLength(3);
    expect(html).not.toMatch(/ 0 /);
  });

  it('says one reply in the singular and groups large counts', () => {
    expect(table([row({})])).toContain('1 su 1.200 contattati');
    expect(table([row({})])).toContain('1 su 1 risposta');
  });

  it('splits sends by channel only when LinkedIn sent something', () => {
    expect(table([row({})])).not.toContain('inviti');
    const linkedin = row({
      counts: { ...row({}).counts, sent: { email: 3, linkedinConnect: 1, linkedinMessage: 2 } },
    });
    expect(table([linkedin])).toContain('3 email · 1 invito · 2 messaggi');
  });

  it('marks an unmatched or deleted person only on a native row', () => {
    const unknown = line({ source: null, list: null });
    expect(table([row({ lineage: [unknown] })])).toContain(
      'Nessuna lista · Persona non abbinata o eliminata',
    );
    const instantly = table([row({ sender: 'instantly', lineage: [unknown] })]);
    expect(instantly).not.toContain('Persona non abbinata');
  });

  it('opens a split by copy variant only under a campaign that tests', () => {
    expect(table([row({})])).not.toContain('Risultati per variante');
    const tested = table([row({ variants: [variant('a', 40), variant('b', 38)] })]);
    expect(tested).toContain('Risultati per variante');
    expect(tested).toContain('Variante A');
    expect(tested).toContain('3 su 40 persone contattate');
  });
});

describe('campaign report notes', () => {
  const instantly = row({ sender: 'instantly' });

  it('says a positive reply can come from a person who replied before the range', () => {
    expect(notes(report([row({})], null), '2026-05-31')).toContain(
      'Una risposta positiva in queste date può venire da una persona che aveva già risposto prima.',
    );
  });

  it('warns when the range reaches past the last complete Instantly read', () => {
    expect(notes(report([instantly], '2026-05-20T10:00:00Z'), '2026-05-31')).toContain(
      'I conteggi di Instantly sono completi fino al',
    );
    expect(notes(report([instantly], '2026-06-02T10:00:00Z'), '2026-05-31')).not.toContain(
      'Instantly',
    );
  });

  it('says no winner is picked only when a campaign tests copy B', () => {
    const tested = row({ variants: [variant('a', 40), variant('b', 38)] });
    expect(notes(report([tested], null), '2026-05-31')).toContain(
      'non indica una variante vincente',
    );
    expect(notes(report([row({})], null), '2026-05-31')).not.toContain('A/B');
  });

  it('says Instantly is pending before the first full read, and nothing without an Instantly row', () => {
    expect(notes(report([instantly], null), '2026-05-31')).toContain('non sono ancora completi');
    expect(notes(report([row({})], null), '2026-05-31')).not.toContain('Instantly');
  });
});
