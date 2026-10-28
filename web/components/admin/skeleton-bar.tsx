import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

/** The placeholder of the admin loading pages: a hair-coloured block sized by the caller. */
export function SkeletonBar({ className }: { className?: string }) {
  return <Skeleton className={cn('h-3 bg-hair', className)} />;
}
