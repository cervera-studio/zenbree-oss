import { describe, expect, it } from 'vite-plus/test';
import { responseError, toAppError } from './error';

describe('toAppError', () => {
  it('reads the API shape and keeps the extra keys as camelCase data', () => {
    const body = { detail: { code: 'ROW.NO_CREDITS', message: 'Not enough', credits_needed: 3 } };
    expect(toAppError(402, body)).toEqual({
      name: 'AppError',
      code: 'ROW.NO_CREDITS',
      message: 'Not enough',
      status: 402,
      data: { creditsNeeded: 3 },
    });
  });

  it('reads the unwrap_or_raise and legacy wrappers', () => {
    expect(
      toAppError(404, { detail: { error: { code: 'LIST.NOT_FOUND', message: 'x' } } }),
    ).toMatchObject({
      code: 'LIST.NOT_FOUND',
    });
    expect(
      toAppError(400, { error: { code: 'X.Y', message: 'x', data: { a_b: 1 } } }),
    ).toMatchObject({
      code: 'X.Y',
      data: { aB: 1 },
    });
  });

  it("names FastAPI's own 422 list as invalid input, with the fields", () => {
    const fields = [{ loc: ['body', 'name'], msg: 'Field required', type: 'missing' }];
    expect(toAppError(422, { detail: fields })).toMatchObject({
      code: 'REQUEST.INVALID_INPUT',
      status: 422,
      data: { fields },
    });
  });

  it('gives a code from the status when the body has none', () => {
    expect(toAppError(404, { detail: 'Not Found' })).toMatchObject({
      code: 'REQUEST.HTTP_404',
      message: 'Not Found',
    });
    expect(toAppError(413, null).code).toBe('REQUEST.TOO_LARGE');
    expect(toAppError(500, 'oops').code).toBe('INTERNAL.UNEXPECTED');
    expect(toAppError(502, null).code).toBe('INTERNAL.SERVICE_UNAVAILABLE');
  });

  it('reads a non-JSON body without throwing', async () => {
    const res = new Response('<html>Bad gateway</html>', { status: 502 });
    expect(await responseError(res)).toMatchObject({
      code: 'INTERNAL.SERVICE_UNAVAILABLE',
      status: 502,
    });
  });
});
