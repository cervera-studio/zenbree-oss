import { describe, expect, it } from 'vite-plus/test';
import { createAutosaver } from './autosave';

type State = { a: boolean; b: boolean };
const start: State = { a: false, b: false };

function deferred() {
  let resolve: (ok: boolean) => void = () => {};
  const promise = new Promise<boolean>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function nth<T>(items: T[], i: number): T {
  const item = items[i];
  if (item === undefined) throw new Error(`no entry ${i}`);
  return item;
}

function setup() {
  const puts: { value: State; done: ReturnType<typeof deferred> }[] = [];
  const shown: State[] = [];
  const results: boolean[] = [];
  const saver = createAutosaver<State>({
    initial: start,
    put: (value) => {
      const done = deferred();
      puts.push({ value, done });
      return done.promise;
    },
    onChange: (v) => shown.push(v),
    onResult: (ok) => results.push(ok),
  });
  const flush = () => new Promise((r) => setTimeout(r, 0));
  return { saver, puts, shown, results, flush };
}

describe('createAutosaver', () => {
  it('holds a second save until the first responds, then sends the latest value', async () => {
    const { saver, puts, results, flush } = setup();
    saver.change((v) => ({ ...v, a: true }));
    await flush();
    saver.change((v) => ({ ...v, b: true }));
    await flush();
    expect(puts).toHaveLength(1);
    expect(nth(puts, 0).value).toEqual({ a: true, b: false });

    nth(puts, 0).done.resolve(true);
    await flush();
    expect(puts).toHaveLength(2);
    expect(nth(puts, 1).value).toEqual({ a: true, b: true });

    nth(puts, 1).done.resolve(true);
    await flush();
    expect(results).toEqual([true, true]);
  });

  it('reverts to the last saved value and drops the queued saves when one fails', async () => {
    const { saver, puts, shown, results, flush } = setup();
    saver.change((v) => ({ ...v, a: true }));
    await flush();
    saver.change((v) => ({ ...v, b: true }));
    await flush();

    nth(puts, 0).done.resolve(false);
    await flush();
    expect(shown.at(-1)).toEqual(start);
    expect(puts).toHaveLength(1);
    expect(results).toEqual([false]);
  });

  it('saves again after a revert, from the saved value', async () => {
    const { saver, puts, flush } = setup();
    saver.change((v) => ({ ...v, a: true }));
    await flush();
    nth(puts, 0).done.resolve(false);
    await flush();

    saver.change((v) => ({ ...v, b: true }));
    await flush();
    expect(puts).toHaveLength(2);
    expect(nth(puts, 1).value).toEqual({ a: false, b: true });
  });

  it('treats a rejected put as a failure', async () => {
    const shown: State[] = [];
    const results: boolean[] = [];
    const saver = createAutosaver<State>({
      initial: start,
      put: () => Promise.reject(new Error('offline')),
      onChange: (v) => shown.push(v),
      onResult: (ok) => results.push(ok),
    });
    saver.change((v) => ({ ...v, a: true }));
    await new Promise((r) => setTimeout(r, 0));
    expect(results).toEqual([false]);
    expect(shown.at(-1)).toEqual(start);
  });
});
