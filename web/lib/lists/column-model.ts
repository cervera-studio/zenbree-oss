import type { ListColumn, ListRow } from './api';
import {
  type CellRender,
  type ColumnSpec,
  displayText,
  type FieldLabel,
  toColumnSpecs,
} from './columns';

export type Entity = 'person' | 'company';

export const entityOf = (entity: string): Entity => (entity === 'person' ? 'person' : 'company');

export type CompositeId = 'fit' | 'person' | 'company' | 'email' | 'phone';

interface CompositeDef {
  id: CompositeId;
  labelKey: `column.${CompositeId}`;
  /** Cell keys this column shows itself, so the generic columns do not repeat them. */
  consumes: readonly string[];
  /** A contact cell the chat panel, which has no composites, also folds into one column with this renderer. */
  panel?: CellRender;
}

const FIT: CompositeDef = { id: 'fit', labelKey: 'column.fit', consumes: ['relevance_score'] };

const COMPOSITES: Record<Entity, readonly CompositeDef[]> = {
  person: [
    FIT,
    {
      id: 'person',
      labelKey: 'column.person',
      consumes: ['full_name', 'first_name', 'last_name', 'title', 'linkedin_url'],
    },
    { id: 'company', labelKey: 'column.company', consumes: ['company_name', 'company_domain'] },
    {
      id: 'email',
      labelKey: 'column.email',
      consumes: ['email', 'email_status'],
      panel: 'personEmail',
    },
    { id: 'phone', labelKey: 'column.phone', consumes: ['phone', 'phone_status'], panel: 'phone' },
  ],
  company: [
    FIT,
    { id: 'company', labelKey: 'column.company', consumes: ['name', 'domain', 'website'] },
  ],
};

interface Composite extends CompositeDef {
  /** A consumed column is required by the plan: the header gets a « *». */
  required: boolean;
}

export interface ColumnModel {
  composites: Composite[];
  /** Every other visible column, in `position` order. */
  rest: ColumnSpec[];
}

export function buildColumnModel(
  columns: ListColumn[],
  entity: Entity,
  fieldLabel?: FieldLabel,
): ColumnModel {
  const specs = toColumnSpecs(columns, fieldLabel);
  const visible = new Set(specs.map((s) => s.key));
  const defs = COMPOSITES[entity];
  const consumed = new Set(defs.flatMap((d) => d.consumes));
  return {
    composites: defs
      .filter((d) => d.consumes.some((k) => visible.has(k)))
      .map((d) => ({
        ...d,
        required: columns.some((c) => c.config?.required && d.consumes.includes(c.key)),
      })),
    rest: specs.filter((s) => !consumed.has(s.key)),
  };
}

/** Each column the chat panel folds → the cell it folds into, keyed like the value column it reads. */
const PANEL_CELLS = new Map(
  COMPOSITES.person.flatMap(({ id, labelKey, consumes, panel }) =>
    panel ? consumes.map((k) => [k, { key: id, render: panel, labelKey }] as const) : [],
  ),
);

/**
 * The chat panel's columns. A person list folds each status into its value's cell (#517, #525); a row lookup
 * that found nothing stores no empty value, so a status column can be the only one of its pair. Every folded
 * cell takes the label `label` gives its composite's key, never the stored one, so the header does not change
 * when a row finds a value.
 */
export function panelColumnSpecs(
  columns: ListColumn[],
  entity: Entity,
  label: (key: CompositeDef['labelKey'] | Parameters<FieldLabel>[0]) => string,
): ColumnSpec[] {
  const specs = toColumnSpecs(columns, label);
  if (entity !== 'person') return specs;
  const keys = new Set(specs.map((s) => s.key));
  return specs.flatMap((s) => {
    const cell = PANEL_CELLS.get(s.key);
    if (!cell) return [s];
    if (s.key !== cell.key && keys.has(cell.key)) return [];
    return [{ ...s, key: cell.key, render: cell.render, label: label(cell.labelKey) }];
  });
}

export interface Identity {
  name: string;
  role: string;
  company: string;
  domain: string;
}

/** The row's who, whatever the entity: what the Persona and Azienda cells, the aside and the cards show. */
export function identity(row: ListRow, entity: Entity): Identity {
  const text = (key: string) => displayText(row.cells[key]);
  if (entity === 'company') {
    const name = text('name');
    return { name, role: '', company: name, domain: text('domain') || text('website') };
  }
  const name =
    text('full_name') || [text('first_name'), text('last_name')].filter(Boolean).join(' ');
  return {
    name,
    role: text('title'),
    company: text('company_name'),
    domain: text('company_domain'),
  };
}
