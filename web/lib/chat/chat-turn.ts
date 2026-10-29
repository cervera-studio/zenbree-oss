/**
 * Where the chat's current turn stands. Each phase carries only its own fields; the top-level
 * `ChatState.jobId` is the last job the chat ran, `jobId` here is this turn's own once its
 * `session` frame names it.
 */
export type ChatTurn =
  | { phase: 'idle' }
  | { phase: 'streaming'; jobId: string | null }
  /**
   * «Ferma» asked the server to stop the job; its closing frames are on the way. With no job yet,
   * the stream stays open until its `session` frame names the job to stop.
   */
  | { phase: 'stopping'; jobId: string | null }
  /** `resume()` of a lost stream, before the server accepted it: the banner stays up. */
  | { phase: 'reattaching'; jobId: string | null; pendingStop?: true }
  /** The stream was cut; a rejoin waits on the backoff or the browser's `online`. */
  | { phase: 'lost'; jobId: string | null; pendingStop?: true };

export type TurnEvent =
  | { type: 'sent' }
  /** `jobId`: the job a loaded chat says still runs; a lost stream learns it from `session`. */
  | { type: 'rejoined'; jobId: string | null }
  | { type: 'attached' }
  | { type: 'jobStarted'; jobId: string }
  | { type: 'stopPressed' }
  | { type: 'stopFailed'; jobId: string }
  | { type: 'dropped' }
  /** The saved chat did not load. A turn sent meanwhile is not lost by it. */
  | { type: 'reloadFailed' }
  | { type: 'resynced' }
  | { type: 'ended' }
  | { type: 'loaded' };

export const IDLE: ChatTurn = Object.freeze({ phase: 'idle' });
const LOST: ChatTurn = Object.freeze({ phase: 'lost', jobId: null });

type Phase<P extends ChatTurn['phase']> = Extract<ChatTurn, { phase: P }>;

/** An event the phase does not take returns `turn` itself, so the store skips the write. */
export function nextTurn(turn: ChatTurn, event: TurnEvent): ChatTurn {
  switch (turn.phase) {
    case 'idle':
      return fromIdle(turn, event);
    case 'streaming':
      return fromStreaming(turn, event);
    case 'stopping':
      return fromStopping(turn, event);
    case 'reattaching':
      return fromReattaching(turn, event);
    case 'lost':
      return fromLost(turn, event);
  }
}

function fromIdle(turn: Phase<'idle'>, event: TurnEvent): ChatTurn {
  switch (event.type) {
    case 'sent':
      return { phase: 'streaming', jobId: null };
    case 'rejoined':
      return { phase: 'streaming', jobId: event.jobId };
    case 'dropped':
    case 'reloadFailed':
      return LOST;
    default:
      return turn;
  }
}

function fromStreaming(turn: Phase<'streaming'>, event: TurnEvent): ChatTurn {
  switch (event.type) {
    case 'jobStarted':
      return turn.jobId === event.jobId ? turn : { phase: 'streaming', jobId: event.jobId };
    case 'stopPressed':
      return { phase: 'stopping', jobId: turn.jobId };
    case 'dropped':
      return { phase: 'lost', jobId: turn.jobId };
    case 'resynced':
    case 'ended':
      return IDLE;
    default:
      return turn;
  }
}

function fromStopping(turn: Phase<'stopping'>, event: TurnEvent): ChatTurn {
  switch (event.type) {
    case 'jobStarted':
      return turn.jobId ? turn : { phase: 'stopping', jobId: event.jobId };
    case 'stopFailed':
      return turn.jobId === event.jobId ? { phase: 'streaming', jobId: turn.jobId } : turn;
    case 'dropped':
      return turn.jobId
        ? { phase: 'lost', jobId: turn.jobId }
        : { phase: 'lost', jobId: null, pendingStop: true };
    case 'resynced':
    case 'ended':
      return IDLE;
    default:
      return turn;
  }
}

function fromReattaching(turn: Phase<'reattaching'>, event: TurnEvent): ChatTurn {
  switch (event.type) {
    case 'attached':
      return { phase: turn.pendingStop ? 'stopping' : 'streaming', jobId: turn.jobId };
    case 'stopPressed':
      return turn.jobId || turn.pendingStop ? { phase: 'stopping', jobId: turn.jobId } : IDLE;
    case 'resynced':
      return IDLE;
    // A rejoin that ends before the server accepted it proved nothing: still lost.
    case 'dropped':
    case 'ended':
      return { ...turn, phase: 'lost' };
    default:
      return turn;
  }
}

function fromLost(turn: Phase<'lost'>, event: TurnEvent): ChatTurn {
  switch (event.type) {
    case 'rejoined':
      return { ...turn, phase: 'reattaching' };
    case 'loaded':
      return IDLE;
    // `ended` keeps it lost: every cut marks the turn lost before its stream ends, and the rejoin
    // is scheduled from that.
    default:
      return turn;
  }
}

/** No turn and no cut: a new message may go. */
export function turnIdle(turn: ChatTurn): boolean {
  return turn.phase === 'idle';
}

/** A stream is open or being opened: no new send, no reload over it. */
export function turnLive(turn: ChatTurn): boolean {
  return turn.phase === 'streaming' || turn.phase === 'stopping' || turn.phase === 'reattaching';
}

export function turnStopping(turn: ChatTurn): boolean {
  return turn.phase === 'stopping';
}

/** The connection banner is up until a rejoin is accepted. */
export function turnOffline(turn: ChatTurn): boolean {
  return turn.phase === 'lost' || turn.phase === 'reattaching';
}

/** The job a `session` frame named for a turn stopped before it: the one to stop, never the last. */
export function jobNamedAfterStop(before: ChatTurn, after: ChatTurn): string | null {
  return before.phase === 'stopping' && !before.jobId && after.phase === 'stopping'
    ? after.jobId
    : null;
}

export function advanced<S extends { turn: ChatTurn }>(state: S, event: TurnEvent): S {
  const turn = nextTurn(state.turn, event);
  return turn === state.turn ? state : { ...state, turn };
}
