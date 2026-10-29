// Apollo-style "min,max" ("1,50"; "1000," is open-ended), or the "1–50" the home API has joined it to.
// A bare number is one headcount, not a range.
export function employeeRange(raw: string, solo: string, num: (n: number) => string): string {
  const m = /^(\d+)(?:([,–-])(\d*))?$/.exec(raw);
  if (!m) return raw;
  const lo = Number(m[1]);
  if (!m[2]) return num(lo);
  if (!m[3]) return `${num(lo)}+`;
  const hi = Number(m[3]);
  if (lo === hi) return lo === 1 ? solo : num(lo);
  return `${num(lo)}–${num(hi)}`;
}

const SENIORITIES = [
  'owner',
  'founder',
  'c_suite',
  'partner',
  'vp',
  'head',
  'director',
  'manager',
  'senior',
  'entry',
  'intern',
] as const;

export const isSeniority = (v: string): v is (typeof SENIORITIES)[number] =>
  (SENIORITIES as readonly string[]).includes(v);
