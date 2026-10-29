import { describe, expect, it } from 'vite-plus/test';
import { cn } from './utils';

describe('cn', () => {
  it.each([
    [['text-sm', 'text-ui'], 'text-ui'],
    [['text-ui', 'text-label'], 'text-label'],
    [['text-ui', 'text-ink'], 'text-ui text-ink'],
    [['text-data-sm', 'text-muted'], 'text-data-sm text-muted'],
    [['shadow-lg', 'shadow-float'], 'shadow-float'],
    [['h-8', 'h-control'], 'h-control'],
    [['w-62', 'w-sidebar'], 'w-sidebar'],
    [['py-4', 'py-section'], 'py-section'],
    [['tracking-wide', 'tracking-label'], 'tracking-label'],
    [['ease-exit', 'ease-snap'], 'ease-snap'],
  ])('merges %j into %s', (classes, merged) => {
    expect(cn(...classes)).toBe(merged);
  });
});
