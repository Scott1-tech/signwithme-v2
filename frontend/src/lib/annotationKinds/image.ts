import type { PageSizePt } from '../layout';
import { shapeBounds } from './shape';
import {
  MIN_BOX_PT,
  clampBoxOn,
  finite,
  groupKey,
  keepBoxOn,
  pageExtent,
  tidy,
  type AnnotationCommon,
  type BoxPt,
  type SanitizeBase,
} from './shared';

/**
 * `image` (contract v5, W4): a picture -- a stamp, a logo -- uploaded ONCE with
 * `POST /api/contracts/{id}/annotation-assets` and drawn by finalize FILLING
 * its box exactly (the server does not keep the aspect ratio: the box IS what
 * the user sized, so the editor keeps the ratio while resizing, Shift frees
 * it). `x_pt` / `y_pt` is the box's top-left; the whole box must be on the
 * page. The asset is a server row (not in the undo history); the annotation
 * that points at it is local and IS in it.
 */

/** `^[0-9a-f]{32}$` -- the server's `uuid4().hex`. */
export const ASSET_ID_PATTERN = /^[0-9a-f]{32}$/;
/** What an image may be before it is uploaded (`app/api/deps.py`). */
export const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
export const MAX_IMAGE_DIMENSION = 4000;
export const MAX_IMAGE_PIXELS = 20_000_000;
/** A new image is placed at its natural size (96 px per inch → 0.75 pt per px) ... */
export const PT_PER_PX = 0.75;
/** ... but never larger than this on its longer side (a stamp, not a poster). */
export const IMAGE_MAX_PLACE_PT = 200;

export interface ImageAnnotation extends AnnotationCommon {
  kind: 'image';
  asset_id: string;
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
  /** F2: the v7 look (`image2`: rotation, opacity). */
  v7?: import('./v7').ImageV7;
}

export interface ImageRequest {
  id: string;
  kind: 'image';
  page: number;
  asset_id: string;
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
  group_id?: string;
}

export function makeImage(
  base: { id: string; page: number; asset_id: string; box: BoxPt; group_id?: string },
  page: PageSizePt | null,
): ImageAnnotation | null {
  if (!ASSET_ID_PATTERN.test(base.asset_id)) return null;
  const box = clampBoxOn(base.box, page);
  const made: ImageAnnotation = {
    id: base.id,
    kind: 'image',
    page: base.page,
    asset_id: base.asset_id,
    x_pt: box.x,
    y_pt: box.y,
    w_pt: box.w,
    h_pt: box.h,
  };
  if (base.group_id) made.group_id = base.group_id;
  return made;
}

/**
 * Where a freshly uploaded picture lands: its natural size (0.75 pt per px),
 * scaled down -- aspect kept -- to fit `IMAGE_MAX_PLACE_PT` and the page, its
 * top-left at the click, then moved (not shrunk) so all of it is on the page.
 */
export function placeImageBox(
  natural: { width: number; height: number },
  at: { x: number; y: number },
  page: PageSizePt | null,
): BoxPt {
  const { width, height } = pageExtent(page);
  let w = Math.max(MIN_BOX_PT, natural.width * PT_PER_PX);
  let h = Math.max(MIN_BOX_PT, natural.height * PT_PER_PX);
  const fit = Math.min(1, IMAGE_MAX_PLACE_PT / Math.max(w, h), width / w, height / h);
  w = Math.max(MIN_BOX_PT, w * fit);
  h = Math.max(MIN_BOX_PT, h * fit);
  return keepBoxOn({ x: at.x, y: at.y, w: tidy(w), h: tidy(h) }, page);
}

export function sanitizeImage({ id, page, group_id, source }: SanitizeBase): ImageAnnotation | null {
  const x = finite(source.x_pt);
  const y = finite(source.y_pt);
  const w = finite(source.w_pt);
  const h = finite(source.h_pt);
  if (x === null || y === null || w === null || h === null) return null;
  if (w < MIN_BOX_PT || h < MIN_BOX_PT) return null;
  if (typeof source.asset_id !== 'string') return null;
  return makeImage({ id, page, asset_id: source.asset_id, box: { x, y, w, h }, group_id }, null);
}

export const imageForRequest = (item: ImageAnnotation): ImageRequest => ({
  id: item.id,
  kind: 'image',
  page: item.page,
  asset_id: item.asset_id,
  x_pt: tidy(item.x_pt),
  y_pt: tidy(item.y_pt),
  w_pt: tidy(item.w_pt),
  h_pt: tidy(item.h_pt),
  ...groupKey(item),
});

export const imageBounds = shapeBounds;

export const sameImage = (a: ImageAnnotation, b: ImageAnnotation): boolean =>
  a.asset_id === b.asset_id && a.w_pt === b.w_pt && a.h_pt === b.h_pt;
