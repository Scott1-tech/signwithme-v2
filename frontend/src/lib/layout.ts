import type { BBox } from '../types/field';
import { FONT_CSS, FONT_FAMILIES, FONT_LABEL, type FontFamily } from './fonts';

/**
 * Where signatures and dates print, as the signer tuned it.
 *
 * Mirrors the `layout` object of `POST /api/contracts/{id}/finalize` (shared
 * contract v1) and the placement maths the backend uses, so the on-screen
 * preview and the generated PDF agree point for point. The server stays the
 * authority: it re-validates every value (422 outside the limits) and does
 * the real drawing.
 */

/** Gap between the bottom of the ink and the rule, in PDF points. */
export const SIGNATURE_INK_GAP = 1.0;
/** Gap between the date baseline and the rule, in PDF points. */
export const DATE_INK_GAP = 1.0;

export const OFFSET_LIMIT = 20;
/** Per-slot offsets (dragged with the mouse) may move a mark much further. */
export const SLOT_OFFSET_LIMIT = 200;
export const OFFSET_STEP = 0.5;
export const OFFSET_STEP_LARGE = 2;
export const SIGNATURE_SCALE_MIN = 0.6;
export const SIGNATURE_SCALE_MAX = 1.5;
/**
 * Per-slot signature size (contract v3, the corner handle), as a multiplier of
 * the slot's own bbox -- deliberately wider than the global slider, which is a
 * fine adjustment for the whole contract.
 */
export const SLOT_SCALE_MIN = 0.3;
export const SLOT_SCALE_MAX = 3;
/** One press of − / + in the panel; Shift takes the large step. */
export const SLOT_SCALE_STEP = 0.05;
export const SLOT_SCALE_STEP_LARGE = 0.25;
/** The server refuses a bigger map (contract v3); the browser never builds one. */
export const MAX_SLOT_SCALES = 500;
export const DATE_FONT_SIZE_MIN = 8;
export const DATE_FONT_SIZE_MAX = 16;

/**
 * The slot dates' font: the same six families a free annotation has
 * (contract v6, `lib/fonts.ts`) -- the three standard ones and the three
 * embedded Liberation ones. A date is digits and slashes, so every family can
 * print it (no character check needed).
 */
export const DATE_FONTS = FONT_FAMILIES;
export type DateFont = FontFamily;

export const DATE_FONT_LABEL: Record<DateFont, string> = FONT_LABEL;

/**
 * Browser stand-in for each family: the metric-compatible Liberation face
 * first (`styles/fonts.css`, the same files the backend embeds), so a date is
 * exactly as wide on the screen as in the PDF.
 */
export const DATE_FONT_CSS: Record<DateFont, string> = FONT_CSS;

export interface FinalizeLayout {
  /** Every signature, in points. Positive moves UP. */
  signature_offset_pt: number;
  /** Every date, in points. Positive moves UP. */
  date_offset_pt: number;
  /** Per field id, vertical; added to the offset of that field's own kind. Positive moves UP. */
  slot_offsets_pt: Record<string, number>;
  /**
   * Per field id, horizontal; positive moves RIGHT. Applies to slot placements
   * only (contract v2). Layouts saved before v2 have no such map.
   */
  slot_offsets_x_pt: Record<string, number>;
  signature_scale: number;
  /**
   * Per **signature** field id, the size of that one signature (contract v3).
   * An entry REPLACES `signature_scale` for that slot -- the two are never
   * multiplied -- so the global slider only moves the slots that have none.
   * Layouts saved before v3 have no such map; it reads as "no slot has its own
   * size". Date slots never appear here (the server answers 422).
   */
  slot_scales: Record<string, number>;
  date_font: DateFont;
  date_font_size: number;
  /**
   * Contract v6: slot dates in bold / italic. Present ONLY as `true` -- a
   * layout nobody bolded has no such key at all, so its request body (and
   * what is stored) is the v5 one byte for byte. The server's StrictBool
   * would take `false`, but saying nothing is what keeps the old body.
   */
  date_bold?: true;
  date_italic?: true;
  /**
   * Contract v7: detected slots (signature or date) the signer deleted. They
   * are hidden on the page, not required at finalize, and print nothing.
   * Present only when non-empty, like the v6 keys.
   */
  removed_slots?: string[];
}

export const DEFAULT_LAYOUT: FinalizeLayout = {
  signature_offset_pt: 0,
  date_offset_pt: 0,
  slot_offsets_pt: {},
  slot_offsets_x_pt: {},
  signature_scale: 1,
  slot_scales: {},
  date_font: 'Helvetica',
  date_font_size: 11,
};

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
// Two decimals is finer than any control produces and keeps float noise
// (0.1 + 0.2) out of both the display and the request body.
const tidy = (value: number) => Math.round(value * 100) / 100;
const finite = (value: unknown, fallback: number) =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

export const clampOffset = (value: number) => tidy(clamp(value, -OFFSET_LIMIT, OFFSET_LIMIT));
export const clampSlotOffset = (value: number) =>
  tidy(clamp(value, -SLOT_OFFSET_LIMIT, SLOT_OFFSET_LIMIT));
/** Drag positions land on the same grid the ▲▼◀▶ buttons step on. */
export const snapOffset = (value: number) => Math.round(value / OFFSET_STEP) * OFFSET_STEP;
export const clampScale = (value: number) =>
  tidy(clamp(value, SIGNATURE_SCALE_MIN, SIGNATURE_SCALE_MAX));
/** One slot's own size, in [0.3, 3.0] and on the 1 % grid `tidy` produces. */
export const clampSlotScale = (value: number) =>
  tidy(clamp(value, SLOT_SCALE_MIN, SLOT_SCALE_MAX));
export const clampFontSize = (value: number) =>
  tidy(clamp(value, DATE_FONT_SIZE_MIN, DATE_FONT_SIZE_MAX));

/**
 * Coerce anything (a stored blob, a hand-edited localStorage entry) into a
 * layout the server will accept. Unknown fonts fall back, numbers are clamped,
 * zero per-slot offsets are dropped since they change nothing.
 */
function sanitizeSlotMap(raw: unknown): Record<string, number> {
  const offsets: Record<string, number> = {};
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    for (const [fieldId, value] of Object.entries(raw as Record<string, unknown>)) {
      const offset = clampSlotOffset(finite(value, 0));
      if (offset !== 0) offsets[fieldId] = offset;
    }
  }
  return offsets;
}

/**
 * The per-slot size map: every value clamped into [0.3, 3.0], anything that is
 * not a number dropped, and never more keys than the server accepts. Unlike
 * the offset maps a value is NOT dropped when it equals the global scale --
 * "this slot keeps its own size" is a decision of its own, and the global
 * slider must not pick it up again. What actually leaves the browser is
 * narrowed once more in `layoutForRequest` (signature slots, and only the
 * values that differ from the global).
 */
function sanitizeSlotScales(raw: unknown): Record<string, number> {
  const scales: Record<string, number> = {};
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    for (const [fieldId, value] of Object.entries(raw as Record<string, unknown>)) {
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      if (Object.keys(scales).length >= MAX_SLOT_SCALES) break;
      scales[fieldId] = clampSlotScale(value);
    }
  }
  return scales;
}

export function sanitizeLayout(raw: unknown): FinalizeLayout {
  if (!raw || typeof raw !== 'object') {
    return { ...DEFAULT_LAYOUT, slot_offsets_pt: {}, slot_offsets_x_pt: {}, slot_scales: {} };
  }
  const source = raw as Record<string, unknown>;

  const font = DATE_FONTS.includes(source.date_font as DateFont)
    ? (source.date_font as DateFont)
    : DEFAULT_LAYOUT.date_font;

  const layout: FinalizeLayout = {
    signature_offset_pt: clampOffset(finite(source.signature_offset_pt, 0)),
    date_offset_pt: clampOffset(finite(source.date_offset_pt, 0)),
    slot_offsets_pt: sanitizeSlotMap(source.slot_offsets_pt),
    // Absent from layouts stored before contract v2: reads as "no horizontal moves".
    slot_offsets_x_pt: sanitizeSlotMap(source.slot_offsets_x_pt),
    signature_scale: clampScale(finite(source.signature_scale, DEFAULT_LAYOUT.signature_scale)),
    // Absent from layouts stored before contract v3: reads as "no slot has its own size".
    slot_scales: sanitizeSlotScales(source.slot_scales),
    date_font: font,
    date_font_size: clampFontSize(finite(source.date_font_size, DEFAULT_LAYOUT.date_font_size)),
  };
  // v6, strict like the server: only the boolean `true` means bold / italic.
  if (source.date_bold === true) layout.date_bold = true;
  if (source.date_italic === true) layout.date_italic = true;
  // v7: deleted slots, ids only, de-duplicated and bounded like the maps.
  if (Array.isArray(source.removed_slots)) {
    const ids = [
      ...new Set(
        source.removed_slots.filter(
          (id): id is string => typeof id === 'string' && id.length > 0 && id.length <= 200,
        ),
      ),
    ].slice(0, MAX_SLOT_SCALES);
    if (ids.length > 0) layout.removed_slots = ids;
  }
  return layout;
}

/** Bring every deleted slot back (the same object when none is deleted). */
export function withoutRemovedSlots(layout: FinalizeLayout): FinalizeLayout {
  if (!layout.removed_slots) return layout;
  const next: FinalizeLayout = { ...layout };
  delete next.removed_slots;
  return next;
}

/** The ids of the slots the signer deleted, as a set. */
export const removedSlotSet = (layout: FinalizeLayout): ReadonlySet<string> =>
  new Set(layout.removed_slots ?? []);

/**
 * Delete (`removed: true`) or bring back one detected slot. Deleting also drops
 * the slot's own move and size, so a restored slot starts where it was found.
 */
export function withSlotRemoved(
  layout: FinalizeLayout,
  fieldId: string,
  removed: boolean,
): FinalizeLayout {
  const current = layout.removed_slots ?? [];
  const has = current.includes(fieldId);
  if (removed === has) return layout;
  const next: FinalizeLayout = removed
    ? { ...withoutSlotOffsets(layout, [fieldId]), removed_slots: [...current, fieldId] }
    : { ...layout, removed_slots: current.filter((id) => id !== fieldId) };
  if (next.removed_slots && next.removed_slots.length === 0) delete next.removed_slots;
  return next;
}

/**
 * The same layout with the slot dates' bold / italic set. `false` REMOVES the
 * key (the v5 shape again); the same object back when nothing changes.
 */
export function withDateStyle(
  layout: FinalizeLayout,
  style: { bold?: boolean; italic?: boolean },
): FinalizeLayout {
  const bold = style.bold ?? Boolean(layout.date_bold);
  const italic = style.italic ?? Boolean(layout.date_italic);
  if (bold === Boolean(layout.date_bold) && italic === Boolean(layout.date_italic)) return layout;
  const next: FinalizeLayout = { ...layout };
  if (bold) next.date_bold = true;
  else delete next.date_bold;
  if (italic) next.date_italic = true;
  else delete next.date_italic;
  return next;
}

export function isDefaultLayout(layout: FinalizeLayout): boolean {
  return (
    layout.signature_offset_pt === 0 &&
    layout.date_offset_pt === 0 &&
    Object.keys(layout.slot_offsets_pt).length === 0 &&
    Object.keys(layout.slot_offsets_x_pt).length === 0 &&
    Object.keys(layout.slot_scales).length === 0 &&
    layout.signature_scale === DEFAULT_LAYOUT.signature_scale &&
    layout.date_font === DEFAULT_LAYOUT.date_font &&
    layout.date_font_size === DEFAULT_LAYOUT.date_font_size &&
    !layout.date_bold &&
    !layout.date_italic &&
    !(layout.removed_slots && layout.removed_slots.length > 0)
  );
}

export const layoutStorageKey = (contractId: string) => `swm.layout.${contractId}`;

/** Best-effort: private mode or a corrupt entry simply yields the defaults. */
export function loadLayout(contractId: string | undefined): FinalizeLayout {
  if (!contractId) return sanitizeLayout(null);
  try {
    const stored = localStorage.getItem(layoutStorageKey(contractId));
    return sanitizeLayout(stored ? JSON.parse(stored) : null);
  } catch {
    return sanitizeLayout(null);
  }
}

/**
 * What goes into `localStorage`. `slot_scales` is left out while it is empty,
 * so a layout nobody resized is stored exactly as it was before contract v3 --
 * and an older build reading it back sees the shape it already understands.
 */
function layoutForStorage(layout: FinalizeLayout): Record<string, unknown> {
  const { slot_scales: scales, ...rest } = layout;
  return Object.keys(scales).length > 0 ? { ...rest, slot_scales: scales } : { ...rest };
}

export function saveLayout(contractId: string | undefined, layout: FinalizeLayout): void {
  if (!contractId) return;
  try {
    if (isDefaultLayout(layout)) localStorage.removeItem(layoutStorageKey(contractId));
    else localStorage.setItem(layoutStorageKey(contractId), JSON.stringify(layoutForStorage(layout)));
  } catch {
    /* private mode: the layout simply does not persist */
  }
}

/**
 * Finalize body: the x map is left out when empty, so a pre-v2 server still
 * accepts it, and so is the size map (contract v3) -- a server that has not
 * learned about it yet never has to see it.
 */
export type FinalizeLayoutRequest = Omit<FinalizeLayout, 'slot_offsets_x_pt' | 'slot_scales'> & {
  slot_offsets_x_pt?: Record<string, number>;
  slot_scales?: Record<string, number>;
};

/**
 * The body's `layout`, restricted to slots this contract actually has, so a
 * stale per-slot entry from an earlier comparison never reaches the server.
 * Zero offsets are already dropped by `sanitizeLayout`.
 *
 * `slot_scales` is narrowed twice more: to **signature** slots (a date field id
 * is a 422) and to the values that actually differ from the global
 * `signature_scale` -- for the rest the server's own default already draws the
 * same mark, so there is nothing to say. Omitted entirely when nothing is left.
 */
export function layoutForRequest(
  layout: FinalizeLayout,
  fieldIds: Iterable<string>,
  signatureFieldIds?: Iterable<string>,
): FinalizeLayoutRequest {
  const known = new Set(fieldIds);
  const signatures = signatureFieldIds ? new Set(signatureFieldIds) : null;
  const clean = sanitizeLayout(layout);
  const only = (map: Record<string, number>) =>
    Object.fromEntries(Object.entries(map).filter(([fieldId]) => known.has(fieldId)));
  const { slot_offsets_x_pt: xMap, slot_scales: scaleMap, removed_slots: removedIds, ...rest } = clean;
  const request: FinalizeLayoutRequest = { ...rest, slot_offsets_pt: only(clean.slot_offsets_pt) };
  // v7: only slots this contract really has; left out entirely when empty.
  const removed = (removedIds ?? []).filter((fieldId) => known.has(fieldId));
  if (removed.length > 0) request.removed_slots = removed;
  const x = only(xMap);
  if (Object.keys(x).length > 0) request.slot_offsets_x_pt = x;
  if (signatures) {
    const scales = Object.fromEntries(
      Object.entries(scaleMap).filter(
        ([fieldId, value]) => signatures.has(fieldId) && value !== clean.signature_scale,
      ),
    );
    if (Object.keys(scales).length > 0) request.slot_scales = scales;
  }
  return request;
}

/** Drop the given field ids from every per-slot map (e.g. after a 422). */
export function withoutSlotOffsets(layout: FinalizeLayout, fieldIds: Iterable<string>): FinalizeLayout {
  const stale = new Set(fieldIds);
  const keep = (map: Record<string, number>) =>
    Object.fromEntries(Object.entries(map).filter(([fieldId]) => !stale.has(fieldId)));
  return {
    ...layout,
    slot_offsets_pt: keep(layout.slot_offsets_pt),
    slot_offsets_x_pt: keep(layout.slot_offsets_x_pt),
    slot_scales: keep(layout.slot_scales),
  };
}

/** The size one signature prints at: its own, else the contract-wide slider. */
export function signatureScaleFor(layout: FinalizeLayout, fieldId?: string | null): number {
  if (fieldId) {
    const own = layout.slot_scales[fieldId];
    if (typeof own === 'number' && Number.isFinite(own)) return own;
  }
  return layout.signature_scale;
}

/** Whether this slot was given a size of its own (so the slider leaves it alone). */
export const hasOwnScale = (layout: FinalizeLayout, fieldId: string): boolean =>
  typeof layout.slot_scales[fieldId] === 'number';

/** Set one signature slot's own size; `null` hands it back to the global slider. */
export function withSlotScale(
  layout: FinalizeLayout,
  fieldId: string,
  scale: number | null,
): FinalizeLayout {
  const next = { ...layout.slot_scales };
  if (scale === null) {
    if (!(fieldId in next)) return layout;
    delete next[fieldId];
  } else {
    if (!(fieldId in next) && Object.keys(next).length >= MAX_SLOT_SCALES) return layout;
    const clamped = clampSlotScale(scale);
    if (next[fieldId] === clamped) return layout;
    next[fieldId] = clamped;
  }
  return { ...layout, slot_scales: next };
}

/** Set one slot's dx/dy (either may be omitted); zero removes the entry. */
export function withSlotOffset(
  layout: FinalizeLayout,
  fieldId: string,
  change: { dx?: number; dy?: number },
): FinalizeLayout {
  const put = (map: Record<string, number>, value: number | undefined) => {
    if (value === undefined) return map;
    const next = { ...map };
    const clamped = clampSlotOffset(value);
    if (clamped === 0) delete next[fieldId];
    else next[fieldId] = clamped;
    return next;
  };
  return {
    ...layout,
    slot_offsets_pt: put(layout.slot_offsets_pt, change.dy),
    slot_offsets_x_pt: put(layout.slot_offsets_x_pt, change.dx),
  };
}

/** One slot a group move applies to, with how far it may go on its own page. */
export interface ShiftTarget {
  fieldId: string;
  /** Page-edge range; absent/`null` while the page size is unknown (then only ±200 pt). */
  bounds?: SlotBounds | null;
}

/**
 * Move many slots by the SAME dx/dy at once ("move all signatures right").
 *
 * Nothing new is sent to the server: every slot's own `slot_offsets_x_pt` /
 * `slot_offsets_pt` entry is shifted, so the finalize contract (and the global
 * ±20 pt `signature_offset_pt` / `date_offset_pt`) stays exactly as it was.
 * Each slot is clamped on its own -- to ±200 pt and to its own page's edges --
 * so a slot that has reached an edge stays there while the others keep going.
 * Returns the same object when nothing moved, which is how the panel decides
 * whether a direction is still available.
 */
export function withSlotsShifted(
  layout: FinalizeLayout,
  targets: Iterable<ShiftTarget>,
  change: { dx?: number; dy?: number },
): FinalizeLayout {
  let next = layout;
  for (const target of targets) {
    const id = target.fieldId;
    const moved: { dx?: number; dy?: number } = {};
    if (change.dx) {
      const current = next.slot_offsets_x_pt[id] ?? 0;
      const value = clampToRange(snapOffset(current + change.dx), target.bounds?.dx ?? null);
      if (value !== current) moved.dx = value;
    }
    if (change.dy) {
      const current = next.slot_offsets_pt[id] ?? 0;
      const value = clampToRange(snapOffset(current + change.dy), target.bounds?.dy ?? null);
      if (value !== current) moved.dy = value;
    }
    if (moved.dx !== undefined || moved.dy !== undefined) next = withSlotOffset(next, id, moved);
  }
  return next;
}

/** Top edge of the rule's ink; without a detected rule, the bbox bottom. */
export function ruleLine(bbox: BBox, ruleY: number | null | undefined): number {
  return typeof ruleY === 'number' && Number.isFinite(ruleY) ? ruleY : bbox.y + bbox.height;
}

export interface PlacedRect {
  x: number;
  y: number;
  width: number;
  height: number;
  /** Bottom edge of the image, i.e. the bottom of the ink. */
  bottom: number;
}

/**
 * Signature image rectangle in PDF points (top-left origin).
 *
 * The image is fitted (contain) into `bbox × scale`, left-aligned on `bbox.x`,
 * and its bottom edge sits at `line − gap − offset`. Until the image's natural
 * size is known the whole box stands in for it.
 *
 * `scale` is the size this slot prints at, which for a slot with an entry in
 * `slot_scales` is that entry and otherwise the global `signature_scale` --
 * resolve it with `signatureScaleFor(layout, fieldId)`, never by multiplying
 * the two. Only the size changes with it: `x` (`bbox.x + dx`) and `bottom`
 * (`line − gap − offset`) do not depend on `scale` at all, which is what keeps
 * the bottom-left corner still while the corner handle is dragged.
 */
export function signatureRect(
  bbox: BBox,
  ruleY: number | null | undefined,
  offset: number,
  scale: number,
  natural?: { width: number; height: number } | null,
  dx = 0,
  inkBottomPx?: number | null,
): PlacedRect {
  const boxWidth = bbox.width * scale;
  const boxHeight = bbox.height * scale;
  let width = boxWidth;
  let height = boxHeight;
  // `pdf_builder._draw_signature`: transparent rows under the ink's lowest row
  // (`_ink_metrics`) hang BELOW the target, so the ink -- not the image edge --
  // stands on it. Omitted (every slot caller): the image bottom is the ink
  // bottom, true of every server-cropped image.
  let below = 0;
  if (natural && natural.width > 0 && natural.height > 0) {
    const ratio = Math.min(boxWidth / natural.width, boxHeight / natural.height);
    width = natural.width * ratio;
    height = natural.height * ratio;
    if (typeof inkBottomPx === 'number' && inkBottomPx > 0 && inkBottomPx <= natural.height) {
      below = (natural.height - inkBottomPx) * ratio;
    }
  }
  const bottom = ruleLine(bbox, ruleY) - SIGNATURE_INK_GAP - offset;
  return { x: bbox.x + dx, y: bottom + below - height, width, height, bottom };
}

/**
 * A FREE signature (or initial) exactly as the PDF builder prints it --
 * contract v1.1, `api/signatures.py` finalize: a free row never snaps to a
 * rule (`line = bbox bottom`), takes no per-slot dx/dy, and gets the GLOBAL
 * `signature_offset_pt` and `signature_scale`. The image is contain-fitted
 * into `bbox × scale`, left edge on `bbox.x`, its INK bottom on
 * `bbox bottom − SIGNATURE_INK_GAP − signature_offset_pt`. Before the image
 * has loaded the whole box stands in for it.
 */
export function freeSignatureRect(
  bbox: BBox,
  layout: Pick<FinalizeLayout, 'signature_offset_pt' | 'signature_scale'>,
  image?: { width: number; height: number; ink?: { bottom: number } | null } | null,
): PlacedRect {
  return signatureRect(
    bbox,
    null,
    layout.signature_offset_pt,
    layout.signature_scale,
    image,
    0,
    image?.ink?.bottom ?? null,
  );
}

/**
 * The on-screen box of a signature/date slot, standing on its rule: bottom
 * edge at `line − offset` (offset = kind's global offset + slot dy), left edge
 * at `bbox.x + dx`, the bbox's own size. The raw bbox reaches well above the
 * rule, so drawing it as-is floats the box over the line above.
 */
export function slotBoxRect(
  bbox: BBox,
  ruleY: number | null | undefined,
  offset: number,
  dx: number,
): PlacedRect {
  const bottom = ruleLine(bbox, ruleY) - offset;
  return { x: bbox.x + dx, y: bottom - bbox.height, width: bbox.width, height: bbox.height, bottom };
}

/** Date baseline in PDF points (top-left origin). */
export function dateBaseline(bbox: BBox, ruleY: number | null | undefined, offset: number): number {
  return ruleLine(bbox, ruleY) - DATE_INK_GAP - offset;
}

export interface PageSizePt {
  width: number;
  height: number;
}

export interface SlotGeometry {
  kind: 'signature' | 'date';
  bbox: BBox;
  rule_y?: number | null;
  /** Lets the bounds use this slot's own size (`slot_scales`) when it has one. */
  field_id?: string;
}

/** Inclusive [min, max] on the 0.5 pt grid; `null` when nothing fits (slot larger than the page). */
export type OffsetRange = [number, number] | null;

export interface SlotBounds {
  dx: OffsetRange;
  dy: OffsetRange;
}

/**
 * How far one slot may move and still print inside its page.
 *
 * The server only enforces ±200 pt, so keeping marks on the page is this
 * function's job. The extent is the larger of the slot box and what actually
 * prints: a signature image fitted into `bbox × scale` standing 1 pt above the
 * line; a date as MM/DD/YYYY text (≈ 5.6 em wide, 1 em tall) on its baseline.
 */
export function slotOffsetBounds(
  slot: SlotGeometry,
  layout: FinalizeLayout,
  page: PageSizePt,
): SlotBounds {
  const { bbox } = slot;
  const line = ruleLine(bbox, slot.rule_y);
  const global = slot.kind === 'signature' ? layout.signature_offset_pt : layout.date_offset_pt;
  let width = bbox.width;
  let above = bbox.height;
  if (slot.kind === 'signature') {
    // This slot's own size when it has one -- a slot resized to 250 % needs
    // more room than the global slider would suggest.
    const scale = signatureScaleFor(layout, slot.field_id);
    width = Math.max(width, bbox.width * scale);
    above = Math.max(above, SIGNATURE_INK_GAP + bbox.height * scale);
  } else {
    width = Math.max(width, layout.date_font_size * 5.6);
    above = Math.max(above, DATE_INK_GAP + layout.date_font_size);
  }
  // Float noise must not cost a whole step: `bbox.width × 1.12` can land a
  // hair beyond the page edge it was computed from, and without this tolerance
  // a mark that fits exactly would be nudged half a point inwards for ever.
  const EPSILON = 1e-9;
  const range = (lo: number, hi: number): OffsetRange => {
    const low = Math.ceil((Math.max(-SLOT_OFFSET_LIMIT, lo) - EPSILON) / OFFSET_STEP) * OFFSET_STEP;
    const high = Math.floor((Math.min(SLOT_OFFSET_LIMIT, hi) + EPSILON) / OFFSET_STEP) * OFFSET_STEP;
    return low <= high ? [tidy(low), tidy(high)] : null;
  };
  return {
    // left >= 0 and right <= page width
    dx: range(-bbox.x, page.width - bbox.x - width),
    // bottom (line - global - dy) <= page height; top (bottom - above) >= 0
    dy: range(line - global - page.height, line - global - above),
  };
}

/** Inclusive [min, max] size for one signature slot; `null` for a date slot. */
export type ScaleRange = [number, number] | null;

/**
 * How large one signature may be drawn and still print inside its page.
 *
 * The bottom-left corner is the anchor and never moves, so growing the mark
 * can only run off the **right** edge or over the **top** of the page:
 *
 *   right:  `bbox.x + dx + bbox.width × s  ≤  page width`
 *   top:    `bottom − (1 pt + bbox.height × s)  ≥  0`, `bottom = line − offset`
 *
 * The maximum is rounded **down** to the 1 % grid, so the clamped value is
 * always inside the page rather than exactly on the edge after rounding. The
 * minimum stays 0.3 even when nothing fits: a mark that small is still visible,
 * and pulling the offsets back in is `slotOffsetBounds`' job, not this one's.
 */
export function slotScaleBounds(
  slot: SlotGeometry,
  layout: FinalizeLayout,
  page: PageSizePt,
): ScaleRange {
  if (slot.kind !== 'signature') return null;
  const { bbox } = slot;
  const fieldId = slot.field_id;
  const dx = fieldId ? (layout.slot_offsets_x_pt[fieldId] ?? 0) : 0;
  const dy = fieldId ? (layout.slot_offsets_pt[fieldId] ?? 0) : 0;
  const bottom = ruleLine(bbox, slot.rule_y) - layout.signature_offset_pt - dy;
  const left = bbox.x + dx;

  let max = SLOT_SCALE_MAX;
  if (bbox.width > 0) max = Math.min(max, (page.width - left) / bbox.width);
  if (bbox.height > 0) max = Math.min(max, (bottom - SIGNATURE_INK_GAP) / bbox.height);
  const floored = Math.floor(max * 100) / 100;
  return [SLOT_SCALE_MIN, Math.max(SLOT_SCALE_MIN, Math.min(SLOT_SCALE_MAX, floored))];
}

/** Clamp a size into `slotScaleBounds`; without a range only [0.3, 3.0] applies. */
export function clampScaleToRange(value: number, range: ScaleRange): number {
  const limited = clampSlotScale(value);
  if (!range) return limited;
  return tidy(Math.min(range[1], Math.max(range[0], limited)));
}

/** Clamp into a range; with no range (nothing fits) only the ±200 pt limit applies. */
export function clampToRange(value: number, range: OffsetRange): number {
  const limited = clampSlotOffset(value);
  if (!range) return limited;
  return tidy(Math.min(range[1], Math.max(range[0], limited)));
}

/**
 * Pull every slot back inside its page (e.g. after a global offset or the
 * signature size changed). Returns the same object when nothing had to move.
 */
export function fitLayoutToPages(
  layout: FinalizeLayout,
  slots: Iterable<Omit<SlotGeometry, 'bbox'> & { bbox: BBox | null; field_id: string; page: number | null }>,
  pageSizeOf: (page: number | null) => PageSizePt | null,
): FinalizeLayout {
  return fitLayoutToPagesReport(layout, slots, pageSizeOf).layout;
}

/** As `fitLayoutToPages`, plus which slots had to be moved. */
export function fitLayoutToPagesReport(
  layout: FinalizeLayout,
  slots: Iterable<Omit<SlotGeometry, 'bbox'> & { bbox: BBox | null; field_id: string; page: number | null }>,
  pageSizeOf: (page: number | null) => PageSizePt | null,
): { layout: FinalizeLayout; moved: string[] } {
  const moved: string[] = [];
  let next = layout;
  for (const slot of slots) {
    const page = pageSizeOf(slot.page);
    if (!page || !slot.bbox) continue;
    const geometry = {
      kind: slot.kind,
      bbox: slot.bbox,
      rule_y: slot.rule_y,
      field_id: slot.field_id,
    };
    let touched = false;
    // Size first, position second. A slot with a size of its own can be too
    // big for its page (a hand-edited entry, or one whose slot was moved
    // sideways afterwards); shrinking it back keeps the corner it is anchored
    // on exactly where the user put it, which sliding the whole mark would
    // not. Slots that follow the global slider are unaffected and keep the
    // older behaviour: they move.
    const own = next.slot_scales[slot.field_id];
    if (typeof own === 'number') {
      const fitted = clampScaleToRange(own, slotScaleBounds(geometry, next, page));
      if (fitted !== own) {
        next = withSlotScale(next, slot.field_id, fitted);
        touched = true;
      }
    }
    const bounds = slotOffsetBounds(geometry, next, page);
    const dx = next.slot_offsets_x_pt[slot.field_id] ?? 0;
    const dy = next.slot_offsets_pt[slot.field_id] ?? 0;
    const fx = clampToRange(dx, bounds.dx);
    const fy = clampToRange(dy, bounds.dy);
    if (fx !== dx || fy !== dy) {
      next = withSlotOffset(next, slot.field_id, { dx: fx, dy: fy });
      touched = true;
    }
    if (touched) moved.push(slot.field_id);
  }
  return { layout: next, moved };
}

/** A size as the panel and the on-page badge show it: `125%`. */
export const formatScale = (value: number) => `${Math.round(value * 100)}%`;

export const formatOffset = (value: number) =>
  `${value > 0 ? '+' : value < 0 ? '-' : ''}${Math.abs(value).toFixed(1)} pt`;
