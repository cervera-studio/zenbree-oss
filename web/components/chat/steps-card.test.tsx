import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { JobProgress, JobStage } from '@/lib/chat/job-progress';
import { StepsCard } from './steps-card';

const stage = (
  tool: string,
  done: number,
  code?: NonNullable<JobStage['error']>['code'],
  extra: Partial<JobStage> = {},
): JobStage => ({
  tool,
  label: tool,
  units: 2,
  done,
  ...(code ? { error: { code } } : {}),
  ...extra,
});

function render(stages: JobStage[]) {
  return html(stages)
    .replace(/<!-- -->/g, '')
    .replace(/&#x27;/g, "'");
}

function html(stages: JobStage[]) {
  const progress: JobProgress = {
    stages,
    phase: null,
    creditsSpent: 0,
    retryStage: null,
    listId: 'L',
  };
  return renderToStaticMarkup(
    <TestIntl>
      <StepsCard
        progress={progress}
        running={false}
        credits={0}
        onRetry={() => {}}
        listHref="/lists/L"
      />
    </TestIntl>,
  );
}

describe('StepsCard when the run has stopped', () => {
  it('says once that the search failed when every stage failed and delivered nothing', () => {
    const html = render([
      stage('search_companies', 0, 'provider'),
      stage('search_people', 0, 'failed'),
    ]);
    expect(html).toContain('non hai usato crediti');
    expect(html).toContain('<details');
    expect(html.match(/>Riprova</g)).toHaveLength(1);
    expect(html).not.toContain("Apri la lista com'è");
    expect(html).not.toMatch(/configurat/i);
  });

  it.each(['no_credits', 'over_budget', 'disabled'] as const)(
    'keeps the visible cause of a %s refusal',
    (code) => {
      const html = render([stage('search_companies', 0, code), stage('search_people', 0)]);
      expect(html).not.toContain('<details');
      expect(html).not.toContain('Riprova tra poco');
    },
  );

  it('keeps the rows and the list link of a stage that delivered some', () => {
    const html = render([stage('search_people', 1, 'provider')]);
    expect(html).not.toContain('<details');
    expect(html).toContain("Apri la lista com'è");
  });

  it('keeps the step cards when another step delivered', () => {
    const html = render([stage('search_companies', 2), stage('search_people', 0, 'provider')]);
    expect(html).not.toContain('<details');
  });

  it('names the unit of a stage: searches, or rows when it counts rows', () => {
    const html = render([
      stage('search_companies', 1, undefined, { units: 2 }),
      stage('search_people', 0, 'provider', { units: 50, perRow: true }),
    ]);
    expect(html).toContain('1 di 2 ricerche');
    expect(html).toContain('0 di 50 righe');
    expect(html).not.toContain('2 aziende mancanti');
  });
});
