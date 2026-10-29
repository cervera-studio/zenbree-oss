// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { control, render } from '@/app/auth/testing';
import { blankStep, type Step } from '@/lib/campaigns/steps';
import { BuilderSteps } from './builder-steps';

const email = { ...blankStep(), subject: 'Ciao', body: 'Ciao {{firstName}}' };
const invite = blankStep(email, 'linkedin_connect');
const HINT = 'Per inviare un Messaggio LinkedIn, aggiungi prima un Invito LinkedIn.';

function steps(list: Step[]) {
  return render(
    <BuilderSteps
      steps={list}
      open={null}
      abTest={false}
      draft
      pending={false}
      chips={[]}
      catalog={null}
      snippetCampaignId={null}
      onToggle={() => {}}
      onChange={() => {}}
      onRemove={() => {}}
      onAdd={() => {}}
      onPick={() => ''}
      onTest={() => undefined}
    />,
  );
}

describe('BuilderSteps add buttons', () => {
  it('keeps a LinkedIn message out until the sequence has an invite, and says why', async () => {
    const view = await steps([email]);
    const message = control(view, 'Aggiungi step: Messaggio LinkedIn') as HTMLButtonElement;
    expect(message.disabled).toBe(true);
    expect(
      document.getElementById(message.getAttribute('aria-describedby') ?? '')?.textContent,
    ).toBe(HINT);
    expect((control(view, 'Aggiungi step: Invito LinkedIn') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('offers a LinkedIn message once an invite is in the sequence', async () => {
    const view = await steps([email, invite]);
    expect((control(view, 'Aggiungi step: Messaggio LinkedIn') as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(view.textContent).not.toContain(HINT);
  });
});
