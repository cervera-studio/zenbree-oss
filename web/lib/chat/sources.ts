import type { Source, SourceKind } from './types';

/**
 * Pure transforms behind the message-level Sources panel .
 *
 * The BE will eventually hand the FE a flat list of consulted sources, with
 * the same URL possibly repeated (one entry per tool call that touched it).
 * The panel needs three derived shapes from that list:
 *
 *   1. de-duped rows (by URL) with an occurrence `count`
 *   2. a cited / all split (cited = has inline `[n]` markers)
 *   3. an "all consulted" view grouped by domain
 *
 * Keeping these as side-effect-free functions makes them unit-testable
 * without mounting the React tree — see `sources.test.ts`.
 */

const SOURCE_KINDS = new Set<SourceKind>(['web', 'scrape', 'research', 'social']);

/**
 * Coerce the BE `sources` payload into the strict `Source[]` DTO
 * . Both transports hand us camelCased keys already — the SSE
 * pipeline runs `toCamelCase` before dispatch, the GET projection runs through
 * httpClient — so `cited_indices` / `tool_id` arrive as `citedIndices` /
 * `toolId`. The single wire→DTO rename this function owns is `kind` →
 * `sourceKind`.
 *
 * Entries missing `url` / `domain` or carrying an unknown `kind` are dropped
 * (defensive against backend version drift, mirrors `parseCTAOptions`). A
 * missing / blank `title` is NOT a drop — it falls back to `domain`, then
 * `url`, so a titleless source still renders. Returns `undefined` for a
 * missing or fully-invalid list
 * so callers can leave the message's `sources` unset — no panel button, no
 * crash (graceful degrade when the BE hasn't shipped the contract yet).
 */
// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: pre-existing, counted by the complexity ratchet (#907)
export function parseSources(raw: unknown): Source[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: Source[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const { url, title, domain, kind } = rec;
    if (
      typeof url !== 'string' ||
      url.trim() === '' ||
      typeof domain !== 'string' ||
      domain.trim() === '' ||
      typeof kind !== 'string' ||
      !SOURCE_KINDS.has(kind as SourceKind)
    ) {
      continue;
    }
    // Title is best-effort: fall back domain → url so a titleless source still
    // renders instead of vanishing from the panel.
    const resolvedTitle =
      typeof title === 'string' && title.trim() !== ''
        ? title
        : domain.trim() !== ''
          ? domain
          : url;
    const citedIndices = Array.isArray(rec.citedIndices)
      ? rec.citedIndices.filter((n): n is number => typeof n === 'number')
      : [];
    const source: Source = {
      url,
      title: resolvedTitle,
      domain,
      sourceKind: kind as SourceKind,
      citedIndices,
    };
    if (typeof rec.favicon === 'string' && rec.favicon.trim() !== '') source.favicon = rec.favicon;
    if (typeof rec.toolId === 'string' && rec.toolId.trim() !== '') source.toolId = rec.toolId;
    // `snippet` is single-word (no casing change); `published_at` arrives as
    // `publishedAt` because the SSE pipeline / httpClient camelCase the wire
    // payload upstream, exactly like `citedIndices` / `toolId` above.
    if (typeof rec.snippet === 'string' && rec.snippet.trim() !== '') source.snippet = rec.snippet;
    if (typeof rec.publishedAt === 'string' && rec.publishedAt.trim() !== '')
      source.publishedAt = rec.publishedAt;
    out.push(source);
  }
  return out.length ? out : undefined;
}

/** De-duped source: a unique URL plus how many raw occurrences collapsed
 * into it and the union of every `[n]` marker it backs. */
export interface DedupedSource extends Source {
  /** Number of raw occurrences of this URL in the original list. */
  count: number;
}

/**
 * Normalize a URL into a stable dedupe key. Lower-cases the host, drops a
 * trailing slash on the path, and ignores the hash so `example.com/a` and
 * `example.com/a#x` collapse. Falsy / unparseable URLs key on themselves so
 * we never throw on malformed mock data.
 */
function sourceKey(url: string): string {
  try {
    const u = new URL(url);
    const path =
      u.pathname.endsWith('/') && u.pathname !== '/' ? u.pathname.slice(0, -1) : u.pathname;
    return `${u.protocol}//${u.host.toLowerCase()}${path}${u.search}`;
  } catch {
    return url.trim().toLowerCase();
  }
}

function sortedUniqueIndices(indices: number[]): number[] {
  return [...new Set(indices)].sort((a, b) => a - b);
}

/**
 * Collapse a raw source list to one row per URL. The first occurrence wins
 * for display fields, except an empty title is upgraded by a later
 * occurrence that has one. `citedIndices` is the union across occurrences;
 * `count` is the raw occurrence tally that drives the `N×` badge.
 */
export function dedupeSources(sources: Source[]): DedupedSource[] {
  const byKey = new Map<string, DedupedSource>();
  const order: string[] = [];

  for (const source of sources) {
    const key = sourceKey(source.url);
    const existing = byKey.get(key);
    if (existing) {
      existing.count += 1;
      existing.citedIndices = sortedUniqueIndices([
        ...existing.citedIndices,
        ...source.citedIndices,
      ]);
      if (!existing.title && source.title) existing.title = source.title;
      if (!existing.favicon && source.favicon) existing.favicon = source.favicon;
      if (!existing.snippet && source.snippet) existing.snippet = source.snippet;
      if (!existing.publishedAt && source.publishedAt) existing.publishedAt = source.publishedAt;
    } else {
      byKey.set(key, {
        ...source,
        citedIndices: sortedUniqueIndices(source.citedIndices),
        count: 1,
      });
      order.push(key);
    }
  }

  // biome-ignore lint/style/noNonNullAssertion: keys come from the same map we just filled
  return order.map((key) => byKey.get(key)!);
}

/**
 * Resolve a favicon URL for a source: an explicit favicon wins, otherwise we
 * derive one from the domain via Google's s2 service. The component falls
 * back to a globe glyph if the image 404s.
 */
export function faviconUrl(source: Source): string {
  if (source.favicon) return source.favicon;
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(source.domain)}&sz=64`;
}
