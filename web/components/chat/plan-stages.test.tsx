import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { AgentPlan, PlanStage } from '@/lib/chat/types';
import { PlanStages } from './plan-stages';

const plan = (stages: PlanStage[]) =>
  ({ targetRows: 500, columns: [], stages }) as unknown as AgentPlan;

function text(stages: PlanStage[]) {
  const html = renderToStaticMarkup(
    <TestIntl>
      <PlanStages plan={plan(stages)} rows={500} dropped={new Set()} />
    </TestIntl>,
  );
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
}

describe('PlanStages step cost (demo QA chat-10)', () => {
  it('says «gratis» once for a free step', () => {
    const page = text([
      { tool: 'search_people', label: 'Trova i CEO', units: 20, perRow: false, unitCost: 0 },
    ]);
    expect(page.match(/gratis/g)).toHaveLength(1);
    expect(page).toContain('20 unità');
  });

  it('keeps the per-unit hint next to the total for a paid step', () => {
    const page = text([
      { tool: 'enrich_email', label: 'Email', units: 500, perRow: true, unitCost: 1 },
    ]);
    expect(page).toContain('1 credito per unità trovata');
    expect(page).toContain('500 crediti');
  });
});
