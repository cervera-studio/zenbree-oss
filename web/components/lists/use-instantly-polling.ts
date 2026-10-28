'use client';

import { useEffect } from 'react';
import { type InstantlyEnrollment, instantlyApi } from '@/lib/lists/instantly';

const POLL_MS = 2000;

const isLive = (e: InstantlyEnrollment) => e.status === 'queued' || e.status === 'running';

export function useInstantlyPolling(
  enrollment: InstantlyEnrollment | null,
  onUpdate: (next: InstantlyEnrollment) => void,
) {
  const id = enrollment?.id;
  const live = enrollment ? isLive(enrollment) : false;
  useEffect(() => {
    if (!id || !live) return;
    let stale = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = () => {
      timer = setTimeout(async () => {
        const res = await instantlyApi.get(id);
        if (stale) return;
        if (res.success) onUpdate(res.data);
        // A failed read tries again; a settled batch needs no next one.
        if (!res.success || isLive(res.data)) poll();
      }, POLL_MS);
    };
    poll();
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [id, live, onUpdate]);
}
