import { afterEach, describe, expect, it, vi } from 'vite-plus/test';
import { logger } from './logger';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('logger on the server', () => {
  it('writes one JSON line at or above LOG_LEVEL and drops the rest', () => {
    vi.stubEnv('LOG_LEVEL', 'warning');
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    logger.info('dropped');
    logger.warn('kept', { error: new Error('boom') });
    expect(info).not.toHaveBeenCalled();
    expect(JSON.parse(String(warn.mock.calls[0]?.[0]))).toMatchObject({
      level: 'warning',
      event: 'kept',
      error: { name: 'Error', message: 'boom', stack: expect.stringContaining('boom') },
    });
  });

  it('never mutes an error, and falls back to info on an unknown LOG_LEVEL', () => {
    vi.stubEnv('LOG_LEVEL', 'silent');
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => undefined);
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    logger.debug('dropped');
    logger.info('kept');
    logger.error('kept');
    expect(debug).not.toHaveBeenCalled();
    expect(info).toHaveBeenCalledOnce();
    expect(error).toHaveBeenCalledOnce();
  });

  it('redacts secrets and contact data, also in an error cause, and keeps counts', () => {
    vi.stubEnv('LOG_LEVEL', 'info');
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const error = new Error('send failed', { cause: { email: 'a@b.it', status: 403 } });
    logger.info('x', { apiKey: 'k', rows: [{ email: 'a@b.it' }], inputTokens: 3, error });
    const line = JSON.parse(String(info.mock.calls[0]?.[0]));
    expect(line).toMatchObject({
      apiKey: '[redacted]',
      rows: [{ email: '[redacted]' }],
      inputTokens: 3,
      error: { cause: { email: '[redacted]', status: 403 } },
    });
    expect(JSON.stringify(line)).not.toContain('a@b.it');
  });
});

describe('logger on bad input', () => {
  it('never throws, and keeps its own level and event over same-named fields', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const cycle: Record<string, unknown> = {};
    cycle.self = cycle;
    expect(() => logger.error('x', { cycle, count: 1n })).not.toThrow();
    logger.error('y', { level: 'debug', event: 'other', count: 2n });
    expect(JSON.parse(String(error.mock.calls[0]?.[0]))).toMatchObject({
      event: 'x',
      loggerError: 'fields not serialisable',
    });
    expect(JSON.parse(String(error.mock.calls[1]?.[0]))).toMatchObject({
      level: 'error',
      event: 'y',
      count: '2',
    });
  });
});

describe('logger in the browser', () => {
  function browser(stored: string | null) {
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubGlobal('window', { localStorage: { getItem: () => stored } });
  }

  it('prints warnings and errors in production, so the error report keeps them as breadcrumbs', () => {
    browser(null);
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    logger.info('dropped');
    logger.warn('chat.stream.stalled', { sessionId: 's1' });
    expect(info).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith('chat.stream.stalled', { sessionId: 's1' });
  });

  it('logs from the stored level up', () => {
    browser('debug');
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => undefined);
    logger.debug('chat.stream.frame', { sessionId: 's1' });
    expect(debug).toHaveBeenCalledWith('chat.stream.frame', { sessionId: 's1' });
  });
});
