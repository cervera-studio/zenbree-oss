'use client';

import { useRouter } from 'next/navigation';
import { useFormatter, useTranslations } from 'next-intl';
import { type SetStateAction, useCallback, useEffect, useReducer } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { useErrorText } from '@/i18n/error-text';
import type { Schema } from '@/lib/api/schema';
import { listsApi, MAX_UPLOAD_BYTES } from '@/lib/lists/api';
import { mappingComplete } from '@/lib/lists/columns';
import { csvImportApi, useImportStream } from '@/lib/lists/csv-import';
import { useCurrentProjectId } from '@/lib/projects';
import type { AppErrorJSON } from '@/lib/types';
import { FileStep } from './csv-file-step';
import { ErrorNotice, ImportFooter, ImportHeader } from './csv-import-chrome';
import { DoneStep, ProgressStep } from './csv-import-progress';
import { ReviewStep } from './csv-import-review';
import { initialState, reducer } from './csv-import-state';
import { MappingStep } from './csv-mapping-step';

/**
 * CSV -> a new chat holding the list, in four steps: type + file, column
 * mapping, review of the counts, the import itself, which the worker runs and the dialog follows
 * (closing it does not stop it; reopening picks it up). Importing is free; finding the missing
 * emails prefills the request in that chat, where the user sends it and approves a plan.
 * `draft` is text the user already typed (the home composer): it leads the chat's draft.
 */
export function CsvImportDialog({
  open,
  onOpenChange,
  draft,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  draft?: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined} className="gap-0 p-0 sm:max-w-180 rounded-xl">
        <ImportFlow draft={draft} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function ImportFlow({ draft, onDone }: { draft?: string; onDone: () => void }) {
  const t = useTranslations('lists');
  const errorText = useErrorText();
  const format = useFormatter();
  const router = useRouter();
  const projectId = useCurrentProjectId();
  const [state, dispatch] = useReducer(reducer, initialState);
  const imp = state.step === 'import' ? state.imp : null;
  const updateImp = useCallback(
    (next: SetStateAction<Schema<'CsvImportState'> | null>) =>
      dispatch({ type: 'importUpdated', next }),
    [],
  );
  useImportStream(imp, updateImp);

  // Reopened while an import runs, or ended unseen: pick it up where it is.
  useEffect(() => {
    let stale = false;
    void csvImportApi.active().then((found) => {
      if (stale || !found.success || !found.data) return;
      dispatch({ type: 'importResumed', imp: found.data });
    });
    return () => {
      stale = true;
    };
  }, []);

  const failed = (e: AppErrorJSON) =>
    dispatch({ type: 'requestFailed', notice: { title: t('uploadError'), body: errorText(e) } });

  // Picking a file reads the detected mapping and moves on: step 1 has no button.
  async function pickFile(f: File) {
    if (state.step !== 'file') return;
    if (f.size > MAX_UPLOAD_BYTES) {
      const size = format.number(f.size / 1_000_000, { maximumFractionDigits: 1 });
      return dispatch({
        type: 'requestFailed',
        notice: {
          title: t('uploadTooLarge'),
          body: t('importTooLargeBody', { name: f.name, size }),
        },
      });
    }
    dispatch({ type: 'requestStarted' });
    const res = await listsApi.preview(f, state.entity);
    if (!res.success) return failed(res.error);
    dispatch({ type: 'previewLoaded', file: f, preview: res.data });
  }

  async function review() {
    if (state.step !== 'map') return;
    dispatch({ type: 'requestStarted' });
    const res = await listsApi.preview(state.file, state.entity, state.mapping);
    if (!res.success) return failed(res.error);
    dispatch({ type: 'reviewLoaded', preview: res.data });
  }

  async function runImport() {
    if (state.step !== 'review' || !projectId) return;
    const { file, entity, preview, name, mapping, findEmails } = state;
    dispatch({ type: 'requestStarted' });
    const res = await listsApi.upload(file, entity, projectId, name.trim() || file.name, mapping);
    if (!res.success) return failed(res.error);
    // Missing emails cost credits: the chat gets the request as a draft, so the user sends it and sees the plan.
    const ask = findEmails && entity === 'person' && preview.incomplete > 0;
    const followUp = [draft?.trim(), ask && t('findEmailsMessage')].filter(Boolean).join('\n\n');
    dispatch({ type: 'importStarted', imp: res.data, followUp });
  }

  // Closing a finished import marks it seen, so the dialog opens on a new upload next time.
  function close() {
    if (imp && (imp.status === 'done' || imp.status === 'failed')) void csvImportApi.seen(imp.id);
    onDone();
  }

  // A failed import leaves the dialog free for another file.
  function restart() {
    if (imp) void csvImportApi.seen(imp.id);
    dispatch({ type: 'restarted' });
  }

  function openList() {
    if (state.step !== 'import' || !state.imp.chat_session_id) return;
    close();
    router.push(
      `/?s=${state.imp.chat_session_id}${state.followUp ? `&${new URLSearchParams({ draft: state.followUp })}` : ''}`,
    );
  }

  // An error belongs to the step it happened on.
  const go = (to: 'file' | 'map' | 'review') => dispatch({ type: 'stepChanged', to });

  const back = (to: 'file' | 'map', busy: boolean) => (
    <Button variant="ghost" disabled={busy} onClick={() => go(to)}>
      {t('importBack')}
    </Button>
  );

  return (
    <>
      <ImportHeader step={state.step} />
      {state.step === 'file' && (
        <>
          <FileStep
            entity={state.entity}
            onEntity={(entity) => dispatch({ type: 'entityChosen', entity })}
            onFile={(f) => void pickFile(f)}
            busy={state.busy}
            error={state.error}
          />
          <ImportFooter note={t('importFileNote')} onCancel={close} />
        </>
      )}
      {state.step === 'map' && (
        <>
          <MappingStep
            entity={state.entity}
            file={state.file}
            preview={state.preview}
            mapping={state.mapping}
            onChange={(mapping) => dispatch({ type: 'mappingChanged', mapping })}
            onChangeFile={() => go('file')}
          />
          <ErrorNotice error={state.error} />
          <ImportFooter back={back('file', state.busy)} note={t('importMapNote')} onCancel={close}>
            <Button
              disabled={state.busy || !mappingComplete(state.entity, state.mapping)}
              onClick={() => void review()}
            >
              {t('importNext')}
            </Button>
          </ImportFooter>
        </>
      )}
      {state.step === 'review' && (
        <>
          <ReviewStep
            entity={state.entity}
            preview={state.preview}
            name={state.name}
            onName={(name) => dispatch({ type: 'nameChanged', name })}
            findEmails={state.findEmails}
            onFindEmails={(value) => dispatch({ type: 'findEmailsToggled', value })}
          />
          <ErrorNotice error={state.error} />
          <ImportFooter
            back={back('map', state.busy)}
            note={t('importReviewNote')}
            onCancel={close}
          >
            <Button
              disabled={state.busy || state.preview.to_import === 0}
              onClick={() => void runImport()}
            >
              {state.busy ? t('uploading') : t('importConfirm', { count: state.preview.to_import })}
            </Button>
          </ImportFooter>
        </>
      )}
      {state.step === 'import' && (
        <>
          {state.imp.status === 'done' || state.imp.status === 'failed' ? (
            <DoneStep imp={state.imp} />
          ) : (
            <ProgressStep imp={state.imp} />
          )}
          <ImportFooter
            note={
              state.imp.status === 'queued' || state.imp.status === 'running'
                ? t('importRunningNote')
                : ''
            }
            onCancel={close}
            cancelLabel={t('importClose')}
          >
            {state.imp.status === 'failed' && <Button onClick={restart}>{t('importAgain')}</Button>}
            {state.imp.status === 'done' && <Button onClick={openList}>{t('importOpen')}</Button>}
          </ImportFooter>
        </>
      )}
    </>
  );
}
