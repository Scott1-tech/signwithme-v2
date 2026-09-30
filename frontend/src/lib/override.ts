import type { ComparisonResult } from '../types/comparison';

/** Prefix the server uses in `review_override_fields` for a waived inconsistency. */
export const INCONSISTENCY_PREFIX = 'inconsistency:';

/** "Pages 1, 14" style suffix; empty when no page is known. */
export function pagesSuffix(pages: Array<number | null | undefined>): string {
  const unique = [...new Set(pages.filter((page): page is number => typeof page === 'number'))].sort(
    (a, b) => a - b,
  );
  if (unique.length === 0) return '';
  return ` (${unique.length === 1 ? 'page' : 'pages'} ${unique.join(', ')})`;
}

const ACRONYMS = new Set(['cdl', 'fmcsa', 'dot', 'mc', 'vin', 'ein', 'ssn', 'id']);

/** `cdl_mismatch` -> "CDL mismatch", `exp_date_conflict` -> "Exp date conflict". */
export function humanizeType(type: string): string {
  const words = type
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => (ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : word.toLowerCase()));
  if (words.length === 0) return type;
  const [first, ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ');
}

export interface OverriddenItem {
  id: string;
  kind: 'missing' | 'inconsistency';
  text: string;
}

/**
 * `review_override_fields` in words: a missing field by its label, an
 * `inconsistency:<type>` by the comparison's own `issue` text (with pages),
 * falling back to a readable form of the type when the result no longer has it.
 */
export function describeOverriddenFields(
  fields: readonly string[],
  result: ComparisonResult | null | undefined,
): OverriddenItem[] {
  return fields.map((id) => {
    if (id.startsWith(INCONSISTENCY_PREFIX)) {
      const type = id.slice(INCONSISTENCY_PREFIX.length);
      const match = result?.inconsistencies.find((issue) => issue.type === type);
      const text = match?.issue
        ? `${match.issue}${pagesSuffix(match.pages ?? [])}`
        : humanizeType(type);
      return { id, kind: 'inconsistency', text };
    }
    const missing = result?.missing_required.find((field) => field.field_id === id);
    return { id, kind: 'missing', text: missing?.label || id };
  });
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/** "2 required fields and 1 inconsistency", leaving out any zero part. */
export function describeOverrideCounts(missing: number, inconsistencies: number): string {
  const parts: string[] = [];
  if (missing > 0) parts.push(plural(missing, 'required field', 'required fields'));
  if (inconsistencies > 0) parts.push(plural(inconsistencies, 'inconsistency', 'inconsistencies'));
  return parts.join(' and ');
}
