'use client';

import { Breadcrumb, type Crumb } from '@/components/ui/breadcrumb';
import { useCurrentProjectId } from '@/lib/projects';
import { useLoadedProjects } from '@/lib/projects/load';
import { useProjectsViewer } from './projects-viewer';

/** The path of a top-level page, after the active client's name. The name follows a switch at once;
 *  until the sidebar's switcher has the list, the path starts at the page. */
export function ProjectBreadcrumb({ crumbs, label }: { crumbs: readonly Crumb[]; label: string }) {
  const id = useCurrentProjectId();
  const project = useLoadedProjects(useProjectsViewer())?.find((p) => p.id === id);
  return <Breadcrumb crumbs={crumbs} label={label} project={project?.name} />;
}
