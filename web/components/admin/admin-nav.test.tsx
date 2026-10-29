// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { AdminNav } from './admin-nav';

vi.mock('next/navigation', () => ({ usePathname: () => '/admin/jobs' }));

const links = (c: HTMLElement) => [...c.querySelectorAll('a')].map((a) => a.textContent);

describe('AdminNav', () => {
  it('shows Flower only where it has an address, opening in a new tab', async () => {
    const without = await render(<AdminNav counts={{}} />);
    expect(links(without)).not.toContain('Flower');
    const withFlower = await render(<AdminNav counts={{}} flower="https://flower.example/" />);
    const flower = [...withFlower.querySelectorAll('a')].find((a) => a.textContent === 'Flower');
    expect(flower?.getAttribute('href')).toBe('https://flower.example/');
    expect(flower?.getAttribute('target')).toBe('_blank');
  });

  it('hides a zero count and speaks a pill count', async () => {
    const quiet = await render(<AdminNav counts={{ jobsAttention: 0 }} />);
    expect(quiet.querySelector('.sr-only')).toBeNull();
    const loud = await render(<AdminNav counts={{ jobsAttention: 3 }} />);
    const jobs = [...loud.querySelectorAll('a')].find((a) => a.getAttribute('aria-current'));
    expect(jobs?.querySelector('[aria-hidden]:not(svg)')?.textContent).toBe('3');
    expect(jobs?.querySelector('.sr-only')?.textContent).toMatch(/3/);
  });

  it('marks only the current section', async () => {
    const c = await render(<AdminNav counts={{}} />);
    const current = [...c.querySelectorAll('a[aria-current="page"]')].map((a) => a.textContent);
    expect(current).toEqual(['Job']);
  });
});
