'use client';

import { type ReactNode, type RefObject, Suspense, use } from 'react';
import type { HomeModeId } from '@/lib/chat/home-modes';
import { chooseHome } from '@/lib/chat/home-overview';
import type { SendOptions } from '@/lib/chat/stream-store-types';
import type { HomeOverview } from '@/lib/home/overview';
import { HomeData, HomeSkeleton, type HomeViewer } from './home-data/home-data';
import { HomeEmpty } from './home-empty';
import { HomeWelcome, type WelcomeView } from './home-welcome';

export interface HomeStartProps {
  overviewRequest: Promise<HomeOverview | null>;
  welcome: WelcomeView | null | undefined;
  viewer: HomeViewer;
  text: string;
  onText: (text: string) => void;
  /** The empty home's mode; the other screens send in the default one. */
  mode: HomeModeId;
  onMode: (mode: HomeModeId) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  onSend: (text: string, opts?: SendOptions) => void;
  disabled: boolean;
  todo?: ReactNode;
  stats?: ReactNode;
}

function Screen({ overviewRequest, welcome, viewer, todo, stats, ...composer }: HomeStartProps) {
  const screen = chooseHome(welcome, welcome ? null : use(overviewRequest));
  if (screen.kind === 'welcome')
    return <HomeWelcome welcome={screen.welcome} todo={todo} {...composer} />;
  if (screen.kind === 'data')
    return <HomeData overview={screen.overview} viewer={viewer} stats={stats} {...composer} />;
  return <HomeEmpty todo={todo} {...composer} />;
}

export function HomeStart(props: HomeStartProps) {
  const { viewer, text, onText, composer, onSend, disabled } = props;
  return (
    <Suspense
      fallback={
        <HomeSkeleton
          viewer={viewer}
          text={text}
          onText={onText}
          composer={composer}
          onSend={onSend}
          disabled={disabled}
        />
      }
    >
      <Screen {...props} />
    </Suspense>
  );
}
