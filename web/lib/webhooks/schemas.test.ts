import { describe, expect, it } from 'vite-plus/test';
import { toCamelCase } from '@/lib/http-client/case-transform';
import { createdSchema, deliveriesSchema, listSchema } from './schemas';

// The wire is snake_case; the client converts it before parsing, so the rows go through the same step.
const parse = <T>(schema: { parse: (v: unknown) => T }, wire: unknown) =>
  schema.parse(toCamelCase(wire));

const delivery = (category: string | null, status: number | null) => ({
  id: 'd1',
  event_id: 'abc',
  event_type: 'campaign.finished',
  is_test: false,
  attempt: 1,
  attempted_at: '2026-10-10T10:00:00Z',
  status_code: status,
  category,
  will_retry: false,
});

describe('webhook schemas', () => {
  it.each([
    ['timeout', null],
    ['blocked', null],
    ['unreachable', null],
    ['rate_limited', 429],
    ['server_error', 503],
    ['rejected', 404],
    ['gone', 410],
    [null, 200],
  ])('parses a delivery row of category %s', (category, status) => {
    const { deliveries } = parse(deliveriesSchema, { deliveries: [delivery(category, status)] });
    expect(deliveries[0]?.category).toBe(category);
  });

  it('refuses a category the API does not name', () => {
    expect(() => parse(deliveriesSchema, { deliveries: [delivery('exploded', 500)] })).toThrow();
  });

  it('keeps the host and drops the URL, in a list and in a create', () => {
    const wire = {
      id: 'w1',
      url: 'https://hooks.zapier.com/hooks/catch/tok-9f8e',
      label: 'Zap',
      event_names: ['campaign.finished'],
      created_at: '2026-10-10T10:00:00Z',
      gone_at: '2026-10-11T10:00:00Z',
    };
    const list = parse(listSchema, { webhooks: [wire], events: ['campaign.finished'] });
    expect(list.webhooks[0]).toMatchObject({ host: 'hooks.zapier.com', goneAt: wire.gone_at });
    const created = parse(createdSchema, { ...wire, secret: 'whsec_x' });
    expect(created.secret).toBe('whsec_x');
    expect(JSON.stringify([list, created])).not.toContain('tok-9f8e');
  });
});
