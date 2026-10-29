import { describe, expect, it } from 'vite-plus/test';
import { dropTrailingQuestionCopy, parseQuestion } from './question';

describe('parseQuestion', () => {
  it('reads rows saved before option descriptions, with plain string options', () => {
    expect(parseQuestion({ question: 'Which?', options: ['Italy', ' ', 'Spain'] })).toEqual({
      question: 'Which?',
      options: [
        { label: 'Italy', description: null },
        { label: 'Spain', description: null },
      ],
    });
  });

  it('reads described options and keeps a plan', () => {
    const plan = { listId: 'L' };
    expect(
      parseQuestion({
        question: '',
        options: [{ label: 'Yes', description: ' Verified only ' }],
        plan,
      }),
    ).toEqual({
      question: '',
      options: [{ label: 'Yes', description: 'Verified only' }],
      plan,
    });
  });

  it('reads the funnel and the estimate and group of each option', () => {
    const steps = [
      { group: 'where', values: ['Piemonte'], count: 64 },
      { group: 'signal', values: ['AE'], count: 0 },
    ];
    expect(
      parseQuestion({
        question: 'q',
        options: [
          {
            label: 'Senza',
            drop: 'signal',
            filters: { x: 1 },
            estimate: { count: 7, noun: 'company' },
          },
        ],
        funnel: { steps, excludedIndex: 1 },
      }),
    ).toEqual({
      question: 'q',
      options: [
        {
          label: 'Senza',
          description: null,
          drop: 'signal',
          estimate: { count: 7, noun: 'company' },
        },
      ],
      funnel: { steps, excludedIndex: 1, entity: 'company' },
    });
  });

  it('reads a people funnel; one saved before #260 is a company one', () => {
    const steps = [{ group: 'who', values: ['CEO'], count: 0 }];
    expect(
      parseQuestion({ question: 'q', funnel: { steps, excludedIndex: 0, entity: 'person' } })
        ?.funnel,
    ).toEqual({ steps, excludedIndex: 0, entity: 'person' });
    expect(
      parseQuestion({ question: 'q', funnel: { steps, excludedIndex: 0 } })?.funnel?.entity,
    ).toBe('company');
  });

  it('drops a funnel whose excluded step or group does not exist', () => {
    const step = { group: 'where', values: [], count: 1 };
    expect(
      parseQuestion({ question: 'q', funnel: { steps: [step], excludedIndex: 3 } }),
    ).not.toHaveProperty('funnel');
    const odd = { steps: [{ ...step, group: 'nope' }], excludedIndex: 0 };
    expect(parseQuestion({ question: 'q', funnel: odd })).not.toHaveProperty('funnel');
  });

  it('is null for anything that is not a question', () => {
    expect(parseQuestion(null)).toBeNull();
    expect(parseQuestion('q')).toBeNull();
    expect(parseQuestion({})).toBeNull();
    expect(parseQuestion({ question: '', options: [] })).toBeNull();
  });
});

describe('dropTrailingQuestionCopy', () => {
  const text = (content: string) => [{ kind: 'text' as const, id: 'a', content }];

  it('drops the question the loop appended to the turn text, also before the tools of a reloaded turn', () => {
    expect(dropTrailingQuestionCopy(text('Found 31.\n\nEmail needed?'), 'Email needed?')).toEqual(
      text('Found 31.'),
    );
    const tool = { kind: 'tool' as const, id: 't', toolCallId: 'c' };
    expect(dropTrailingQuestionCopy([...text('Found.\n\nQ?'), tool], 'Q?')).toEqual([
      ...text('Found.'),
      tool,
    ]);
  });

  it('drops a text part that was only the question', () => {
    expect(dropTrailingQuestionCopy(text('Email needed?'), 'Email needed?')).toEqual([]);
  });

  it('leaves text that does not end with the question', () => {
    expect(dropTrailingQuestionCopy(text('Found 31.'), 'Email needed?')).toEqual(text('Found 31.'));
    expect(dropTrailingQuestionCopy(text('Found 31.'), undefined)).toEqual(text('Found 31.'));
  });
});
