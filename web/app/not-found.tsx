import { NotFoundView } from '@/components/app/not-found-view';
import { Logo } from '@/components/logo';

export default function NotFound() {
  return (
    <div className="flex min-h-dvh flex-col">
      <Logo className="mx-10 my-7 self-start phone:mx-4" />
      <NotFoundView />
    </div>
  );
}
