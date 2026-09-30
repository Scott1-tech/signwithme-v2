import type { PageSizePt } from '../layout';
import {
  MIN_BOX_PT,
  clampBoxOn,
  finite,
  groupKey,
  keepBoxOn,
  tidy,
  type AnnotationCommon,
  type BoxPt,
  type SanitizeBase,
} from './shared';

/**
 * `whiteout` (contract v5): an OPAQUE WHITE box, `x_pt` / `y_pt` its top-left
 * corner, `w_pt` / `h_pt` ≥ 1 pt, the whole box on its page. No colour, no
 * stroke, no size, no font.
 *
 * Two ways to get one:
 *  * the Whiteout tool (W5, `workspace/tools/whiteout`): drag a box; it is
 *    free -- moved, resized by its eight handles, deleted, undone like a rect;
 *  * an "edit existing text" (WE): the whiteout over the edited line, in a
 *    `group_id` with the new text. That one is LOCKED where the line is (plan
 *    §7-6) and is never offered handles.
 *
 * WHAT IT IS NOT: a redaction. The server paints a white rectangle over the
 * page (under the signatures -- its z-order is whiteout → signatures → every
 * other annotation) and records WHERE in the audit, but the covered text
 * stays in the page's content stream: copy, search and text extraction still
 * find it. The UI says so (`WHITEOUT_NOTICE`).
 *
 * The dashed grey edge the workspace draws around a free whiteout is a
 * SCREEN-ONLY aid (white on white paper would otherwise be invisible to the
 * person who placed it): nothing of it is in the request, and the PDF gets
 * the plain white box.
 */

export interface WhiteoutAnnotation extends AnnotationCommon {
  kind: 'whiteout';
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
}

export interface WhiteoutRequest {
  id: string;
  kind: 'whiteout';
  page: number;
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
  group_id?: string;
}

/** What the tool's description and its one-time notice say, in one place. */
export const WHITEOUT_NOTICE =
  'Whiteout only hides what is underneath on the page — the original text stays inside the file ' +
  'and can reappear when the text is copied or searched.';

/** A whiteout the Whiteout tool drew (no group): free to move and resize. */
export const isFreeWhiteout = (item: { kind: string; group_id?: string }): boolean =>
  item.kind === 'whiteout' && !item.group_id;

/** A fresh whiteout, already clamped to its page (both corners, ≥ 1 pt). */
export function makeWhiteout(
  base: { id: string; page: number; x: number; y: number; w: number; h: number; group_id?: string },
  page: PageSizePt | null,
): WhiteoutAnnotation {
  const box = clampBoxOn(base, page);
  const made: WhiteoutAnnotation = {
    id: base.id,
    kind: 'whiteout',
    page: base.page,
    x_pt: box.x,
    y_pt: box.y,
    w_pt: box.w,
    h_pt: box.h,
  };
  if (base.group_id) made.group_id = base.group_id;
  return made;
}

/** A stored entry read back (`x_pt`, `y_pt` finite; `w_pt`, `h_pt` ≥ 1), or `null`. */
export function sanitizeWhiteout({ id, page, group_id, source }: SanitizeBase): WhiteoutAnnotation | null {
  const x = finite(source.x_pt);
  const y = finite(source.y_pt);
  const w = finite(source.w_pt);
  const h = finite(source.h_pt);
  if (x === null || y === null || w === null || h === null) return null;
  if (w < MIN_BOX_PT || h < MIN_BOX_PT) return null;
  return makeWhiteout({ id, page, x, y, w, h, group_id }, null);
}

/** `{id, kind, page, x_pt, y_pt, w_pt, h_pt, group_id?}` -- the v5 entry, key for key. */
export const whiteoutForRequest = (item: WhiteoutAnnotation): WhiteoutRequest => ({
  id: item.id,
  kind: 'whiteout',
  page: item.page,
  x_pt: tidy(item.x_pt),
  y_pt: tidy(item.y_pt),
  w_pt: tidy(item.w_pt),
  h_pt: tidy(item.h_pt),
  ...groupKey(item),
});

export const whiteoutBounds = (item: WhiteoutAnnotation): BoxPt => ({
  x: item.x_pt,
  y: item.y_pt,
  w: item.w_pt,
  h: item.h_pt,
});

/** The rest of what would make two whiteouts print differently (id, page, corner and group are compared by the caller). */
export const sameWhiteout = (a: WhiteoutAnnotation, b: WhiteoutAnnotation): boolean =>
  a.w_pt === b.w_pt && a.h_pt === b.h_pt;

/**
 * Pulled inside its page. A FREE whiteout keeps its size and moves (like a
 * rect: it covers what it was drawn over, just not past the edge); a grouped
 * one is clipped instead (it must stay over the line it hides -- only its
 * 0.5 pt margin can ever reach past an edge). Same object when nothing changed.
 */
export function clampWhiteout(item: WhiteoutAnnotation, page: PageSizePt | null): WhiteoutAnnotation {
  const current = { x: item.x_pt, y: item.y_pt, w: item.w_pt, h: item.h_pt };
  const box = isFreeWhiteout(item) ? keepBoxOn(current, page) : clampBoxOn(current, page);
  if (box.x === item.x_pt && box.y === item.y_pt && box.w === item.w_pt && box.h === item.h_pt) return item;
  return { ...item, x_pt: box.x, y_pt: box.y, w_pt: box.w, h_pt: box.h };
}
