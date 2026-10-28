import { forbidden, redirect } from 'next/navigation';
import type { ReactNode } from 'react';
import { AdminShell } from '@/components/admin/admin-shell';
import { getSession } from '@/lib/auth-token';
import { hasRole } from '@/lib/roles';

export default async function AdminLayout({ children }: { children: ReactNode }) {
  const session = await getSession();
  if (!session) redirect('/auth/sign-in');
  if (!hasRole(session.user.role, 'admin')) forbidden();

  return (
    <AdminShell
      viewer={{ name: session.user.name || session.user.email, email: session.user.email }}
    >
      {children}
    </AdminShell>
  );
}
