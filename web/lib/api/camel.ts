type Digit = '0' | '1' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9';
type Joined<First extends string> = First extends Digit
  ? First
  : First extends Lowercase<First>
    ? Uppercase<First> extends First
      ? never
      : Uppercase<First>
    : never;

type CamelKey<S extends string> = S extends `${infer Head}_${infer First}${infer Rest}`
  ? [Joined<First>] extends [never]
    ? `${Head}_${CamelKey<`${First}${Rest}`>}`
    : `${Head}${Joined<First>}${CamelKey<Rest>}`
  : S;

/**
 * The type `toCamelCase` gives a response model: keys in camelCase, deep.
 * It matches `snakeToCamel` in `case-transform.ts` (`_` followed by `[a-z0-9]`).
 */
export type Camel<T> = T extends readonly (infer U)[]
  ? Camel<U>[]
  : T extends object
    ? { [K in keyof T as K extends string ? CamelKey<K> : K]: Camel<T[K]> }
    : T;
