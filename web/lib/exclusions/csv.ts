export const EXCLUSION_KINDS = ['email', 'domain', 'company_name', 'linkedin_url'] as const;
export type ExclusionKind = (typeof EXCLUSION_KINDS)[number];
// Matches the API's per-request cap.
export const MAX_EXCLUSIONS_PER_IMPORT = 5000;
export type ExclusionInput = { kind?: ExclusionKind; value: string };

// Header names (lowercased, `_`/`-` as spaces) and the kind of value their column holds.
const COLUMNS: Record<string, ExclusionKind> = {
  email: 'email',
  'email address': 'email',
  'e mail': 'email',
  'linkedin url': 'linkedin_url',
  linkedin: 'linkedin_url',
  'linkedin profile': 'linkedin_url',
  domain: 'domain',
  website: 'domain',
  'company domain': 'domain',
  dominio: 'domain',
  sito: 'domain',
  'company name': 'company_name',
  company: 'company_name',
  name: 'company_name',
  azienda: 'company_name',
  nome: 'company_name',
};
const PERSON_HEADERS = new Set([
  'full name',
  'first name',
  'last name',
  'email',
  'email address',
  'e mail',
]);
// Per row, the first non-empty column in this order wins. A people list identifies the
// person (email, else LinkedIn), never their company: that would exclude all their colleagues.
const PERSON_ORDER: ExclusionKind[] = ['email', 'linkedin_url'];
const COMPANY_ORDER: ExclusionKind[] = ['domain', 'linkedin_url', 'company_name'];

function parseRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  const delimiter = (text.split('\n', 1)[0] ?? '').includes(';') ? ';' : ',';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delimiter) {
      row.push(cell);
      cell = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      rows.push([...row, cell]);
      row = [];
      cell = '';
    } else cell += ch;
  }
  rows.push([...row, cell]);
  return rows.filter((r) => r.some((c) => c.trim()));
}

/**
 * Values to exclude from a CSV or a plain list (one per line). With known headers
 * each row gives one value: a person's email or LinkedIn URL, or a company's
 * domain/website, LinkedIn URL or name. Otherwise the first column, and the API
 * guesses the kind.
 */
export function parseExclusionsCsv(text: string): ExclusionInput[] {
  const rows = parseRows(text.replace(/^\uFEFF/, ''));
  const header = (rows[0] ?? []).map((h) => h.trim().toLowerCase().replace(/[_-]/g, ' '));
  const order = header.some((h) => PERSON_HEADERS.has(h)) ? PERSON_ORDER : COMPANY_ORDER;
  const columns = order.flatMap((kind) =>
    header.flatMap((h, col) => (COLUMNS[h] === kind ? [{ col, kind }] : [])),
  );
  if (columns.length === 0) {
    return rows.map((r) => ({ value: (r[0] ?? '').trim() })).filter((item) => item.value);
  }
  return rows.slice(1).flatMap((r) => {
    const hit = columns.find(({ col }) => (r[col] ?? '').trim());
    return hit ? [{ kind: hit.kind, value: (r[hit.col] ?? '').trim() }] : [];
  });
}
