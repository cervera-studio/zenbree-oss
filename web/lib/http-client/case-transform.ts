/**
 * Deep transform object keys between snake_case and camelCase.
 * Used by httpClient to convert backend responses (snake) to frontend DTOs (camel)
 * and frontend requests (camel) to backend format (snake).
 */

function snakeToCamel(str: string): string {
  return str.replace(/_([a-z0-9])/g, (_, char: string) => char.toUpperCase());
}

function camelToSnake(str: string): string {
  // Handle consecutive uppercase: apiURL → api_url, HTMLParser → html_parser
  return str
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase();
}

const DANGEROUS_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

function transformKeys(obj: unknown, transformFn: (key: string) => string): unknown {
  if (obj === null || obj === undefined) return obj;
  if (Array.isArray(obj)) return obj.map((item) => transformKeys(item, transformFn));
  if (typeof obj === 'object' && Object.getPrototypeOf(obj) === Object.prototype) {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (DANGEROUS_KEYS.has(key)) continue;
      result[transformFn(key)] = transformKeys(value, transformFn);
    }
    return result;
  }
  return obj;
}

/** Convert snake_case keys to camelCase (deep). Use on API responses. */
export function toCamelCase<T>(obj: unknown): T {
  return transformKeys(obj, snakeToCamel) as T;
}

/** Convert camelCase keys to snake_case (deep). Use on API request bodies. */
export function toSnakeCase(obj: unknown): unknown {
  return transformKeys(obj, camelToSnake);
}
