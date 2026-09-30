import type { PageSizePt } from '../layout';

/**
 * What every annotation kind is built from (W4).
 *
 * `lib/annotations.ts` stays the ONE barrel the rest of the app imports from
 * (it re-exports everything here); the per-kind modules of W4
 * (`ink.ts`, `shape.ts`, `line.ts`, `note.ts`, `image.ts`) import from THIS
 * file, never from the barrel, so there is no import cycle between them.
 *
 * WHY W4 SPLIT ONLY THE NEW KINDS OUT
 * -----------------------------------
 * Plan §2.5 wants `lib/annotationKinds/<kind>.ts` for every kind. The seven
 * kinds that existed before W4 (text, date, check, cross, whiteout, the three
 * markups) are pinned byte for byte by ~200 browser checks and their request
 * bodies by v4/v5 compatibility; moving them is risk with no user-visible
 * gain. The five NEW families live in their own files with the same small
 * surface (`make`, `sanitize`, `forRequest`, `same`, `translate`, `bounds`,
 * `clamp`), and the barrel dispatches to them -- so W5's `whiteout` tool and
 * any later kind follow the same pattern, and the old kinds can move over one
 * at a time when there is a reason to touch them.
 */

/* -------------------------------------------------------------------------- */
/* The palette (contract v5, `pdf_builder.py:177`)                             */
/* -------------------------------------------------------------------------- */

/**
 * No white, on purpose: a white mark would be a redaction that is not one.
 * The preview draws with exactly these hex values, so what is on the screen
 * is what prints.
 */
export const ANNOTATION_COLORS = [
  'black',
  'blue',
  'red',
  'green',
  'yellow',
  'orange',
  'purple',
] as const;
export type AnnotationColor = (typeof ANNOTATION_COLORS)[number];
export const DEFAULT_COLOR: AnnotationColor = 'black';
export const ANNOTATION_COLOR_HEX: Record<AnnotationColor, string> = {
  black: '#000000',
  blue: '#1D4ED8',
  red: '#DC2626',
  green: '#16A34A',
  yellow: '#FACC15',
  orange: '#EA580C',
  purple: '#7E22CE',
};
export const ANNOTATION_COLOR_LABEL: Record<AnnotationColor, string> = {
  black: 'Black',
  blue: 'Blue',
  red: 'Red',
  green: 'Green',
  yellow: 'Yellow',
  orange: 'Orange',
  purple: 'Purple',
};
export const isAnnotationColor = (value: unknown): value is AnnotationColor =>
  typeof value === 'string' && (ANNOTATION_COLORS as readonly string[]).includes(value);

/* -------------------------------------------------------------------------- */
/* Numbers                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Absolute ceiling for a stored coordinate when no page size is known yet.
 * The PDF format itself stops at 14 400 pt, so anything past this is a
 * corrupt entry rather than a page nobody has measured.
 */
export const COORD_LIMIT = 20000;

/** A box (whiteout, shape, image, markup rect) is at least this big on both axes (`MIN_BOX_PT`). */
export const MIN_BOX_PT = 1;

/** Every coordinate lands on this grid when a person places or drags it. */
export const GRID_PT = 0.5;

export const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
/** Two decimals: finer than any control produces, and no `0.1 + 0.2` in the body. */
export const tidy = (value: number) => Math.round(value * 100) / 100;
export const finite = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/** A length onto the half-point grid. */
export const snapPt = (value: number) => tidy(Math.round(value / GRID_PT) * GRID_PT);

/** The page's width / height in points, or the absolute ceiling while it is unknown. */
export function pageExtent(page: PageSizePt | null): { width: number; height: number } {
  return {
    width: page && page.width > 0 ? page.width : COORD_LIMIT,
    height: page && page.height > 0 ? page.height : COORD_LIMIT,
  };
}

/** A point pulled inside the page. */
export function clampPointOn(point: { x: number; y: number }, page: PageSizePt | null): { x: number; y: number } {
  const { width, height } = pageExtent(page);
  return { x: tidy(clamp(point.x, 0, width)), y: tidy(clamp(point.y, 0, height)) };
}

/** A box pulled inside its page (both corners), never smaller than `MIN_BOX_PT`. */
export function clampBoxOn(
  box: { x: number; y: number; w: number; h: number },
  page: PageSizePt | null,
): { x: number; y: number; w: number; h: number } {
  const { width, height } = pageExtent(page);
  const left = clamp(box.x, 0, Math.max(0, width - MIN_BOX_PT));
  const top = clamp(box.y, 0, Math.max(0, height - MIN_BOX_PT));
  const right = clamp(box.x + box.w, left + MIN_BOX_PT, width);
  const bottom = clamp(box.y + box.h, top + MIN_BOX_PT, height);
  return { x: tidy(left), y: tidy(top), w: tidy(right - left), h: tidy(bottom - top) };
}

/** A box of the same size, moved (not shrunk) so it lies inside the page. */
export function keepBoxOn(
  box: { x: number; y: number; w: number; h: number },
  page: PageSizePt | null,
): { x: number; y: number; w: number; h: number } {
  const { width, height } = pageExtent(page);
  const w = Math.min(box.w, width);
  const h = Math.min(box.h, height);
  return {
    x: tidy(clamp(box.x, 0, Math.max(0, width - w))),
    y: tidy(clamp(box.y, 0, Math.max(0, height - h))),
    w: tidy(w),
    h: tidy(h),
  };
}

/* -------------------------------------------------------------------------- */
/* Stroke (ink, rect, ellipse, line, arrow -- contract v5)                     */
/* -------------------------------------------------------------------------- */

export const STROKE_MIN_PT = 0.5;
export const STROKE_MAX_PT = 12;
export const DEFAULT_STROKE_PT = 2;
export const STROKE_STEP_PT = 0.5;

/** A stroke width into 0.5…12 pt, on the half-point grid. */
export function clampStroke(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_STROKE_PT;
  return tidy(clamp(Math.round(value / STROKE_STEP_PT) * STROKE_STEP_PT, STROKE_MIN_PT, STROKE_MAX_PT));
}

/** A shape's translucent fill (`pdf_builder.SHAPE_FILL_ALPHA`). Never opaque. */
export const SHAPE_FILL_ALPHA = 0.2;

/* -------------------------------------------------------------------------- */
/* What every kind shares                                                      */
/* -------------------------------------------------------------------------- */

/** `[A-Za-z0-9_-]{1,64}`, and unique inside one request. `group_id` uses it too. */
export const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export interface AnnotationCommon {
  id: string;
  /** 1-based, inside the document. */
  page: number;
  /** v5, optional: binds the whiteout + text pair of an "edit existing text". */
  group_id?: string;
}

/** A box in PDF points, top-left origin. */
export interface BoxPt {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** `{ group_id }` when it is a valid id, else nothing. */
export const groupKey = (item: AnnotationCommon): { group_id?: string } =>
  item.group_id && ID_PATTERN.test(item.group_id) ? { group_id: item.group_id } : {};

/** What the sanitizers of every kind are handed: the parts already checked. */
export interface SanitizeBase {
  id: string;
  page: number;
  group_id?: string;
  source: Record<string, unknown>;
}
