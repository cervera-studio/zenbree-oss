'use client';

import { useTranslations } from 'next-intl';
import { ChipFace, chipClass } from '@/components/lists/list-bits';
import { Badge, type Tone } from '@/components/ui/badge';
import type { ListEntity } from '@/lib/chat/chat-lists';
import type { ChatStatus, ChatView } from '@/lib/chat/chat-view';
import { cn } from '@/lib/utils';

const STATUS: Record<
  ChatStatus,
  {
    key: 'offline' | 'running' | 'planReady' | 'needsAnswer' | 'stepError' | 'allFailed' | 'done';
    tone: Tone;
  }
> = {
  offline: { key: 'offline', tone: 'warning' },
  running: { key: 'running', tone: 'accent' },
  plan_ready: { key: 'planReady', tone: 'accent' },
  needs_answer: { key: 'needsAnswer', tone: 'warning' },
  step_error: { key: 'stepError', tone: 'negative' },
  all_failed: { key: 'allFailed', tone: 'negative' },
  done: { key: 'done', tone: 'positive' },
};

type Chip = ChatView['chips'][number];

function ListChipButton({
  chip,
  open,
  onToggle,
}: {
  chip: Chip;
  open: boolean;
  onToggle: (entity: ListEntity) => void;
}) {
  const t = useTranslations('chat');
  const { entity } = chip.list;
  if (chip.kind === 'rows') {
    return (
      <button
        type="button"
        aria-pressed={open}
        onClick={() => onToggle(entity)}
        className={chipClass(open ? 'current' : 'plain', 'phone:h-11 phone:px-3.5')}
      >
        <ChipFace kind={entity} count={chip.count} quiet={!open} />
      </button>
    );
  }
  return (
    <span className={chipClass('dashed', 'phone:h-11 phone:px-3.5')}>
      <ChipFace
        kind={entity}
        count={chip.kind === 'draft' ? t('draftChip') : (chip.list.rowCount ?? '—')}
      />
    </span>
  );
}

/**
 * What the chat adds to the topbar: the status, the chat's lists and its credits. On a phone the
 * topbar has no room for it, so the same parts sit in a strip under the topbar (`placement`).
 */
export function ChatHeader({
  view,
  openEntity,
  onToggleList,
  placement = 'topbar',
}: {
  view: ChatView;
  openEntity: ListEntity | null;
  onToggleList: (entity: ListEntity) => void;
  placement?: 'topbar' | 'strip';
}) {
  const t = useTranslations('chat');
  const status = view.status ? STATUS[view.status] : null;
  const credits = t('creditsUsed', { count: view.creditsUsed });
  const strip = placement === 'strip';
  return (
    <div
      className={cn(
        'flex min-w-0 items-center gap-3',
        strip && 'flex-wrap gap-y-2 border-b px-4 py-2',
      )}
    >
      <h1 className="sr-only">{view.title ?? t('untitled')}</h1>
      {strip && status && (
        <p className="text-small text-muted">
          <span role="status">{t(`status.${status.key}`)}</span> · {credits}
        </p>
      )}
      {!strip && status && (
        <span role="status">
          <Badge tone={status.tone} dot>
            {t(`status.${status.key}`)}
          </Badge>
        </span>
      )}
      {view.chips.length > 0 && (
        <fieldset
          aria-label={t('listsGroup')}
          className={cn(
            'm-0 flex min-w-0 items-center gap-2 border-0 p-0',
            openEntity && 'desktop:hidden',
          )}
        >
          {view.chips.map((chip) => (
            <ListChipButton
              key={chip.list.id}
              chip={chip}
              open={openEntity === chip.list.entity}
              onToggle={onToggleList}
            />
          ))}
        </fieldset>
      )}
      {!strip && status && (
        <span className="text-small text-muted whitespace-nowrap tabular-nums">{credits}</span>
      )}
    </div>
  );
}
