import { describe, expect, it } from 'vite-plus/test';
import { dragOffset, releaseVelocity, shouldDismiss } from './sheet-drag';

describe('sheet drag', () => {
  it('follows the finger down 1:1 and resists upwards', () => {
    expect(dragOffset(120, 600)).toBe(120);
    const up = dragOffset(-120, 600);
    expect(up).toBeLessThan(0);
    expect(up).toBeGreaterThan(-120);
  });

  it('measures velocity over the last 100 ms only', () => {
    const samples = [
      { y: 0, t: 0 },
      { y: 10, t: 200 },
      { y: 60, t: 250 },
      { y: 110, t: 300 },
    ];
    expect(releaseVelocity(samples)).toBe(1000);
    expect(releaseVelocity([{ y: 40, t: 0 }])).toBe(0);
    // Pulled fast, then held still before lifting: the throw is over.
    expect(releaseVelocity([...samples, { y: 110, t: 600 }])).toBe(0);
    expect(releaseVelocity([])).toBe(0);
  });

  it('closes a slow drag past half the height, and keeps one short of it', () => {
    expect(shouldDismiss(310, 0, 600)).toBe(true);
    expect(shouldDismiss(250, 0, 600)).toBe(false);
  });

  it('closes on a flick from near the top, and springs back on a flick up', () => {
    expect(shouldDismiss(40, 3000, 600)).toBe(true);
    expect(shouldDismiss(250, -1500, 600)).toBe(false);
  });
});
