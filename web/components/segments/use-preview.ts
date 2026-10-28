'use client';

import { useEffect, useRef, useState } from 'react';
import { previewAction } from '@/app/(app)/segments/actions';
import { unreachable } from '@/lib/api/error';
import type { SegmentPreview } from '@/lib/segments/api';
import { complete, type Entity, type RuleGroup } from '@/lib/segments/rule';
import { type AppErrorJSON, fail } from '@/lib/types';

const PREVIEW_DELAY_MS = 300;

type Keyed<T> = { key: string; value: T };

export function usePreview(entity: Entity, rule: RuleGroup) {
  const key = complete(rule) ? JSON.stringify({ entity, rule }) : null;
  const [answer, setAnswer] = useState<Keyed<SegmentPreview> | null>(null);
  const [failed, setFailed] = useState<Keyed<AppErrorJSON> | null>(null);
  const [waiting, setWaiting] = useState<string | null>(null);
  const ticket = useRef(0);

  useEffect(() => {
    const mine = ++ticket.current;
    if (!key) {
      setWaiting(null);
      return;
    }
    setWaiting(key);
    const timer = setTimeout(async () => {
      const result = await previewAction(JSON.parse(key)).catch(() => fail(unreachable()));
      if (mine !== ticket.current) return;
      setWaiting(null);
      if (result.success) {
        setAnswer({ key, value: result.data });
        setFailed(null);
      } else {
        setFailed({ key, value: result.error });
      }
    }, PREVIEW_DELAY_MS);
    return () => clearTimeout(timer);
  }, [key]);

  return {
    preview: answer?.value ?? null,
    fresh: answer !== null && answer.key === key,
    checking: waiting !== null && waiting === key,
    failed: failed && failed.key === key ? failed.value : null,
    sendable: key !== null,
  };
}
