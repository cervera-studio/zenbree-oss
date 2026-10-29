import { useTranslations } from 'next-intl';
import { Skeleton } from '@/components/ui/skeleton';

const range = (n: number) => Array.from({ length: n }, (_, i) => i);
const pick = <T,>(options: T[], i: number): T => options[i % options.length] as T;

const INDEX_GRID = 'grid h-row grid-cols-6 items-center gap-6 px-2.5';

/** Mirrors the lists table, so nothing jumps when the data arrives. */
export function IndexSkeleton() {
  const t = useTranslations('lists');
  return (
    <div aria-busy="true">
      <span role="status" className="sr-only">
        {t('loading')}
      </span>
      <div className="border-line bg-ground overflow-hidden rounded-lg border phone:hidden">
        <div className="bg-raised border-line h-row-head border-b" />
        {range(6).map((i) => (
          <div key={i} className={`${INDEX_GRID} border-hair border-b last:border-0`}>
            <div className="col-span-2 flex flex-col gap-1.5">
              <Skeleton
                className="h-3.5"
                style={{ width: pick(['62%', '48%', '70%', '55%'], i) }}
              />
              <Skeleton className="h-2.75" style={{ width: pick(['40%', '52%', '35%'], i) }} />
            </div>
            <Skeleton className="h-5 w-22.5 rounded-sm" />
            <Skeleton className="h-3 w-3/4" />
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        ))}
      </div>
      {range(4).map((i) => (
        <div
          key={i}
          className="border-line mb-2 flex flex-col gap-2 rounded-xl border p-3.5 desktop:hidden"
        >
          <Skeleton className="h-3.5 w-3/5" />
          <Skeleton className="h-2.75 w-2/5" />
        </div>
      ))}
    </div>
  );
}

const DETAIL_GRID = 'grid h-row grid-cols-12 items-center gap-4.5 px-2.5';

/** The detail page's title, stats, tabs and table, as the page lays them out. */
export function DetailSkeleton() {
  const t = useTranslations('lists');
  return (
    <div aria-busy="true" className="flex flex-col gap-4">
      <span role="status" className="sr-only">
        {t('loading')}
      </span>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-5.5 w-85 max-w-full" />
        <Skeleton className="h-3 w-55 max-w-full" />
      </div>
      <Skeleton className="h-16.5 rounded-lg phone:hidden" />
      <div className="flex gap-4.5">
        <Skeleton className="h-4 w-17.5" />
        <Skeleton className="h-4 w-22.5" />
        <Skeleton className="h-4 w-22.5" />
      </div>
      <div className="border-line bg-ground overflow-hidden rounded-lg border phone:hidden">
        <div className="bg-raised border-line h-row-head border-b" />
        {range(10).map((i) => (
          <div key={i} className={`${DETAIL_GRID} border-hair border-b last:border-0`}>
            <div className="col-span-2 flex flex-col gap-1.5">
              <Skeleton
                className="h-3.25"
                style={{ width: pick(['70%', '55%', '62%', '80%'], i) }}
              />
              <Skeleton className="h-2.5" style={{ width: pick(['50%', '38%', '60%'], i) }} />
            </div>
            <Skeleton
              className="col-span-2 h-3"
              style={{ width: pick(['60%', '75%', '50%'], i) }}
            />
            <Skeleton className="col-span-3 h-3 w-5/6" />
            <Skeleton className="h-3 w-10" />
            <Skeleton
              className="col-span-3 h-3"
              style={{ width: pick(['50%', '38%', '60%'], i) }}
            />
            <Skeleton className="size-control-sm" />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-2 desktop:hidden">
        {range(5).map((i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
    </div>
  );
}
