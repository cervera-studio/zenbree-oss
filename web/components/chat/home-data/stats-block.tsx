import { Suspense } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { getHomeStats } from '@/lib/api/home';
import { hasActivity } from '@/lib/chat/home-overview';
import type { HomeOverview } from '@/lib/home/overview';
import { HomeStatsPanels } from './home-stats';

/** Holds the space of the panels so the page does not jump when they arrive. */
function StatsPlaceholder() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-4">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-80 w-full" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}

async function Stats({ overview }: { overview: Promise<HomeOverview | null> }) {
  const home = await overview;
  // Only the data screen shows the panels: the welcome and the empty home skip the request.
  if (!home || !hasActivity(home)) return null;
  const stats = await getHomeStats();
  return stats && <HomeStatsPanels stats={stats} />;
}

/** Server component: the home page passes it down as a slot, so a turn's `router.refresh()` renews it. */
export function StatsBlock({ overview }: { overview: Promise<HomeOverview | null> }) {
  return (
    <Suspense fallback={<StatsPlaceholder />}>
      <Stats overview={overview} />
    </Suspense>
  );
}
