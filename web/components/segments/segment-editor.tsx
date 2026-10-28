'use client';

import { Loader2, Upload } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import {
  createSegmentAction,
  editSegmentAction,
  listColumnsAction,
  publishSegmentAction,
} from '@/app/(app)/segments/actions';
import { Labelled } from '@/components/automations/editor/fields';
import { useLeaveGuard } from '@/components/automations/editor/use-leave-guard';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SegmentedControl } from '@/components/ui/segmented-control';
import { Select } from '@/components/ui/select';
import { useErrorText } from '@/i18n/error-text';
import { unreachable } from '@/lib/api/error';
import { useAction } from '@/lib/hooks/use-action';
import type { Segment } from '@/lib/segments/api';
import type { FieldCatalog, FieldList, ListColumn } from '@/lib/segments/fields';
import {
  complete,
  type Entity,
  emptyRule,
  nodeOf,
  type RuleGroup,
  sameRule,
} from '@/lib/segments/rule';
import { type AppErrorJSON, fail } from '@/lib/types';
import { ApplyDialog } from './apply-dialog';
import type { Edit } from './condition-row';
import { FrozenBanner } from './frozen-banner';
import { PreviewPanel } from './preview-panel';
import { RuleEditor } from './rule-editor';
import { usePreview } from './use-preview';

const ENTITIES: readonly Entity[] = ['person', 'company'];

type Refusal = { sent: RuleGroup; error: AppErrorJSON };

const aboutRule = (error: AppErrorJSON) => error.code === 'SEGMENT.INVALID_RULE';

// A rule refusal points at a node of the rule that was sent, so an edit makes it stale.
function refusalShown(
  refused: Refusal | null,
  rule: RuleGroup,
  previewFailed: AppErrorJSON | null,
) {
  const alert =
    refused && (!aboutRule(refused.error) || refused.sent === rule) ? refused.error : null;
  const ruleError = alert && aboutRule(alert) ? alert : previewFailed;
  const path = ruleError && aboutRule(ruleError) ? ruleError.data?.path : undefined;
  return { alert, marked: typeof path === 'string' ? nodeOf(path) : null };
}

type Base = Pick<Segment, 'id' | 'version' | 'name' | 'rule' | 'published_at' | 'problems'>;

export function SegmentEditor({
  saved,
  lists,
  columns: preloaded,
}: {
  saved?: Segment;
  lists: FieldList[];
  columns: Record<string, ListColumn[]>;
}) {
  const t = useTranslations('segments.editor');
  const router = useRouter();
  const [base, setBase] = useState<Base | null>(saved ?? null);
  const [name, setName] = useState(saved?.name ?? '');
  const [entity, setEntity] = useState<Entity>(saved?.entity ?? 'person');
  const [rule, setRule] = useState<RuleGroup>(saved?.rule ?? emptyRule());
  const [columns, setColumns] = useState(preloaded);
  const [applying, setApplying] = useState<'publish' | 'edit' | null>(null);
  const [refused, setRefused] = useState<Refusal | null>(null);
  const { pending, run } = useAction();

  const catalog: FieldCatalog = {
    entity,
    lists: lists.filter((l) => l.entity === entity),
    columns,
  };
  const preview = usePreview(entity, rule);
  const ruleChanged = !base || !sameRule(rule, base.rule);
  const dirty = !base
    ? name.trim() !== '' || rule.children.length > 0
    : ruleChanged || name.trim() !== base.name;
  const published = base?.published_at != null;
  const ready = complete(rule) && name.trim() !== '';
  useLeaveGuard(dirty, t('leave'));

  const { alert, marked } = refusalShown(refused, rule, preview.failed);

  const edit: Edit = (change) => setRule((r) => change(r));

  function remember(segment: Segment) {
    setBase(segment);
    setRule(segment.rule);
    setName(segment.name);
  }

  const refuse = (sent: RuleGroup) => (error: AppErrorJSON) => setRefused({ sent, error });

  function save(include?: boolean) {
    setRefused(null);
    const body = {
      name: name.trim(),
      ...(ruleChanged && { rule }),
      ...(include !== undefined && { include_new: include }),
    };
    if (!base)
      return run(() => createSegmentAction({ ...body, entity, rule }), {
        onSuccess: (created) => {
          remember(created);
          router.replace(`/segments/${created.id}`);
        },
        onError: refuse(rule),
        successToast: t('saved'),
      });
    return run(() => editSegmentAction(base.id, { version: base.version, ...body }), {
      onSuccess: (edited) => remember(edited.segment),
      onError: refuse(rule),
      successToast: t('saved'),
    });
  }

  async function publish(include: boolean) {
    if (!base) return;
    setRefused(null);
    let sent = rule;
    if (dirty) {
      const edited = await editSegmentAction(base.id, {
        version: base.version,
        name: name.trim(),
        rule,
      }).catch(() => fail(unreachable()));
      if (!edited.success) {
        refuse(rule)(edited.error);
        return;
      }
      remember(edited.data.segment);
      sent = edited.data.segment.rule;
    }
    await run(() => publishSegmentAction(base.id, include), {
      onSuccess: (done) => remember(done.segment),
      onError: refuse(sent),
      successToast: t('publishedToast'),
    });
  }

  async function addColumnsOf(listId: string) {
    const result = await listColumnsAction(listId);
    if (result.success) setColumns((c) => ({ ...c, [listId]: result.data }));
  }

  const unloaded = catalog.lists.filter((l) => !columns[l.id]);
  return (
    <div className="flex flex-col gap-6 desktop:flex-row desktop:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {base && <FrozenBanner problems={base.problems} catalog={catalog} />}
        <Labelled label={t('name')}>
          {({ id }) => (
            <Input
              id={id}
              maxLength={100}
              className="phone:h-11"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Labelled>
        {!base && (
          <div className="flex flex-col gap-1.5">
            <span className="text-ui-strong">{t('entity')}</span>
            <SegmentedControl
              label={t('entity')}
              options={ENTITIES.map((value) => ({ value, label: t(`entities.${value}`) }))}
              value={entity}
              onChange={(value) => {
                setEntity(value);
                setRule(emptyRule());
              }}
            />
          </div>
        )}
        <section className="flex flex-col gap-3" aria-labelledby="segment-rule">
          <h2 id="segment-rule" className="text-ui-strong">
            {t('rule')}
          </h2>
          <RuleEditor rule={rule} catalog={catalog} marked={marked} edit={edit} />
          <ColumnsPicker lists={unloaded} onPick={addColumnsOf} />
        </section>
        <SaveBar
          mode={!base ? 'create' : published ? 'published' : 'draft'}
          canSave={ready && dirty}
          canPublish={ready}
          pending={pending}
          error={alert}
          onSave={() => (published && ruleChanged ? setApplying('edit') : save())}
          onPublish={() => setApplying('publish')}
        />
      </div>
      <div className="desktop:sticky desktop:top-4 desktop:w-96 desktop:shrink-0">
        <PreviewPanel
          {...preview}
          entity={entity}
          why={base && !ruleChanged ? { segmentId: base.id, catalog } : null}
        />
      </div>
      {applying && base && (
        <ApplyDialog
          kind={applying}
          segmentId={base.id}
          entity={entity}
          rule={rule}
          onClose={() => setApplying(null)}
          onConfirm={(include) => (applying === 'publish' ? publish(include) : save(include))}
        />
      )}
    </div>
  );
}

function ColumnsPicker({ lists, onPick }: { lists: FieldList[]; onPick: (id: string) => void }) {
  const t = useTranslations('segments.editor');
  if (lists.length === 0) return null;
  return (
    <Labelled label={t('columnsOf')} hint={t('columnsHint')}>
      {({ id, describedBy }) => (
        <Select
          id={id}
          aria-describedby={describedBy}
          touch
          className="desktop:w-80"
          value=""
          onChange={(e) => e.target.value && onPick(e.target.value)}
        >
          <option value="">{t('pickList')}</option>
          {lists.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
      )}
    </Labelled>
  );
}

function SaveBar({
  mode,
  canSave,
  canPublish,
  pending,
  error,
  onSave,
  onPublish,
}: {
  mode: 'create' | 'draft' | 'published';
  canSave: boolean;
  canPublish: boolean;
  pending: boolean;
  error: AppErrorJSON | null;
  onSave: () => void;
  onPublish: () => void;
}) {
  const t = useTranslations('segments.editor');
  const errorText = useErrorText();
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2 phone:flex-col">
        <Button
          size="lg"
          variant={mode === 'draft' ? 'outline' : 'primary'}
          className="phone:h-11"
          disabled={!canSave || pending}
          onClick={onSave}
        >
          {pending && <Loader2 aria-hidden className="animate-spin" />}
          {mode === 'create' ? t('create') : t('save')}
        </Button>
        {mode === 'draft' && (
          <Button
            size="lg"
            className="phone:h-11"
            disabled={!canPublish || pending}
            onClick={onPublish}
          >
            <Upload aria-hidden />
            {t('publish')}
          </Button>
        )}
      </div>
      {mode !== 'published' && <p className="text-ink text-small">{t('draftNote')}</p>}
      {error && (
        <div role="alert" className="text-negative text-ui flex flex-wrap items-center gap-2">
          {errorText(error)}
          {error.code === 'SEGMENT.STALE_VERSION' && (
            <Button
              variant="outline"
              size="sm"
              className="phone:h-11"
              onClick={() => window.location.reload()}
            >
              {t('reload')}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
