import type { LayoutSlotRow } from '../components/LayoutPanel';
import type { BulkFailure, BulkSkip } from '../components/BulkSignPanel';
import { ApiError } from '../api/client';
import { NEW_ISSUES_MESSAGE, isNewIssuesConflict } from '../api/signatures';
import type { MissingSlotRef } from '../api/signatures';
import {
  hasOwnScale,
  slotOffsetBounds,
  slotScaleBounds,
  type FinalizeLayout,
  type PageSizePt,
} from './layout';
import type { BBox } from '../types/field';
import type { SignatureSlot } from '../types/comparison';

/**
 * The signing page's plain pieces: types, constants and pure helpers with no
 * React in them. Kept apart from the page so the workspace (W1) can share them
 * without importing a screen.
 */

/** What the server said was still missing when finalize was refused. */
export interface FinalizeMissing {
  signatures: MissingSlotRef[];
  dates: MissingSlotRef[];
}

/**
 * Two ways to sign, chosen by the user and remembered between visits.
 * `bulk`: one signature and one date for every slot at once.
 * `slot`: the per-slot cards, each with its own signature and date.
 */
export const SIGN_MODES = ['bulk', 'slot'] as const;
export type SignMode = (typeof SIGN_MODES)[number];

export const MODE_KEY = 'swm.signMode';
export const AUTOFILL_KEY = 'swm.autoFill';

/**
 * At most this many requests in flight when the page still has to work slot by
 * slot: the removals a "Replace all" does first, the undo deletes, and the
 * legacy fallback loop for a server without the bulk endpoint.
 */
export const BULK_CONCURRENCY = 3;

/** What one bulk run actually did, so it can be described and undone. */
export interface BulkOutcome {
  placedSignatureIds: string[];
  filledDateFieldIds: string[];
  failures: BulkFailure[];
  skipped: BulkSkip[];
  /**
   * The run was refused as a whole (bad image, contract not signable, no
   * access). Carried rather than thrown so a partial run keeps the placements
   * it did make -- undo still has to know about them.
   */
  error: string | null;
}

/** Plain-language versions of the closed set of per-slot refusal reasons. */
export const SLOT_FAILURE_TEXT: Record<string, string> = {
  unknown_field: 'This slot is not part of this contract’s template.',
  not_a_signature_field: 'This field cannot hold a signature.',
  page_out_of_range: 'This slot is on a page the uploaded contract does not have.',
};

export const describeSlotFailure = (reason: string) =>
  SLOT_FAILURE_TEXT[reason] ?? `The server refused this slot (${reason}).`;

/**
 * A finalize 422 about `layout` arrives in one of two shapes:
 * - FastAPI validation: `{"detail": [{"loc": [...], "msg": "..."}]}` -- the API
 *   client stores that array JSON-encoded in `ApiError.message`;
 * - the app's own: `{"message": "...", "details": {"unknown_slot_offsets": [...]}}`.
 * Returns readable text plus any slot keys the server did not recognise.
 */
export function readLayoutRejection(cause: ApiError): { text: string; unknownSlots: string[] } {
  const raw = cause.details.unknown_slot_offsets;
  const unknownSlots = Array.isArray(raw) ? raw.filter((id): id is string => typeof id === 'string') : [];
  let text = cause.message;
  try {
    const parsed: unknown = JSON.parse(cause.message);
    if (Array.isArray(parsed)) {
      const lines = parsed
        .map((item) => {
          if (!item || typeof item !== 'object') return null;
          const { loc, msg } = item as { loc?: unknown; msg?: unknown };
          if (typeof msg !== 'string') return null;
          const where = Array.isArray(loc)
            ? loc.filter((part) => part !== 'body').map(String).join(' › ')
            : '';
          return where ? `${where}: ${msg}` : msg;
        })
        .filter((line): line is string => Boolean(line));
      if (lines.length > 0) text = lines.join('; ');
    }
  } catch {
    /* not JSON: an ordinary app message */
  }
  return { text, unknownSlots };
}

export const slotCardId = (fieldId: string) => `slot-card-${fieldId}`;

/**
 * How long the on-page mini panel survives the pointer leaving the slot.
 *
 * The panel is drawn a few pixels clear of the slot box (it must never cover
 * the surface `useSlotDrag` presses on), so the pointer crosses a gap on its
 * way there. A short grace period bridges that gap without a hit area that
 * would sit on top of the slot.
 */
export const HOVER_GRACE_MS = 220;

/**
 * Placement/finalize refusal in words. A 409 from the signing gate on an
 * overridden contract means an issue appeared that the override did not cover.
 */
export function signingErrorText(cause: unknown, fallback: string): string {
  if (isNewIssuesConflict(cause)) return NEW_ISSUES_MESSAGE;
  return cause instanceof Error ? cause.message : fallback;
}

/*
 * NO AUTOMATIC DATE (user's decision, 2026-09-25): a date is never put on a
 * contract on its own -- not by a default, not by the automatic fill, not by a
 * "Today" shortcut. Every date box starts empty and the user picks the date,
 * every time. (The old default, `new Date().toISOString().slice(0, 10)`, was
 * the UTC date: between 00:00 and 05:00 in Tashkent it dated contracts
 * YESTERDAY.) So there is deliberately no `today()` here any more.
 */

/** What every date-placing gesture says when no date has been chosen. */
export const CHOOSE_DATE_FIRST = 'Choose the date first';

/** The standing reminder while date fields are still empty. */
export const datesNeededText = (count: number) =>
  `Choose the date for ${count} date ${count === 1 ? 'field' : 'fields'} — ` +
  'dates are never filled in automatically.';

/**
 * Move the keyboard focus to a date box, by its `data-testid`. A no-op when
 * that box is not on screen (a collapsed section, the toolbar's overflow).
 */
export function focusDateInput(testId: string): void {
  const input = document.querySelector<HTMLInputElement>(`input[data-testid="${testId}"]`);
  if (!input) return;
  input.scrollIntoView({ block: 'nearest' });
  input.focus();
}

/** Slots that can be drawn, bucketed by page. One without a bbox has no place
 *  on the page and is only reachable from the panel. */
export function groupPositionedByPage(slots: SignatureSlot[]): Map<number, SignatureSlot[]> {
  const byPage = new Map<number, SignatureSlot[]>();
  slots.forEach((slot) => {
    if (!slot.page || !slot.bbox) return;
    const list = byPage.get(slot.page);
    if (list) list.push(slot);
    else byPage.set(slot.page, [slot]);
  });
  return byPage;
}

/**
 * One row per positioned slot for the per-slot nudges: each signature
 * followed by its own date, then the dates tied to no signature.
 */
export function buildLayoutRows({
  sigSlots,
  dateSlots,
  pairedDateFor,
  pageSizeOf,
  layout,
  scaleOf,
}: {
  sigSlots: SignatureSlot[];
  dateSlots: SignatureSlot[];
  pairedDateFor: (sig: SignatureSlot) => SignatureSlot | null;
  pageSizeOf: (page: number | null) => PageSizePt | null;
  layout: FinalizeLayout;
  scaleOf: (fieldId: string | null | undefined) => number;
}): LayoutSlotRow[] {
  const rows: LayoutSlotRow[] = [];
  const seen = new Set<string>();
  const push = (slot: SignatureSlot) => {
    if (!slot.bbox || seen.has(slot.field_id)) return;
    seen.add(slot.field_id);
    const page = pageSizeOf(slot.page);
    const positioned = slot as typeof slot & { bbox: BBox };
    const signature = slot.kind === 'signature';
    rows.push({
      fieldId: slot.field_id,
      label: slot.label,
      kind: slot.kind,
      page: slot.page,
      bounds: page ? slotOffsetBounds(positioned, layout, page) : undefined,
      // Only signatures have a size; a date field id in `slot_scales` is a 422.
      scale: signature ? scaleOf(slot.field_id) : undefined,
      scaleBounds: signature && page ? slotScaleBounds(positioned, layout, page) : undefined,
      ownScale: signature && hasOwnScale(layout, slot.field_id),
    });
  };
  sigSlots.forEach((sig) => {
    push(sig);
    const date = pairedDateFor(sig);
    if (date) push(date);
  });
  dateSlots.forEach(push);
  return rows;
}
