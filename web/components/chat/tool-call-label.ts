/**
 * Extract a short, human-readable label from a tool call's `input` JSON.
 * Used to distinguish N parallel calls of the same tool in the chat UI,
 * e.g. `social_search({query: "Gen Z tone of voice"})` gives "Gen Z tone of
 * voice".
 *
 * Returns a plain string; React escapes it on render. `null` means the input
 * carries nothing worth showing, so the caller falls back to the tool's own
 * translated label.
 *
 * Only the keys below are ever read. The previous version fell through to
 * "first string anywhere in the input", then to `JSON.stringify(input)`, which
 * turned an input of `{result_id: "call_00_Zk3891W1B1GHEKqReauT1523"}` into
 * that provider-internal id, printed verbatim in the user's transcript. An
 * allow-list is the only version of this that cannot leak a key we have not
 * looked at: a new tool with an opaque id field is unlabelled, not exposed.
 *
 * Every entry is declared by at least one tool in the BE registry
 * (`ToolDef.input_schema.properties`). Ordered by how well the key identifies
 * a call to a human, not by how many tools declare it. Deliberately absent:
 *
 *   - `*_id` of any kind, which is what this module exists to stop printing.
 *   - `email`. `verify_email` and `enrich_email` take a third party's address,
 *     and putting it in a transcript row is a PII surface a privacy fix should
 *     not open. The tool's own label already names the action.
 *   - `format`, `platform`, `new_status` and friends, which repeat across
 *     parallel calls and so distinguish nothing.
 *   - the body being written: `content`, `new_content`, `new_paragraph`, `doc`.
 *     `append_table` requires `content` and declares no other key here, so
 *     including it turned the row into raw markdown and made the tool's own
 *     label unreachable. It costs `remember` its subject, which is the price.
 *   - `file_path`, an internal R2 storage key on `ai_scrape_document` despite
 *     the name, and the same class of string as the ids above.
 */
const LABEL_KEYS = [
  'query',
  'search_term',
  'q',
  'hashtag',
  'role_keyword',
  'titles',
  'job_titles',
  'industries',
  'keyword_tags',
  'prompt',
  'title',
  'subject',
  'company_name',
  'organization_name',
  'brand',
  'name',
  'identifier',
  'topic',
  'channel_identifier',
  'username',
  'domain',
  'url',
  'post_url',
  'ad_url',
  'careers_url',
  'board_url',
  'origin',
  'dest',
  'locations',
  // Above `location` on purpose: `search_jobs_broad` pairs a role family with
  // ONE shared location, so the family is what varies across parallel calls.
  'role_family',
  'location',
  'provincia',
  'filename',
] as const;

const MAX_LEN = 60;

export function toolCallLabel(input: Record<string, unknown> | undefined): string | null {
  if (!input || typeof input !== 'object') return null;

  for (const key of LABEL_KEYS) {
    const value = readable(input[key]);
    if (value) return truncate(value);
  }

  return null;
}

/**
 * A key's value is showable when it survives cleaning as a non-empty string,
 * or as a list of them: `discover_companies` and `people_directory_search`
 * describe themselves entirely through `string[]` fields (`locations`,
 * `job_titles`, `industries`), so a string-only guard leaves their parallel
 * calls indistinguishable.
 *
 * Cleaning happens per element, before the join. Cleaning the joined string
 * instead lets a list of unprintables collapse to a row of bare separators.
 */
function readable(value: unknown): string | null {
  if (typeof value === 'string') return clean(value) || null;
  if (Array.isArray(value)) {
    const parts = value
      .filter((v): v is string => typeof v === 'string')
      .map(clean)
      .filter(Boolean);
    return parts.length ? parts.join(', ') : null;
  }
  return null;
}

// biome-ignore lint/suspicious/noControlCharactersInRegex: stripping control chars from AI-supplied URLs/queries is the point
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/g;

function clean(s: string): string {
  return s.replace(CONTROL_CHARS, '').replace(/\s+/g, ' ').trim();
}

/** Measure and slice in the same unit, code points, so a surrogate pair is
 * neither split by the slice nor counted twice by the guard. */
function truncate(s: string): string {
  const points = [...s];
  if (points.length <= MAX_LEN) return s;
  return `${points.slice(0, MAX_LEN - 1).join('')}…`;
}
