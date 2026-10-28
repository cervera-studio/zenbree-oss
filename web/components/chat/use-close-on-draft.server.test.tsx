// @vitest-environment node
import { renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vite-plus/test';
import { useCloseOnDraft } from './use-close-on-draft';

vi.mock('next/navigation', () => ({
  useSearchParams: () =>
    new URLSearchParams({ s: 'c1', draft: 'Trova di nuovo l’email della riga r1.' }),
}));

function Probe({ onClose }: { onClose: () => void }) {
  useCloseOnDraft(onClose);
  return <p>ok</p>;
}

describe('useCloseOnDraft on the server', () => {
  it('renders a full page load with a draft without measuring the screen or closing the panel', () => {
    const onClose = vi.fn();
    expect(renderToString(<Probe onClose={onClose} />)).toContain('ok');
    expect(onClose).not.toHaveBeenCalled();
  });
});
