import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vite-plus/test';
import { FORMATS } from '@/i18n/config';
import type { TodoItem } from '@/lib/api/home';
import { MESSAGES } from '@/messages';
import { TodoList } from './todo-list';

const render = (items: TodoItem[]) =>
  renderToStaticMarkup(
    <NextIntlClientProvider locale="it" messages={MESSAGES.it} formats={FORMATS}>
      <TodoList items={items} />
    </NextIntlClientProvider>,
  );

describe('TodoList', () => {
  it('renders one linked row per item with its count, label and title, and nothing for no item', () => {
    const html = render([
      { kind: 'approvals', count: 12, title: 'Agenzie marketing Milano', href: '/campaigns/c1' },
      { kind: 'replies', count: 1234, title: null, href: '/inbox' },
      { kind: 'running', count: 1, title: 'Cerco CEO', href: '/?s=s1' },
    ]);
    expect(html).toContain('Da fare');
    expect(html).toContain('href="/campaigns/c1"');
    expect(html).toContain('Messaggi da approvare');
    expect(html).toContain('Agenzie marketing Milano');
    expect(html).toContain('1.234');
    expect(html).toContain('Risposte da leggere');
    expect(html).toContain('href="/?s=s1"');
    expect(html).toContain('Ricerca in corso');
    expect(render([])).toBe('');
  });
});
