/**
 * URL/param serialization tests.
 *
 * Lives in its own module (separate from `index.test.ts`) because
 * `index.ts` carries `import 'server-only'` which throws on require in
 * the jsdom test runner — we test the pure serializer directly.
 *
 * Pins the array-param contract introduced for the F1 filter UI: the
 * BE expects Fastthe API's repeated-query-string shape for `list[str]`
 * params (e.g. `ateco_codes=25&ateco_codes=28`), so each array element
 * must produce a separate key/value pair rather than a comma-joined
 * string.
 */

import { describe, expect, it } from 'vite-plus/test';
import { VALIDATION_ERROR_CODES } from '@/lib/types';
import { buildQueryUrl } from './query-params';

const BASE = 'http://localhost:8000';

function parsed(path: string, params: Parameters<typeof buildQueryUrl>[2]): URL {
  const result = buildQueryUrl(path, BASE, params);
  if (!result.success) throw new Error(`unexpected failure: ${result.error.code}`);
  return new URL(result.data);
}

describe('buildQueryUrl — unsafe path segments', () => {
  it.each([
    // Dot segments, in every spelling `new URL`/`fetch` or API resolve.
    '/api/jobs/./x',
    '/api/jobs/../admin/users',
    '/api/jobs/%2e/x',
    '/api/jobs/%2E%2e/x',
    '/api/jobs/.%2e/x',
    '/api/jobs/%2e./x',
    '/api/jobs/..',
    '/api/jobs\\..\\admin',
    '/api/jobs/..?f=x',
    // Encoded separators: API decodes before routing, so `w1%2Fx` is
    // matched as two segments.
    '/api/workspaces/w1%2Fbusiness-profile',
    '/api/files/a%2F..%2Fb/export.csv',
    '/api/jobs/a%5Cb',
    '/api/jobs/a%3Fadmin%3D1',
    '/api/jobs/a%23frag',
    // Malformed escapes: nothing `API_ROUTES` builds produces one.
    '/api/jobs/%zz',
    '/api/jobs/a%',
    '/api/jobs/%E0%A4%A',
    // U+0000–U+0020: `new URL` removes tab/CR/LF anywhere and trims C0
    // controls and spaces at either end, so each of these would become `..`.
    '/api/jobs/.\t./admin',
    '/api/jobs/.\r./admin',
    '/api/jobs/.\n./admin',
    '/api/jobs/a\tb',
    '/api/jobs/.. ',
    '/api/jobs/..\u0000',
    '/api/jobs/..\u001f',
    ' /api/jobs/x',
    '/api/jobs/a b',
    '/api/jobs/x?q=a b',
    // Another origin: a protocol-relative path or a scheme.
    '//evil.com/api/x',
    'http://evil.com/api/x',
    '\\\\evil.com/api/x',
    '/\\evil.com/api/x',
    // Not an absolute path.
    'api/jobs/x',
    // Empty segments: Starlette redirects `/sessions/` to the collection.
    '/api/chat/sessions/',
    '/api/chat/sessions//messages/m1/regenerate',
    '/api/chat/sessions/?x=1',
    '/',
  ])('refuses %j with VALIDATION.INVALID_PATH', (path) => {
    const result = buildQueryUrl(path, BASE, { limit: 1 });

    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.code).toBe('VALIDATION.INVALID_PATH');
      expect(result.error.status).toBe(422);
      expect((VALIDATION_ERROR_CODES as readonly string[]).includes(result.error.code)).toBe(true);
    }
  });

  it('proves the cases are real: `new URL` alone resolves them as `..`', () => {
    // The inputs the check exists for, stated against the parser itself.
    expect(new URL('/api/jobs/.\t./admin', BASE).pathname).toBe('/api/admin');
    expect(new URL('/api/jobs/.. ', BASE).pathname).toBe('/api/');
    expect(new URL('/api/jobs/..\u0000', BASE).pathname).toBe('/api/');
  });

  it('keeps U+007F, which WHATWG neither strips nor trims: it is percent-encoded in place', () => {
    // `..\u007f` is not a dot segment: the parser writes it as `..%7F`, one
    // segment on the same route, so the rule has no reason to refuse it.
    expect(buildQueryUrl('/api/jobs/..\u007f', BASE)).toEqual({
      success: true,
      data: `${BASE}/api/jobs/..%7F`,
    });
  });

  it.each([
    // Double-encoded: API decodes once and reads text, not a separator.
    '/api/jobs/a%252Fb',
    '/api/jobs/%252e%252e',
    // Dots that are not a whole segment, and ordinary encoded text.
    '/api/outreach/linkedin/campaigns/c1/export.csv',
    '/api/jobs/a..b',
    '/api/knowledge-base/docs/my%20doc',
    '/api/jobs/caf%C3%A9',
  ])('keeps %s as it is', (path) => {
    expect(buildQueryUrl(path, BASE)).toEqual({ success: true, data: `${BASE}${path}` });
  });

  it('does not read the query or the fragment as path', () => {
    const result = buildQueryUrl('/api/files/art_1?f=../x.csv', BASE);

    expect(result).toEqual({
      success: true,
      data: 'http://localhost:8000/api/files/art_1?f=../x.csv',
    });
  });
});

describe('buildQueryUrl — query-string serialization', () => {
  it('emits array params as repeated keys (foo=a&foo=b), the FastAPI list[str] shape', () => {
    const url = parsed('/api/companies/italian/search', {
      ateco_codes: ['25', '28'],
      provincia: ['MI', 'BS'],
    });

    expect(url.searchParams.getAll('ateco_codes')).toEqual(['25', '28']);
    expect(url.searchParams.getAll('provincia')).toEqual(['MI', 'BS']);
  });

  it('drops undefined and null scalars (no ?employees_min= empty params)', () => {
    const url = parsed('/api/companies/italian/search', {
      ateco_codes: ['25'],
      employees_min: undefined,
      regione: null,
    });

    expect(url.searchParams.has('employees_min')).toBe(false);
    expect(url.searchParams.has('regione')).toBe(false);
    expect(url.searchParams.getAll('ateco_codes')).toEqual(['25']);
  });

  it('skips empty arrays entirely (no foo= empty entries)', () => {
    const url = parsed('/api/x', {
      ateco_codes: [],
      company_name: 'acme',
    });
    expect(url.searchParams.has('ateco_codes')).toBe(false);
    expect(url.searchParams.get('company_name')).toBe('acme');
  });

  it('coerces booleans and numbers via String()', () => {
    const url = parsed('/api/x', { flag: true, count: 0 });
    expect(url.searchParams.get('flag')).toBe('true');
    expect(url.searchParams.get('count')).toBe('0');
  });
});
