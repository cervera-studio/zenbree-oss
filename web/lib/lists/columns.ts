import { reorder } from '@atlaskit/pragmatic-drag-and-drop/reorder';
import type { ListColumn, ListRow } from './api';

export function reorderColumns(order: string[], source: string, target: string): string[] {
  const startIndex = order.indexOf(source);
  const finishIndex = order.indexOf(target);
  if (startIndex < 0 || finishIndex < 0 || startIndex === finishIndex) return order;
  return reorder({ list: order, startIndex, finishIndex });
}

/** `personEmail`: a person's email with the search's answer; `email`: any other address. */
export type CellRender = 'score' | 'ai' | 'link' | 'email' | 'personEmail' | 'phone' | 'text';

export interface ColumnSpec {
  key: string;
  label: string;
  render: CellRender;
  /** AI columns: status of the last fill run (`queued`, `running`, `done`, `failed`). */
  fill?: string;
}

const LINK_KEYS = new Set(['website', 'linkedin_url', 'url', 'domain', 'company_domain']);

/** API columns → display specs, in `position` order; score column first. The API hides vendor plumbing. */
export function toColumnSpecs(columns: ListColumn[], fieldLabel?: FieldLabel): ColumnSpec[] {
  const specs = [...columns]
    .sort((a, b) => a.position - b.position)
    .map((c) => ({
      key: c.key,
      label: fieldLabel ? labelOf(c, fieldLabel) : c.label,
      render: renderFor(c),
      fill: c.config?.fill?.status,
    }));
  return [
    ...specs.filter((s) => s.render === 'score'),
    ...specs.filter((s) => s.render !== 'score'),
  ];
}

function renderFor(c: ListColumn): CellRender {
  if (c.kind === 'score' || c.key === 'relevance_score') return 'score';
  if (c.kind === 'ai') return 'ai';
  if (c.key === 'email') return 'email';
  if (c.key === 'phone') return 'phone';
  if (LINK_KEYS.has(c.key)) return 'link';
  return 'text';
}

/** True while an AI column fill is queued or running: the open list follows its fill stream. */
export function isFilling(columns: ListColumn[]): boolean {
  return columns.some(
    (c) => c.kind === 'ai' && ['queued', 'running'].includes(c.config?.fill?.status ?? ''),
  );
}

export function fillErrorKey(code?: string | null) {
  if (code === 'insufficient_credits') return 'aiNoCredits';
  if (code === 'project_cap_reached') return 'aiCapReached';
  if (code === 'vendor_disabled') return 'aiDisabled';
  if (code === 'fill_rate_limited') return 'aiRateLimited';
  return 'aiFillError';
}

/** `relevance_score` lives on the row, not in `cells`. */
export function cellValue(row: ListRow, key: string): unknown {
  return key === 'relevance_score' ? row.relevance_score : row.cells[key];
}

export function displayText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(displayText).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Bare domains / paths get a scheme so the link isn't relative to our app. */
export function hrefFor(value: string): string {
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

export type ScoreTone = 'high' | 'mid' | 'low' | 'none';

export function scoreTone(score: number | null | undefined): ScoreTone {
  if (score === null || score === undefined || Number.isNaN(score)) return 'none';
  if (score >= 70) return 'high';
  if (score >= 40) return 'mid';
  return 'low';
}

/** The template a user downloads from the import dialog: one row under the headers it maps. */
export const SAMPLE_CSV: Record<'company' | 'person', string> = {
  person:
    'full_name,title,company_name,company_domain,email,linkedin_url\n' +
    'Valentina Rossi,Marketing manager,Studio Kappa,studiokappa.it,v.rossi@studiokappa.it,linkedin.com/in/valentina-rossi-mk\n',
  company: 'name,domain,industry,city\nStudio Kappa,studiokappa.it,Marketing,Milano\n',
};

/** Keys a CSV column can be mapped to in the import dialog, as `TARGETS` in `api/app/lists/csv_import.py`. */
export const IMPORT_TARGETS = {
  person: [
    'full_name',
    'first_name',
    'last_name',
    'title',
    'company_name',
    'company_domain',
    'linkedin_url',
    'email',
    'email_status',
    'city',
    'country',
  ],
  company: [
    'name',
    'domain',
    'website',
    'linkedin_url',
    'industry',
    'employees',
    'city',
    'country',
    'description',
  ],
} as const;

type ImportField = (typeof IMPORT_TARGETS)[keyof typeof IMPORT_TARGETS][number];
const IMPORT_FIELDS: ReadonlySet<string> = new Set(Object.values(IMPORT_TARGETS).flat());

export type FieldLabel = (key: `importFields.${ImportField}`) => string;

/** Mirrors `_label` in `api/app/lists/rows.py` (Python's `capitalize`): the API marks no column as named by it. */
function apiLabel(key: string): string {
  const words = key.replaceAll('_', ' ');
  return words.charAt(0).toUpperCase() + words.slice(1).toLowerCase();
}

function labelOf(c: ListColumn, fieldLabel: FieldLabel): string {
  return c.kind === 'static' && IMPORT_FIELDS.has(c.key) && c.label === apiLabel(c.key)
    ? fieldLabel(`importFields.${c.key as ImportField}`)
    : c.label;
}

/** A mapping the import accepts: a name plus one of email, domain or LinkedIn. */
export function mappingComplete(entity: 'company' | 'person', mapping: string[]): boolean {
  const has = (...keys: string[]) => keys.some((k) => mapping.includes(k));
  if (entity === 'person') {
    return (
      has('full_name', 'first_name', 'last_name') && has('email', 'company_domain', 'linkedin_url')
    );
  }
  return has('name') && has('domain', 'website', 'linkedin_url');
}
