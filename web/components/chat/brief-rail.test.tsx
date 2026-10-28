import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import type { Brief } from '@/lib/chat/brief';
import { BriefRail } from './brief-rail';

const brief: Brief = { who: ['CEO'], companies: [], where: [], signal: [], email: null };

function render(b: Brief) {
  return renderToStaticMarkup(
    <TestIntl>
      <BriefRail brief={b} stage="brief" onEdit={() => {}} />
    </TestIntl>,
  );
}

describe('BriefRail', () => {
  it('marks the rule that excludes everyone and says the empty search was free', () => {
    const html = render({ ...brief, where: ['Bolzano'], excluded: 'where' });
    expect(html).toContain('È la regola che esclude tutti');
    expect(html).toContain('Una ricerca senza risultati non costa crediti.');
    expect(html).toContain('Bolzano');
  });

  it('shows an excluded field even without chips, and nothing of it otherwise', () => {
    expect(render({ ...brief, excluded: 'companies' })).toContain('Aziende');
    expect(render(brief)).not.toContain('esclude tutti');
  });
});
