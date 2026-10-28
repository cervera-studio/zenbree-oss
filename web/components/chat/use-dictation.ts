'use client';

import { useLocale, useTranslations } from 'next-intl';
import {
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { flushSync } from 'react-dom';
import { toast } from '@/components/ui/sonner';

type DictationState = 'unsupported' | 'idle' | 'listening' | 'denied';

// The DOM lib does not ship the Web Speech API types: only what this hook reads and sets.
type RecognitionResultEvent = {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
};
type RecognitionErrorEvent = { error: string };
type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: RecognitionResultEvent) => void) | null;
  onerror: ((event: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
};
type RecognitionConstructor = new () => Recognition;
type SpeechWindow = {
  SpeechRecognition?: RecognitionConstructor;
  webkitSpeechRecognition?: RecognitionConstructor;
};

type ErrorMessage = 'denied' | 'noMic' | 'network' | 'noSpeech' | 'failed';
type ErrorOutcome = { denied: boolean; message?: ErrorMessage };

const ERRORS = new Map<string, ErrorOutcome>([
  ['not-allowed', { denied: true, message: 'denied' }],
  // The browser turned its speech service off: the site permission is not the cause.
  ['service-not-allowed', { denied: false, message: 'failed' }],
  ['audio-capture', { denied: false, message: 'noMic' }],
  ['network', { denied: false, message: 'network' }],
  ['no-speech', { denied: false, message: 'noSpeech' }],
  ['aborted', { denied: false }],
]);
const UNKNOWN_ERROR: ErrorOutcome = { denied: false, message: 'failed' };

const subscribe = () => () => {};
const recognitionConstructor = () => {
  const w = window as Window & SpeechWindow;
  // Brave ships the API without Google's speech service: every start fails with `network`, and
  // `available()` still answers "available", so only the browser itself tells it apart.
  if ('brave' in navigator) return null;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

// Safari maps a bare `en` to `en-GB`, and a Mac without that offline model and with Siri off hears
// nothing. The browser's own regional tag for the interface language is the one its user has installed.
function recognitionLang(locale: string) {
  return navigator.languages.find((tag) => new Intl.Locale(tag).language === locale) ?? locale;
}

function join(text: string, transcript: string) {
  return text === '' || /\s$/.test(text) ? text + transcript : `${text} ${transcript}`;
}

/** Browser speech-to-text for a composer: one spoken sentence is appended to `text`, never sent. */
export function useDictation({
  text,
  onText,
  composer,
  disabled,
}: {
  text: string;
  onText: (text: string) => void;
  composer: RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
}): { state: DictationState; toggle: () => void } {
  const t = useTranslations('chat.dictation');
  const locale = useLocale();
  const Constructor = useSyncExternalStore(subscribe, recognitionConstructor, () => null);
  // A session listens for the text it started on: any other change to the box ends it.
  const [session, setSession] = useState<{ text: string } | null>(null);
  const [denied, setDenied] = useState(false);
  const stop = useRef<(() => void) | null>(null);
  // The session effect reads these at event time, so a new callback from the parent does not restart it.
  const latest = useRef({ onText, t });

  if (session && (session.text !== text || disabled)) setSession(null);

  useEffect(() => {
    latest.current = { onText, t };
  });

  // A layout effect: the cleanup must run in the commit that changes the text, before a late result.
  useLayoutEffect(() => {
    if (!Constructor || !session) return;
    let live = true;
    // Settled once the session has a result, an error, or a stop from the user: an end before that heard nothing.
    let settled = false;
    const r = new Constructor();
    r.lang = recognitionLang(locale);
    r.continuous = false;
    r.interimResults = false;
    r.maxAlternatives = 1;
    r.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript.trim();
      if (!live || !transcript) return;
      settled = true;
      const next = join(session.text, transcript);
      flushSync(() => latest.current.onText(next));
      composer.current?.focus();
      composer.current?.setSelectionRange(next.length, next.length);
    };
    r.onerror = (event) => {
      if (!live) return;
      settled = true;
      const outcome = ERRORS.get(event.error) ?? UNKNOWN_ERROR;
      if (outcome.denied) setDenied(true);
      if (outcome.message) toast.error(latest.current.t(`error.${outcome.message}`));
      setSession(null);
    };
    r.onend = () => {
      if (!live) return;
      // Safari ends a session that heard no sound without a result or an error.
      if (!settled) toast.error(latest.current.t('error.noSpeech'));
      setSession(null);
    };
    stop.current = () => {
      settled = true;
      r.stop();
    };
    r.start();
    return () => {
      live = false;
      stop.current = null;
      // abort, not stop: stop would still deliver the pending result, late, into a box the user changed.
      r.abort();
    };
  }, [Constructor, session, locale, composer]);

  const toggle = () => {
    if (stop.current) {
      stop.current();
      return;
    }
    setDenied(false);
    setSession({ text });
  };

  const state: DictationState = !Constructor
    ? 'unsupported'
    : session
      ? 'listening'
      : denied
        ? 'denied'
        : 'idle';
  return { state, toggle };
}
