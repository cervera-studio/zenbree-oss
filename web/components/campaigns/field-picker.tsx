'use client';

import { Plus, Sparkles } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  type CampaignField,
  type CatalogEntry,
  CONTACT_TOKENS,
  entryOf,
  type FieldCatalog,
} from '@/lib/campaigns/fields';

export function useFieldLabel(catalog: FieldCatalog | null) {
  const t = useTranslations('campaignBuilder.fields.signal');
  return (field: Pick<CampaignField, 'source'>) =>
    field.source.kind === 'signal'
      ? t(field.source.key)
      : (entryOf(field, catalog)?.label ?? field.source.key);
}

export function useTokenChips(fields: CampaignField[], catalog: FieldCatalog | null) {
  const t = useTranslations('campaignBuilder.steps.tokens');
  const label = useFieldLabel(catalog);
  return [
    ...CONTACT_TOKENS.map((token) => ({ token, label: t(token) })),
    ...fields.map((f) => ({ token: f.token, label: label(f) })),
  ];
}

export function FieldPicker({
  catalog,
  onPick,
  onAddSnippet,
}: {
  catalog: FieldCatalog;
  onPick: (entry: CatalogEntry) => void;
  onAddSnippet?: () => void;
}) {
  const t = useTranslations('campaignBuilder.fields');
  const label = useFieldLabel(catalog);
  const item = (entry: CatalogEntry) => {
    const filled = t('filled', { filled: entry.filled, people: catalog.people });
    // Without it the name runs the label, the tag and the count together: «Città4 di 5 persone».
    const name = [label(entry), entry.ai && t('ai'), filled].filter(Boolean).join(' · ');
    return (
      <DropdownMenuItem key={entry.token} aria-label={name} onSelect={() => onPick(entry)}>
        <span className="min-w-0 flex-1 truncate">{label(entry)}</span>
        {entry.ai && <Badge tone="accent">{t('ai')}</Badge>}
        <span className="text-muted text-data-sm shrink-0 tabular-nums">{filled}</span>
      </DropdownMenuItem>
    );
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t('addLabel')}
          className="border-line-strong text-soft text-data-sm hover:bg-hair inline-flex items-center gap-0.5 rounded-sm border border-dashed px-1.5 font-mono"
        >
          <Plus className="size-3" aria-hidden="true" />
          {t('add')}
        </button>
      </DropdownMenuTrigger>
      {/* Focus goes back to the step's text, where the token lands, not to this button. */}
      <DropdownMenuContent
        align="start"
        className="w-72"
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <p className="text-label text-muted px-2 py-1 uppercase">{t('columns')}</p>
        {catalog.columns.length === 0 && (
          <p className="text-muted text-small px-2 py-1.5">{t('noColumns')}</p>
        )}
        {catalog.columns.map(item)}
        {catalog.signal.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <p className="text-label text-muted px-2 py-1 uppercase">{t('signalGroup')}</p>
            {catalog.signal.map(item)}
          </>
        )}
        {onAddSnippet && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onAddSnippet}>
              <Sparkles className="size-4" aria-hidden="true" />
              {t('addSnippet')}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
