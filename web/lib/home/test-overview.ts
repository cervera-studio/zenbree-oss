import type { FinishedSearch, HomeOverview, RunningSearch } from './overview';

export function overviewOf(over: Partial<HomeOverview> = {}): HomeOverview {
  return {
    approvals: { total: 0, campaigns: [] },
    replies: { count: 0, latest: null },
    running: [],
    finished: [],
    lists: [],
    campaigns: [],
    campaignHint: null,
    ...over,
  };
}

export function runOf(over: Partial<RunningSearch> = {}): RunningSearch {
  return {
    jobId: 'j1',
    listId: 'l1',
    newList: true,
    sessionId: 's1',
    title: 'Agenzie Bologna',
    stageTool: null,
    entity: 'person',
    stagesDone: 2,
    stagesTotal: 4,
    progressDone: 20,
    progressTotal: 41,
    rows: 20,
    startedBy: null,
    startedAt: '2026-10-07T09:00:00Z',
    ...over,
  };
}

export function finishedOf(over: Partial<FinishedSearch> = {}): FinishedSearch {
  return {
    jobId: 'j1',
    sessionId: 's1',
    title: 'Agenzie Bologna',
    entity: 'person',
    listId: 'l1',
    newList: true,
    rows: 41,
    verifiedEmails: 38,
    stagesTotal: 4,
    ...over,
  };
}

export function listOf(id: string, name = id): HomeOverview['lists'][number] {
  return {
    id,
    name,
    entity: 'person',
    mode: 'build',
    rows: 41,
    verifiedEmails: 38,
    updatedAt: '2026-10-07T09:10:00Z',
    owner: null,
  };
}
