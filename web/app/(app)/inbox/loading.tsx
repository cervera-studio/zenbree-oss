import { getTranslations } from 'next-intl/server';
import { InboxFrame } from '@/components/inbox/frame';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

const sk = (className: string) => <Skeleton className={cn('bg-hair rounded-full', className)} />;
const block = (className: string) => <Skeleton className={cn('bg-hair rounded-lg', className)} />;

/** C4c: the title and chips with seven rows on the left, a thread taking shape on the right. */
export default async function InboxLoading() {
  const [t, tc] = await Promise.all([getTranslations('inbox'), getTranslations('common')]);
  return (
    <InboxFrame flush busy>
      <span className="sr-only">{tc('loading')}</span>
      <div className="border-line flex shrink-0 flex-col desktop:w-95 desktop:border-r phone:flex-1">
        <div className="flex flex-col gap-3 p-4">
          <h1 className="text-title">{t('title')}</h1>
          <div className="flex items-center gap-2">
            {sk('h-6 w-15')}
            {sk('h-6 w-20')}
            {sk('h-6 w-20')}
          </div>
        </div>
        <ul className="border-line border-t">
          {Array.from({ length: 7 }, (_, i) => (
            <li key={i} className="border-hair flex items-start gap-2.5 border-b px-4 py-3.5">
              {sk('size-7.5 shrink-0')}
              <div className="flex min-w-0 flex-1 flex-col gap-2">
                <div className="flex items-center justify-between gap-1.5">
                  {sk('h-2.5 w-27.5')}
                  {sk('h-2.5 w-9')}
                </div>
                {sk('h-2.5 w-62.5 max-w-full')}
                {sk('h-2.5 w-37.5')}
              </div>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-4 px-5 py-4 phone:hidden">
        <div className="flex items-center gap-3">
          {sk('size-11 shrink-0')}
          <div className="flex flex-col gap-2">
            {sk('h-3 w-55')}
            {sk('h-2.5 w-35')}
          </div>
        </div>
        {block('h-30 w-full')}
        <div className="flex justify-end">{block('h-22.5 w-4/5')}</div>
        {block('h-35 w-full')}
      </div>
    </InboxFrame>
  );
}
