import { describe, expect, it } from 'vite-plus/test';
import {
  canDrop,
  droppedColumns,
  planBudget,
  planCredits,
  rowPresets,
  rowsWithin,
  stageCost,
} from './plan';
import type { AgentPlan, PlanStage } from './types';

const plan = { creditsFixed: 3, creditsPerRow: 2, largeRunCredits: 100 } as AgentPlan;

const phoned = {
  targetRows: 100,
  creditsFixed: 3,
  creditsPerRow: 12,
  creditsResearchPerRow: 0,
  largeRunCredits: 100,
  stages: [
    { tool: 'search_people', label: 'Find', units: 3, perRow: false, unitCost: 1, optional: false },
    {
      tool: 'enrich_email',
      label: 'Emails',
      units: 100,
      perRow: true,
      unitCost: 2,
      optional: false,
    },
    {
      tool: 'enrich_phone',
      label: 'Phones',
      units: 100,
      perRow: true,
      unitCost: 10,
      optional: true,
    },
  ],
} as AgentPlan;

describe('planCredits', () => {
  it('scales per-row stages and keeps fixed ones (matches the API)', () => {
    expect(planCredits(plan, 100)).toBe(203);
    expect(planCredits(plan, 60)).toBe(123);
    expect(planCredits({ ...plan, creditsPerRow: 0.3333 }, 300)).toBe(103);
  });

  it('reprices without the dropped stages', () => {
    expect(planCredits(phoned, 100)).toBe(1203);
    expect(planCredits(phoned, 100, new Set([2]))).toBe(203);
    expect(planCredits(phoned, 60, new Set([2]))).toBe(123);
  });

  it('keeps the research cells when a stage is dropped', () => {
    const researched = { ...phoned, creditsPerRow: 14, creditsResearchPerRow: 2 } as AgentPlan;
    expect(planCredits(researched, 100, new Set([2]))).toBe(403);
  });
});

describe('the credits the API approves', () => {
  it('prices a drop like the API does, with no rounding of the per-row cost (api: test_per_row_is_not_rounded)', () => {
    const stage = (units: number, unitCost: number, optional: boolean) =>
      ({ tool: 't', label: 't', units, perRow: true, unitCost, optional }) as PlanStage;
    const parity = {
      targetRows: 64,
      creditsFixed: 0,
      creditsPerRow: 8.03125,
      largeRunCredits: 100,
      stages: [stage(66, 1, false), stage(64, 2, false), stage(64, 5, true)],
    } as AgentPlan;
    expect(planCredits(parity, 64, new Set([2]))).toBe(194);
  });

  it('prices the plan itself, with no floor', () => {
    expect(planCredits(phoned, 1, new Set([2]))).toBe(5);
    expect(rowsWithin(phoned, new Set([2]), 60)).toBe(28);
  });
});

describe('planBudget', () => {
  const priced = (credits: number) =>
    ({
      creditsFixed: credits,
      creditsPerRow: 0,
      largeRunCredits: 100,
      stages: [],
    }) as unknown as AgentPlan;
  const budget = (credits: number, balance: number | null) =>
    planBudget(priced(credits), 1, new Set(), balance);

  it('says a small plan spends its own ceiling, not the large-run threshold', () => {
    expect(budget(30, 50)).toEqual({ ceiling: 30, missing: 0, after: 20 });
  });

  it('counts the credits missing against the plan, not the floor', () => {
    expect(budget(1010, 942)).toEqual({ ceiling: 1010, missing: 68, after: 0 });
    expect(budget(100, 942).missing).toBe(0);
    expect(budget(1203, null)).toMatchObject({ missing: 0, after: null });
    expect(budget(300, 1240)).toEqual({ ceiling: 300, missing: 0, after: 940 });
  });
});

describe('droppedColumns', () => {
  const columns = [
    { key: 'email' },
    { key: 'phone' },
    { key: 'phone_status' },
    { key: 'full_name' },
  ];
  const withColumns = { ...phoned, columns } as AgentPlan;

  it('hides the columns only a dropped stage fills', () => {
    expect(droppedColumns(withColumns, new Set([2]))).toEqual(new Set(['phone', 'phone_status']));
    expect(droppedColumns(withColumns, new Set())).toEqual(new Set());
  });

  it('keeps a column a kept stage still fills', () => {
    const twice = {
      ...withColumns,
      stages: [...phoned.stages, { ...phoned.stages[2], label: 'Phones 2' }],
    } as AgentPlan;
    expect(droppedColumns(twice, new Set([2]))).toEqual(new Set());
  });
});

describe('rowsWithin', () => {
  it('is the largest row count the balance covers', () => {
    expect(rowsWithin(phoned, new Set(), 942)).toBe(78);
    expect(planCredits(phoned, 78)).toBeLessThanOrEqual(942);
    expect(planCredits(phoned, 79)).toBeGreaterThan(942);
    expect(rowsWithin(phoned, new Set([2]), 942)).toBe(469);
  });

  it('is 0 when the fixed part alone exceeds the balance, and capped at the maximum', () => {
    expect(rowsWithin(phoned, new Set(), 2)).toBe(0);
    expect(rowsWithin({ ...phoned, creditsPerRow: 0 } as AgentPlan, new Set(), 150)).toBe(2000);
  });
});

describe('stage helpers', () => {
  it('prices a stage for the chosen rows and only drops optional, priced ones', () => {
    const [, emails, phones] = phoned.stages as [PlanStage, PlanStage, PlanStage];
    expect(stageCost(phoned, emails, 40)).toBe(80);
    expect(stageCost(phoned, phones, 40)).toBe(400);
    expect(canDrop(phones)).toBe(true);
    expect(canDrop(emails)).toBe(false);
    expect(canDrop({ ...phones, unitCost: undefined })).toBe(false);
  });

  it('offers the usual sizes plus the plan target', () => {
    expect(rowPresets(31)).toEqual([25, 31, 50, 100]);
    expect(rowPresets(50)).toEqual([25, 50, 100]);
  });
});
