// @vitest-environment happy-dom
import { useEffect } from 'react';
import { expect, test, vi } from 'vite-plus/test';
import { render } from './testing';

function Probe({ onCleanup }: { onCleanup: () => void }) {
  useEffect(() => {
    return onCleanup;
  }, [onCleanup]);
  return <p>probe</p>;
}

test('a second render unmounts the first, so its effect cleanup runs', async () => {
  const cleanup = vi.fn();
  await render(<Probe onCleanup={cleanup} />);
  expect(cleanup).not.toHaveBeenCalled();
  await render(<p>next</p>);
  expect(cleanup).toHaveBeenCalledTimes(1);
});

// A plain counter: observed here, the calls of a `vi.fn()` are cleared between tests (a bare two-test file shows it), which would hide the call.
let lastCleanups = 0;
const lastCleanup = () => {
  lastCleanups += 1;
};

test('the last render of a test stays mounted until the test ends', async () => {
  await render(<Probe onCleanup={lastCleanup} />);
  expect(lastCleanups).toBe(0);
});

// Depends on the case above running first (tests of a file run in order).
test('and is unmounted when the next test starts', () => {
  expect(lastCleanups).toBe(1);
});
