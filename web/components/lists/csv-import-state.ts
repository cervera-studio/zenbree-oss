import type { SetStateAction } from 'react';
import type { Schema } from '@/lib/api/schema';

export type Entity = 'company' | 'person';
export type Notice = { title: string; body: string };
type Imp = Schema<'CsvImportState'>;
type Status = { busy: boolean; error: Notice | null };
type Loaded = {
  entity: Entity;
  file: File;
  preview: Schema<'CsvPreview'>;
  mapping: string[];
  name: string;
  findEmails: boolean;
};

/** Each step carries only its own data, so a step without its file cannot exist. */
export type ImportState =
  | ({ step: 'file'; entity: Entity } & Status)
  | ({ step: 'map' } & Loaded & Status)
  | ({ step: 'review' } & Loaded & Status)
  | { step: 'import'; entity: Entity; imp: Imp; followUp: string };

export type Action =
  | { type: 'entityChosen'; entity: Entity }
  | { type: 'requestStarted' }
  | { type: 'requestFailed'; notice: Notice }
  | { type: 'previewLoaded'; file: File; preview: Schema<'CsvPreview'> }
  | { type: 'mappingChanged'; mapping: string[] }
  | { type: 'reviewLoaded'; preview: Schema<'CsvPreview'> }
  | { type: 'nameChanged'; name: string }
  | { type: 'findEmailsToggled'; value: boolean }
  | { type: 'stepChanged'; to: 'file' | 'map' | 'review' }
  | { type: 'importStarted'; imp: Imp; followUp: string }
  | { type: 'importResumed'; imp: Imp }
  | { type: 'importUpdated'; next: SetStateAction<Imp | null> }
  | { type: 'restarted' };

export const initialState: ImportState = {
  step: 'file',
  entity: 'person',
  busy: false,
  error: null,
};

export function reducer(state: ImportState, action: Action): ImportState {
  switch (action.type) {
    case 'entityChosen':
      return state.step === 'file' ? { ...state, entity: action.entity } : state;
    case 'requestStarted':
      return withStatus(state, { busy: true, error: null });
    case 'requestFailed':
      return withStatus(state, { busy: false, error: action.notice });
    case 'previewLoaded':
      if (state.step !== 'file') return state;
      return {
        step: 'map',
        entity: state.entity,
        file: action.file,
        preview: action.preview,
        mapping: action.preview.mapping,
        name: action.file.name.replace(/\.csv$/i, ''),
        findEmails: true,
        busy: false,
        error: null,
      };
    case 'mappingChanged':
      return state.step === 'map' ? { ...state, mapping: action.mapping } : state;
    case 'reviewLoaded':
      if (state.step !== 'map') return state;
      return { ...state, step: 'review', preview: action.preview, busy: false, error: null };
    case 'nameChanged':
      return state.step === 'review' ? { ...state, name: action.name } : state;
    case 'findEmailsToggled':
      return state.step === 'review' ? { ...state, findEmails: action.value } : state;
    case 'stepChanged':
      return changeStep(state, action.to);
    case 'importStarted':
      if (state.step !== 'review') return state;
      return { step: 'import', entity: state.entity, imp: action.imp, followUp: action.followUp };
    case 'importResumed':
      return state.step === 'import'
        ? { ...state, imp: action.imp }
        : { step: 'import', entity: state.entity, imp: action.imp, followUp: '' };
    case 'importUpdated':
      return updateImp(state, action.next);
    case 'restarted':
      return { step: 'file', entity: state.entity, busy: false, error: null };
  }
}

function changeStep(state: ImportState, to: 'file' | 'map' | 'review'): ImportState {
  if (state.step === 'import' || state.step === 'file') return state;
  if (to === 'file') return { step: 'file', entity: state.entity, busy: false, error: null };
  return { ...state, step: to, error: null };
}

function withStatus(state: ImportState, status: Status): ImportState {
  return state.step === 'import' ? state : { ...state, ...status };
}

function updateImp(state: ImportState, next: SetStateAction<Imp | null>): ImportState {
  if (state.step !== 'import') return state;
  const imp = typeof next === 'function' ? next(state.imp) : next;
  return imp ? { ...state, imp } : state;
}
