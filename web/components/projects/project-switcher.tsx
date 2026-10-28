'use client';

import { Check, ChevronsUpDown, Pencil, Plus, Trash2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useState } from 'react';
import { rowFocus } from '@/components/app/nav';
import { Logo } from '@/components/logo';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/sonner';
import { useErrorText } from '@/i18n/error-text';
import {
  getCurrentProjectId,
  projectsApi,
  setCurrentProjectId,
  useCurrentProjectId,
} from '@/lib/projects';
import { isLeaving, loadProjects, useLoadedProjects } from '@/lib/projects/load';
import { cn } from '@/lib/utils';

/** Picks the current project, i.e. the client (#307): its profile, exclusions, senders, campaigns and inbox.
 * The trigger is the design's workspace row: the mark, the client's name and the chevrons.
 * `userId` is the server's session user: the shared list is never reused across users.
 * `onSwitch` runs after the user moves to another one. A new client starts at its profile step.
 * `phone` gives the row a 48 px target (the drawer). */
export function ProjectSwitcher({
  userId,
  onSwitch,
  phone = false,
}: {
  userId: string;
  onSwitch?: () => void;
  phone?: boolean;
}) {
  const t = useTranslations('projects');
  const errorText = useErrorText();
  const router = useRouter();
  const currentId = useCurrentProjectId();
  const projects = useLoadedProjects(userId);
  const [naming, setNaming] = useState<'create' | 'rename' | null>(null);
  const [name, setName] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = useCallback(
    async (fresh = false) => {
      const res = await loadProjects(userId, fresh);
      if (!res.success) return isLeaving() ? undefined : toast.error(t('loadError'));
      // Read storage, not the hook: during hydration the hook still reports null.
      const stored = res.data.some((p) => p.id === getCurrentProjectId());
      if (!stored && res.data[0]) await setCurrentProjectId(res.data[0].id);
    },
    [t, userId],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const current = projects?.find((p) => p.id === currentId);

  const select = async (id: string) => {
    if (id === currentId) return;
    await setCurrentProjectId(id);
    onSwitch?.();
  };

  const openNaming = (mode: 'create' | 'rename') => {
    setName(mode === 'rename' ? (current?.name ?? '') : '');
    setNaming(mode);
  };

  const submitName = async () => {
    const value = name.trim();
    if (!value) return;
    const res =
      naming === 'rename' && current
        ? await projectsApi.rename(current.id, value)
        : await projectsApi.create(value);
    if (!res.success) return toast.error(errorText(res.error, t('saveError')));
    setNaming(null);
    await refresh(true);
    if (naming !== 'create') return;
    await select(res.data.project.id);
    router.push('/onboarding');
  };

  const remove = async () => {
    if (!current) return;
    const res = await projectsApi.remove(current.id);
    if (!res.success) {
      return toast.error(errorText(res.error, t('deleteError')));
    }
    const next = projects?.find((p) => p.id !== current.id);
    if (next) await select(next.id);
    await refresh(true);
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t('label', { name: current?.name ?? '' })}
            className={cn(
              'hover:bg-hair data-[state=open]:bg-hair flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 text-left',
              rowFocus,
              phone ? 'min-h-12' : 'h-9',
            )}
          >
            <Logo markOnly decorative />
            <span className="text-ui-strong flex-1 truncate">{current?.name ?? '…'}</span>
            <ChevronsUpDown className="text-muted size-3.5 shrink-0" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-60">
          {projects?.map((p) => (
            <DropdownMenuItem key={p.id} onSelect={() => void select(p.id)}>
              <Check className={p.id === currentId ? 'size-4' : 'size-4 opacity-0'} />
              <span className="truncate">{p.name}</span>
              {p.creditCap ? (
                <span
                  className={
                    p.overCap
                      ? 'text-negative ml-auto shrink-0 text-xs'
                      : 'text-muted ml-auto shrink-0 text-xs'
                  }
                >
                  {t('cap', { spent: p.creditsThisMonth, cap: p.creditCap })}
                </span>
              ) : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => openNaming('create')}>
            <Plus className="size-4" /> {t('new')}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!current} onSelect={() => openNaming('rename')}>
            <Pencil className="size-4" /> {t('rename')}
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={!current || (projects?.length ?? 0) <= 1}
            onSelect={() => setConfirmDelete(true)}
          >
            <Trash2 className="size-4" /> {t('delete')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={naming !== null} onOpenChange={(open) => !open && setNaming(null)}>
        <DialogContent>
          <form
            className="grid gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              void submitName();
            }}
          >
            <DialogHeader>
              <DialogTitle>{naming === 'rename' ? t('rename') : t('new')}</DialogTitle>
            </DialogHeader>
            <Input
              autoFocus
              value={name}
              maxLength={120}
              placeholder={t('namePlaceholder')}
              aria-label={t('name')}
              onChange={(e) => setName(e.target.value)}
            />
            <DialogFooter>
              <Button type="submit" disabled={!name.trim()}>
                {t('save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t('deleteTitle', { name: current?.name ?? '' })}
        description={t('deleteBody')}
        confirmLabel={t('delete')}
        onConfirm={remove}
      />
    </>
  );
}
