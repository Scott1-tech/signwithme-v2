import type { Annotation, MarkupRect } from './annotations';
import { inkBounds, type InkPoint } from './annotationKinds/ink';
import { tidy } from './annotationKinds/shared';
import {
  mapPointPt,
  mapRectPt,
  normalizeQuarter,
  UNROTATED,
  type PageFrame,
  type Quarter,
} from './viewport';

/**
 * ROTFIX (2026-09-30): the markup on a page that has its OWN `/Rotate`.
 *
 * WHICH SPACE THE SERVER DRAWS IN (measured, `pdf_builder` + `pdf_engine/markup.py`)
 *  * the legacy kinds (v1-v6: text, date, check, cross, whiteout, highlight /
 *    underline / strikeout, ink, rect, ellipse, line, arrow, note, image) --
 *    the page AS DISPLAYED (`pdf_builder._draw_page` / `_merge_overlay`);
 *  * the v7 kinds (`textbox`, `stamp`, `image2`, `shape2`, `ink2`,
 *    `square_mark`) -- the UNROTATED page (the engine, §0.2), `rotation_deg`
 *    clockwise about the element's own centre.
 *
 * WHAT THE BROWSER KEEPS: every annotation in the space of the page as it is
 * displayed now -- the space the tools draw in and the overlay box is -- so
 * nothing in the annotation layer knows about rotation at all. The two
 * functions below are the ONLY places a coordinate changes space:
 *
 *  * `mapWireElement` -- at the wire (markup PUT / GET, the draft's markup,
 *    the finalize body): a v7 element between the displayed page and the
 *    engine's unrotated one. A legacy element is displayed on both sides.
 *  * `rotateAnnotation` -- when a page is REALLY turned (a `page.rotate` of
 *    the edit session), the page's annotations turn with it, so each stays on
 *    the words it marks. A mark with a direction (a box of text, a stamp, a
 *    picture) turns too (`rotation_deg`, a v5 picture becomes an `image2`);
 *    a legacy run of text (`text` / `date`) cannot turn in the PDF, so it
 *    stays upright and only its start point follows the page.
 */

/** Kinds the PDF ENGINE draws (the unrotated page), as they are named on the wire. */
export const ENGINE_MARKUP_KINDS: ReadonlySet<string> = new Set([
  'textbox',
  'stamp',
  'image2',
  'shape2',
  'ink2',
  'square_mark',
]);

const turnBy = (degrees: unknown, from: Quarter, to: Quarter): number => {
  const start = typeof degrees === 'number' && Number.isFinite(degrees) ? degrees : 0;
  // Seen on screen: `r_space + (screen − space)` -> the same look in another space.
  return tidy((((start + (to - from)) % 360) + 360) % 360);
};

const pt = (point: { x: number; y: number }) => ({ x_pt: tidy(point.x), y_pt: tidy(point.y) });

/** A box that turns about its own centre: the centre moves, the sides stay its own. */
function centreMoved(
  box: { x_pt: number; y_pt: number; w_pt: number; h_pt: number },
  frame: PageFrame,
  from: Quarter,
  to: Quarter,
): { x_pt: number; y_pt: number } {
  const centre = mapPointPt({ x: box.x_pt + box.w_pt / 2, y: box.y_pt + box.h_pt / 2 }, frame, from, to);
  return { x_pt: tidy(centre.x - box.w_pt / 2), y_pt: tidy(centre.y - box.h_pt / 2) };
}

function rectMoved(
  box: { x_pt: number; y_pt: number; w_pt: number; h_pt: number },
  frame: PageFrame,
  from: Quarter,
  to: Quarter,
): { x_pt: number; y_pt: number; w_pt: number; h_pt: number } {
  const moved = mapRectPt({ x: box.x_pt, y: box.y_pt, w: box.w_pt, h: box.h_pt }, frame, from, to);
  return { x_pt: tidy(moved.x), y_pt: tidy(moved.y), w_pt: tidy(moved.w), h_pt: tidy(moved.h) };
}

const pointMoved = (x: number, y: number, frame: PageFrame, from: Quarter, to: Quarter) =>
  pt(mapPointPt({ x, y }, frame, from, to));

const pathsMoved = (paths: readonly (readonly InkPoint[])[], frame: PageFrame, from: Quarter, to: Quarter) =>
  paths.map((path) => path.map((point) => pointMoved(point.x_pt, point.y_pt, frame, from, to)));

/**
 * One element of the wire (a markup element or a finalize entry) from space
 * `from` to space `to`. Legacy kinds and `from === to` come back untouched
 * (the same object).
 */
export function mapWireElement<T extends { kind: string }>(element: T, frame: PageFrame, from: Quarter, to: Quarter): T {
  if (from === to || !ENGINE_MARKUP_KINDS.has(element.kind)) return element;
  const source = element as unknown as Record<string, unknown>;
  const num = (key: string) => Number(source[key]);
  switch (element.kind) {
    case 'textbox':
    case 'stamp':
    case 'image2': {
      const box = { x_pt: num('x_pt'), y_pt: num('y_pt'), w_pt: num('w_pt'), h_pt: num('h_pt') };
      const rotation = turnBy(source.rotation_deg, from, to);
      const next: Record<string, unknown> = { ...source, ...centreMoved(box, frame, from, to) };
      if (element.kind === 'image2') next.rotation_deg = rotation;
      else if (rotation) next.rotation_deg = rotation;
      else delete next.rotation_deg;
      return next as unknown as T;
    }
    case 'shape2': {
      if ('x1_pt' in source) {
        const a = pointMoved(num('x1_pt'), num('y1_pt'), frame, from, to);
        const b = pointMoved(num('x2_pt'), num('y2_pt'), frame, from, to);
        return { ...source, x1_pt: a.x_pt, y1_pt: a.y_pt, x2_pt: b.x_pt, y2_pt: b.y_pt } as unknown as T;
      }
      const box = { x_pt: num('x_pt'), y_pt: num('y_pt'), w_pt: num('w_pt'), h_pt: num('h_pt') };
      return { ...source, ...rectMoved(box, frame, from, to) } as unknown as T;
    }
    case 'ink2':
      return {
        ...source,
        paths: pathsMoved((source.paths as InkPoint[][]) ?? [], frame, from, to),
      } as unknown as T;
    case 'square_mark':
      return { ...source, ...pointMoved(num('x_pt'), num('y_pt'), frame, from, to) } as unknown as T;
    default:
      return element;
  }
}

/** Displayed page (the browser's) -> what the server stores / draws. */
export const toServerSpace = <T extends { kind: string }>(element: T, frame: PageFrame | null): T =>
  frame ? mapWireElement(element, frame, frame.rotation, UNROTATED) : element;

/** What the server stores -> the page as displayed (the browser's). */
export const fromServerSpace = <T extends { kind: string }>(element: T, frame: PageFrame | null): T =>
  frame ? mapWireElement(element, frame, UNROTATED, frame.rotation) : element;

/**
 * One annotation of a page that is REALLY turned from `from` to `to` (both the
 * page as displayed): the same spot of the page's content. See the header for
 * what turns and what only moves.
 */
export function rotateAnnotation(item: Annotation, frame: PageFrame, from: Quarter, to: Quarter): Annotation {
  if (normalizeQuarter(from) === normalizeQuarter(to)) return item;
  switch (item.kind) {
    case 'whiteout':
    case 'rect':
    case 'ellipse':
      return { ...item, ...rectMoved(item, frame, from, to) };
    case 'highlight':
    case 'underline':
    case 'strikeout': {
      const rects: MarkupRect[] = item.rects.map((rect) => rectMoved(rect, frame, from, to));
      const left = Math.min(...rects.map((rect) => rect.x_pt));
      const top = Math.min(...rects.map((rect) => rect.y_pt));
      return { ...item, rects, x_pt: tidy(left), y_pt: tidy(top) };
    }
    case 'ink': {
      const paths = pathsMoved(item.paths, frame, from, to);
      const bounds = inkBounds({ paths });
      return { ...item, paths, x_pt: tidy(bounds.x), y_pt: tidy(bounds.y) };
    }
    case 'line':
    case 'arrow': {
      const a = pointMoved(item.x1_pt, item.y1_pt, frame, from, to);
      const b = pointMoved(item.x2_pt, item.y2_pt, frame, from, to);
      return {
        ...item,
        x1_pt: a.x_pt,
        y1_pt: a.y_pt,
        x2_pt: b.x_pt,
        y2_pt: b.y_pt,
        x_pt: Math.min(a.x_pt, b.x_pt),
        y_pt: Math.min(a.y_pt, b.y_pt),
      };
    }
    case 'textbox':
    case 'stamp': {
      const rotation = turnBy(item.rotation_deg, from, to);
      const next = { ...item, ...centreMoved(item, frame, from, to) };
      if (rotation) next.rotation_deg = rotation;
      else delete next.rotation_deg;
      return next;
    }
    case 'image': {
      const rotation = turnBy(item.v7?.rotation_deg, from, to);
      const opacity = item.v7?.opacity ?? 1;
      const moved = { ...item, ...centreMoved(item, frame, from, to) };
      // Upright and opaque again (a turn undone): the plain v5 picture it was.
      if (rotation === 0 && opacity === 1) {
        delete moved.v7;
        return moved;
      }
      return { ...moved, v7: { opacity, rotation_deg: rotation } };
    }
    default:
      // check / cross / square_mark (centre), note (its point), text / date
      // (the start of the baseline: the run itself stays upright).
      return { ...item, ...pointMoved(item.x_pt, item.y_pt, frame, from, to) } as Annotation;
  }
}
