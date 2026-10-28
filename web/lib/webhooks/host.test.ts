import { describe, expect, it } from 'vite-plus/test';
import { hostOf } from './host';

describe('hostOf', () => {
  it('drops the path, the query and the credentials: a token there must not reach the browser', () => {
    expect(hostOf('https://hooks.zapier.com:8443/hooks/catch/tok-9f8e?k=secret')).toBe(
      'hooks.zapier.com:8443',
    );
    expect(hostOf('https://user:pw@example.com/x')).toBe('example.com');
  });

  it('is empty for a value that is not a URL', () => {
    expect(hostOf('not a url')).toBe('');
  });
});
