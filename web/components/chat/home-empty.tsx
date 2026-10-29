'use client';

import { Check, ChevronDown, Paperclip } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { type ReactNode, type RefObject, useState } from 'react';
import { AppPage } from '@/components/app/page';
import { CsvImportDialog } from '@/components/lists/csv-import-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  chatActionFor,
  HOME_CHIPS,
  HOME_MODE_IDS,
  HOME_MODES,
  type HomeModeId,
} from '@/lib/chat/home-modes';
import type { SendOptions } from '@/lib/chat/stream-store-types';
import { HomeComposer } from './home-composer';

export function HomeEmpty({
  text,
  onText,
  mode,
  onMode,
  composer,
  onSend,
  disabled,
  todo,
}: {
  text: string;
  onText: (text: string) => void;
  mode: HomeModeId;
  onMode: (mode: HomeModeId) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  onSend: (text: string, opts?: SendOptions) => void;
  disabled: boolean;
  /** Phone only: a server component, so the home page can refresh it. */
  todo?: ReactNode;
}) {
  const t = useTranslations('chat');
  const th = useTranslations('chat.home');
  const tNav = useTranslations('nav');
  const [importDraft, setImportDraft] = useState<string | null>(null);
  const { icon: ModeIcon, action } = HOME_MODES[mode];

  const submit = (brief: string) => {
    if (action.kind === 'import') return setImportDraft(brief);
    onText('');
    onSend(brief, { goal: action.goal });
  };

  const pick = (id: HomeModeId) => {
    if (HOME_MODES[id].action.kind === 'import') return setImportDraft('');
    onMode(id);
    composer.current?.focus();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <AppPage
        crumbs={[{ label: tNav('searches'), href: '/chats' }, { label: t('newChat') }]}
        crumbsLabel={t('breadcrumb')}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="m-auto flex w-full max-w-180 flex-col gap-7">
          <div className="flex flex-col gap-2.5 desktop:items-center desktop:text-center">
            <h1 className="text-title desktop:text-headline">{t('emptyTitle')}</h1>
            <p className="text-ui text-muted desktop:text-lead">{t('emptyBody')}</p>
          </div>

          <HomeComposer
            text={text}
            onText={onText}
            composer={composer}
            onSubmit={submit}
            disabled={disabled}
            placeholder={th(`modes.${mode}.placeholder`)}
            tools={
              <>
                <Button
                  type="button"
                  variant="outline"
                  size="icon-xs"
                  className="phone:size-11"
                  aria-label={th('attach')}
                  disabled={disabled}
                  onClick={() => setImportDraft('')}
                >
                  <Paperclip />
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      className="text-soft font-normal phone:hidden"
                      aria-label={th('modeLabel')}
                      disabled={disabled}
                    >
                      <ModeIcon className="size-3.5" />
                      {th(`modes.${mode}.label`)}
                      <ChevronDown className="size-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start">
                    {HOME_MODE_IDS.map((id) => {
                      const Icon = HOME_MODES[id].icon;
                      return (
                        <DropdownMenuItem key={id} onSelect={() => onMode(id)}>
                          <Icon />
                          {th(`modes.${id}.label`)}
                          {id === mode && <Check className="ml-auto" />}
                        </DropdownMenuItem>
                      );
                    })}
                  </DropdownMenuContent>
                </DropdownMenu>
              </>
            }
          />

          <div className="flex flex-wrap justify-center gap-2 phone:-mx-4 phone:flex-nowrap phone:justify-start phone:overflow-x-auto phone:px-4 scrollbar-none">
            {HOME_CHIPS.map(({ id, icon: Icon }) => (
              <button
                key={id}
                type="button"
                disabled={disabled}
                onClick={() => onSend(th(`chips.${id}.brief`), { goal: chatActionFor(mode).goal })}
                className="text-ui border-line text-soft hover:bg-raised focus-visible:ring-accent/50 flex h-control-sm shrink-0 items-center gap-1.5 rounded-md border px-2.25 whitespace-nowrap transition-colors outline-none focus-visible:ring-3 disabled:opacity-45 phone:h-10"
              >
                <Icon className="size-3.5" />
                <span className="phone:hidden">{th(`chips.${id}.brief`)}</span>
                <span className="desktop:hidden">{th(`chips.${id}.short`)}</span>
              </button>
            ))}
          </div>

          <div className="grid grid-cols-3 gap-3 phone:hidden">
            {HOME_MODE_IDS.map((id) => {
              const Icon = HOME_MODES[id].icon;
              return (
                <button
                  key={id}
                  type="button"
                  disabled={disabled}
                  onClick={() => pick(id)}
                  className="border-line bg-ground hover:bg-raised focus-visible:ring-accent/50 flex flex-col items-start gap-1.5 rounded-lg border p-3.5 text-left transition-colors outline-none focus-visible:ring-3 disabled:opacity-45"
                >
                  <span className="bg-accent-tint text-accent-ink flex size-8 items-center justify-center rounded-md">
                    <Icon className="size-4" />
                  </span>
                  <span className="text-ui-strong">{th(`modes.${id}.title`)}</span>
                  <span className="text-small text-muted">{th(`modes.${id}.body`)}</span>
                </button>
              );
            })}
          </div>

          <div className="desktop:hidden">{todo}</div>
        </div>
      </AppPage>

      <CsvImportDialog
        open={importDraft !== null}
        onOpenChange={(open) => {
          if (!open) setImportDraft(null);
        }}
        draft={importDraft ?? undefined}
      />
    </div>
  );
}
