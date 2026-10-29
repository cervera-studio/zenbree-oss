// @vitest-environment happy-dom
import { act, useRef, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { click, control, render } from '@/app/auth/testing';
import { MESSAGES } from '@/messages';
import { DictationButton } from './dictation-button';

const toastError = vi.hoisted(() => vi.fn());
vi.mock('@/components/ui/sonner', () => ({ toast: { error: toastError } }));

const copy = MESSAGES.it.chat.dictation;

class FakeRecognition {
  static instances: FakeRecognition[] = [];
  lang = '';
  continuous = true;
  interimResults = true;
  maxAlternatives = 0;
  onresult: ((event: { results: { transcript: string }[][] }) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();
  abort = vi.fn();
  constructor() {
    FakeRecognition.instances.push(this);
  }
  async hear(transcript: string) {
    await act(async () => {
      this.onresult?.({ results: [[{ transcript }]] });
      this.onend?.();
    });
  }
  async fail(error: string) {
    await act(async () => {
      this.onerror?.({ error });
      this.onend?.();
    });
  }
}

const onText = vi.fn();

function Composer({ initial }: { initial: string }) {
  const [text, setText] = useState(initial);
  const composer = useRef<HTMLTextAreaElement>(null);
  const change = (next: string) => {
    onText(next);
    setText(next);
  };
  return (
    <form>
      <textarea ref={composer} value={text} onChange={(e) => setText(e.target.value)} />
      <button type="button" onClick={() => setText('')}>
        send
      </button>
      <DictationButton
        text={text}
        onText={change}
        composer={composer}
        disabled={false}
        size="icon-sm"
      />
    </form>
  );
}

function Closable() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(false)}>
        close
      </button>
      {open && <Composer initial="" />}
    </>
  );
}

async function startDictation(initial = '') {
  const container = await render(<Composer initial={initial} />);
  await click(control(container, copy.start));
  const [recognition] = FakeRecognition.instances;
  if (!recognition) throw new Error('the click started no recognition');
  return {
    container,
    recognition,
    box: container.querySelector('textarea') as HTMLTextAreaElement,
  };
}

beforeEach(() => {
  FakeRecognition.instances = [];
  Object.assign(window, { SpeechRecognition: FakeRecognition });
  toastError.mockReset();
  onText.mockReset();
});
afterEach(() => {
  Reflect.deleteProperty(window, 'SpeechRecognition');
});

describe('DictationButton', () => {
  it('renders nothing when the browser has no speech recognition', async () => {
    Reflect.deleteProperty(window, 'SpeechRecognition');
    const container = await render(<Composer initial="" />);
    expect(() => control(container, copy.start)).toThrow();
  });

  it('renders nothing in Brave, which has the API but no speech service behind it', async () => {
    Object.assign(navigator, { brave: { isBrave: async () => true } });
    try {
      const container = await render(<Composer initial="" />);
      expect(() => control(container, copy.start)).toThrow();
    } finally {
      Reflect.deleteProperty(navigator, 'brave');
    }
  });

  it('listens once, in the interface language', async () => {
    const { container, recognition } = await startDictation();
    expect(FakeRecognition.instances).toHaveLength(1);
    expect(recognition.start).toHaveBeenCalledTimes(1);
    expect(recognition.lang).toBe('it');
    expect(recognition.continuous).toBe(false);
    expect(recognition.interimResults).toBe(false);
    expect(control(container, copy.stop).getAttribute('aria-pressed')).toBe('true');
  });

  it.each([
    [['en-US', 'it-IT'], 'it-IT'],
    [['en-US'], 'it'],
  ])('with browser languages %j, asks for %j', async (languages, lang) => {
    const spy = vi.spyOn(navigator, 'languages', 'get').mockReturnValue(languages);
    try {
      const { recognition } = await startDictation();
      expect(recognition.lang).toBe(lang);
    } finally {
      spy.mockRestore();
    }
  });

  it.each([
    ['ciao', 'ciao mondo'],
    ['', 'mondo'],
  ])('appends what it hears to %j and leaves the caret at the end', async (initial, expected) => {
    const { recognition, box } = await startDictation(initial);
    await recognition.hear(' mondo ');
    expect(box.value).toBe(expected);
    expect(document.activeElement).toBe(box);
    expect(box.selectionStart).toBe(expected.length);
  });

  it('shows the microphone as blocked when the browser denies it', async () => {
    const { container, recognition } = await startDictation();
    await recognition.fail('not-allowed');
    expect(toastError).toHaveBeenCalledWith(copy.error.denied);
    expect(control(container, copy.denied)).toBeTruthy();
  });

  it('drops a late result once the box changed under it', async () => {
    const { container, recognition, box } = await startDictation('ciao');
    await click(control(container, 'send'));
    expect(recognition.abort).toHaveBeenCalled();
    await recognition.hear('mondo');
    expect(box.value).toBe('');
    expect(onText).not.toHaveBeenCalled();
  });

  it('does not call a browser speech service block a blocked microphone', async () => {
    const { container, recognition } = await startDictation();
    await recognition.fail('service-not-allowed');
    expect(toastError).toHaveBeenCalledWith(copy.error.failed);
    expect(control(container, copy.start)).toBeTruthy();
  });

  it('drops a late result once the composer is gone, as on a switch to another chat', async () => {
    const container = await render(<Closable />);
    await click(control(container, copy.start));
    const [recognition] = FakeRecognition.instances;
    await click(control(container, 'close'));
    expect(recognition?.abort).toHaveBeenCalled();
    await recognition?.hear('mondo');
    expect(onText).not.toHaveBeenCalled();
  });

  it('says it heard nothing on a no-speech error', async () => {
    const { container, recognition } = await startDictation();
    await recognition.fail('no-speech');
    expect(toastError).toHaveBeenCalledWith(copy.error.noSpeech);
    expect(control(container, copy.start).getAttribute('aria-pressed')).toBe('false');
  });

  // Safari ends a session that heard no sound with no result and no error.
  it('says it heard nothing when the session ends with no result and no error', async () => {
    const { container, recognition } = await startDictation();
    await act(async () => recognition.onend?.());
    expect(toastError).toHaveBeenCalledWith(copy.error.noSpeech);
    expect(control(container, copy.start).getAttribute('aria-pressed')).toBe('false');
  });

  it('stays quiet when the user stops before saying anything', async () => {
    const { container, recognition } = await startDictation();
    await click(control(container, copy.stop));
    expect(recognition.stop).toHaveBeenCalled();
    await act(async () => recognition.onend?.());
    expect(toastError).not.toHaveBeenCalled();
    expect(control(container, copy.start)).toBeTruthy();
  });
});
