import type { MarkupElement } from '../api/document';
import { annotationsForRequest, sanitizeAnnotations, type Annotation } from './annotations';
import { fromServerSpace, toServerSpace } from './markupFrames';
import type { PageFrame } from './viewport';

/**
 * F1b (EDITOR-V2-PLAN §3 «Markup»): the annotation list <-> the server's
 * markup layer (`PUT/GET /api/contracts/{id}/document/markup`).
 *
 * The wire IS the finalize body's `AnnotationRequest` (contract v4-v6), byte
 * for byte, with ONE difference: the page is named by its stable `page_uid`
 * instead of its number (a `page` key is a 422 there). Nothing else is
 * renamed, added or dropped -- so what the server stores is exactly what the
 * finalize body would have carried, and `fromWire(toWire(x))` is `x`.
 *
 * `uidOf` / `pageOf` come from the current version's pages
 * (`GET …/versions/{no}/pages`). An element whose page has no uid (the map is
 * not loaded, or the page does not exist) is NOT silently dropped: `toWire`
 * reports it in `unplaced`, and the caller refuses to send a partial list.
 *
 * ROTFIX: `frameOf` (optional) says how each page is displayed now; a v7
 * element is then turned between the displayed page (the browser's) and the
 * engine's unrotated one (`lib/markupFrames.ts`). Without it, or on an upright
 * page, the wire is exactly what it was.
 */

export interface WireResult {
  elements: MarkupElement[];
  /** Ids whose page this document does not have (nothing is sent while any). */
  unplaced: string[];
}

export function toWire(
  items: readonly Annotation[],
  uidOf: (page: number) => string | null,
  frameOf?: (page: number) => PageFrame | null,
): WireResult {
  const elements: MarkupElement[] = [];
  const unplaced: string[] = [];
  annotationsForRequest(items).forEach((request) => {
    const { page, ...rest } = request as { page: number } & Record<string, unknown>;
    const uid = uidOf(page);
    if (!uid) {
      unplaced.push(String(rest.id));
      return;
    }
    const placed = toServerSpace(rest as { id: string; kind: string }, frameOf ? frameOf(page) : null);
    elements.push({ ...placed, page_uid: uid } as MarkupElement);
  });
  return { elements, unplaced };
}

export interface FromWireResult {
  items: Annotation[];
  /** Ids the server holds on a page this version does not have (kept on the server, not drawn). */
  orphaned: string[];
}

export function fromWire(
  elements: readonly MarkupElement[],
  pageOf: (uid: string) => number | null,
  frameOf?: (page: number) => PageFrame | null,
): FromWireResult {
  const raw: Record<string, unknown>[] = [];
  const orphaned: string[] = [];
  elements.forEach((element) => {
    const { page_uid: uid, ...rest } = element as MarkupElement;
    const page = typeof uid === 'string' ? pageOf(uid) : null;
    if (!page) {
      orphaned.push(String(rest.id));
      return;
    }
    const shown = fromServerSpace(rest as { id: string; kind: string }, frameOf ? frameOf(page) : null);
    raw.push({ ...shown, page });
  });
  return { items: sanitizeAnnotations(raw), orphaned };
}

/** JSON with object keys sorted at every level (a stable fingerprint). */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    const entries = Object.keys(value as Record<string, unknown>)
      .sort()
      .filter((key) => (value as Record<string, unknown>)[key] !== undefined)
      .map((key) => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`);
    return `{${entries.join(',')}}`;
  }
  return JSON.stringify(value);
}

/** Same list for the server (order-insensitive, by the wire form). */
export function sameWire(a: readonly MarkupElement[], b: readonly MarkupElement[]): boolean {
  if (a.length !== b.length) return false;
  const key = (list: readonly MarkupElement[]) => list.map((element) => canonical(element)).sort();
  const left = key(a);
  const right = key(b);
  return left.every((value, index) => value === right[index]);
}
