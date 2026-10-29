'use client';

import { MotionConfig } from 'motion/react';
import posthog from 'posthog-js';
import { PostHogProvider } from 'posthog-js/react';
import type { ReactNode } from 'react';
import { ThemeProvider } from '@/components/ui/theme-provider';
import { isUnsubscribePage, scrubEvent } from '@/lib/analytics';

const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
if (key && typeof window !== 'undefined' && !isUnsubscribePage(window.location.pathname)) {
  posthog.init(key, {
    api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST ?? 'https://eu.i.posthog.com',
    defaults: '2025-11-30',
    before_send: scrubEvent,
    // The stored first-visit URLs ($initial_current_url, $session_entry_url) also ride the /flags
    // request, which `before_send` never sees: mask the reset token where they are recorded.
    mask_personal_data_properties: true,
    custom_personal_data_properties: ['token'],
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const themed = (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      <MotionConfig reducedMotion="user" transition={{ duration: 0.18 }}>
        {children}
      </MotionConfig>
    </ThemeProvider>
  );
  // No key → no-op: posthog stays uninitialised and nothing is wrapped.
  return key ? <PostHogProvider client={posthog}>{themed}</PostHogProvider> : themed;
}
