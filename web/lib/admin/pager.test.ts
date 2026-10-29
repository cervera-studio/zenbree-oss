import { describe, expect, it } from 'vite-plus/test';
import { pageOffsets, pageRange } from './pager';

describe('pageOffsets', () => {
  it('disables prev on the first page', () => {
    expect(pageOffsets(0, 50, true)).toEqual({ prev: null, next: 50 });
  });

  it('never goes below the first row', () => {
    expect(pageOffsets(20, 50, false)).toEqual({ prev: 0, next: null });
  });

  it('steps back one page', () => {
    expect(pageOffsets(100, 50, true)).toEqual({ prev: 50, next: 150 });
  });
});

describe('pageRange', () => {
  it('counts rows from one', () => {
    expect(pageRange(0, 10, 214)).toEqual({ from: 1, to: 10, total: 214 });
  });

  it('ends at the last row shown on a short last page', () => {
    expect(pageRange(200, 14, 214)).toEqual({ from: 201, to: 214, total: 214 });
  });

  it('shows no range for an empty page', () => {
    expect(pageRange(250, 0, 214)).toBeNull();
  });
});
