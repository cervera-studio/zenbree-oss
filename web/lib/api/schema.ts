import type { components } from './schema.gen';

/** A response model of the API, by its name in `schema.gen.ts`. The keys keep the wire's snake_case. */
export type Schema<K extends keyof components['schemas']> = components['schemas'][K];
