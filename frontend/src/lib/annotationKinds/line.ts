import type { PageSizePt } from '../layout';
import {
  DEFAULT_COLOR,
  DEFAULT_STROKE_PT,
  clampPointOn,
  clampStroke,
  finite,
  groupKey,
  isAnnotationColor,
  pageExtent,
  tidy,
  type AnnotationColor,
  type AnnotationCommon,
  type BoxPt,
  type SanitizeBase,
} from './shared';

/**
 * `line` / `arrow` (contract v5, W4): from (x1, y1) to (x2, y2), BUTT caps (the
 * ink ends exactly at the endpoints), `stroke_pt` wide. An arrow's head is a
 * FILLED triangle whose tip is (x2, y2): length 6 × stroke, half-width
 * 3 × stroke, both scaled down together when the line is shorter than the
 * head (then there is no shaft) -- `pdf_builder.arrow_head`, mirrored in
 * `arrowHead` below so the preview is the print. Both endpoints must be on the
 * page; the line must be at least 1 pt long.
 *
 * `x_pt` / `y_pt` is DERIVED (the top-left of the two endpoints' bounding box)
 * so a move is a move of a point like every other kind; it is never sent.
 */

export type LineKind = 'line' | 'arrow';
export const LINE_KINDS: readonly LineKind[] = ['line', 'arrow'];
export const isLineKind = (kind: unknown): kind is LineKind => kind === 'line' || kind === 'arrow';

export const MIN_LINE_LENGTH_PT = 1;
export const ARROW_HEAD_LENGTH_RATIO = 6;
export const ARROW_HEAD_HALF_WIDTH_RATIO = 3;

export interface LineAnnotation extends AnnotationCommon {
  kind: LineKind;
  x1_pt: number;
  y1_pt: number;
  x2_pt: number;
  y2_pt: number;
  stroke_pt: number;
  color: AnnotationColor;
  /** Derived: min(x1, x2). */
  x_pt: number;
  /** Derived: min(y1, y2). */
  y_pt: number;
  /** F2: the v7 look (`shape2`: dash, opacity, heads). */
  v7?: import('./v7').LineV7;
}

export interface LineRequest {
  id: string;
  kind: LineKind;
  page: number;
  x1_pt: number;
  y1_pt: number;
  x2_pt: number;
  y2_pt: number;
  stroke_pt: number;
  color: AnnotationColor;
  group_id?: string;
}

export const lineLength = (item: { x1_pt: number; y1_pt: number; x2_pt: number; y2_pt: number }) =>
  Math.hypot(item.x2_pt - item.x1_pt, item.y2_pt - item.y1_pt);

/** The two endpoints -> an annotation (both clamped to the page), or `null` when shorter than 1 pt. */
export function makeLine(
  kind: LineKind,
  base: {
    id: string;
    page: number;
    from: { x: number; y: number };
    to: { x: number; y: number };
    stroke?: number;
    color?: AnnotationColor;
    group_id?: string;
  },
  page: PageSizePt | null,
): LineAnnotation | null {
  const from = clampPointOn(base.from, page);
  const to = clampPointOn(base.to, page);
  if (Math.hypot(to.x - from.x, to.y - from.y) < MIN_LINE_LENGTH_PT) return null;
  const made: LineAnnotation = {
    id: base.id,
    kind,
    page: base.page,
    x1_pt: from.x,
    y1_pt: from.y,
    x2_pt: to.x,
    y2_pt: to.y,
    stroke_pt: clampStroke(base.stroke ?? DEFAULT_STROKE_PT),
    color: base.color ?? DEFAULT_COLOR,
    x_pt: Math.min(from.x, to.x),
    y_pt: Math.min(from.y, to.y),
  };
  if (base.group_id) made.group_id = base.group_id;
  return made;
}

export function sanitizeLine(kind: LineKind, { id, page, group_id, source }: SanitizeBase): LineAnnotation | null {
  const x1 = finite(source.x1_pt);
  const y1 = finite(source.y1_pt);
  const x2 = finite(source.x2_pt);
  const y2 = finite(source.y2_pt);
  if (x1 === null || y1 === null || x2 === null || y2 === null) return null;
  return makeLine(
    kind,
    {
      id,
      page,
      from: { x: x1, y: y1 },
      to: { x: x2, y: y2 },
      stroke: finite(source.stroke_pt) ?? DEFAULT_STROKE_PT,
      color: isAnnotationColor(source.color) ? source.color : DEFAULT_COLOR,
      group_id,
    },
    null,
  );
}

export const lineForRequest = (item: LineAnnotation): LineRequest => ({
  id: item.id,
  kind: item.kind,
  page: item.page,
  x1_pt: tidy(item.x1_pt),
  y1_pt: tidy(item.y1_pt),
  x2_pt: tidy(item.x2_pt),
  y2_pt: tidy(item.y2_pt),
  stroke_pt: clampStroke(item.stroke_pt),
  color: item.color,
  ...groupKey(item),
});

export const lineBounds = (item: LineAnnotation): BoxPt => ({
  x: Math.min(item.x1_pt, item.x2_pt),
  y: Math.min(item.y1_pt, item.y2_pt),
  w: Math.abs(item.x2_pt - item.x1_pt),
  h: Math.abs(item.y2_pt - item.y1_pt),
});

export const sameLine = (a: LineAnnotation, b: LineAnnotation): boolean =>
  a.x1_pt === b.x1_pt &&
  a.y1_pt === b.y1_pt &&
  a.x2_pt === b.x2_pt &&
  a.y2_pt === b.y2_pt &&
  a.stroke_pt === b.stroke_pt &&
  a.color === b.color;

/** Both endpoints translated so the bounding box's corner lands on (x, y). */
export function translateLine(item: LineAnnotation, x_pt: number, y_pt: number): LineAnnotation {
  const dx = x_pt - item.x_pt;
  const dy = y_pt - item.y_pt;
  if (dx === 0 && dy === 0) return item;
  const x1 = tidy(item.x1_pt + dx);
  const y1 = tidy(item.y1_pt + dy);
  const x2 = tidy(item.x2_pt + dx);
  const y2 = tidy(item.y2_pt + dy);
  return { ...item, x1_pt: x1, y1_pt: y1, x2_pt: x2, y2_pt: y2, x_pt: Math.min(x1, x2), y_pt: Math.min(y1, y2) };
}

/** One endpoint moved (clamped to the page). Same object when shorter than 1 pt or unchanged. */
export function moveLineEnd(
  item: LineAnnotation,
  end: 'start' | 'end',
  point: { x: number; y: number },
  page: PageSizePt | null,
): LineAnnotation {
  const at = clampPointOn(point, page);
  const x1 = end === 'start' ? at.x : item.x1_pt;
  const y1 = end === 'start' ? at.y : item.y1_pt;
  const x2 = end === 'end' ? at.x : item.x2_pt;
  const y2 = end === 'end' ? at.y : item.y2_pt;
  if (x1 === item.x1_pt && y1 === item.y1_pt && x2 === item.x2_pt && y2 === item.y2_pt) return item;
  if (Math.hypot(x2 - x1, y2 - y1) < MIN_LINE_LENGTH_PT) return item;
  return { ...item, x1_pt: x1, y1_pt: y1, x2_pt: x2, y2_pt: y2, x_pt: Math.min(x1, x2), y_pt: Math.min(y1, y2) };
}

/**
 * Shift: the free end snapped to the nearest 45° around the fixed one, same
 * length -- then pulled back ALONG THAT RAY (not clamped per axis, which
 * would bend the angle) until it is on the page.
 */
export function snapTo45(
  fixed: { x: number; y: number },
  free: { x: number; y: number },
  page: PageSizePt | null,
): { x: number; y: number } {
  const dx = free.x - fixed.x;
  const dy = free.y - fixed.y;
  const length = Math.hypot(dx, dy);
  if (length === 0) return { ...free };
  const step = Math.PI / 4;
  const angle = Math.round(Math.atan2(dy, dx) / step) * step;
  const ux = Math.abs(Math.cos(angle)) < 1e-9 ? 0 : Math.cos(angle);
  const uy = Math.abs(Math.sin(angle)) < 1e-9 ? 0 : Math.sin(angle);
  const { width, height } = pageExtent(page);
  let reach = length;
  if (ux > 0) reach = Math.min(reach, (width - fixed.x) / ux);
  if (ux < 0) reach = Math.min(reach, (0 - fixed.x) / ux);
  if (uy > 0) reach = Math.min(reach, (height - fixed.y) / uy);
  if (uy < 0) reach = Math.min(reach, (0 - fixed.y) / uy);
  reach = Math.max(0, reach);
  return { x: fixed.x + ux * reach, y: fixed.y + uy * reach };
}

/**
 * The arrow head, exactly as `pdf_builder.arrow_head` computes it:
 * (base, left corner, right corner), tip = (x2, y2).
 */
export function arrowHead(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  stroke: number,
): { base: [number, number]; left: [number, number]; right: [number, number] } {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  let headLength = ARROW_HEAD_LENGTH_RATIO * stroke;
  let halfWidth = ARROW_HEAD_HALF_WIDTH_RATIO * stroke;
  if (headLength > length) {
    halfWidth *= length / headLength;
    headLength = length;
  }
  const base: [number, number] =
    headLength === length ? [x1, y1] : [x2 - ux * headLength, y2 - uy * headLength];
  return {
    base,
    left: [base[0] - uy * halfWidth, base[1] + ux * halfWidth],
    right: [base[0] + uy * halfWidth, base[1] - ux * halfWidth],
  };
}
