// @vitest-environment happy-dom
import { describe, expect, it } from 'vite-plus/test';
import { render } from '@/app/auth/testing';
import type { AppErrorJSON } from '@/lib/types';
import { BuilderError } from './builder-parts';

const chips = [
  { token: 'firstName', label: 'Nome' },
  { token: 'recentNews', label: 'Notizie recenti' },
];
const nobody = (data?: Record<string, unknown>): AppErrorJSON => ({
  name: 'AppError',
  code: 'CAMPAIGN.NO_TARGETS',
  message: 'this campaign has nobody to contact',
  status: 422,
  ...(data && { data }),
});

describe('BuilderError', () => {
  it('says how many people miss a value and for which fields when nobody can start', async () => {
    const view = await render(
      <BuilderError
        error={nobody({ waiting: 2, missing: ['recentNews', 'pain'] })}
        chips={chips}
      />,
    );
    expect(view.textContent).toBe(
      'Questa campagna non ha nessuno da contattare. 2 persone non hanno un valore per Notizie recenti, {{pain}}. Aggiungi i valori nella lista, o dai a ciascuno di questi campi un testo di riserva.',
    );
  });

  it('names the LinkedIn order rule and the step that breaks it', async () => {
    const error: AppErrorJSON = {
      name: 'AppError',
      code: 'CAMPAIGN.LINKEDIN_NO_INVITE',
      message: 'step 2: A LinkedIn message requires an earlier LinkedIn invitation',
      status: 422,
      data: { step: 2 },
    };
    const view = await render(<BuilderError error={error} chips={chips} />);
    expect(view.textContent).toBe(
      "Step 2: Questo messaggio ha bisogno di un Invito LinkedIn in uno step precedente: su LinkedIn puoi scrivere solo a chi ha accettato l'invito.",
    );
  });
});
