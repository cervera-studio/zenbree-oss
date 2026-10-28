import type { UIToolCall } from './stream-store-types';
import {
  type AgentPlan,
  type AgentQuestion,
  FILTER_GROUPS,
  type FilterGroup,
  type Funnel,
  type MessagePart,
  parseFoundCount,
  type QuestionOption,
} from './types';

function parseGroup(raw: unknown): FilterGroup | undefined {
  return FILTER_GROUPS.find((g) => g === raw);
}

function parseOption(raw: unknown): QuestionOption | null {
  const obj = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : null;
  const label = (obj ? obj.label : raw) ?? '';
  if (typeof label !== 'string' || !label.trim()) return null;
  const description = typeof obj?.description === 'string' ? obj.description.trim() : '';
  const estimate = parseFoundCount(obj?.estimate);
  const drop = parseGroup(obj?.drop);
  return {
    label: label.trim(),
    description: description || null,
    ...(estimate && { estimate }),
    ...(drop && { drop }),
  };
}

function parseFunnel(raw: unknown): Funnel | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const { steps, excludedIndex: excluded, entity } = raw as Record<string, unknown>;
  if (!Array.isArray(steps) || typeof excluded !== 'number') return null;
  const parsed = steps.flatMap((s: unknown) => {
    const { group, values, count } = (typeof s === 'object' && s !== null ? s : {}) as Record<
      string,
      unknown
    >;
    const known = parseGroup(group);
    return known && Array.isArray(values) && typeof count === 'number'
      ? [{ group: known, values: values.map(String), count }]
      : [];
  });
  return parsed.length === steps.length && parsed[excluded]
    ? { steps: parsed, excludedIndex: excluded, entity: entity === 'person' ? 'person' : 'company' }
    : null;
}

export function parseQuestion(raw: unknown): AgentQuestion | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const { question, options, plan, funnel } = raw as Record<string, unknown>;
  const parsedFunnel = parseFunnel(funnel);
  const text = typeof question === 'string' ? question : '';
  const hasPlan = typeof plan === 'object' && plan !== null;
  if (!text && !hasPlan) return null;
  return {
    question: text,
    options: Array.isArray(options) ? options.flatMap((o) => parseOption(o) ?? []) : [],
    ...(hasPlan && { plan: plan as AgentPlan }),
    ...(parsedFunnel && { funnel: parsedFunnel }),
  };
}

export function dropTrailingQuestionCopy(
  parts: MessagePart[],
  question: string | undefined,
): MessagePart[] {
  const asked = question?.trim();
  const at = parts.findLastIndex((p) => p.kind === 'text');
  const part = parts[at];
  if (!asked || part?.kind !== 'text' || !part.content.trimEnd().endsWith(asked)) return parts;
  const content = part.content.trimEnd().slice(0, -asked.length).trimEnd();
  return content
    ? parts.map((p, i) => (i === at ? { ...part, content } : p))
    : parts.filter((_, i) => i !== at);
}

export function memorySaved(calls: readonly UIToolCall[]): string[] {
  return calls.flatMap((c) => c.memorySaved ?? []);
}

export function parseMemorySaved(raw: unknown): string | undefined {
  return typeof raw === 'string' && raw ? raw : undefined;
}
