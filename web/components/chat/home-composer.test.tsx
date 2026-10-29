// @vitest-environment happy-dom
import { createRef } from 'react';
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import { HomeComposer } from './home-composer';

const mount = (text: string) =>
  render(
    <HomeComposer
      text={text}
      onText={() => {}}
      composer={createRef<HTMLTextAreaElement>()}
      onSubmit={() => {}}
      disabled={false}
      placeholder="p"
    />,
  );

describe('HomeComposer focus (#872)', () => {
  it('takes the focus when it mounts with a draft, as the loaded screen does over the skeleton', async () => {
    const c = await mount('Trova le email mancanti');
    expect(document.activeElement).toBe(c.querySelector('textarea'));
  });

  it('leaves the focus alone when it mounts empty', async () => {
    await mount('');
    expect(document.activeElement).toBe(document.body);
  });
});
