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
 * `ink` (contract v5, W4): freehand strokes. Each path is a polyline drawn
 * with ROUND caps and joins, `stroke_pt` wide, centred on the points. The
 * server's limits (`app/schemas/contract.py`): 1…20 paths per element,
 * 2…2000 points per path, ≤ 4000 points per element, ≤ 20 000 points per
 * request -- every point on the page.
 *
 * `x_pt` / `y_pt` is DERIVED (the top-left of the points' bounding box), so a
 * move is a translation of every point; it is never sent.
 */

export const MAX_INK_PATHS = 20;
export const MIN_INK_PATH_POINTS = 2;
export const MAX_INK_PATH_POINTS = 2000;
export const MAX_INK_POINTS_PER_ELEMENT = 4000;
export const MAX_INK_POINTS_TOTAL = 20_000;

export interface InkPoint {
  x_pt: number;
  y_pt: number;
}

export interface InkAnnotation extends AnnotationCommon {
  kind: 'ink';
  paths: InkPoint[][];
  stroke_pt: number;
  color: AnnotationColor;
  /** Derived: the bounding box's left. */
  x_pt: number;
  /** Derived: the bounding box's top. */
  y_pt: number;
  /** F2: the v7 look (`ink2`: opacity, freehand highlight). */
  v7?: import('./v7').InkV7;
}

export interface InkRequest {
  id: string;
  kind: 'ink';
  page: number;
  paths: InkPoint[][];
  stroke_pt: number;
  color: AnnotationColor;
  group_id?: string;
}

export const inkPointCount = (item: { paths: readonly (readonly InkPoint[])[] }): number =>
  item.paths.reduce((sum, path) => sum + path.length, 0);

export function inkBounds(item: { paths: readonly (readonly InkPoint[])[] }): BoxPt {
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  item.paths.forEach((path) =>
    path.forEach((point) => {
      left = Math.min(left, point.x_pt);
      top = Math.min(top, point.y_pt);
      right = Math.max(right, point.x_pt);
      bottom = Math.max(bottom, point.y_pt);
    }),
  );
  if (!Number.isFinite(left)) return { x: 0, y: 0, w: 0, h: 0 };
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/**
 * Paths -> annotation: every point clamped to the page and put on the
 * two-decimal grid; a one-point path (a click, a dot) is doubled so it is a
 * valid 2-point path that prints as a round dot; paths outside the limits are
 * dropped (a caller that wants them kept splits first -- `splitInk`).
 * `null` when nothing is left.
 */
export function makeInk(
  base: {
    id: string;
    page: number;
    paths: ReadonlyArray<ReadonlyArray<{ x: number; y: number }>>;
    stroke?: number;
    color?: AnnotationColor;
    group_id?: string;
  },
  page: PageSizePt | null,
): InkAnnotation | null {
  const paths: InkPoint[][] = [];
  let total = 0;
  for (const raw of base.paths) {
    if (paths.length >= MAX_INK_PATHS) break;
    const path: InkPoint[] = [];
    for (const point of raw) {
      if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
      const at = clampPointOn(point, page);
      path.push({ x_pt: at.x, y_pt: at.y });
    }
    if (path.length === 1) path.push({ ...path[0] });
    if (path.length < MIN_INK_PATH_POINTS || path.length > MAX_INK_PATH_POINTS) continue;
    if (total + path.length > MAX_INK_POINTS_PER_ELEMENT) break;
    total += path.length;
    paths.push(path);
  }
  if (paths.length === 0) return null;
  const bounds = inkBounds({ paths });
  const made: InkAnnotation = {
    id: base.id,
    kind: 'ink',
    page: base.page,
    paths,
    stroke_pt: clampStroke(base.stroke ?? DEFAULT_STROKE_PT),
    color: base.color ?? DEFAULT_COLOR,
    x_pt: tidy(bounds.x),
    y_pt: tidy(bounds.y),
  };
  if (base.group_id) made.group_id = base.group_id;
  return made;
}

/**
 * A stroke too long for one element, cut to fit the server's limits: every
 * path into pieces of ≤ 2000 points (consecutive pieces share their boundary
 * point, so the line stays unbroken on paper), then the pieces packed into
 * groups of ≤ 20 paths and ≤ 4000 points -- one group per ink element.
 */
export function splitInk<T>(paths: ReadonlyArray<ReadonlyArray<T>>): T[][][] {
  const pieces: T[][] = [];
  paths.forEach((path) => {
    if (path.length <= MAX_INK_PATH_POINTS) {
      pieces.push([...path]);
      return;
    }
    for (let start = 0; start < path.length - 1; start += MAX_INK_PATH_POINTS - 1) {
      pieces.push(path.slice(start, Math.min(path.length, start + MAX_INK_PATH_POINTS)));
    }
  });
  const elements: T[][][] = [];
  let current: T[][] = [];
  let count = 0;
  pieces.forEach((piece) => {
    if (current.length >= MAX_INK_PATHS || count + piece.length > MAX_INK_POINTS_PER_ELEMENT) {
      elements.push(current);
      current = [];
      count = 0;
    }
    current.push(piece);
    count += piece.length;
  });
  if (current.length > 0) elements.push(current);
  return elements;
}

export function sanitizeInk({ id, page, group_id, source }: SanitizeBase): InkAnnotation | null {
  if (!Array.isArray(source.paths)) return null;
  const paths: Array<Array<{ x: number; y: number }>> = [];
  for (const raw of source.paths as unknown[]) {
    if (!Array.isArray(raw)) continue;
    const path: Array<{ x: number; y: number }> = [];
    for (const point of raw as unknown[]) {
      if (!point || typeof point !== 'object') continue;
      const x = finite((point as Record<string, unknown>).x_pt);
      const y = finite((point as Record<string, unknown>).y_pt);
      if (x === null || y === null) continue;
      path.push({ x, y });
    }
    paths.push(path);
  }
  return makeInk(
    {
      id,
      page,
      paths,
      stroke: finite(source.stroke_pt) ?? DEFAULT_STROKE_PT,
      color: isAnnotationColor(source.color) ? source.color : DEFAULT_COLOR,
      group_id,
    },
    null,
  );
}

export const inkForRequest = (item: InkAnnotation): InkRequest => ({
  id: item.id,
  kind: 'ink',
  page: item.page,
  paths: item.paths.map((path) => path.map((point) => ({ x_pt: tidy(point.x_pt), y_pt: tidy(point.y_pt) }))),
  stroke_pt: clampStroke(item.stroke_pt),
  color: item.color,
  ...groupKey(item),
});

export function sameInk(a: InkAnnotation, b: InkAnnotation): boolean {
  if (a.stroke_pt !== b.stroke_pt || a.color !== b.color || a.paths.length !== b.paths.length) return false;
  if (a.paths === b.paths) return true;
  return a.paths.every((path, index) => {
    const other = b.paths[index];
    return (
      path.length === other.length &&
      path.every((point, at) => point.x_pt === other[at].x_pt && point.y_pt === other[at].y_pt)
    );
  });
}

/** Every point translated so the bounding box's corner lands on (x, y). */
export function translateInk(item: InkAnnotation, x_pt: number, y_pt: number): InkAnnotation {
  const dx = x_pt - item.x_pt;
  const dy = y_pt - item.y_pt;
  if (dx === 0 && dy === 0) return item;
  const paths = item.paths.map((path) =>
    path.map((point) => ({ x_pt: tidy(point.x_pt + dx), y_pt: tidy(point.y_pt + dy) })),
  );
  const bounds = inkBounds({ paths });
  return { ...item, paths, x_pt: tidy(bounds.x), y_pt: tidy(bounds.y) };
}

/** Where the corner of an ink's bounding box may go (every point stays on the page). */
export function clampInkCorner(
  item: InkAnnotation,
  point: { x: number; y: number },
  page: PageSizePt | null,
): { x: number; y: number } {
  const bounds = inkBounds(item);
  const { width, height } = pageExtent(page);
  return {
    x: tidy(Math.min(Math.max(point.x, 0), Math.max(0, width - bounds.w))),
    y: tidy(Math.min(Math.max(point.y, 0), Math.max(0, height - bounds.h))),
  };
}

/** SVG path data for the preview, in CSS pixels. */
export function inkPathData(item: { paths: readonly (readonly InkPoint[])[] }, scale: number): string {
  return item.paths
    .map((path) =>
      path
        .map((point, index) => `${index === 0 ? 'M' : 'L'}${(point.x_pt * scale).toFixed(2)} ${(point.y_pt * scale).toFixed(2)}`)
        .join(' '),
    )
    .join(' ');
}
