import type { PageSizePt } from '../layout';
import { MIN_BOX_PT, pageExtent, tidy, type BoxPt } from './shared';

/**
 * Resizing a box by one of its eight handles (W4: rect, ellipse, image).
 *
 * The edge / corner opposite the handle stays where it is; the box never
 * flips inside out (it stops at `MIN_BOX_PT`) and never leaves the page.
 *
 *  * `aspect` (an image, unless Shift is held): the box keeps its w / h ratio.
 *    A corner scales by whichever axis moved further; an edge scales the
 *    other axis along (a side edge keeps the top, a top/bottom edge the left).
 *    When the page stops one axis, the other stops with it -- the ratio wins.
 *  * `square` (a rect / ellipse with Shift, corners only): w = h.
 *
 * Pure: points in, a box out. `dx` / `dy` are the pointer's travel in PDF
 * points since the press.
 */

export type BoxHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';
export const BOX_HANDLES: readonly BoxHandle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

export const isCornerHandle = (handle: BoxHandle) => handle.length === 2;

/** The CSS cursor each handle shows. */
export const HANDLE_CURSOR: Record<BoxHandle, string> = {
  nw: 'nwse-resize',
  se: 'nwse-resize',
  ne: 'nesw-resize',
  sw: 'nesw-resize',
  n: 'ns-resize',
  s: 'ns-resize',
  e: 'ew-resize',
  w: 'ew-resize',
};

/** Where a handle sits on its box, as fractions of (w, h). */
export const HANDLE_AT: Record<BoxHandle, [number, number]> = {
  nw: [0, 0],
  n: [0.5, 0],
  ne: [1, 0],
  e: [1, 0.5],
  se: [1, 1],
  s: [0.5, 1],
  sw: [0, 1],
  w: [0, 0.5],
};

export function resizeBox(
  start: BoxPt,
  handle: BoxHandle,
  dx: number,
  dy: number,
  options: { aspect?: boolean; square?: boolean },
  page: PageSizePt | null,
): BoxPt {
  const { width, height } = pageExtent(page);
  const left0 = start.x;
  const top0 = start.y;
  const right0 = start.x + start.w;
  const bottom0 = start.y + start.h;
  const east = handle.includes('e');
  const west = handle.includes('w');
  const north = handle.includes('n');
  const south = handle.includes('s');

  const proportional = (options.aspect && start.w > 0 && start.h > 0) || (options.square && isCornerHandle(handle));
  if (!proportional) {
    let left = left0;
    let right = right0;
    let top = top0;
    let bottom = bottom0;
    if (west) left = Math.min(Math.max(0, left0 + dx), right0 - MIN_BOX_PT);
    if (east) right = Math.max(Math.min(width, right0 + dx), left0 + MIN_BOX_PT);
    if (north) top = Math.min(Math.max(0, top0 + dy), bottom0 - MIN_BOX_PT);
    if (south) bottom = Math.max(Math.min(height, bottom0 + dy), top0 + MIN_BOX_PT);
    return { x: tidy(left), y: tidy(top), w: tidy(right - left), h: tidy(bottom - top) };
  }

  // Which way the box grows from its fixed anchor, on each axis. An edge
  // handle grows the other axis to the right / downwards.
  const sx = west ? -1 : 1;
  const sy = north ? -1 : 1;
  const anchorX = west ? right0 : left0;
  const anchorY = north ? bottom0 : top0;
  const freeX = (west ? left0 : right0) + (east || west ? dx : 0);
  const freeY = (north ? top0 : bottom0) + (north || south ? dy : 0);
  const rawW = Math.max(MIN_BOX_PT, sx * (freeX - anchorX));
  const rawH = Math.max(MIN_BOX_PT, sy * (freeY - anchorY));
  const availW = sx > 0 ? width - anchorX : anchorX;
  const availH = sy > 0 ? height - anchorY : anchorY;

  let w: number;
  let h: number;
  if (options.square && !options.aspect) {
    let side = Math.max(rawW, rawH);
    side = Math.max(MIN_BOX_PT, Math.min(side, availW, availH));
    w = side;
    h = side;
  } else {
    const ratio = start.w / start.h;
    let scale: number;
    if (isCornerHandle(handle)) scale = Math.max(rawW / start.w, rawH / start.h);
    else if (east || west) scale = rawW / start.w;
    else scale = rawH / start.h;
    w = start.w * scale;
    h = start.h * scale;
    const fit = Math.min(1, availW / w, availH / h);
    w *= fit;
    h *= fit;
    // Never below the minimum on either axis (keeping the ratio as far as it goes).
    if (w < MIN_BOX_PT) {
      w = MIN_BOX_PT;
      h = Math.max(MIN_BOX_PT, w / ratio);
    }
    if (h < MIN_BOX_PT) {
      h = MIN_BOX_PT;
      w = Math.max(MIN_BOX_PT, h * ratio);
    }
  }
  const x = sx > 0 ? anchorX : anchorX - w;
  const y = sy > 0 ? anchorY : anchorY - h;
  return { x: tidy(x), y: tidy(y), w: tidy(w), h: tidy(h) };
}
