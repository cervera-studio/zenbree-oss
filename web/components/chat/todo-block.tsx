import { Suspense } from 'react';
import { getHomeTodo } from '@/lib/api/home';
import { TodoList } from './todo-list';

async function Todo() {
  const res = await getHomeTodo();
  return res.success ? <TodoList items={res.data.items} /> : null;
}

/** Server component: the home page passes it down as a slot, so a turn's `router.refresh()` renews it. */
export function TodoBlock() {
  return (
    <Suspense fallback={null}>
      <Todo />
    </Suspense>
  );
}
