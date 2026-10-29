import { ArrowRight, Clock, type LucideIcon } from 'lucide-react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

const TONES = {
  warning: 'bg-warning-tint text-warning',
  negative: 'bg-negative-tint text-negative',
} as const;

/** A one-line notice under the page head: the approval banner (warning) and the builder's blockers (negative). */
export function Banner({
  tone,
  icon: Icon,
  role,
  children,
}: {
  tone: keyof typeof TONES;
  icon: LucideIcon;
  role: 'status' | 'alert';
  children: ReactNode;
}) {
  return (
    <div
      role={role}
      className={cn(
        'text-ui flex min-h-10 items-center gap-2.5 rounded-lg px-3.5 py-2',
        TONES[tone],
      )}
    >
      <Icon className="size-3.75 shrink-0" aria-hidden />
      {children}
    </div>
  );
}

/** Messages waiting for approval, where they wait, and «Rivedi» into the approval screen. */
export function ApprovalBanner({
  total,
  where,
  href,
}: {
  total: number;
  where: string;
  href: string;
}) {
  const t = useTranslations('campaigns');
  return (
    <Banner tone="warning" icon={Clock} role="status">
      <p className="text-ui min-w-0 grow">
        <strong className="text-ui-strong">{t('banner', { count: total })}</strong>
        <span>
          {' · '}
          {where}
        </span>
      </p>
      <Link
        href={href}
        className="text-accent-ink text-ui-strong inline-flex shrink-0 items-center gap-1 hover:underline phone:min-h-11"
      >
        {t('review')}
        <ArrowRight className="size-3.5" aria-hidden />
      </Link>
    </Banner>
  );
}
