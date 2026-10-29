import { describe, expect, it } from 'vite-plus/test';
import { blockers, rewriteHref } from './builder';
import type { CampaignSender } from './senders';
import { blankStep } from './steps';

const google: CampaignSender = {
  id: '6f1c2b0e-0000-4000-8000-000000000001',
  provider: 'google',
  accountLabel: 'me@acme.test',
  status: 'connected',
  picked: true,
  state: 'ready',
  reason: null,
  blocker: null,
  resumesAt: null,
  sentToday: 0,
  dailyCap: 25,
  warmingUp: true,
};
const paused: CampaignSender = { ...google, state: 'held', reason: 'paused', blocker: 'paused' };
const resting: CampaignSender = { ...google, state: 'held', reason: 'throttled' };
const email = [blankStep()];
const linkedIn = [blankStep(undefined, 'linkedin_connect')];

describe('blockers', () => {
  it('blocks a switched-on A/B test until some step has copy B (#934)', () => {
    const tested = [{ ...blankStep(), b: { subject: 'B', body: 'Copy B' } }];
    const blank = [{ ...blankStep(), b: { subject: '', body: '' } }];
    expect(blockers(email, [google], true, 3, true)).toEqual(['noCopyB']);
    expect(blockers(blank, [google], true, 3, true)).toEqual(['noCopyB']);
    expect(blockers(email, [google], true, 3, false)).toEqual([]);
    expect(blockers(tested, [google], true, 3, true)).toEqual([]);
    const threaded = [
      blankStep(),
      { ...blankStep(blankStep()), body: 'Again', b: { subject: 'Stale', body: '' } },
    ];
    expect(blockers(threaded, [google], true, 3, true)).toEqual(['noCopyB']);
  });

  it('blocks a campaign with nothing to send', () => {
    expect(blockers([], [google], true, 3)).toEqual(['noSteps']);
  });

  it('says there is no mailbox, or that none is picked, only for email steps', () => {
    expect(blockers(email, [], true, 3)).toEqual(['noMailbox']);
    expect(blockers(email, [{ ...google, status: 'error' }], true, 3)).toEqual(['noMailbox']);
    expect(blockers(email, [{ ...google, picked: false }], true, 3)).toEqual(['pickMailbox']);
    expect(blockers(email, [google], true, 3)).toEqual([]);
    expect(blockers(linkedIn, [], true, 3)).toEqual([]);
  });

  it('counts only a picked account that nothing blocks', () => {
    expect(blockers(email, [paused], true, 3)).toEqual(['pickMailbox']);
    expect(blockers(email, [paused, { ...google, picked: false }], true, 3)).toEqual([
      'pickMailbox',
    ]);
    expect(blockers(email, [resting], true, 3)).toEqual([]);
  });

  it('blocks a campaign with nobody to contact', () => {
    expect(blockers(email, [google], true, 0)).toEqual(['noTargets']);
  });

  it('needs LinkedIn for LinkedIn steps', () => {
    expect(blockers(linkedIn, [], false, 3)).toEqual(['noLinkedIn']);
  });

  it('leaves an unknown mailbox list to the API', () => {
    expect(blockers(email, null, true, 3)).toEqual([]);
  });
});

describe('rewriteHref', () => {
  it('opens the chat with the request prefilled and escaped', () => {
    const href = rewriteHref('s-1', 'Riscrivi «A&B» (campaign_id c1)');
    const url = new URL(href, 'https://app.test');
    expect(url.pathname).toBe('/');
    expect(url.searchParams.get('s')).toBe('s-1');
    expect(url.searchParams.get('draft')).toBe('Riscrivi «A&B» (campaign_id c1)');
  });
});
