import { createTranslator } from 'next-intl';
import { describe, expect, it } from 'vite-plus/test';
import { MESSAGES } from '@/messages';
import { FORMATS } from './config';
import { authError, errorText } from './error-text';

const t = createTranslator({
  locale: 'it',
  messages: MESSAGES.it,
  namespace: 'errors',
  formats: FORMATS,
});
const { errors } = MESSAGES.it;

describe('errorText', () => {
  it('reads the text of a known code, over the fallback', () => {
    expect(errorText(t, { code: 'LIST.NOT_FOUND' }, 'fallback')).toBe(errors.LIST.NOT_FOUND);
  });

  it('lets the fallback win over a code that does not say what failed', () => {
    expect(errorText(t, { code: 'INTERNAL.UNEXPECTED' }, 'fallback')).toBe('fallback');
    expect(errorText(t, { code: 'INTERNAL.UNEXPECTED' })).toBe(errors.INTERNAL.UNEXPECTED);
  });

  it('names the step a campaign step error carries', () => {
    expect(errorText(t, { code: 'CAMPAIGN.INVALID_STEPS', data: { step: 4 } })).toBe(
      `Step 4: ${errors.CAMPAIGN.INVALID_STEPS}`,
    );
  });

  it('falls back for an unknown code, never showing it', () => {
    expect(errorText(t, { code: 'NEW.CODE' }, 'fallback')).toBe('fallback');
    expect(errorText(t, { code: 'not a code' })).toBe(errors.generic);
    expect(errorText(t, null)).toBe(errors.generic);
  });

  it("reads Better Auth's codes under AUTH_CLIENT, and the API's as they are", () => {
    expect(errorText(t, authError({ code: 'INVALID_EMAIL_OR_PASSWORD' }))).toBe(
      errors.AUTH_CLIENT.INVALID_EMAIL_OR_PASSWORD,
    );
    expect(errorText(t, authError({ code: 'ACCOUNT.LAST_ADMIN' }))).toBe(errors.ACCOUNT.LAST_ADMIN);
    expect(errorText(t, authError({ status: 429 }))).toBe(errors.REQUEST.HTTP_429);
  });
});
