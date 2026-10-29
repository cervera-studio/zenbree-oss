import type { ReactNode } from 'react';

// The recipient of someone else's campaign lands here (#172): the page speaks for the sender, so no
// Zenbree header, sign-in or call to action. The page itself carries the controller and privacy line.
export default function UnsubscribeLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-svh bg-side px-4 py-12 phone:py-4">
      <main className="mx-auto w-full max-w-130">{children}</main>
    </div>
  );
}
