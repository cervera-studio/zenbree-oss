/** The offsets the prev and next links lead to; `null` disables the link. */
export function pageOffsets(offset: number, limit: number, hasNext: boolean) {
  return {
    prev: offset === 0 ? null : Math.max(0, offset - limit),
    next: hasNext ? offset + limit : null,
  };
}

/** "1–10 di 214": the 1-based rows a page shows. `null` for an empty page, which shows no range. */
export function pageRange(offset: number, shown: number, total: number) {
  return shown > 0 ? { from: offset + 1, to: offset + shown, total } : null;
}
