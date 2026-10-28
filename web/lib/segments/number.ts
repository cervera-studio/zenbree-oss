const literal = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function separators(locale: string) {
  const parts = new Intl.NumberFormat(locale).formatToParts(12345.6);
  const of = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return { group: of('group'), decimal: of('decimal') || '.' };
}

export function parseNumber(raw: string, locale: string): number | null {
  const { group, decimal } = separators(locale);
  // A language that groups with a narrow no-break space gets a plain space from the keyboard.
  const g = /^\s$/.test(group) ? '\\s' : literal(group);
  const groupsOfThreeOrPlain = group ? `\\d{1,3}(?:${g}\\d{3})+|\\d+` : '\\d+';
  const match = new RegExp(`^([-−]?)(${groupsOfThreeOrPlain})(?:${literal(decimal)}(\\d+))?$`).exec(
    raw.trim(),
  );
  if (!match) return null;
  const [, sign, digits = '', fraction = '0'] = match;
  const n = Number(`${sign ? '-' : ''}${digits.replace(/\D/g, '')}.${fraction}`);
  return Number.isFinite(n) ? n : null;
}

export const formatNumber = (n: number, locale: string) =>
  new Intl.NumberFormat(locale, { useGrouping: false, maximumFractionDigits: 20 }).format(n);
