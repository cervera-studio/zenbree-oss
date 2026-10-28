// @vitest-environment happy-dom
import { act, type ReactElement, useRef } from 'react';
import { describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { DEFAULT_MODE } from '@/lib/chat/home-modes';
import type { SendOptions } from '@/lib/chat/stream-store-types';
import { ComposerTextProvider, useComposerText } from './composer-text';
import { HomeSkeleton } from './home-data/home-data';
import { HomeEmpty } from './home-empty';
import { HomeWelcome, type WelcomeView } from './home-welcome';

vi.mock('@/app/(app)/actions', () => ({ dismissWelcomeAction: vi.fn() }));
vi.mock('@/components/lists/csv-import-dialog', () => ({ CsvImportDialog: () => null }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: () => {} }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));

const welcome: WelcomeView = {
  firstName: 'Ale',
  companyName: 'Ritmo Sales',
  knowsFrom: 'website',
  segments: [],
  steps: [{ kind: 'profile', done: true }],
};

type Surface = (props: {
  text: string;
  onText: (text: string) => void;
  onSend: (text: string, opts?: SendOptions) => void;
  composer: React.RefObject<HTMLTextAreaElement | null>;
}) => ReactElement;

const SURFACES: Record<string, Surface> = {
  HomeEmpty: (p) => <HomeEmpty {...p} mode={DEFAULT_MODE} onMode={() => {}} disabled={false} />,
  HomeWelcome: (p) => <HomeWelcome {...p} welcome={welcome} disabled={false} />,
  HomeData: (p) => <HomeSkeleton {...p} viewer={{ firstName: null, id: null }} disabled={false} />,
};

/** The store refuses a send with no credits: it calls `onRejected` before `send` returns. */
const refuse = (opts?: SendOptions) => opts?.onRejected?.();

function Harness({ Surface: S }: { Surface: Surface }) {
  const { draft, setDraft, giveBack } = useComposerText();
  const composer = useRef<HTMLTextAreaElement>(null);
  return (
    <S
      text={draft}
      onText={setDraft}
      composer={composer}
      onSend={(text, opts) => refuse({ ...opts, onRejected: giveBack(text) })}
    />
  );
}

describe.each(Object.keys(SURFACES))('%s with a send blocked for no credits (#1195)', (name) => {
  it('keeps the typed text in the composer', async () => {
    const c = await render(
      <ComposerTextProvider sessionId={null}>
        <Harness Surface={SURFACES[name] as Surface} />
      </ComposerTextProvider>,
    );
    const box = c.querySelector('textarea') as HTMLTextAreaElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
    await act(async () => {
      setter?.call(box, 'agenzie a Milano');
      box.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(control(c, 'Invia'));
    expect(box.value).toBe('agenzie a Milano');
  });
});
