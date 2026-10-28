import { describe, expect, it } from 'vite-plus/test';
import { endLabelShifts } from './charts';

const series = [
  { dataKey: 'vendor', label: 'Fornitori', color: 'var(--chart-1)' },
  { dataKey: 'llm', label: 'LLM', color: 'var(--chart-2)' },
  { dataKey: 'revenue', label: 'Ricavi', color: 'var(--chart-3)' },
];

describe('endLabelShifts', () => {
  it('stacks labels of lines that end on the same value', () => {
    expect(endLabelShifts(series, { label: 'x', vendor: 0, llm: 0, revenue: 5 })).toEqual({
      vendor: -7,
      llm: 7,
      revenue: 0,
    });
  });

  it('leaves distinct end values in place', () => {
    expect(endLabelShifts(series, { label: 'x', vendor: 1, llm: 2, revenue: 3 })).toEqual({
      vendor: 0,
      llm: 0,
      revenue: 0,
    });
  });
});
