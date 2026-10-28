/**
 * One review visit (#171, #864): the messages still waiting are the server's queue minus the ids
 * decided on this visit, so a decision leaves the screen before the refresh lands. Progress counts
 * against what the visit decided plus what still waits: an arrival grows the total, a message
 * decided elsewhere shrinks it, and neither moves the count of reviewed ones.
 */
export function reviewQueue<T extends { id: string }>(items: T[], decided: ReadonlySet<string>) {
  const queue = items.filter((i) => !decided.has(i.id));
  return {
    current: queue[0],
    next: queue.slice(1),
    queue,
    reviewed: decided.size,
    total: decided.size + queue.length,
  };
}

export function withIds(decided: ReadonlySet<string>, ids: string[]) {
  return new Set([...decided, ...ids]);
}

export function withoutIds(decided: ReadonlySet<string>, ids: string[]) {
  return new Set([...decided].filter((id) => !ids.includes(id)));
}

/** The API applied none of them (decided elsewhere, the person moved on): they were not this visit's. */
export function afterDecision(decided: ReadonlySet<string>, ids: string[], applied: number) {
  return applied === 0 ? withoutIds(decided, ids) : decided;
}

/** Restored ones wait again; one the worker moved on stays reviewed, as it will not come back. */
export function afterUndo(decided: ReadonlySet<string>, ids: string[], restored: number) {
  return restored === ids.length ? withoutIds(decided, ids) : decided;
}
