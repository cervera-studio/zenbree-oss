import * as BetterStack from '@sentry/nextjs';
import { rememberBrowserTimeZone } from '@/i18n/time-zone';
import { isUnsubscribePage } from '@/lib/analytics';
import { errorReportingOptions } from '@/lib/error-reporting';

// Unset → no initialization.
const betterstackDsn = process.env.NEXT_PUBLIC_BETTERSTACK_WEB_ERROR_DSN;
if (betterstackDsn) BetterStack.init({ dsn: betterstackDsn, ...errorReportingOptions });

if (!isUnsubscribePage(window.location.pathname)) rememberBrowserTimeZone();

export const onRouterTransitionStart = BetterStack.captureRouterTransitionStart;
