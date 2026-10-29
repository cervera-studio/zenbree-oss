import { describe, expect, it } from 'vite-plus/test';
import { applyListFrame, applyPlanList, type ChatLists } from './chat-lists';

const people: ChatLists = {
  person: { id: 'P2', entity: 'person', name: 'Persone', rowCount: 24, version: 3 },
};

describe('applyPlanList', () => {
  it('keeps the current list when an enrich plan targets an older one', () => {
    expect(applyPlanList(people, { listId: 'P1', entity: 'person', mode: 'enrich' })).toBe(people);
  });
});

describe('applyListFrame', () => {
  it('keeps each slot when a frame with a null entity names the company list', () => {
    const both: ChatLists = {
      ...people,
      company: { id: 'C1', entity: 'company', name: 'Aziende', rowCount: 9, version: 1 },
    };
    const next = applyListFrame(both, { listId: 'C1', entity: null });
    expect(next?.person).toBe(both.person);
    expect(next?.company).toMatchObject({ id: 'C1', rowCount: 9, version: 2 });
  });

  it('cannot place a frame for an unknown list without entity', () => {
    expect(applyListFrame(people, { listId: 'X' })).toBeNull();
  });
});
