/**
 * Where the INK of a signature image is, measured exactly the way the server
 * measures it -- so the workspace preview of a free signature (and the box an
 * initial is fitted into) is the PDF's own geometry, not a guess.
 *
 * Two server rules are mirrored here, verbatim in behaviour:
 *
 *  1. `backend/app/services/pdf_builder.py` `_ink_metrics` / `_projected_box`
 *     / `_ink_rows` -- how the PDF builder finds the ink BOTTOM of a stored
 *     image. `_draw_signature` puts that row (not the image's own bottom) on
 *     `line − SIGNATURE_INK_GAP − offset`; any transparent padding below it
 *     hangs under the line (`below_ink`). A server-cropped image has none, but
 *     the rule is the rule.
 *  2. `backend/app/services/signature_image.py` `prepare_signature_image` /
 *     `ink_box` / `_span` / `crop_to_ink` -- how the server crops an image
 *     that arrives with transparency (a drawing-pad capture) before storing
 *     it. Used BEFORE placing an initial, so its box can be fitted to the
 *     ratio of the image the server will actually keep (W5 fix: the pad's raw
 *     3:1 box printed an 11:1 crop 5 pt tall inside a 20 pt box).
 *
 * An OPAQUE image (a photo or a scan) is not predicted: the server first
 * knocks its paper out (`remove_background`, per-image thresholds), and that is
 * not reproduced here -- `predictStoredSize` answers `null` and the caller
 * keeps the image's own size. The preview stays exact either way, because it
 * is drawn from the STORED image the server hands back (`GET …/image`).
 */

/** pdf_builder / signature_image: below this alpha a pixel is padding. */
export const INK_ALPHA_THRESHOLD = 8;
/** pdf_builder: an opaque image's ink is anything darker than this (luma). */
export const INK_LUMA_THRESHOLD = 250;
/** pdf_builder `_projected_box` noise filter. */
export const INK_MIN_LINE_FRACTION = 0.005;
export const INK_MIN_RUN = 2;
export const INK_STRONG_LINE_FRACTION = 0.05;
/** signature_image: an image with this share of alpha ≤ 250 pixels is "transparent" (cropped, not knocked out). */
const TRANSPARENT_ALPHA_MAX = 250;
const SIGNIFICANT_TRANSPARENCY = 0.02;
/** signature_image `crop_to_ink`: margin kept on the left, top and right (none below). */
export const CROP_MARGIN = 2;
const CORE_MIN_PX = 4;
const CORE_SHARE = 0.05;

export interface Pixels {
  /** RGBA, row-major, as `ImageData.data`. */
  data: Uint8ClampedArray;
  width: number;
  height: number;
}

/** `_ink_metrics`: the image size and its ink box (right / bottom EXCLUSIVE, pixels). */
export interface InkMetrics {
  width: number;
  height: number;
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/* -------------------------------------------------------------------------- */
/* pdf_builder._ink_metrics                                                    */
/* -------------------------------------------------------------------------- */

/** `_ink_rows`: rows dense enough AND (in a run ≥ INK_MIN_RUN OR strong on their own). */
function inkRows(counts: readonly number[], minCount: number, strongCount: number): boolean[] {
  const keep = new Array<boolean>(counts.length).fill(false);
  let index = 0;
  while (index < counts.length) {
    if (counts[index] < minCount) {
      index += 1;
      continue;
    }
    const start = index;
    while (index < counts.length && counts[index] >= minCount) index += 1;
    let strong = false;
    for (let i = start; i < index; i += 1) if (counts[i] >= strongCount) strong = true;
    if (index - start >= INK_MIN_RUN || strong) for (let i = start; i < index; i += 1) keep[i] = true;
  }
  return keep;
}

/** `_projected_box`: (left, top, right, bottom) of the ink in a 0/1 mask, right/bottom exclusive. */
function projectedBox(mask: Uint8Array, width: number, height: number): [number, number, number, number] | null {
  const rows = new Array<number>(height).fill(0);
  const cols = new Array<number>(width).fill(0);
  let any = false;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (mask[y * width + x]) {
        rows[y] += 1;
        cols[x] += 1;
        any = true;
      }
    }
  }
  if (!any) return null;
  // Python `int(width * f)` truncates; the products are positive, so floor.
  const rowKeep = inkRows(
    rows,
    Math.max(1, Math.floor(width * INK_MIN_LINE_FRACTION)),
    Math.max(1, Math.floor(width * INK_STRONG_LINE_FRACTION)),
  );
  const colKeep = inkRows(
    cols,
    Math.max(1, Math.floor(height * INK_MIN_LINE_FRACTION)),
    Math.max(1, Math.floor(height * INK_STRONG_LINE_FRACTION)),
  );
  const top = rowKeep.indexOf(true);
  const left = colKeep.indexOf(true);
  if (top < 0 || left < 0) return null;
  return [left, top, colKeep.lastIndexOf(true) + 1, rowKeep.lastIndexOf(true) + 1];
}

/** Pillow `alpha_composite(white, rgba)` of one channel (AlphaComposite.c, dst opaque white). */
function overWhite(channel: number, alpha: number): number {
  const coef1 = alpha * 128;
  const coef2 = 255 * 128 - coef1;
  const tmp = channel * coef1 + 255 * coef2 + (0x80 << 7);
  return (((tmp >> 8) + tmp) >> 8) >> 7;
}

/**
 * `_ink_metrics` of a STORED signature image (always an RGBA PNG: the server
 * re-encodes every image to RGBA). Alpha decides what is ink when any pixel is
 * see-through; else luma on white. Never empty: the whole image when nothing
 * qualifies, as the server does.
 */
export function inkMetrics({ data, width, height }: Pixels): InkMetrics {
  const whole: InkMetrics = { width, height, left: 0, top: 0, right: width, bottom: height };
  if (width <= 0 || height <= 0) return whole;
  const count = width * height;
  let minAlpha = 255;
  for (let i = 0; i < count; i += 1) if (data[i * 4 + 3] < minAlpha) minAlpha = data[i * 4 + 3];
  const mask = new Uint8Array(count);
  let box: [number, number, number, number] | null = null;
  if (minAlpha < INK_ALPHA_THRESHOLD) {
    for (let i = 0; i < count; i += 1) mask[i] = data[i * 4 + 3] >= INK_ALPHA_THRESHOLD ? 1 : 0;
    box = projectedBox(mask, width, height);
  }
  if (box === null) {
    for (let i = 0; i < count; i += 1) {
      const a = data[i * 4 + 3];
      const r = overWhite(data[i * 4], a);
      const g = overWhite(data[i * 4 + 1], a);
      const b = overWhite(data[i * 4 + 2], a);
      // Pillow RGB -> L (Convert.c L24).
      const luma = (r * 19595 + g * 38470 + b * 7471 + 0x8000) >> 16;
      mask[i] = luma < INK_LUMA_THRESHOLD ? 1 : 0;
    }
    box = projectedBox(mask, width, height);
  }
  if (box === null) return whole;
  return { width, height, left: box[0], top: box[1], right: box[2], bottom: box[3] };
}

/* -------------------------------------------------------------------------- */
/* signature_image.prepare_signature_image (crop only)                          */
/* -------------------------------------------------------------------------- */

/** `_span`: the anchored ink span of a projection (inclusive ends). */
function span(counts: readonly number[]): [number, number] {
  const busiest = Math.max(...counts);
  const minimum = Math.max(CORE_MIN_PX, CORE_SHARE * busiest);
  let core: number[] = [];
  counts.forEach((c, i) => {
    if (c >= minimum - 1e-6) core.push(i);
  });
  if (core.length === 0) {
    core = [];
    counts.forEach((c, i) => {
      if (c >= 0.5) core.push(i);
    });
  }
  let low = core[0];
  let high = core[core.length - 1];
  while (low > 0 && counts[low - 1] >= 0.5) low -= 1;
  while (high < counts.length - 1 && counts[high + 1] >= 0.5) high += 1;
  return [low, high];
}

/**
 * `ink_box`: (left, top, right, bottom) INCLUSIVE, specks ignored. A mask pixel
 * survives when its 3 × 3 neighbourhood (edges clamped, as Pillow's BoxBlur)
 * holds at least two ink pixels -- `BoxBlur(1) ≥ 42` on a 0/255 mask is
 * exactly that (one pixel blurs to 28, two to 57).
 */
function inkBox({ data, width, height }: Pixels): [number, number, number, number] | null {
  let rawLeft = width;
  let rawTop = height;
  let rawRight = -1;
  let rawBottom = -1;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (data[(y * width + x) * 4 + 3] >= INK_ALPHA_THRESHOLD) {
        if (x < rawLeft) rawLeft = x;
        if (x > rawRight) rawRight = x;
        if (y < rawTop) rawTop = y;
        if (y > rawBottom) rawBottom = y;
      }
    }
  }
  if (rawRight < 0) return null;
  const ox = Math.max(0, rawLeft - 1);
  const oy = Math.max(0, rawTop - 1);
  const ex = Math.min(width, rawRight + 2);
  const ey = Math.min(height, rawBottom + 2);
  const w = ex - ox;
  const h = ey - oy;
  const ink = (x: number, y: number) => {
    const cx = x < 0 ? 0 : x >= w ? w - 1 : x;
    const cy = y < 0 ? 0 : y >= h ? h - 1 : y;
    return data[((cy + oy) * width + (cx + ox)) * 4 + 3] >= INK_ALPHA_THRESHOLD ? 1 : 0;
  };
  const rows = new Array<number>(h).fill(0);
  const cols = new Array<number>(w).fill(0);
  let any = false;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      if (!ink(x, y)) continue;
      let around = 0;
      for (let dy = -1; dy <= 1; dy += 1) for (let dx = -1; dx <= 1; dx += 1) around += ink(x + dx, y + dy);
      if (around >= 2) {
        rows[y] += 1;
        cols[x] += 1;
        any = true;
      }
    }
  }
  if (!any) return null;
  const [top, bottom] = span(rows);
  const [left, right] = span(cols);
  return [left + ox, top + oy, right + ox, bottom + oy];
}

/**
 * The pixel size of the image the server will STORE for `pixels`
 * (`prepare_signature_image`), or `null` when that cannot be told here (an
 * image without significant transparency: the server knocks its paper out
 * first). For a drawing-pad capture this is the crop to the ink with a
 * `CROP_MARGIN` margin left / top / right and none below.
 */
export function predictStoredSize(pixels: Pixels): { width: number; height: number } | null {
  const { data, width, height } = pixels;
  if (width <= 0 || height <= 0) return null;
  let seeThrough = 0;
  for (let i = 0; i < width * height; i += 1) if (data[i * 4 + 3] <= TRANSPARENT_ALPHA_MAX) seeThrough += 1;
  if (seeThrough < SIGNIFICANT_TRANSPARENCY * width * height) return null;
  const box = inkBox(pixels);
  if (box === null) return { width, height };
  const [left, top, right, bottom] = box;
  const x0 = Math.max(0, left - CROP_MARGIN);
  const y0 = Math.max(0, top - CROP_MARGIN);
  const x1 = Math.min(width, right + CROP_MARGIN + 1);
  const y1 = bottom + 1;
  return { width: x1 - x0, height: y1 - y0 };
}

/** The RGBA pixels of an image URL (a data URL or a same-origin blob URL). */
export function readPixels(url: string): Promise<Pixels> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const width = image.naturalWidth;
      const height = image.naturalHeight;
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context || width === 0 || height === 0) {
        reject(new Error('The image could not be read'));
        return;
      }
      context.drawImage(image, 0, 0);
      resolve({ data: context.getImageData(0, 0, width, height).data, width, height });
    };
    image.onerror = () => reject(new Error('The image could not be decoded'));
    image.src = url;
  });
}
