import * as BetterStack from '@sentry/nextjs';
import { errorReportingOptions } from '@/lib/error-reporting';

export function register() {
  const betterstackDsn = process.env.NEXT_PUBLIC_BETTERSTACK_WEB_ERROR_DSN;
  if (betterstackDsn) BetterStack.init({ dsn: betterstackDsn, ...errorReportingOptions });
}

export const onRequestError = BetterStack.captureRequestError;
