import { getFormatter, getLocale, getTimeZone, getTranslations } from 'next-intl/server';
import { Empty } from '@/components/admin/api-error';
import { Badge, type Tone } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatDate, formatDay, formatMoney } from '@/lib/admin/format';
import { cn } from '@/lib/utils';

type Tx = {
  id: string;
  type: string;
  status: string;
  amount: number;
  balanceAfter: number;
  referenceType: string | null;
  description: string | null;
  toolName: string | null;
  createdAt: string;
  /** Who, on an organization's ledger (#327). */
  email?: string | null;
};

type Purchase = {
  id: string;
  packId: string;
  credits: number;
  amountCents: number;
  refundedCents: number;
  createdAt: string;
};

// `amount` is always positive in the database: the type decides whether it adds or removes credits.
const KINDS = {
  deduction: { sign: -1, tone: 'neutral' },
  reservation: { sign: -1, tone: 'neutral' },
  refund: { sign: 1, tone: 'positive' },
  renewal: { sign: 1, tone: 'accent' },
  purchase: { sign: 1, tone: 'accent' },
  bonus: { sign: 1, tone: 'accent' },
  adjustment: { sign: 1, tone: 'warning' },
} as const satisfies Record<string, { sign: 1 | -1; tone: Tone }>;

type Kind = keyof typeof KINDS;
const isKind = (type: string): type is Kind => Object.hasOwn(KINDS, type);

/** A wallet's movements: a user's, or (with `who`) an organization's. */
export async function LedgerTable({ ledger, who = false }: { ledger: Tx[]; who?: boolean }) {
  const [t, locale, timeZone, format] = [
    await getTranslations('admin'),
    await getLocale(),
    await getTimeZone(),
    await getFormatter(),
  ];
  if (ledger.length === 0) return <Empty />;
  return (
    <Table frame>
      <TableHeader>
        <TableRow>
          <TableHead>{t('createdAt')}</TableHead>
          {who && <TableHead>{t('orgDetail.who')}</TableHead>}
          <TableHead>{t('userDetail.type')}</TableHead>
          <TableHead>{t('userDetail.txStatus')}</TableHead>
          <TableHead num>{t('userDetail.amount')}</TableHead>
          <TableHead num>{t('userDetail.balanceAfter')}</TableHead>
          <TableHead>{t('userDetail.description')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ledger.map((tx) => {
          const kind = isKind(tx.type) ? KINDS[tx.type] : null;
          const admin = tx.referenceType === 'admin';
          const amount = format.number(tx.amount, 'credits');
          return (
            <TableRow key={tx.id}>
              <TableCell>{formatDate(tx.createdAt, { locale, timeZone })}</TableCell>
              {who && (
                <TableCell className="max-w-48 truncate">{tx.email ?? t('formerMember')}</TableCell>
              )}
              <TableCell>
                {kind ? (
                  <Badge tone={admin ? 'warning' : kind.tone}>
                    {t(`userDetail.txTypes.${admin ? 'admin' : (tx.type as Kind)}`)}
                  </Badge>
                ) : (
                  <Badge tone="neutral">{tx.type}</Badge>
                )}
              </TableCell>
              <TableCell>{tx.status}</TableCell>
              <TableCell num className={cn('text-ui-strong', kind?.sign === 1 && 'text-positive')}>
                {kind ? `${kind.sign === 1 ? '+' : '\u2212'}${amount}` : amount}
              </TableCell>
              <TableCell num>{format.number(tx.balanceAfter, 'credits')}</TableCell>
              <TableCell className="max-w-xs truncate">
                {tx.description ?? tx.toolName ?? '—'}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export async function PurchasesTable({ purchases }: { purchases: Purchase[] }) {
  const [t, locale, timeZone, format] = [
    await getTranslations('admin'),
    await getLocale(),
    await getTimeZone(),
    await getFormatter(),
  ];
  if (purchases.length === 0) return <Empty />;
  return (
    <Table frame>
      <TableHeader>
        <TableRow>
          <TableHead>{t('userDetail.date')}</TableHead>
          <TableHead>{t('userDetail.pack')}</TableHead>
          <TableHead num>{t('userDetail.credits')}</TableHead>
          <TableHead num>{t('userDetail.paid')}</TableHead>
          <TableHead num>{t('userDetail.refunded')}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {purchases.map((p) => (
          <TableRow key={p.id}>
            <TableCell>{formatDay(p.createdAt, { locale, timeZone })}</TableCell>
            <TableCell>{p.packId}</TableCell>
            <TableCell num>{format.number(p.credits, 'credits')}</TableCell>
            <TableCell num>{formatMoney(p.amountCents / 100, 'EUR', locale)}</TableCell>
            <TableCell num>
              {p.refundedCents ? formatMoney(p.refundedCents / 100, 'EUR', locale) : '—'}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
