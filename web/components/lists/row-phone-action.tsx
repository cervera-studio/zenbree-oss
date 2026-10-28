'use client';

import { Loader2, Phone } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { type ListRow, listsApi } from '@/lib/lists/api';
import { PHONE_ANSWER, type PhoneAnswer, phoneView } from '@/lib/lists/lookups';

type Outcome = PhoneAnswer | 'no_credits' | 'busy' | 'excluded';

const ERROR_OUTCOME: Record<string, Outcome> = {
  'ROW.NO_CREDITS': 'no_credits',
  'ROW.PROJECT_CAP': 'no_credits',
  'ROW.PHONE_RUNNING': 'busy',
  'ROW.PHONE_UNAVAILABLE': 'unavailable',
  'ROW.EXCLUDED': 'excluded',
};

/** «Cerca il telefono» on a person row: charged only when a number comes back, the answer shown under it.
 *  A row whose search found nothing offers no retry, which the vendor would bill again, until a found email
 *  gives the search a new anchor (enrich_email clears the verdict); one whose search failed offers it again. */
export function RowPhoneAction({
  listId,
  row,
  credits,
  onRowChange,
}: {
  listId: string;
  row: ListRow;
  /** What a found number costs. */
  credits: number;
  onRowChange: (row: ListRow) => void;
}) {
  const t = useTranslations('lists');
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [charged, setCharged] = useState(0);

  const search = async () => {
    setBusy(true);
    setOutcome(null);
    const res = await listsApi.searchPhone(listId, row.id);
    setBusy(false);
    if (!res.success) return setOutcome(ERROR_OUTCOME[res.error.code] ?? 'failed');
    onRowChange(res.data.row);
    setCharged(res.data.credits);
    setOutcome(PHONE_ANSWER[res.data.status]);
    if (res.data.credits > 0) router.refresh(); // the sidebar's balance is server-rendered
  };

  const view = phoneView(row.lookups.phone);
  if (!view && !outcome) return null;

  return (
    <div className="flex flex-col gap-1.5">
      {view?.search && (
        <Button variant="outline" disabled={busy} onClick={() => void search()}>
          {busy ? <Loader2 className="animate-spin" /> : <Phone />}
          {busy ? t('phoneSearching') : t('phoneSearch')}
          {!busy && <span className="text-muted font-normal">{t('phoneCost', { credits })}</span>}
        </Button>
      )}
      <p role="status" className="text-muted min-h-4 text-xs">
        {outcome === 'found' && t('phoneFound', { credits: charged })}
        {outcome && outcome !== 'found' && t(`phoneOutcome.${outcome}`)}
        {!outcome && view?.statusKey && t(view.statusKey)}
      </p>
    </div>
  );
}
