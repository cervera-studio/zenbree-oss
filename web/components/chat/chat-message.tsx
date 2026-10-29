'use client';

import { Sparkles } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { allSearchesFailed } from '@/lib/chat/chat-view';
import { HIDDEN_TOOLS } from '@/lib/chat/hidden-tools';
import { allStagesFailed, failedStage, type JobProgress } from '@/lib/chat/job-progress';
import type { TopUpPack } from '@/lib/chat/plan';
import { dropTrailingQuestionCopy, memorySaved } from '@/lib/chat/question';
import type { SendOptions, UIMessage } from '@/lib/chat/stream-store-types';
import type { AgentPlan } from '@/lib/chat/types';
import { CampaignDraftCard } from './campaign-draft-card';
import { MemoryNote } from './memory-note';
import { NoResultsCard } from './no-results-card';
import { PlanCard } from './plan-card';
import { QuestionCard } from './question-card';
import { SourcesList } from './sources-list';
import { StepsCard } from './steps-card';
import { ToolSummaryCard } from './tool-summary-card';

function Markdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm text-ui text-ink dark:prose-invert max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // A fetched page can steer the answer into an image whose URL carries chat data (#407).
        disallowedElements={['img']}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export interface MessageSteps {
  progress: JobProgress;
  running: boolean;
  credits: number;
  onRetry?: (stageIndex: number) => void;
  listHref?: string;
}

function AllFailedNotice({ credits, rows }: { credits: number; rows: ReactNode[] }) {
  const t = useTranslations('chat.steps');
  return (
    <>
      <p role="alert" className="leading-normal">
        {t('allFailed', { credits })}
      </p>
      {rows.length > 0 && (
        <details className="text-small text-muted">
          <summary className="cursor-pointer">{t('details')}</summary>
          <div className="mt-2 flex flex-col gap-2">{rows}</div>
        </details>
      )}
    </>
  );
}

function TurnNotices({ message, showTurnFailed }: { message: UIMessage; showTurnFailed: boolean }) {
  const t = useTranslations('chat');
  const tErr = useTranslations('chat.errors');
  return (
    <>
      {message.terminalCode && (
        <p className="bg-warning-tint text-ui text-warning rounded-md px-3 py-2">
          {tErr.has(`terminals.${message.terminalCode}` as 'terminals.max_turns_reached')
            ? tErr(`terminals.${message.terminalCode}` as 'terminals.max_turns_reached')
            : tErr('genericError')}
        </p>
      )}
      {showTurnFailed && (
        <p role="alert" className="bg-negative-tint text-ui text-negative rounded-md px-3 py-2.5">
          {t('turnFailed', { credits: message.creditsUsed ?? 0 })}
        </p>
      )}
    </>
  );
}

export function ChatMessage({
  message,
  streaming,
  credits,
  topUpPack = null,
  companyRows,
  onAnswer,
  onEditPlan,
  steps,
}: {
  message: UIMessage;
  streaming: boolean;
  credits: number | null;
  topUpPack?: TopUpPack | null;
  companyRows: number | null;
  /** Set while this message's `ask_user` question (or plan) is still the open one. */
  onAnswer?: (text: string, opts?: SendOptions) => void;
  onEditPlan?: () => void;
  steps?: MessageSteps;
}) {
  const t = useTranslations('chat');
  const plan = message.question?.plan;
  const notes = memorySaved(message.toolCalls);
  const approveText = (p: AgentPlan, rows: number, credits: number, dropped: number[]) =>
    t(dropped.length > 0 ? 'plan.approveMessageDropped' : 'plan.approveMessage', {
      version: p.version,
      rows,
      credits,
      dropped: dropped.map((i) => p.stages[i]?.label).join(', '),
    });

  if (message.role === 'user') {
    return (
      <div className="bg-accent-tint text-ui text-ink max-w-bubble self-start rounded-lg px-3 py-2.5 whitespace-pre-wrap">
        {message.content}
      </div>
    );
  }

  const byId = new Map(message.toolCalls.map((tc) => [tc.id, tc]));
  const anyRunning = message.toolCalls.some((tc) => tc.status === 'running');
  const showPulse = streaming && !anyRunning && (message.isWriting || !message.content);
  const ownNotice = !streaming && !steps && allSearchesFailed(message.toolCalls);
  const stagesFailed = !!steps && !steps.running && allStagesFailed(steps.progress);
  const hasText = message.parts.some((p) => p.kind === 'text' && p.content.trim() !== '');
  const toolRows: ReactNode[] = [];
  const parts = dropTrailingQuestionCopy(message.parts, message.question?.question).map((part) => {
    if (part.kind === 'text') return <Markdown key={part.id} text={part.content} />;
    const call = byId.get(part.toolCallId);
    if (call?.campaign) return <CampaignDraftCard key={part.id} campaign={call.campaign} />;
    if (!call || HIDDEN_TOOLS.has(call.name) || call.memorySaved) return null;
    const row = <ToolSummaryCard key={part.id} call={call} />;
    if (stagesFailed) return null;
    if (!ownNotice) return row;
    toolRows.push(row);
    return null;
  });
  // A failed step already says what broke and offers the retry: no second notice.
  const showTurnFailed =
    message.turnFailed && !(steps && failedStage(steps.progress)) && !ownNotice;

  return (
    <div className="flex flex-col gap-1.5">
      {(hasText || ownNotice || stagesFailed) && (
        <span className="text-small text-accent-ink flex items-center gap-1.25">
          <Sparkles className="size-3.25" aria-hidden />
          {t('agent')}
        </span>
      )}
      {steps && (
        <StepsCard
          progress={steps.progress}
          running={steps.running}
          credits={steps.credits}
          onRetry={steps.onRetry}
          listHref={steps.listHref}
        />
      )}
      {ownNotice && <AllFailedNotice credits={message.creditsUsed ?? 0} rows={toolRows} />}
      {parts}
      {showPulse && <p className="text-ui text-muted animate-pulse">{t('thinking')}</p>}
      {plan && (
        <PlanCard
          plan={plan}
          balance={credits}
          topUpPack={topUpPack}
          companyRows={companyRows}
          onEdit={onAnswer && onEditPlan}
          onApprove={
            onAnswer &&
            ((rows, credits, droppedStages) =>
              onAnswer(approveText(plan, rows, credits, droppedStages), {
                planApproval: {
                  listId: plan.listId,
                  version: plan.version,
                  rows,
                  droppedStages,
                },
              }))
          }
        />
      )}
      {message.question?.funnel && (
        <NoResultsCard
          question={message.question}
          funnel={message.question.funnel}
          creditsUsed={message.creditsUsed ?? 0}
          onAnswer={onAnswer && ((text, pick) => onAnswer(text, { pick }))}
        />
      )}
      {message.question && !plan && !message.question.funnel && (
        <QuestionCard question={message.question} onAnswer={onAnswer} />
      )}
      {notes.length > 0 && <MemoryNote notes={notes} />}
      <TurnNotices message={message} showTurnFailed={!!showTurnFailed} />
      {message.interrupted && !streaming && (
        <p className="text-ui text-muted">{t('interrupted')}</p>
      )}
      {message.sources && message.sources.length > 0 && <SourcesList sources={message.sources} />}
    </div>
  );
}
