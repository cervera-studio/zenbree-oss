// ponytail: copies of the product's prices and thresholds, for the plan on the page: email 1 credit
// (api/app/agent/tools/handlers/enrich_email.py), phone 8 (enrich_phone.py), an AI cell 1
// (api/app/lists/ai_columns.py); a plan up to 100 credits starts at once (LARGE_RUN_CREDITS in
// api/app/agent/plan.py), over 500 asks twice (CONFIRM_CREDITS in web/lib/chat/plan.ts).
// Kept apart from content.ts so the page script does not bundle every language.

/** A column a plan can make required, priced per row. */
export type PlanColumn = 'email' | 'phone' | 'ai';

export const tariffs: Record<PlanColumn, number> = { email: 1, phone: 8, ai: 1 };
const autoStartCredits = 100;
const confirmCredits = 500;

export type PlanRules = { instant: string; approve: string; confirm: string };

/** The most a plan can cost. Found companies and people are free, so it is rows times the columns' prices. */
export function planCost(rows: number, columns: readonly PlanColumn[]): number {
  return rows * columns.reduce((sum, c) => sum + tariffs[c], 0);
}

/** The app's gate: the first plan of a turn starts at once up to 100 credits, and the web asks twice over 500. */
export function planRule(cost: number, rules: PlanRules): string {
  if (cost <= autoStartCredits) return rules.instant;
  return cost > confirmCredits ? `${rules.approve} ${rules.confirm}` : rules.approve;
}
