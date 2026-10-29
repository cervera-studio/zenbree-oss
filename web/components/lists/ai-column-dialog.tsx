'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/sonner';
import { Textarea } from '@/components/ui/textarea';
import { useErrorText } from '@/i18n/error-text';
import { type CreditPrices, listsApi } from '@/lib/lists/api';
import { aiColumnCredits } from '@/lib/lists/view';

/** Adds a column the AI researches for every delivered row; `onAdded` lets the caller refetch. */
export function AiColumnDialog({
  listId,
  delivered,
  balance,
  prices,
  open,
  onOpenChange,
  onAdded,
}: {
  listId: string;
  delivered: number;
  balance: number | null;
  prices: CreditPrices;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: () => void;
}) {
  const t = useTranslations('lists');
  const tc = useTranslations('common');
  const errorText = useErrorText();
  const [label, setLabel] = useState('');
  const [prompt, setPrompt] = useState('');
  const credits = aiColumnCredits(prices, delivered);
  const left = balance === null ? null : Math.max(0, balance);
  const shortfall =
    left !== null && left < credits
      ? { balance: left, rows: Math.floor(left / prices.ai_cell) }
      : null;

  const add = async () => {
    const res = await listsApi.addAiColumn(listId, label.trim(), prompt.trim());
    if (!res.success) return toast.error(errorText(res.error, t('aiAddError')));
    toast.success(t('aiAdded', { label: label.trim() }));
    onOpenChange(false);
    setLabel('');
    setPrompt('');
    onAdded();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <DialogHeader>
            <DialogTitle className="text-lg font-bold">{t('aiNew')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="ai-column-label">{t('aiLabel')}</Label>
            <Input
              id="ai-column-label"
              autoFocus
              value={label}
              maxLength={60}
              placeholder={t('aiLabelPlaceholder')}
              onChange={(e) => setLabel(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ai-column-prompt">{t('aiPrompt')}</Label>
            <Textarea
              id="ai-column-prompt"
              value={prompt}
              maxLength={500}
              placeholder={t('aiPromptPlaceholder')}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </div>
          <div className="bg-ground flex flex-col gap-1 rounded-lg border p-3 text-xs-plus leading-normal">
            <span className="font-bold tabular-nums">
              {t('aiEstimate', { credits, rows: delivered })}
            </span>
            <span className="text-muted">{t('aiCost')}</span>
          </div>
          {shortfall && (
            <p
              role="status"
              className="bg-warning-tint text-warning rounded-lg p-3 text-xs-plus leading-normal"
            >
              {shortfall.rows === 0
                ? t('aiBalanceNone', { balance: shortfall.balance })
                : t('aiBalanceShort', shortfall)}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tc('cancel')}
            </Button>
            <Button type="submit" disabled={!label.trim() || prompt.trim().length < 3}>
              {t('aiCreate')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
