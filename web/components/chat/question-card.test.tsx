import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { TestIntl } from '@/app/auth/testing';
import { QuestionCard } from './question-card';

const question = {
  question: "L'email è obbligatoria?",
  options: [
    { label: 'Sì', description: 'Solo email verificate.' },
    { label: 'No', description: null },
  ],
};

function render(props: Partial<Parameters<typeof QuestionCard>[0]>) {
  return renderToStaticMarkup(
    <TestIntl>
      <QuestionCard question={question} {...props} />
    </TestIntl>,
  );
}

describe('QuestionCard', () => {
  it('numbers the options, shows their descriptions and offers Salta, then is only its title once answered', () => {
    const html = render({ onAnswer: () => {} });
    expect(html).toContain('Domanda');
    expect(html).toContain('>1<');
    expect(html).toContain('Solo email verificate.');
    expect(html).toContain('Salta');

    const answered = render({});
    expect(answered).toContain('L&#x27;email è obbligatoria?');
    expect(answered).not.toContain('Salta');
    expect(answered).not.toContain('Solo email verificate.');
  });
});
