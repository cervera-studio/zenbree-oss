// @vitest-environment happy-dom
import { act, useState } from 'react';
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { BusinessProfile } from '@/lib/onboarding/schema';
import { ProfileStep } from './step-profile';

const start: BusinessProfile = {
  companyName: 'Cervera Studio',
  summary: '',
  personas: [],
  markets: ['Italy', 'Germany'],
  painPoints: [],
  solution: '',
  buyingTriggers: [],
  exclusions: [],
  proof: '',
};

let saved = start;

function Editor() {
  const [profile, setProfile] = useState(start);
  return (
    <ProfileStep
      profile={profile}
      busy={null}
      error={null}
      onChange={(p) => {
        saved = p;
        setProfile(p);
      }}
      onBack={() => {}}
      onConfirm={() => {}}
    />
  );
}

const market = (page: HTMLElement, n: number) =>
  page.querySelector<HTMLTextAreaElement>(`textarea[aria-label="Mercati, voce ${n}"]`);

async function type(box: HTMLTextAreaElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(box, text);
    box.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('ProfileStep markets (demo QA auth-3)', () => {
  it('shows the stored English names in the page language', async () => {
    const page = await render(<Editor />);
    expect(market(page, 1)?.value).toBe('Italia');
    expect(market(page, 2)?.value).toBe('Germania');
    expect(page.querySelector('button[aria-label="Rimuovi Italia"]')).not.toBeNull();
  });

  it('keeps what the user types on screen and stores it as typed', async () => {
    const page = await render(<Editor />);
    await type(market(page, 1) as HTMLTextAreaElement, 'Spain');
    expect(market(page, 1)?.value).toBe('Spain');
    expect(market(page, 2)?.value).toBe('Germania');
    expect(saved.markets).toEqual(['Spain', 'Germany']);
  });
});
