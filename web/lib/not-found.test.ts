import { describe, expect, it } from 'vite-plus/test';
import { missingResource, otherDestinations } from './not-found';

describe('missingResource', () => {
  it('names the resource from the first path segment', () => {
    expect(missingResource('/lists/abc')).toEqual({ kind: 'list', href: '/lists' });
    expect(missingResource('/campaigns/abc/edit')).toEqual({
      kind: 'campaign',
      href: '/campaigns',
    });
    expect(missingResource('/chat/abc')).toEqual({ kind: 'chat', href: '/' });
  });

  it('falls back to a generic page for anything else', () => {
    expect(missingResource('/listsx')).toEqual({ kind: 'page', href: '/' });
    expect(missingResource('/admin/users/abc')).toEqual({ kind: 'page', href: '/' });
    expect(missingResource('/')).toEqual({ kind: 'page', href: '/' });
  });
});

describe('otherDestinations', () => {
  it('leaves out the primary destination and the new chat', () => {
    expect(otherDestinations('/lists').map((d) => d.kind)).toEqual(['campaign']);
    expect(otherDestinations('/campaigns').map((d) => d.kind)).toEqual(['list']);
  });

  it('lists the lists and campaigns when the primary is the home page', () => {
    expect(otherDestinations('/').map((d) => d.kind)).toEqual(['list', 'campaign']);
  });
});
