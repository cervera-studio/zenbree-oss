'use client';

import { createContext, type PropsWithChildren, useContext } from 'react';

const Ctx = createContext<string | null>(null);

/** The signed-in user, for components that read the client list without loading it (the page
 *  crumb): sign-out, sign-in and impersonation keep the tab, and the list must match its user. */
export function ProjectsViewer({ userId, children }: PropsWithChildren<{ userId: string }>) {
  return <Ctx.Provider value={userId}>{children}</Ctx.Provider>;
}

export const useProjectsViewer = () => useContext(Ctx);
