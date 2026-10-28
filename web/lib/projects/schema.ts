import { z } from 'zod';

const projectSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  // #307: the optional monthly credit cap and this month's spend (`soft` only reports it).
  creditCap: z.number().nullish(),
  creditCapMode: z.enum(['hard', 'soft']).nullish(),
  creditsThisMonth: z.number().default(0),
  overCap: z.boolean().default(false),
});
export type Project = z.infer<typeof projectSchema>;

export const projectsSchema = z.looseObject({ projects: z.array(projectSchema) });
export const oneProjectSchema = z.looseObject({ project: projectSchema });
