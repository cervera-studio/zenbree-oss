import Link from 'next/link';
import { useFormatter, useTranslations } from 'next-intl';
import { StatusDot, type Tone } from '@/components/ui/badge';
import { TabLinks } from '@/components/ui/tab-links';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { CampaignSummary } from '@/lib/campaigns/api';
import { approvalBanner, inTab, TABS, type Tab } from '@/lib/campaigns/list-view';
import type { Channel } from '@/lib/campaigns/steps';
import { ApprovalBanner } from './banner';
import { ProgressBar } from './progress-bar';
import { StepIcon } from './step-icon';

const TONE: Record<CampaignSummary['status'], Tone> = {
  active: 'positive',
  draft: 'neutral',
  paused: 'warning',
  completed: 'accent',
  stopped: 'neutral',
};

export function CampaignsIndex({ campaigns, tab }: { campaigns: CampaignSummary[]; tab: Tab }) {
  const t = useTranslations('campaigns');
  const format = useFormatter();
  const shown = campaigns.filter((c) => inTab(c.status, tab));
  return (
    <>
      <IndexBanner campaigns={campaigns} />
      <TabLinks
        label={t('tabsLabel')}
        active={tab}
        tabs={TABS.map((key) => ({
          key,
          label: t(`tabs.${key}`),
          href: key === 'all' ? '/campaigns' : `/campaigns?status=${key}`,
          count: format.number(campaigns.filter((c) => inTab(c.status, key)).length, 'credits'),
        }))}
      />
      {shown.length === 0 ? (
        <p className="text-muted text-ui">{t('emptyTab')}</p>
      ) : (
        <>
          <CampaignTable campaigns={shown} />
          <ul className="flex flex-col gap-2 desktop:hidden">
            {shown.map((c) => (
              <li
                key={c.id}
                className="border-line bg-ground flex flex-col gap-2.5 rounded-lg border p-3.5"
              >
                <NameCell campaign={c} />
                <StatusCell campaign={c} />
                <div className="flex items-center justify-between gap-3">
                  <Channels channels={c.stepChannels} />
                  <Replies campaign={c} />
                </div>
                <Progress campaign={c} />
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function IndexBanner({ campaigns }: { campaigns: CampaignSummary[] }) {
  const t = useTranslations('campaigns');
  const banner = approvalBanner(campaigns);
  if (!banner) return null;
  return (
    <ApprovalBanner
      total={banner.total}
      where={
        banner.campaigns === 1 ? banner.top.name : t('bannerMany', { count: banner.campaigns })
      }
      href={`/campaigns/${banner.top.id}/approvals`}
    />
  );
}

function CampaignTable({ campaigns }: { campaigns: CampaignSummary[] }) {
  const t = useTranslations('campaigns.columns');
  return (
    <div className="phone:hidden">
      <Table frame className="[&_td]:h-13">
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('campaign')}</TableHead>
            <TableHead scope="col" className="w-50">
              {t('status')}
            </TableHead>
            <TableHead scope="col" className="w-30">
              {t('steps')}
            </TableHead>
            <TableHead scope="col" className="w-65">
              {t('progress')}
            </TableHead>
            <TableHead scope="col" className="w-30" num>
              {t('replies')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {campaigns.map((c) => (
            <TableRow key={c.id}>
              <TableCell className="whitespace-normal">
                <NameCell campaign={c} />
              </TableCell>
              <TableCell>
                <StatusCell campaign={c} />
              </TableCell>
              <TableCell>
                <Channels channels={c.stepChannels} />
              </TableCell>
              <TableCell>
                <Progress campaign={c} />
              </TableCell>
              <TableCell num>
                <Replies campaign={c} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function NameCell({ campaign: c }: { campaign: CampaignSummary }) {
  const t = useTranslations('campaigns');
  return (
    <div className="flex flex-col gap-px">
      <Link href={`/campaigns/${c.id}`} className="text-ui-strong text-ink hover:underline">
        {c.name}
      </Link>
      <span className="text-small text-muted flex items-center gap-1">
        {c.listName && <span className="truncate">{c.listName} ·</span>}
        <span className="whitespace-nowrap">{t('people', { count: c.targetCount })}</span>
      </span>
    </div>
  );
}

function StatusCell({ campaign: c }: { campaign: CampaignSummary }) {
  const t = useTranslations('campaigns');
  return (
    <div className="flex flex-wrap items-center gap-2">
      <StatusDot tone={TONE[c.status]}>{t(`status.${c.status}`)}</StatusDot>
      {c.awaitingApproval > 0 && (
        <span className="text-warning text-small whitespace-nowrap">
          {t('awaiting', { count: c.awaitingApproval })}
        </span>
      )}
      {c.awaitingApprovalHeld > 0 && (
        <span className="text-muted text-small whitespace-nowrap">
          {t('awaitingHeld', { count: c.awaitingApprovalHeld })}
        </span>
      )}
    </div>
  );
}

function Channels({ channels }: { channels: Channel[] }) {
  const t = useTranslations('campaigns.step.channels');
  if (channels.length === 0) return <span className="text-muted">—</span>;
  return (
    <ul className="flex gap-1">
      {channels.map((channel, i) => (
        <li
          // Steps are positional and repeat channels.
          key={i}
          title={t(channel)}
        >
          <StepIcon channel={channel} />
          <span className="sr-only">{t(channel)}</span>
        </li>
      ))}
    </ul>
  );
}

function Progress({ campaign: c }: { campaign: CampaignSummary }) {
  const t = useTranslations('campaigns');
  return (
    <div className="flex w-full flex-col gap-1.25">
      <ProgressBar
        value={c.stepsDone}
        max={Math.max(c.plannedSends, c.stepsDone)}
        label={t('columns.progress')}
      />
      <span className="text-data-sm text-muted font-mono whitespace-nowrap">
        {t('progress', { sent: c.sent, failed: c.failed })}
      </span>
    </div>
  );
}

function Replies({ campaign: c }: { campaign: CampaignSummary }) {
  const format = useFormatter();
  return (
    <span className="text-data text-ink font-mono whitespace-nowrap tabular-nums">
      {c.replied}
      {c.contacted > 0 && (
        <span>
          {' · '}
          {format.number(c.repliedContacted / c.contacted, { style: 'percent' })}
        </span>
      )}
    </span>
  );
}
