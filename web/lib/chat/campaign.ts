export interface CampaignDraft {
  id: string;
  name: string;
  targetCount: number;
}

export function parseCampaign(raw: unknown): CampaignDraft | undefined {
  if (typeof raw !== 'object' || raw === null) return undefined;
  const { id, name, targetCount } = raw as Record<string, unknown>;
  return typeof id === 'string' && id && typeof name === 'string' && typeof targetCount === 'number'
    ? { id, name, targetCount }
    : undefined;
}
