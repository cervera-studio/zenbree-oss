type Shared = { accepted: boolean };

/** Each step carries only its own fields, so a checkpoint has no password and idle has no code. */
export type ConnectState =
  | ({ step: 'idle' } & Shared)
  | ({ step: 'credentials'; username: string; password: string } & Shared)
  | ({ step: 'checkpoint'; accountId: string; code: string } & Shared);

export type Action =
  | { type: 'acceptedChanged'; value: boolean }
  | { type: 'credentialsOpened' }
  | { type: 'usernameChanged'; value: string }
  | { type: 'passwordChanged'; value: string }
  | { type: 'codeChanged'; value: string }
  | { type: 'checkpointReached'; accountId: string }
  | { type: 'connected' }
  | { type: 'secretsCleared' }
  | { type: 'cancelled' };

export const initialState: ConnectState = { step: 'idle', accepted: false };

export function reducer(state: ConnectState, action: Action): ConnectState {
  switch (action.type) {
    case 'acceptedChanged':
      return state.step === 'idle' ? { ...state, accepted: action.value } : state;
    case 'credentialsOpened':
      return state.step === 'idle'
        ? { ...state, step: 'credentials', username: '', password: '' }
        : state;
    case 'usernameChanged':
      return state.step === 'credentials' ? { ...state, username: action.value } : state;
    case 'passwordChanged':
      return state.step === 'credentials' ? { ...state, password: action.value } : state;
    case 'codeChanged':
      return state.step === 'checkpoint' ? { ...state, code: action.value } : state;
    case 'checkpointReached':
      return {
        step: 'checkpoint',
        accepted: state.accepted,
        accountId: action.accountId,
        code: '',
      };
    case 'connected':
    case 'cancelled':
      return { step: 'idle', accepted: state.accepted };
    case 'secretsCleared':
      return clearSecrets(state);
  }
}

function clearSecrets(state: ConnectState): ConnectState {
  if (state.step === 'credentials') return { ...state, password: '' };
  if (state.step === 'checkpoint') return { ...state, code: '' };
  return state;
}
