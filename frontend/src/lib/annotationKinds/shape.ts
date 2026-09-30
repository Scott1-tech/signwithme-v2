import type { PageSizePt } from '../layout';
import {
  DEFAULT_COLOR,
  DEFAULT_STROKE_PT,
  MIN_BOX_PT,
  clampBoxOn,
  clampStroke,
  finite,
  groupKey,
  isAnnotationColor,
  keepBoxOn,
  pageExtent,
  tidy,
  type AnnotationColor,
  type AnnotationCommon,
  type BoxPt,
  type SanitizeBase,
} from './shared';

/**
 * `rect` / `ellipse` (contract v5, W4): an outline of `stroke_pt` CENTRED on
 * the box's edge (as SVG draws one), optionally with a TRANSLUCENT fill of
 * the same colour (alpha 0.2 -- never opaque: an opaque white box is a
 * whiteout and says so in the audit). `x_pt` / `y_pt` is the box's top-left
 * corner; the WHOLE box must be on the page (`annotation_outside_page`).
 */

export type ShapeKind = 'rect' | 'ellipse';
export const SHAPE_KINDS: readonly ShapeKind[] = ['rect', 'ellipse'];
export const isShapeKind = (kind: unknown): kind is ShapeKind => kind === 'rect' || kind === 'ellipse';

export interface ShapeAnnotation extends AnnotationCommon {
  kind: ShapeKind;
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
  stroke_pt: number;
  /** Always named (the server's default is black, but a shape says what it is). */
  color: AnnotationColor;
  fill: boolean;
  /** F2: the v7 look (`shape2`: fill colour, dash, opacity). */
  v7?: import('./v7').ShapeV7;
}

export interface ShapeRequest {
  id: string;
  kind: ShapeKind;
  page: number;
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
  stroke_pt: number;
  color: AnnotationColor;
  fill: boolean;
  group_id?: string;
}

export function makeShape(
  kind: ShapeKind,
  base: {
    id: string;
    page: number;
    box: BoxPt;
    stroke?: number;
    color?: AnnotationColor;
    fill?: boolean;
    group_id?: string;
  },
  page: PageSizePt | null,
): ShapeAnnotation {
  const box = clampBoxOn(base.box, page);
  const made: ShapeAnnotation = {
    id: base.id,
    kind,
    page: base.page,
    x_pt: box.x,
    y_pt: box.y,
    w_pt: box.w,
    h_pt: box.h,
    stroke_pt: clampStroke(base.stroke ?? DEFAULT_STROKE_PT),
    color: base.color ?? DEFAULT_COLOR,
    fill: Boolean(base.fill),
  };
  if (base.group_id) made.group_id = base.group_id;
  return made;
}

export function sanitizeShape(kind: ShapeKind, { id, page, group_id, source }: SanitizeBase): ShapeAnnotation | null {
  const x = finite(source.x_pt);
  const y = finite(source.y_pt);
  const w = finite(source.w_pt);
  const h = finite(source.h_pt);
  if (x === null || y === null || w === null || h === null) return null;
  if (w < MIN_BOX_PT || h < MIN_BOX_PT) return null;
  return makeShape(
    kind,
    {
      id,
      page,
      box: { x, y, w, h },
      stroke: finite(source.stroke_pt) ?? DEFAULT_STROKE_PT,
      color: isAnnotationColor(source.color) ? source.color : DEFAULT_COLOR,
      // Strict, like the server's bool: only `true` fills.
      fill: source.fill === true,
      group_id,
    },
    null,
  );
}

export const shapeForRequest = (item: ShapeAnnotation): ShapeRequest => ({
  id: item.id,
  kind: item.kind,
  page: item.page,
  x_pt: tidy(item.x_pt),
  y_pt: tidy(item.y_pt),
  w_pt: tidy(item.w_pt),
  h_pt: tidy(item.h_pt),
  stroke_pt: clampStroke(item.stroke_pt),
  color: item.color,
  fill: item.fill,
  ...groupKey(item),
});

export const shapeBounds = (item: { x_pt: number; y_pt: number; w_pt: number; h_pt: number }): BoxPt => ({
  x: item.x_pt,
  y: item.y_pt,
  w: item.w_pt,
  h: item.h_pt,
});

export const sameShape = (a: ShapeAnnotation, b: ShapeAnnotation): boolean =>
  a.w_pt === b.w_pt &&
  a.h_pt === b.h_pt &&
  a.stroke_pt === b.stroke_pt &&
  a.color === b.color &&
  a.fill === b.fill;

/** The same box with a new geometry (clamped to the page). Same object when nothing changed. */
export function reboxShape<T extends ShapeAnnotation | { x_pt: number; y_pt: number; w_pt: number; h_pt: number }>(
  item: T,
  box: BoxPt,
  page: PageSizePt | null,
): T {
  const next = clampBoxOn(box, page);
  if (next.x === item.x_pt && next.y === item.y_pt && next.w === item.w_pt && next.h === item.h_pt) return item;
  return { ...item, x_pt: next.x, y_pt: next.y, w_pt: next.w, h_pt: next.h };
}

/** Pulled (moved, never shrunk unless larger than the page) inside the page. */
export function keepShapeOn<T extends { x_pt: number; y_pt: number; w_pt: number; h_pt: number }>(
  item: T,
  page: PageSizePt | null,
): T {
  const next = keepBoxOn({ x: item.x_pt, y: item.y_pt, w: item.w_pt, h: item.h_pt }, page);
  if (next.x === item.x_pt && next.y === item.y_pt && next.w === item.w_pt && next.h === item.h_pt) return item;
  return { ...item, x_pt: next.x, y_pt: next.y, w_pt: next.w, h_pt: next.h };
}

/** Where a box's corner may go so the whole box stays on the page. */
export function clampBoxCorner(
  size: { w: number; h: number },
  point: { x: number; y: number },
  page: PageSizePt | null,
): { x: number; y: number } {
  const { width, height } = pageExtent(page);
  return {
    x: tidy(Math.min(Math.max(point.x, 0), Math.max(0, width - size.w))),
    y: tidy(Math.min(Math.max(point.y, 0), Math.max(0, height - size.h))),
  };
}
