'use client';

import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { useSignOut } from './sign-out-button';

export function SwitchAccountButton({ children }: { children: ReactNode }) {
  const signOut = useSignOut();
  return (
    <Button variant="outline" className="font-semibold" onClick={signOut}>
      {children}
    </Button>
  );
}
