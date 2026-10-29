'use client';

import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import type { ListRow } from '@/lib/lists/api';
import { CrmHold } from './crm-hold';

/** Escludi / Includi as the list page's row aside says it, with the HubSpot hold beside it when the
 * read gate holds the person (#120). A checkbox here read as a row selection. */
export function RowInclude({ row, onToggle }: { row: ListRow; onToggle: (row: ListRow) => void }) {
  const t = useTranslations('lists');
  return (
    <span className="flex items-center gap-2">
      <Button variant="outline" size="xs" onClick={() => onToggle(row)}>
        {row.excluded ? t('include') : t('exclude')}
      </Button>
      {row.crm_hold && <CrmHold hold={row.crm_hold} />}
    </span>
  );
}
