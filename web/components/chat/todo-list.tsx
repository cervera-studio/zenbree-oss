import { ChevronRight, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import type { TodoItem } from '@/lib/api/home';
import { cn } from '@/lib/utils';

const KIND_STYLE = {
  approvals: {
    row: 'bg-warning/10 border-warning/35',
    badge: 'bg-warning/20 text-warning',
  },
  replies: { row: 'bg-ground', badge: 'bg-accent-tint text-accent-ink' },
  running: { row: 'bg-ground', badge: 'bg-accent-tint text-accent-ink' },
} satisfies Record<TodoItem['kind'], { row: string; badge: string }>;

export function TodoList({ items }: { items: TodoItem[] }) {
  const t = useTranslations('chat.home.todo');
  const format = useFormatter();
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="home-todo" className="flex flex-col gap-2.5">
      <h2
        id="home-todo"
        className="text-muted font-mono text-2xs font-medium tracking-widest uppercase"
      >
        {t('title')}
      </h2>
      {items.map((item) => (
        <Link
          key={item.kind}
          href={item.href}
          className={cn(
            'hover:border-accent-ink focus-visible:outline-accent flex items-center gap-3 rounded-xl border p-3.5 transition-colors focus-visible:outline-2',
            KIND_STYLE[item.kind].row,
          )}
        >
          <span
            className={cn(
              'flex size-10 shrink-0 items-center justify-center rounded-lg font-bold tabular-nums',
              KIND_STYLE[item.kind].badge,
            )}
          >
            {item.kind === 'running' ? (
              <Loader2 aria-hidden="true" className="size-4.5 animate-spin" />
            ) : (
              format.number(item.count, 'credits')
            )}
          </span>
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-sm font-bold">{t(item.kind)}</span>
            {item.title && <span className="text-muted text-xs-plus truncate">{item.title}</span>}
          </span>
          <ChevronRight aria-hidden="true" className="text-muted size-4.5 shrink-0" />
        </Link>
      ))}
    </section>
  );
}
