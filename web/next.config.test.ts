import { describe, expect, it } from 'vite-plus/test';
import nextConfig from './next.config';

describe('Content-Security-Policy', () => {
  it('limits images on every page to the app and the favicon service', async () => {
    const rules = (await nextConfig.headers?.()) ?? [];
    const csp = rules
      .filter((rule) => rule.source === '/(.*)')
      .flatMap((rule) => rule.headers)
      .find((header) => header.key === 'Content-Security-Policy')?.value;
    const imgSrc = csp
      ?.split(';')
      .map((directive) => directive.trim().split(/\s+/))
      .find(([name]) => name === 'img-src')
      ?.slice(1);
    expect(imgSrc).toContain("'self'");
    expect(imgSrc).toContain('https://lh3.googleusercontent.com'); // a Google sign-in photo
    expect(imgSrc).not.toContain('https:');
    expect(imgSrc).not.toContain('*');
  });
});

describe('build memory settings', () => {
  it('keep the type check and the default workers in every build that does not opt out', () => {
    expect(nextConfig.typescript?.ignoreBuildErrors).toBe(false);
    expect(nextConfig.experimental?.cpus).toBeUndefined();
  });
});
