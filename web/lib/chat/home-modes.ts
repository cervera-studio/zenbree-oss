import { Briefcase, Building, Search, Send, Sparkles, User } from 'lucide-react';
import type { ComponentType } from 'react';
import type { ChatGoal } from './stream-store-types';

/**
 * The home composer's modes. The selector, the entry cards, the placeholder and the submit all map
 * over this table; copy lives under `chat.home.modes.<id>`. Only `action.kind` branches behaviour.
 */
export const HOME_MODE_IDS = ['find', 'enrich', 'contact'] as const;
export type HomeModeId = (typeof HOME_MODE_IDS)[number];

export interface ChatAction {
  kind: 'chat';
  goal?: ChatGoal;
}
type HomeAction = ChatAction | { kind: 'import' };

interface HomeMode {
  icon: ComponentType<{ className?: string }>;
  action: HomeAction;
}

export const DEFAULT_MODE: HomeModeId = 'find';

export const HOME_MODES: Record<HomeModeId, HomeMode> = {
  find: { icon: Search, action: { kind: 'chat' } },
  enrich: { icon: Sparkles, action: { kind: 'import' } },
  contact: { icon: Send, action: { kind: 'chat', goal: 'campaign' } },
};

/** A chip or card on the enrich mode sends as the default chat, since enrich only imports. */
export function chatActionFor(mode: HomeModeId): ChatAction {
  const { action } = HOME_MODES[mode];
  if (action.kind === 'chat') return action;
  return { kind: 'chat' };
}

/** Starter briefs; copy under `chat.home.chips.<id>` (`brief` on desktop, `short` on a phone). */
export const HOME_CHIPS = [
  { id: 'agencies', icon: Briefcase },
  { id: 'saas', icon: Building },
  { id: 'marketing', icon: User },
] as const;
