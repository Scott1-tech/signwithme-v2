/**
 * Wrong-file warnings. Both shapes are additive on the server: absent, `null`
 * or malformed values read as "nothing to say" -- never as an error.
 */

export interface FilledCount {
  filled: number;
  total: number;
}

/** `GET /api/templates[/{id}]`: how much of a template was already filled in. */
export interface PrefilledSummary {
  signatures: FilledCount;
  dates: FilledCount;
  warning: boolean;
}

/** `GET /api/contracts/{id}/compare`: how closely the contract file matches its template. */
export interface TemplateMatch {
  identical: boolean;
  page_similarity: number;
  image_match_ratio: number;
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const count = (value: unknown): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : 0;

const readCount = (value: unknown): FilledCount =>
  isObject(value) ? { filled: count(value.filled), total: count(value.total) } : { filled: 0, total: 0 };

/** The summary only when the server flagged it as a warning. */
export function readPrefilledWarning(raw: unknown): PrefilledSummary | null {
  if (!isObject(raw) || raw.warning !== true) return null;
  return { signatures: readCount(raw.signatures), dates: readCount(raw.dates), warning: true };
}

/** The match only when the server says the contract is the template file itself. */
export function readIdenticalTemplate(raw: unknown): TemplateMatch | null {
  if (!isObject(raw) || raw.identical !== true) return null;
  return {
    identical: true,
    page_similarity: count(raw.page_similarity),
    image_match_ratio: count(raw.image_match_ratio),
  };
}

export const similarityPercent = (match: TemplateMatch) =>
  Math.round(Math.min(1, Math.max(0, match.page_similarity)) * 100);
