import type { AgentPlan, PlanStage } from './types';

export const MAX_PLAN_ROWS = 2000;

/** The smallest credit pack, named by «Ricarica crediti» on a plan the balance can't cover. */
export interface TopUpPack {
  credits: number;
  priceEurCents: number;
}

export function smallestPack(packs: readonly TopUpPack[]): TopUpPack | null {
  const pack = packs.reduce<TopUpPack | null>(
    (min, p) => (!min || p.credits < min.credits ? p : min),
    null,
  );
  return pack && { credits: pack.credits, priceEurCents: pack.priceEurCents };
}

export const CONFIRM_CREDITS = 500;

const ROW_PRESETS = [25, 50, 100];

const NONE: ReadonlySet<number> = new Set();

function terms(plan: AgentPlan, dropped: ReadonlySet<number>) {
  if (dropped.size === 0) return { fixed: plan.creditsFixed, perRow: plan.creditsPerRow };
  let fixed = 0;
  let perRow = plan.creditsResearchPerRow ?? 0;
  plan.stages.forEach((s, i) => {
    if (dropped.has(i)) return;
    const cost = (s.unitCost ?? 0) * s.units;
    if (s.perRow) perRow += cost / plan.targetRows;
    else fixed += cost;
  });
  return { fixed, perRow };
}

export function planCredits(
  plan: AgentPlan,
  rows: number,
  dropped: ReadonlySet<number> = NONE,
): number {
  const { fixed, perRow } = terms(plan, dropped);
  return fixed + Math.ceil(perRow * rows - 1e-9);
}

// the run spends at most the plan's ceiling (api loop._run_plan, #259), so the card says that and no more
export function planBudget(
  plan: AgentPlan,
  rows: number,
  dropped: ReadonlySet<number>,
  balance: number | null,
) {
  const ceiling = planCredits(plan, rows, dropped);
  const missing = balance === null ? 0 : Math.max(0, ceiling - balance);
  return { ceiling, missing, after: balance === null ? null : Math.max(0, balance - ceiling) };
}

export function droppedStages(plan: AgentPlan): ReadonlySet<number> {
  return new Set(plan.stages.flatMap((s, i) => (s.dropped ? [i] : [])));
}

// ponytail: the plan does not say which columns a stage fills; this table keeps a dropped stage's columns off the card
const TOOL_COLUMNS: Record<string, readonly string[]> = {
  enrich_email: ['email', 'email_status', 'email_source'],
  enrich_phone: ['phone', 'phone_status', 'phone_source'],
};

export function droppedColumns(plan: AgentPlan, dropped: ReadonlySet<number>): ReadonlySet<string> {
  const keysOf = (wantDropped: boolean) =>
    new Set(
      plan.stages.flatMap((s, i) =>
        dropped.has(i) === wantDropped ? (TOOL_COLUMNS[s.tool] ?? []) : [],
      ),
    );
  const kept = keysOf(false);
  const listed = new Set(plan.columns.map((c) => c.key));
  return new Set([...keysOf(true)].filter((key) => listed.has(key) && !kept.has(key)));
}

export function canDrop(stage: PlanStage): boolean {
  return stage.optional === true && stage.unitCost !== undefined;
}

export function stageUnits(plan: AgentPlan, stage: PlanStage, rows: number): number {
  return stage.perRow ? Math.ceil((stage.units * rows) / plan.targetRows) : stage.units;
}

export function stageCost(plan: AgentPlan, stage: PlanStage, rows: number): number | null {
  return stage.unitCost === undefined
    ? null
    : Math.ceil(stage.unitCost * stageUnits(plan, stage, rows) - 1e-9);
}

export function rowsWithin(plan: AgentPlan, dropped: ReadonlySet<number>, balance: number): number {
  const { fixed, perRow } = terms(plan, dropped);
  if (perRow <= 0) return planCredits(plan, 1, dropped) <= balance ? MAX_PLAN_ROWS : 0;
  let rows = Math.min(MAX_PLAN_ROWS, Math.max(0, Math.floor((balance - fixed) / perRow)));
  while (rows < MAX_PLAN_ROWS && planCredits(plan, rows + 1, dropped) <= balance) rows++;
  while (rows > 0 && planCredits(plan, rows, dropped) > balance) rows--;
  return rows;
}

export function rowPresets(targetRows: number): number[] {
  return [...new Set([...ROW_PRESETS, targetRows])]
    .filter((n) => n <= MAX_PLAN_ROWS)
    .sort((a, b) => a - b);
}
