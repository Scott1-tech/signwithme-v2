import type { PageSizePt } from './layout';
import { predictStoredSize, readPixels } from './signatureInk';
import type { Signature } from '../types/contract';
import type { BBox } from '../types/field';

/**
 * Initials (W5, plan §7-7): a SMALL free signature.
 *
 * The backend is unchanged -- an initial is a row of the ordinary free
 * signature endpoint (`POST /api/contracts/{id}/signatures`, `field_id: null`),
 * hashed and audited as a signature, printed in the signatures' layer. So it
 * is a server write: off on a contract nobody approved, and not in the undo
 * history (undo would be a hidden DELETE, redo a new row with a new hash).
 *
 * HOW THE WORKSPACE TELLS AN INITIAL FROM A SIGNATURE
 * ---------------------------------------------------
 * The server has no "kind" column, so the browser decides, by two rules:
 *
 *  1. GEOMETRY -- the rule that works everywhere. This frontend places every
 *     free signature in a 180 × 34 pt box (`PdfViewer` PLACEMENT_SIZE) and
 *     every initial in a box fitted INSIDE 60 × 24 pt (`INITIALS_BOX`, ratio
 *     kept); a placed row is never resized (server rows do not move). So a
 *     free row whose box fits in 60 × 24 is an initial -- in any browser, on
 *     any machine, after a reload, for every user.
 *  2. RECORD -- the ids this browser placed as initials
 *     (`swm.initials.<contractId>`). Belt and braces: it keeps a row this
 *     browser made an initial even if the default box ever changes.
 *
 * Limits, said plainly: it is a LABEL in the workspace only. The server, the
 * audit trail (`signature_added`) and the final PDF know no difference; a
 * free signature made by some other client in a box ≤ 60 × 24 would be
 * labelled "Initials" here; a slot signature never is.
 */

/** The box an initial is fitted into (PDF points), ratio kept. */
export const INITIALS_BOX = { width: 60, height: 24 } as const;
/** Rounding slack when a stored box is compared with `INITIALS_BOX`. */
const FIT_SLACK_PT = 0.01;

export const initialsStorageKey = (contractId: string) => `swm.initials.${contractId}`;
/** No id is longer than this (the server's are UUIDs); rubbish is dropped. */
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const MAX_RECORDED = 1000;

export function loadInitialIds(contractId: string | undefined): Set<string> {
  if (!contractId) return new Set();
  try {
    const raw = localStorage.getItem(initialsStorageKey(contractId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return new Set();
    return new Set(
      parsed.filter((value): value is string => typeof value === 'string' && ID_RE.test(value)).slice(0, MAX_RECORDED),
    );
  } catch {
    return new Set();
  }
}

export function saveInitialIds(contractId: string | undefined, ids: ReadonlySet<string>): void {
  if (!contractId) return;
  try {
    if (ids.size === 0) localStorage.removeItem(initialsStorageKey(contractId));
    else localStorage.setItem(initialsStorageKey(contractId), JSON.stringify([...ids].slice(-MAX_RECORDED)));
  } catch {
    /* private mode: the geometry rule still labels them */
  }
}

/** Does this box fit the initials box (the geometry rule)? */
export const fitsInitialsBox = (bbox: Pick<BBox, 'width' | 'height'>): boolean =>
  bbox.width <= INITIALS_BOX.width + FIT_SLACK_PT && bbox.height <= INITIALS_BOX.height + FIT_SLACK_PT;

/** Is this server row an initial (see the header)? A slot signature never is. */
export function isInitialsRow(row: Pick<Signature, 'id' | 'field_id' | 'bbox'>, recorded: ReadonlySet<string>): boolean {
  if (row.field_id !== null) return false;
  return recorded.has(row.id) || fitsInitialsBox(row.bbox);
}

const round2 = (value: number) => Math.round(value * 100) / 100;

/**
 * The box of an initial of natural size `natural` (any unit: only the ratio
 * counts), centred on `at` and pulled inside the page. Largest box that fits
 * `INITIALS_BOX` with the image's ratio.
 */
export function fitInitialsBox(
  natural: { width: number; height: number },
  at: { x: number; y: number },
  page: PageSizePt | null,
): BBox {
  const ratio = natural.width > 0 && natural.height > 0 ? natural.width / natural.height : INITIALS_BOX.width / INITIALS_BOX.height;
  let width: number = INITIALS_BOX.width;
  let height = width / ratio;
  if (height > INITIALS_BOX.height) {
    height = INITIALS_BOX.height;
    width = height * ratio;
  }
  width = Math.max(1, round2(width));
  height = Math.max(1, round2(height));
  const pageWidth = page && page.width > 0 ? page.width : Infinity;
  const pageHeight = page && page.height > 0 ? page.height : Infinity;
  const x = Math.min(Math.max(0, at.x - width / 2), Math.max(0, pageWidth - width));
  const y = Math.min(Math.max(0, at.y - height / 2), Math.max(0, pageHeight - height));
  return { x: round2(x), y: round2(y), width, height };
}

/**
 * Where a copy of an initial goes on another page (plan §7-7 "every page"):
 * the same distance from the page's RIGHT and BOTTOM edges -- where initials
 * live on a contract -- so a page of another size still gets it in its corner.
 * Same size as the source; pulled inside the page if that page is smaller.
 */
export function initialsBoxOnPage(source: BBox, from: PageSizePt | null, to: PageSizePt | null): BBox {
  if (!from || !to) return { ...source };
  const right = from.width - (source.x + source.width);
  const bottom = from.height - (source.y + source.height);
  const x = Math.min(Math.max(0, to.width - right - source.width), Math.max(0, to.width - source.width));
  const y = Math.min(Math.max(0, to.height - bottom - source.height), Math.max(0, to.height - source.height));
  return { x: round2(x), y: round2(y), width: source.width, height: source.height };
}

/** Is there already an initial on `page` within half a point of `box`? (A second press adds none.) */
export function hasInitialAt(rows: readonly Signature[], page: number, box: BBox, isInitial: (row: Signature) => boolean): boolean {
  return rows.some(
    (row) =>
      row.page_number === page &&
      isInitial(row) &&
      Math.abs(row.bbox.x - box.x) <= 0.5 &&
      Math.abs(row.bbox.y - box.y) <= 0.5,
  );
}

/**
 * The size of the image the SERVER will store for `dataUrl` -- the ratio an
 * initial's box must have, or the printed ink is a thin strip inside it (fix
 * 2026-09-27: a 480 × 160 pad drawing is cropped by the server to its ink,
 * e.g. 426 × 38, so a 3:1 box printed it 5.35 pt tall in a 20 pt box).
 *
 * Predicted with the server's own crop rule (`predictStoredSize`, mirrored
 * from `signature_image.py`), which is exact for a drawing-pad capture or any
 * image with transparency. An opaque picture (photo / scan) has its paper
 * removed by the server first, which is not predicted: then the picture's
 * own size is the best answer there is. Either way the preview is drawn from
 * the stored image afterwards (`freeSignatureRect`), so it is never wrong --
 * at worst the ink fills less of the box.
 */
export async function storedImageSizeOf(dataUrl: string): Promise<{ width: number; height: number }> {
  try {
    const predicted = predictStoredSize(await readPixels(dataUrl));
    if (predicted && predicted.width > 0 && predicted.height > 0) return predicted;
  } catch {
    /* unreadable here: the plain size below still answers */
  }
  return imageSizeOf(dataUrl);
}

/** A data URL's pixel size (the initial keeps its ratio). */
export function imageSizeOf(dataUrl: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    // A picture the browser cannot read still gets the default box.
    image.onerror = () => resolve({ width: INITIALS_BOX.width, height: INITIALS_BOX.height });
    image.src = dataUrl;
  });
}

/** A blob as a `data:` URL (the placement endpoint's `image_data`). */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read the initials image'));
    reader.onload = () => resolve(reader.result as string);
    reader.readAsDataURL(blob);
  });
}

/** A library entry saved as initials: its label says so (the backend's `label`, no kind column). */
export const INITIALS_LABEL = 'Initials';
export const isInitialsLabel = (label: string | null | undefined): boolean =>
  typeof label === 'string' && /^initials\b/i.test(label.trim());
