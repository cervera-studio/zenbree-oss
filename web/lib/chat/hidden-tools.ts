export const HIDDEN_TOOLS: ReadonlySet<string> = new Set(['ask_user', 'set_brief']);

export function visiblePhase(phase: string | null | undefined): string | null {
  return phase && !HIDDEN_TOOLS.has(phase) ? phase : null;
}
