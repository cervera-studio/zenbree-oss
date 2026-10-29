import { getTranslations } from 'next-intl/server';
import { StatusDot, Tag } from '@/components/ui/badge';
import { hasRole } from '@/lib/roles';

export async function RolePill({ role }: { role?: string | null }) {
  const t = await getTranslations('admin.userList');
  return hasRole(role, 'admin') ? (
    <Tag tone="warning">{t('roleAdmin')}</Tag>
  ) : (
    <Tag tone="neutral">{t('roleUser')}</Tag>
  );
}

export async function BanPill({
  banned,
  reason,
}: {
  banned?: boolean | null;
  reason?: string | null;
}) {
  const t = await getTranslations('admin.userList');
  return banned ? (
    <span title={reason ?? undefined}>
      <StatusDot tone="negative">{t('banned')}</StatusDot>
    </span>
  ) : (
    <StatusDot tone="positive">{t('active')}</StatusDot>
  );
}
