import { describe, expect, it, vi } from 'vite-plus/test';

vi.mock('server-only', () => ({}));
vi.mock('@/lib/auth-token', () => ({ getToken: async () => undefined }));
vi.mock('@/lib/http-client', () => ({ httpClient: {} }));

const { costsSchema } = await import('./api');
const { toCamelCase } = await import('@/lib/http-client/case-transform');

const money = {
  llm_cost_usd: 0.42,
  vendor_cost_usd: 0.1,
  cost_usd: 0.52,
  credits_spent: 30,
  revenue_eur_cents: 1000,
  revenue_eur: 10,
  margin_eur: 9.52,
};

// Wire shape of GET /v1/admin/costs (api/app/admin/costs.py), snake_case.
const wire = {
  from: '2026-09-01',
  to: '2026-09-30',
  eur_per_usd: 0.92,
  totals: { ...money, credits_purchased: 100 },
  users: [{ ...money, user_id: 'u1', email: 'a@b.it', credits_purchased: 100 }],
  daily: [{ ...money, day: '2026-09-01' }],
  by_vendor: [{ vendor: 'apollo', calls: 3, units: 3, cost_usd: 0.1 }],
  by_provider: [{ provider: 'pdl', channel: 'phone', lookups: 4, hits: 1 }],
};

describe('costsSchema', () => {
  it('parses the camelCased wire payload', () => {
    const parsed = costsSchema.parse(toCamelCase(wire));
    expect(parsed.users[0]?.marginEur).toBe(9.52);
    expect(parsed.byVendor[0]?.costUsd).toBe(0.1);
    expect(parsed.eurPerUsd).toBe(0.92);
    expect(parsed.byProvider[0]?.hits).toBe(1);
  });
});
