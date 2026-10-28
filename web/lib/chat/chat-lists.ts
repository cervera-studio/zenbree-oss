import type { AgentPlan } from './types';

export type ListEntity = 'company' | 'person';

export interface ChatList {
  id: string;
  entity: ListEntity;
  name: string | null;
  rowCount: number | null;
  version: number;
}

export type ChatLists = Partial<Record<ListEntity, ChatList>>;

const ENTITIES: readonly ListEntity[] = ['company', 'person'];

export function parseEntity(raw: unknown): ListEntity | null {
  return ENTITIES.find((e) => e === raw) ?? null;
}

export function listsFromSession(
  rows: readonly { id: string; name: string; entity: string; rowCount: number | null }[],
): ChatLists {
  const out: ChatLists = {};
  for (const row of rows) {
    const entity = parseEntity(row.entity);
    if (entity)
      out[entity] = { id: row.id, entity, name: row.name, rowCount: row.rowCount, version: 0 };
  }
  return out;
}

export function applyListFrame(
  lists: ChatLists,
  frame: { listId: string; entity?: unknown; rowCount?: unknown },
): ChatLists | null {
  const entity =
    parseEntity(frame.entity) ?? ENTITIES.find((e) => lists[e]?.id === frame.listId) ?? null;
  if (!entity) return null;
  const prev = lists[entity];
  const same = prev?.id === frame.listId;
  const rowCount =
    typeof frame.rowCount === 'number' ? frame.rowCount : same ? (prev?.rowCount ?? null) : null;
  return {
    ...lists,
    [entity]: {
      id: frame.listId,
      entity,
      name: same ? (prev?.name ?? null) : null,
      rowCount,
      version: (same ? (prev?.version ?? 0) : 0) + 1,
    },
  };
}

export function applyPlanList(
  lists: ChatLists,
  plan: Pick<AgentPlan, 'listId' | 'entity' | 'mode'>,
): ChatLists {
  const current = lists[plan.entity];
  if (current?.id === plan.listId || (current && plan.mode !== 'build')) return lists;
  return {
    ...lists,
    [plan.entity]: { id: plan.listId, entity: plan.entity, name: null, rowCount: 0, version: 0 },
  };
}
