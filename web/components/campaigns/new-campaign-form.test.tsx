// @vitest-environment happy-dom
import { act } from 'react';
import { describe, expect, it, vi } from 'vite-plus/test';
import { control, field, render } from '@/app/auth/testing';
import { NewCampaignForm } from './new-campaign-form';

vi.mock('@/app/(app)/campaigns/actions', () => ({ createCampaignAction: vi.fn() }));

const NEWEST = '1c6a9b52-7d0e-4f3a-9b8c-2e5d4f6a7b81';
const OLDER = '3e8c1d74-9f2a-4b5c-9dae-4a7f6b8c9da3';
const DELETED = '5a0e3f96-b14c-4d7e-9fc0-6c9b8daebfc5';
const LISTS = [
  { id: NEWEST, name: 'Newest people' },
  { id: OLDER, name: 'Older people' },
];
const NOTE = 'La lista del link non è disponibile. Scegli una lista.';

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function openNamed(defaultListId?: string) {
  const container = await render(<NewCampaignForm lists={LISTS} defaultListId={defaultListId} />);
  await type(field(container, 'Nome della campagna'), 'Spring outreach');
  return {
    list: container.querySelector('select')?.value,
    text: container.textContent ?? '',
    create: control(container, 'Crea bozza') as HTMLButtonElement,
  };
}

describe('NewCampaignForm', () => {
  it('selects no list, says so, and cannot create, when the requested list is not offered', async () => {
    const form = await openNamed(DELETED);
    expect(form.list).toBe('');
    expect(form.text).toContain(NOTE);
    expect(form.create.disabled).toBe(true);
  });

  it('preselects the requested list when it is offered', async () => {
    const form = await openNamed(OLDER);
    expect(form.list).toBe(OLDER);
    expect(form.text).not.toContain(NOTE);
    expect(form.create.disabled).toBe(false);
  });

  it('starts on the newest list when no list is requested', async () => {
    const form = await openNamed();
    expect(form.list).toBe(NEWEST);
    expect(form.text).not.toContain(NOTE);
    expect(form.create.disabled).toBe(false);
  });
});
