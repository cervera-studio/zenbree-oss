// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { ReminderSet, SequenceStopped, UnverifiedMarker, UnverifiedNotice } from './thread-notices';

const NOTICE = 'Non siamo riusciti a verificare il mittente';
const MARKER = 'Non verificato';

describe('unverified sender', () => {
  it('marks the list row and warns in the thread when the DMARC check failed', async () => {
    const row = await render(<UnverifiedMarker authenticated={false} />);
    expect(row.textContent).toBe(MARKER);
    const thread = await render(<UnverifiedNotice authenticated={false} />);
    expect(thread.querySelector('[role="status"]')?.textContent).toContain(NOTICE);
    expect(thread.textContent).toContain('link');
  });

  it.each([true, null])('shows nothing for %s', async (authenticated) => {
    const row = await render(<UnverifiedMarker authenticated={authenticated} />);
    expect(row.textContent).toBe('');
    const thread = await render(<UnverifiedNotice authenticated={authenticated} />);
    expect(thread.textContent).toBe('');
  });
});

describe('sequence stopped notice', () => {
  const STOPPED = 'La sequenza per Marco si è fermata quando ha risposto.';
  const show = (kind: 'reply' | 'auto_reply', targetId: string | null) =>
    render(<SequenceStopped message={{ kind, targetId }} name="Marco" />);

  it('says the sequence stopped when the reply has a target', async () => {
    expect((await show('reply', 't1')).textContent).toBe(STOPPED);
  });

  it('says nothing for a colleague reply filed under a campaign with no target', async () => {
    expect((await show('reply', null)).textContent).toBe('');
  });

  it('says nothing for an auto-reply', async () => {
    expect((await show('auto_reply', 't1')).textContent).toBe('');
  });
});

describe('reminder notice', () => {
  const show = (hasTarget: boolean) =>
    render(<ReminderSet hasTarget={hasTarget} name="Marco" remindOn="2099-01-15" />);

  it('adds the stopped sequence when the reply has a target', async () => {
    expect((await show(true)).textContent).toBe(
      'Ho creato un promemoria per gennaio 2099. La sequenza per Marco si è fermata.',
    );
  });

  it('leaves the sequence out for a reply with no target', async () => {
    expect((await show(false)).textContent).toBe('Ho creato un promemoria per gennaio 2099.');
  });
});
