/**
 * PDF points <-> CSS pixels: the render scale, applied in exactly one place.
 *
 * Every mark the overlay draws (slot boxes, placed signatures, date text) is
 * computed in PDF points by `lib/layout.ts` and then has to be multiplied by
 * the viewer's render scale to land on the rendered page -- and the pointer's
 * travel has to be divided by it on the way back. That multiplication used to
 * be written out by hand in seven different components; a continuous-scroll
 * viewer gives every page its own position context, and seven hand-written
 * copies of the same formula is where such a change goes wrong.
 *
 * Rules this module keeps:
 *
 * * **Pure.** No React, no DOM, no module state -- same inputs, same numbers.
 * * **Page-local.** Nothing here knows where a page sits in the document; the
 *   caller positions the page, these helpers only scale within it. That is
 *   what makes them safe to reuse once several pages are on screen at once.
 * * **No `devicePixelRatio`.** The canvas renders at device resolution and is
 *   presented at CSS size (`hooks/usePdfViewer.ts`), so overlay maths stays in
 *   CSS pixels. Bringing pixel density in here would double-apply it.
 * * **No rounding.** Callers that format a value (`toFixed(2)` on the `*-pt`
 *   attributes) keep doing so on the point value, before it ever gets here.
 */

/** A length in PDF points -> CSS pixels, at the viewer's render scale. */
export function ptToPx(value: number, scale: number): number {
  return value * scale;
}

/** A length in CSS pixels -> PDF points. The single inverse of `ptToPx`. */
export function pxToPt(value: number, scale: number): number {
  return value / scale;
}

/** A position in either space, top-left origin. */
export interface Point {
  x: number;
  y: number;
}

/**
 * A point measured in CSS pixels from the page's own top-left corner -> PDF
 * points. The caller subtracts the page's origin first (that is the part which
 * changes when pages scroll), so this stays page-local.
 */
export function pxPointToPt(point: Point, scale: number): Point {
  return { x: pxToPt(point.x, scale), y: pxToPt(point.y, scale) };
}

/** A rectangle in PDF points, top-left origin -- e.g. a `BBox` or a `PlacedRect`. */
export interface RectPt {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** An absolutely positioned box in CSS pixels, ready to hand to `style`. */
export interface PxBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * A PDF-point rectangle -> the CSS box that draws it on the rendered page.
 * Relative to the page element, which is the overlay's positioning context.
 */
export function ptRectToPxBox(rect: RectPt, scale: number): PxBox {
  return {
    left: ptToPx(rect.x, scale),
    top: ptToPx(rect.y, scale),
    width: ptToPx(rect.width, scale),
    height: ptToPx(rect.height, scale),
  };
}

/* ==========================================================================
   View rotation
   --------------------------------------------------------------------------
   A quarter turn applied for *reading* only: the file is never touched, and
   neither are the stored coordinates. Every mark the overlay draws stays in
   the page's own unrotated point space -- what turns is the layer they live
   in, by exactly the affine map below. That is why a rotated view cannot put
   a signature box somewhere else: there is one transform, it is exact for
   multiples of 90 degrees, and it is applied to the whole layer at once
   rather than re-derived per mark.

   Same three rules as the rest of this file: pure, page-local, CSS pixels.
   ========================================================================== */

/** The only rotations offered: quarter turns, clockwise, in degrees. */
export type Quarter = 0 | 90 | 180 | 270;

const QUARTERS: readonly Quarter[] = [0, 90, 180, 270];

/** Any angle -> the quarter turn it is, normalised into [0, 360). */
export function normalizeQuarter(degrees: number): Quarter {
  if (!Number.isFinite(degrees)) return 0;
  return QUARTERS[(((Math.round(degrees / 90) % 4) + 4) % 4)];
}

/** The two rotations that swap a page's width and height. */
export function swapsAxes(rotation: Quarter): boolean {
  return rotation === 90 || rotation === 270;
}

/** A page's box as it is laid out at `rotation` -- the size to reserve. */
export function rotatedSize<T extends { width: number; height: number }>(
  size: T,
  rotation: Quarter,
): { width: number; height: number } {
  return swapsAxes(rotation)
    ? { width: size.height, height: size.width }
    : { width: size.width, height: size.height };
}

/**
 * The CSS transform that maps an *unrotated* layer of `width` x `height` CSS
 * pixels onto the page box as it is drawn at `rotation`.
 *
 * Use with `transform-origin: 0 0` on a layer that is explicitly sized to the
 * unrotated page (`left: 0; top: 0; width; height`). The translate is what
 * brings the turned layer back into the positive quadrant, so the result
 * covers the rotated box exactly -- corner for corner, no scaling, no drift.
 */
export function rotationTransform(rotation: Quarter, width: number, height: number): string {
  switch (rotation) {
    case 90:
      return `translate(${height}px, 0px) rotate(90deg)`;
    case 180:
      return `translate(${width}px, ${height}px) rotate(180deg)`;
    case 270:
      return `translate(0px, ${width}px) rotate(270deg)`;
    default:
      return 'none';
  }
}

/**
 * A point in the unrotated page (CSS pixels, top-left origin) -> where it
 * lands inside the rotated page box. `width`/`height` are the **unrotated**
 * page size in CSS pixels.
 *
 * This is `rotationTransform` written out for a single point; the two must
 * agree, which is what the checks in `tests/ui/test_rotation.py` pin down.
 */
export function rotatePointPx(point: Point, rotation: Quarter, width: number, height: number): Point {
  switch (rotation) {
    case 90:
      return { x: height - point.y, y: point.x };
    case 180:
      return { x: width - point.x, y: height - point.y };
    case 270:
      return { x: point.y, y: width - point.x };
    default:
      return { x: point.x, y: point.y };
  }
}

/** The inverse of `rotatePointPx`: rotated box -> unrotated page. */
export function unrotatePointPx(
  point: Point,
  rotation: Quarter,
  width: number,
  height: number,
): Point {
  switch (rotation) {
    case 90:
      return { x: point.y, y: height - point.x };
    case 180:
      return { x: width - point.x, y: height - point.y };
    case 270:
      return { x: width - point.y, y: point.x };
    default:
      return { x: point.x, y: point.y };
  }
}

/* ==========================================================================
   Page frames (ROTFIX 2026-09-30): the page's OWN rotation
   --------------------------------------------------------------------------
   A page whose file says `/Rotate 90` is laid out by pdf.js turned: its box
   (`pageSizes`) is the DISPLAYED page. What is measured on it comes in two
   spaces, and they are the only two there are:

   * the displayed page at some quarter turn R -- signatures, slots, dates,
     review issues, the legacy (v1-v6) markup, the pdf.js text layer and the
     search (the backend's `pdf_builder` draws these in the displayed space;
     a signature box is remapped by the server when a page is turned);
   * the UNROTATED page -- everything the PDF engine speaks (EDITOR-V2-PLAN
     §0.2: text blocks, links, pictures of the PDF, form widgets, redactions,
     the v7 markup kinds that `pdf_engine/markup.py` draws).

   The unrotated page is simply "displayed at 0". So a space is a quarter,
   and ONE map takes a layer from the space it is measured in to the box on
   screen: `rotationTransform(to - from)` -- the very transform the reader's
   view turn already uses (`PdfViewer`). Nothing is re-derived per mark.
   ========================================================================== */

/** One page as the file has it: its own `/Rotate` and its UNROTATED size (pt). */
export interface PageFrame {
  rotation: Quarter;
  width: number;
  height: number;
}

/** The space everything the PDF engine speaks is measured in. */
export const UNROTATED: Quarter = 0;

/** `to - from`, as the quarter turn that takes a layer of space `from` onto space `to`. */
export function quarterDelta(to: Quarter, from: Quarter): Quarter {
  return normalizeQuarter(to - from);
}

/** The page's box (pt) in the space displayed at `space`. */
export function frameSize(frame: PageFrame, space: Quarter): { width: number; height: number } {
  return rotatedSize({ width: frame.width, height: frame.height }, space);
}

/** A point (pt) of the page displayed at `from` -> the same spot displayed at `to`. */
export function mapPointPt(point: Point, frame: PageFrame, from: Quarter, to: Quarter): Point {
  if (from === to) return { x: point.x, y: point.y };
  const flat = unrotatePointPx(point, from, frame.width, frame.height);
  return rotatePointPx(flat, to, frame.width, frame.height);
}

/** A box (pt, top-left, `w`/`h`) from space `from` to space `to` (a quarter turn swaps the sides). */
export function mapRectPt(
  rect: { x: number; y: number; w: number; h: number },
  frame: PageFrame,
  from: Quarter,
  to: Quarter,
): { x: number; y: number; w: number; h: number } {
  if (from === to) return { ...rect };
  const a = mapPointPt({ x: rect.x, y: rect.y }, frame, from, to);
  const b = mapPointPt({ x: rect.x + rect.w, y: rect.y + rect.h }, frame, from, to);
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) };
}

/**
 * A pointer's travel on screen (any unit) -> the same travel inside a layer
 * that is turned `turn` relative to the screen. The inverse of the layer's
 * rotation, without the translation (a vector has no origin).
 */
export function unrotateVector(dx: number, dy: number, turn: Quarter): Point {
  switch (turn) {
    case 90:
      return { x: dy, y: -dx };
    case 180:
      return { x: -dx, y: -dy };
    case 270:
      return { x: -dy, y: dx };
    default:
      return { x: dx, y: dy };
  }
}

/** Inline style of a layer measured in space `from`, drawn on a page box displayed at `frame.rotation`. */
export interface FrameLayerStyle {
  position: 'absolute';
  left: number;
  top: number;
  right: 'auto';
  bottom: 'auto';
  width: number;
  height: number;
  transform: string;
  transformOrigin: string;
}

/**
 * The layer style that puts a layer measured in space `from` onto the page
 * box (displayed at `frame.rotation`), at `scale` CSS px per pt; `undefined`
 * when the two spaces are the same (the layer then fills the box as before,
 * and nothing about it changes). Same map as the reader's view turn.
 */
export function frameLayerStyle(
  frame: PageFrame | null | undefined,
  from: Quarter,
  scale: number,
): FrameLayerStyle | undefined {
  if (!frame) return undefined;
  const turn = quarterDelta(frame.rotation, from);
  if (turn === 0) return undefined;
  const size = frameSize(frame, from);
  const width = ptToPx(size.width, scale);
  const height = ptToPx(size.height, scale);
  return {
    position: 'absolute',
    left: 0,
    top: 0,
    right: 'auto',
    bottom: 'auto',
    width,
    height,
    transform: rotationTransform(turn, width, height),
    transformOrigin: '0 0',
  };
}

/**
 * A pointer at (`clientX`, `clientY`) -> a point (pt) of a layer measured in
 * space `from`, whose element is `element` (drawn with `frameLayerStyle`, or
 * filling an upright page box). Exact for quarter turns: the element's
 * bounding box on screen IS its rotated box, so the offset into it is
 * unrotated by the same map.
 */
export function clientToLayerPt(
  element: Element,
  clientX: number,
  clientY: number,
  frame: PageFrame | null | undefined,
  from: Quarter,
  scale: number,
): Point {
  const box = element.getBoundingClientRect();
  const local = { x: clientX - box.left, y: clientY - box.top };
  const turn = frame ? quarterDelta(frame.rotation, from) : 0;
  if (turn === 0) return pxPointToPt(local, scale);
  const size = frameSize(frame as PageFrame, from);
  const flat = unrotatePointPx(local, turn, ptToPx(size.width, scale), ptToPx(size.height, scale));
  return pxPointToPt(flat, scale);
}
