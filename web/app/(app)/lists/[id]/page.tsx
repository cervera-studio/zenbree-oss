import { notFound } from 'next/navigation';
import { ListPage } from '@/components/lists/list-page';
import { getMe } from '@/lib/api/me';
import { isUuid } from '@/lib/config/uuid';
import { toMinFit, toRowTab } from '@/lib/views/href';

export default async function ListDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{
    row?: string;
    tab?: string;
    q?: string | string[];
    fit?: string | string[];
  }>;
}) {
  const { id } = await params;
  const { row, tab, q, fit } = await searchParams;
  if (!isUuid(id)) notFound();
  const me = await getMe();
  return (
    <ListPage
      listId={id}
      balance={me.success ? me.data.creditsRemaining : null}
      initialRowId={row && isUuid(row) ? row : null}
      initialTab={toRowTab(tab)}
      initialQuery={[q].flat()[0]?.slice(0, 200) ?? ''}
      initialMinFit={toMinFit(fit)}
    />
  );
}
