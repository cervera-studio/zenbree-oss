import { describe, expect, it } from 'vite-plus/test';
import type { HomeOverview } from '@/lib/home/overview';
import { chooseHome, dayPart, dayRelation, initials } from './home-overview';

const EMPTY: HomeOverview = {
  approvals: { total: 0, campaigns: [] },
  replies: { count: 0, latest: null },
  running: [],
  finished: [],
  lists: [],
  campaigns: [],
  campaignHint: null,
};

const list = {
  id: 'l1',
  name: 'Milano',
  entity: 'person' as const,
  mode: 'build' as const,
  rows: 1,
  verifiedEmails: 1,
  updatedAt: '2026-10-01T09:00:00Z',
  owner: null,
};

describe('chooseHome', () => {
  const welcome = { firstName: 'Ale' };

  it('shows the first-access welcome over everything', () => {
    expect(chooseHome(welcome, { ...EMPTY, lists: [list] })).toEqual({ kind: 'welcome', welcome });
  });

  it('shows the Home with data when the project has a list, a campaign or a running search', () => {
    const campaign = {
      id: 'c',
      name: 'C',
      status: 'active' as const,
      channels: [],
      sent: 0,
      replies: 0,
      owner: null,
    };
    const run = {
      jobId: 'j',
      listId: null,
      newList: false,
      sessionId: 's',
      title: null,
      stageTool: null,
      entity: null,
      stagesDone: 0,
      stagesTotal: 0,
      progressDone: 0,
      progressTotal: 0,
      rows: 0,
      startedBy: null,
      startedAt: '2026-10-01T09:00:00Z',
    };
    for (const overview of [
      { ...EMPTY, lists: [list] },
      { ...EMPTY, campaigns: [campaign] },
      { ...EMPTY, running: [run] },
      // Something that waits on the user is never hidden.
      { ...EMPTY, approvals: { total: 2, campaigns: [] } },
      { ...EMPTY, replies: { count: 1, latest: null } },
    ]) {
      expect(chooseHome(null, overview)).toEqual({ kind: 'data', overview });
    }
  });

  it('shows the bare composer for a project with no activity, or when the overview failed', () => {
    expect(chooseHome(null, EMPTY)).toEqual({ kind: 'empty' });
    expect(chooseHome(undefined, null)).toEqual({ kind: 'empty' });
  });
});

describe('dayRelation', () => {
  const now = new Date('2026-10-06T10:00:00Z');

  it('reads the calendar day in the given zone, not in UTC', () => {
    expect(dayRelation(new Date('2026-10-05T22:30:00Z'), now, 'Europe/Rome')).toBe('today');
    expect(dayRelation(new Date('2026-10-05T22:30:00Z'), now, 'UTC')).toBe('yesterday');
  });

  it('tells yesterday from earlier', () => {
    expect(dayRelation(new Date('2026-10-05T12:00:00Z'), now, 'Europe/Rome')).toBe('yesterday');
    expect(dayRelation(new Date('2026-09-29T12:00:00Z'), now, 'Europe/Rome')).toBe('earlier');
  });
});

describe('dayPart', () => {
  it('greets by the hour in the given zone', () => {
    expect(dayPart(new Date('2026-10-06T07:00:00Z'), 'Europe/Rome')).toBe('morning');
    expect(dayPart(new Date('2026-10-06T13:00:00Z'), 'Europe/Rome')).toBe('afternoon');
    expect(dayPart(new Date('2026-10-06T18:00:00Z'), 'Europe/Rome')).toBe('evening');
    expect(dayPart(new Date('2026-10-06T22:30:00Z'), 'Europe/Rome')).toBe('evening');
  });
});

describe('initials', () => {
  it('takes the first letters of the first two words', () => {
    expect(initials('Ale Poli')).toBe('AP');
    expect(initials('  davide  rinaldi  bianchi')).toBe('DR');
    expect(initials('Sara')).toBe('S');
  });
});
