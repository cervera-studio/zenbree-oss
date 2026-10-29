'use client';

import { Paperclip, Plus } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { type ReactNode, type RefObject, useState, useSyncExternalStore } from 'react';
import { AppPage } from '@/components/app/page';
import { CsvImportDialog } from '@/components/lists/csv-import-dialog';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useBrowserTimeZone } from '@/i18n/browser-time-zone';
import { chatActionFor, DEFAULT_MODE } from '@/lib/chat/home-modes';
import { dayPart } from '@/lib/chat/home-overview';
import type { SendOptions } from '@/lib/chat/stream-store-types';
import { visibleLists } from '@/lib/home/live';
import type { HomeOverview } from '@/lib/home/overview';
import { homeStoreFor } from '@/lib/home/store';
import { HomeComposer } from '../home-composer';
import { HomeCampaigns } from './home-campaigns';
import { HomeLists } from './home-lists';
import { HomeLiveRegion } from './home-live-region';
import { HomeRunning } from './home-running';
import { HomeTodo } from './home-todo';

export interface HomeViewer {
  firstName: string | null;
  id: string | null;
}

function SkeletonSections() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-4">
      <Skeleton className="h-28 w-full" />
      <div className="grid grid-cols-12 gap-4 phone:grid-cols-1">
        <Skeleton className="col-span-7 h-48 w-full phone:col-span-1" />
        <Skeleton className="col-span-5 h-48 w-full phone:col-span-1" />
      </div>
    </div>
  );
}

interface ComposerProps {
  viewer: HomeViewer;
  text: string;
  onText: (text: string) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  onSend: (text: string, opts?: SendOptions) => void;
  disabled: boolean;
}

function HomeShell({
  busy,
  viewer,
  text,
  onText,
  composer,
  onSend,
  disabled,
  children,
}: ComposerProps & { busy: boolean; children: ReactNode }) {
  const t = useTranslations('chat.home.data');
  const th = useTranslations('chat.home');
  const tc = useTranslations('chat');
  const tn = useTranslations('nav');
  const format = useFormatter();
  const [importOpen, setImportOpen] = useState(false);
  const goal = chatActionFor(DEFAULT_MODE).goal;

  const timeZone = useBrowserTimeZone();
  const now = new Date();
  const date = timeZone
    ? format.dateTime(now, { weekday: 'long', day: 'numeric', month: 'long', timeZone })
    : '';
  const greeting = timeZone
    ? t('greeting', { part: dayPart(now, timeZone), name: viewer.firstName ?? 'none' })
    : '';

  return (
    <AppPage
      crumbs={[{ label: tn('overview') }]}
      inProject
      crumbsLabel={tc('breadcrumb')}
      topbarActions={
        <Button
          type="button"
          size="sm"
          disabled={disabled}
          onClick={() => composer.current?.focus()}
        >
          <Plus aria-hidden />
          {tc('newChat')}
        </Button>
      }
      title={tn('overview')}
      meta={
        timeZone && (
          <>
            <span className="phone:hidden">{`${date.charAt(0).toUpperCase()}${date.slice(1)} · `}</span>
            {greeting}
          </>
        )
      }
      busy={busy}
    >
      <HomeComposer
        text={text}
        onText={onText}
        composer={composer}
        onSubmit={(brief) => {
          onText('');
          onSend(brief, { goal });
        }}
        disabled={disabled}
        placeholder={th('modes.find.placeholder')}
        tools={
          <Button
            type="button"
            variant="outline"
            size="icon-md"
            className="phone:size-11"
            aria-label={th('attach')}
            disabled={disabled}
            onClick={() => setImportOpen(true)}
          >
            <Paperclip />
          </Button>
        }
      />

      {children}

      <CsvImportDialog open={importOpen} onOpenChange={setImportOpen} draft="" />
    </AppPage>
  );
}

export function HomeSkeleton(props: ComposerProps) {
  return (
    <HomeShell busy {...props}>
      <SkeletonSections />
    </HomeShell>
  );
}

export function HomeData({
  overview,
  stats,
  ...props
}: ComposerProps & { overview: HomeOverview; stats?: ReactNode }) {
  const router = useRouter();
  // The refresh renders the overview of the organization a removed member moved to: a new store.
  const store = homeStoreFor(overview, () => router.refresh());
  const live = useSyncExternalStore(store.subscribe, store.get, store.get);
  return (
    <HomeShell busy={false} {...props}>
      <HomeLiveRegion announced={live.announced} />
      <HomeTodo overview={live.overview} pulses={live.pulses} />
      <HomeRunning runs={live.runs} viewerId={props.viewer.id} onEvent={store.dispatch} />
      {stats}
      <div className="grid grid-cols-12 items-start gap-4 phone:grid-cols-1">
        <div className="col-span-7 min-w-0 phone:col-span-1">
          <HomeLists lists={visibleLists(live)} fresh={live.fresh} />
        </div>
        <div className="col-span-5 min-w-0 phone:col-span-1">
          <HomeCampaigns
            campaigns={live.overview.campaigns}
            hint={live.overview.campaignHint}
            pulses={live.pulses}
          />
        </div>
      </div>
    </HomeShell>
  );
}
