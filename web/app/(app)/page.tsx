import { getLocale } from 'next-intl/server';
import { Suspense } from 'react';
import { ChatApp } from '@/components/chat/chat-app';
import { StatsBlock } from '@/components/chat/home-data/stats-block';
import { TodoBlock } from '@/components/chat/todo-block';
import { getHomeOverview, getHomeWelcome } from '@/lib/api/home';
import { getMe } from '@/lib/api/me';
import { getSession } from '@/lib/auth-token';
import { getPacks } from '@/lib/billing/api';
import { smallestPack } from '@/lib/chat/plan';

export default async function Home() {
  const welcomeRequest = getLocale().then(getHomeWelcome);
  const overviewRequest = welcomeRequest.then((w) => (w ? null : getHomeOverview()));
  const [me, packs, session, welcome] = await Promise.all([
    getMe(),
    getPacks(),
    getSession(),
    welcomeRequest,
  ]);
  const firstName = session?.user.name?.trim().split(/\s+/)[0] || null;
  return (
    <Suspense>
      <ChatApp
        initialCredits={me.success ? me.data.creditsRemaining : null}
        topUpPack={packs.success ? smallestPack(packs.data.packs) : null}
        todo={<TodoBlock />}
        stats={<StatsBlock overview={overviewRequest} />}
        welcome={welcome && { ...welcome, firstName }}
        overviewRequest={overviewRequest}
        viewer={{ firstName, id: session?.user.id ?? null }}
      />
    </Suspense>
  );
}
