import { describe, expect, it } from 'vite-plus/test';
import { ADMIN_NAV, isCurrent } from './nav-entries';

const current = (pathname: string) =>
  ADMIN_NAV.filter((e) => isCurrent(e, pathname)).map((e) => e.labelKey);

describe('isCurrent', () => {
  it('keeps Utenti lit on a user and on a chat session, and only there', () => {
    expect(current('/admin')).toEqual(['users']);
    expect(current('/admin/users/abc')).toEqual(['users']);
    expect(current('/admin/chat-sessions/s1')).toEqual(['users']);
  });

  it('lights one section for its own pages', () => {
    expect(current('/admin/jobs')).toEqual(['jobs']);
    expect(current('/admin/organizations/o1')).toEqual(['organizations']);
    expect(current('/admin/costs/x')).toEqual(['costs']);
  });
});
