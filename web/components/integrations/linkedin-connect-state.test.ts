import { describe, expect, it } from 'vite-plus/test';
import { type Action, initialState, reducer } from './linkedin-connect-state';

const run = (...actions: Action[]) => actions.reduce(reducer, initialState);
const accepted: Action = { type: 'acceptedChanged', value: true };
const typing = (): Action[] => [
  accepted,
  { type: 'credentialsOpened' },
  { type: 'usernameChanged', value: 'a@b.co' },
  { type: 'passwordChanged', value: 'secret' },
];
const atCheckpoint = (): Action[] => [
  accepted,
  { type: 'checkpointReached', accountId: 'acc1' },
  { type: 'codeChanged', value: '123456' },
];

describe('linkedin connect reducer', () => {
  it('starts idle, not accepted', () => {
    expect(initialState).toEqual({ step: 'idle', accepted: false });
  });

  it('credentialsOpened opens empty fields and keeps the acceptance', () => {
    expect(run(accepted, { type: 'credentialsOpened' })).toEqual({
      step: 'credentials',
      accepted: true,
      username: '',
      password: '',
    });
  });

  it('acceptance changes only while idle', () => {
    expect(run(accepted).accepted).toBe(true);
    expect(run(...typing(), { type: 'acceptedChanged', value: false }).accepted).toBe(true);
  });

  it('field edits apply only in the step that owns the field', () => {
    expect(run({ type: 'usernameChanged', value: 'x' })).toEqual(initialState);
    expect(run({ type: 'passwordChanged', value: 'x' })).toEqual(initialState);
    expect(run({ type: 'codeChanged', value: '1' })).toEqual(initialState);
    expect(run(...typing(), { type: 'codeChanged', value: '1' })).toMatchObject({
      username: 'a@b.co',
      password: 'secret',
    });
    expect(run(...atCheckpoint(), { type: 'passwordChanged', value: 'x' })).toMatchObject({
      code: '123456',
    });
  });

  it('checkpointReached moves to the checkpoint with an empty code and no password', () => {
    const state = run(...typing(), { type: 'checkpointReached', accountId: 'acc1' });
    expect(state).toEqual({
      step: 'checkpoint',
      accepted: true,
      accountId: 'acc1',
      code: '',
    });
  });

  it('connected returns to idle and drops every typed secret', () => {
    expect(run(...atCheckpoint(), { type: 'connected' })).toEqual({ step: 'idle', accepted: true });
    expect(run(...typing(), { type: 'connected' })).not.toHaveProperty('password');
  });

  it('secretsCleared on idle changes nothing', () => {
    expect(run(accepted, { type: 'secretsCleared' })).toEqual({ step: 'idle', accepted: true });
  });

  it('secretsCleared empties the password or the code and nothing else', () => {
    expect(run(...typing(), { type: 'secretsCleared' })).toMatchObject({
      username: 'a@b.co',
      password: '',
    });
    expect(run(...atCheckpoint(), { type: 'secretsCleared' })).toMatchObject({ code: '' });
  });

  it('cancelled returns to idle and drops the typed values', () => {
    expect(run(...typing(), { type: 'cancelled' })).toEqual({ step: 'idle', accepted: true });
    expect(run(...atCheckpoint(), { type: 'cancelled' })).toMatchObject({ step: 'idle' });
  });

  it('credentialsOpened does nothing outside idle', () => {
    const state = run(...typing());
    expect(reducer(state, { type: 'credentialsOpened' })).toBe(state);
  });
});
