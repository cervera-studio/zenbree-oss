import { describe, expect, it } from 'vite-plus/test';
import {
  ACTIONS,
  type AppNotification,
  channelOf,
  filterCounts,
  filterItems,
  groupByDay,
  safeHref,
} from './group';

const item = (id: string, createdAt: Date): AppNotification => ({
  id,
  kind: 'reply',
  href: '/inbox',
  data: {},
  read: false,
  createdAt: createdAt.toISOString(),
  browser: false,
});

describe('groupByDay', () => {
  const now = new Date(2026, 9, 2, 10, 0); // local time, like the panel

  it('groups newest first by local day', () => {
    const groups = groupByDay(
      [
        item('old', new Date(2026, 8, 28, 9, 0)),
        item('today-early', new Date(2026, 9, 2, 0, 5)),
        item('yesterday-late', new Date(2026, 9, 1, 23, 55)),
        item('today-late', new Date(2026, 9, 2, 9, 30)),
        item('old-2', new Date(2026, 8, 28, 18, 0)),
      ],
      now,
    );
    expect(groups.map((g) => [g.day, g.items.map((i) => i.id)])).toEqual([
      ['today', ['today-late', 'today-early']],
      ['yesterday', ['yesterday-late']],
      ['2026-09-28', ['old-2', 'old']],
    ]);
  });

  it('knows yesterday across a month boundary', () => {
    const groups = groupByDay([item('a', new Date(2026, 8, 30, 12, 0))], new Date(2026, 9, 1, 8));
    expect(groups[0]?.day).toBe('yesterday');
  });

  it('returns nothing for no items', () => {
    expect(groupByDay([], now)).toEqual([]);
  });
});

const make = (
  kind: AppNotification['kind'],
  over: Partial<AppNotification> = {},
): AppNotification => ({
  id: `${kind}-${Math.random()}`,
  kind,
  href: '/inbox?m=m1',
  data: {},
  read: false,
  createdAt: new Date().toISOString(),
  browser: false,
  ...over,
});

describe('filters', () => {
  const feed = [
    make('reply'),
    make('reply_urgent', { read: true }),
    make('approval_needed'),
    make('agent_question', { read: true }),
    make('search_done'),
    make('payment_failed'),
  ];
  const kinds = (f: Parameters<typeof filterItems>[1]) => filterItems(feed, f).map((n) => n.kind);

  it('keeps each group, read or not', () => {
    expect(kinds('all')).toHaveLength(6);
    expect(kinds('reply')).toEqual(['reply', 'reply_urgent']);
    expect(kinds('decide')).toEqual(['approval_needed', 'agent_question']);
  });

  it('unread drops what was read', () => {
    expect(kinds('unread')).toEqual(['reply', 'approval_needed', 'search_done', 'payment_failed']);
  });

  it('counts unread items per group and takes the unread total from the server', () => {
    expect(filterCounts(feed, 57)).toEqual({ all: 0, unread: 57, reply: 1, decide: 1 });
  });
});

describe('actions', () => {
  const labels = (n: AppNotification) =>
    ACTIONS[n.kind](n).map((a) => [a.label, a.href, a.primary]);

  it('a reply answers in the draft and opens the thread', () => {
    expect(labels(make('reply'))).toEqual([
      ['reply', '/inbox?m=m1#reply', true],
      ['open', '/inbox?m=m1', false],
    ]);
    expect(labels(make('reply_urgent', { data: { label: 'interested' } }))).toHaveLength(2);
  });

  it('a meeting request accepts in the same draft instead of replying', () => {
    expect(labels(make('reply_urgent', { data: { label: 'meeting_request' } }))).toEqual([
      ['accept', '/inbox?m=m1#reply', true],
    ]);
  });

  it('approvals are reviewed, a stopped search is retried from its chat, the rest has none', () => {
    expect(labels(make('approval_needed', { href: '/campaigns/c1' }))).toEqual([
      ['review', '/campaigns/c1', true],
    ]);
    expect(labels(make('search_failed', { href: '/?s=s1' }))).toEqual([['retry', '/?s=s1', false]]);
    for (const kind of [
      'search_done',
      'agent_question',
      'low_credits',
      'payment_failed',
    ] as const) {
      expect(labels(make(kind))).toEqual([]);
    }
  });
});

describe('channelOf', () => {
  it('shows the meeting badge for a meeting request, else the channel it came on', () => {
    expect(
      channelOf(make('reply_urgent', { data: { label: 'meeting_request', channel: 'email' } })),
    ).toBe('meeting');
    expect(
      channelOf(
        make('reply', { data: { label: 'meeting_request', channel: 'email', unverified: true } }),
      ),
    ).toBe('email');
    expect(channelOf(make('reply', { data: { channel: 'linkedin' } }))).toBe('linkedin');
    expect(channelOf(make('reply', { data: { channel: 'sms' } }))).toBeNull();
    expect(channelOf(make('reply'))).toBeNull();
  });
});

describe('draft actions behind /switch', () => {
  it('keep #reply inside next, so the redirect lands on it', () => {
    const href = '/switch?org=o&next=%2Finbox%3Fm%3Dm1';
    const reply = ACTIONS.reply?.(make('reply', { href }))[0];
    expect(reply?.href).toBe(`${href}%23reply`);
    expect(new URL(safeHref(reply?.href ?? ''), 'http://x').searchParams.get('next')).toBe(
      '/inbox?m=m1#reply',
    );
  });
});

describe('safeHref', () => {
  it('keeps in-app paths only', () => {
    expect(safeHref('/inbox?m=1')).toBe('/inbox?m=1');
    expect(safeHref('//evil.test')).toBe('/');
    expect(safeHref('/.//evil.test')).toBe('/');
    expect(safeHref('https://app.zenbree.invalid//evil.test')).toBe('/');
    expect(safeHref('javascript:alert(1)')).toBe('/');
    expect(safeHref('/\\evil.test')).toBe('/');
    expect(safeHref('https://evil.test')).toBe('/');
    expect(safeHref('/\t/evil.test/x')).toBe('/');
    expect(safeHref('/\n/evil.test')).toBe('/');
    expect(safeHref('/\r\\evil.test')).toBe('/');
  });
});
