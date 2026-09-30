/**
 * Server timestamps, read as what they are: UTC.
 *
 * Every `*_at` / `timestamp` the API sends is a UTC instant. The backend now
 * says so (`...Z` / `+00:00`, `models.types.UTCDateTime`), but a server that
 * has not been restarted yet -- or any older payload -- still sends the SQLite
 * shape without an offset (`2026-09-24T22:15:28`, even `2026-09-24 22:15:28`).
 * `new Date()` reads an offset-less date-time as LOCAL time, which put the
 * audit trail five hours early in Tashkent (UTC+5). So every place that shows
 * or compares a server time goes through here, and an offset-less date-time
 * is taken as UTC. A value that already carries its offset is left alone.
 */

const HAS_OFFSET = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** The instant a server timestamp names, or `null` if it is not one. */
export function parseServerTime(value: string | null | undefined): Date | null {
  if (!value) return null;
  let text = value.trim();
  // A bare calendar date is already read as UTC midnight by the spec.
  if (!DATE_ONLY.test(text)) {
    text = text.replace(' ', 'T');
    if (!HAS_OFFSET.test(text)) text = `${text}Z`;
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** Milliseconds since the epoch; `NaN` for a missing or unreadable value. */
export function serverTimeMs(value: string | null | undefined): number {
  return parseServerTime(value)?.getTime() ?? Number.NaN;
}

/** Local date and time, for a table cell or a title. */
export function formatServerDateTime(value: string | null | undefined): string {
  const parsed = parseServerTime(value);
  return parsed ? parsed.toLocaleString() : (value ?? '');
}

/** Local calendar date only. */
export function formatServerDate(value: string | null | undefined): string {
  const parsed = parseServerTime(value);
  return parsed ? parsed.toLocaleDateString() : (value ?? '');
}
