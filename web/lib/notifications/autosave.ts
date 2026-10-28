export function createAutosaver<T>({
  initial,
  put,
  onChange,
  onResult,
}: {
  initial: T;
  put: (value: T) => Promise<boolean>;
  onChange: (value: T) => void;
  onResult: (ok: boolean) => void;
}) {
  let saved = initial;
  let value = initial;
  let tail: Promise<void> = Promise.resolve();

  return {
    change(update: (current: T) => T) {
      value = update(value);
      onChange(value);
      tail = tail.then(async () => {
        if (value === saved) return;
        const sent = value;
        const ok = await put(sent).catch(() => false);
        if (ok) {
          saved = sent;
        } else {
          value = saved;
          onChange(value);
        }
        onResult(ok);
      });
    },
  };
}
