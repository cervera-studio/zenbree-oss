import { describe, expect, it } from 'vite-plus/test';
import type { Job } from '@/lib/admin/api';
import { stageText, stageTone } from './job-status';

type Stage = NonNullable<Job['stages']>[number];
const stage = (over: Partial<Stage>): Stage => ({
  tool: 'verify',
  label: 'Verify',
  units: 100,
  done: 0,
  ...over,
});
const count = (v: number) => String(v);
const duration = () => '0:12';
const text = (s: Stage) => stageText(s, count, duration);

describe('stageText', () => {
  it('shows only the time of a stage that completed', () => {
    expect(text(stage({ done: 100, startedAt: 'T' }))).toBe('0:12');
  });
  it('shows the count and the time of a stage that failed part way', () => {
    expect(text(stage({ done: 36, error: 'boom', startedAt: 'T' }))).toBe('36/100 · 0:12');
  });
  it('shows the count and the elapsed time of a running stage', () => {
    expect(text(stage({ done: 64, startedAt: 'T' }))).toBe('64/100 · 0:12');
  });
  it('shows the count of a stage that never started', () => {
    expect(text(stage({}))).toBe('0/100');
  });
});

describe('stageTone', () => {
  it('follows the job only while it runs or waits', () => {
    expect(stageTone(stage({ done: 5 }), 'running')).toBe('accent');
    expect(stageTone(stage({ done: 5 }), 'needs_input')).toBe('warning');
    expect(stageTone(stage({ done: 5 }), 'failed')).toBe('warning');
    expect(stageTone(stage({ done: 5 }), 'succeeded')).toBe('warning');
    expect(stageTone(stage({ error: 'x' }), 'failed')).toBe('negative');
    expect(stageTone(stage({ done: 100 }), 'failed')).toBe('positive');
  });
});
