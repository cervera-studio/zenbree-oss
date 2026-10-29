import type { HomeOverview } from '@/lib/home/overview';

export function hasActivity(o: HomeOverview): boolean {
  return (
    o.lists.length > 0 ||
    o.campaigns.length > 0 ||
    o.running.length > 0 ||
    o.approvals.total > 0 ||
    o.replies.count > 0
  );
}

export type HomeScreen<W> =
  | { kind: 'welcome'; welcome: W }
  | { kind: 'data'; overview: HomeOverview }
  | { kind: 'empty' };

export function chooseHome<W>(
  welcome: W | null | undefined,
  overview: HomeOverview | null,
): HomeScreen<W> {
  if (welcome) return { kind: 'welcome', welcome };
  if (overview && hasActivity(overview)) return { kind: 'data', overview };
  return { kind: 'empty' };
}

export function approvalsBreakdown(approvals: HomeOverview['approvals']) {
  const named = approvals.campaigns.reduce((sum, c) => sum + c.count, 0);
  return { campaigns: approvals.campaigns, other: Math.max(approvals.total - named, 0) };
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => [...w][0]?.toUpperCase() ?? '')
    .join('');
}

const DAY_MS = 86_400_000;

function dayNumber(date: Date, timeZone: string): number {
  const part = (type: string) =>
    Number(
      new Intl.DateTimeFormat('en-US', {
        timeZone,
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
      })
        .formatToParts(date)
        .find((p) => p.type === type)?.value,
    );
  return Date.UTC(part('year'), part('month') - 1, part('day')) / DAY_MS;
}

export function dayRelation(
  at: Date,
  now: Date,
  timeZone: string,
): 'today' | 'yesterday' | 'earlier' {
  const diff = dayNumber(now, timeZone) - dayNumber(at, timeZone);
  return diff === 0 ? 'today' : diff === 1 ? 'yesterday' : 'earlier';
}

export function dayPart(now: Date, timeZone: string): 'morning' | 'afternoon' | 'evening' {
  const hour = Number(
    new Intl.DateTimeFormat('en-US', { timeZone, hour: 'numeric', hourCycle: 'h23' })
      .formatToParts(now)
      .find((p) => p.type === 'hour')?.value,
  );
  if (hour >= 5 && hour < 13) return 'morning';
  if (hour >= 13 && hour < 18) return 'afternoon';
  return 'evening';
}
