import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, PointerEvent } from 'react';
import {
  SLOT_SCALE_STEP,
  SLOT_SCALE_STEP_LARGE,
  clampScaleToRange,
  signatureScaleFor,
  slotScaleBounds,
  withSlotScale,
  type FinalizeLayout,
  type PageSizePt,
  type ScaleRange,
} from '../lib/layout';
import type { PxBox } from '../lib/viewport';
import type { SignatureSlot } from '../types/comparison';

/**
 * Dragging the corner handle of a signature to resize it (contract v3).
 *
 * WHY THIS IS A SEPARATE HOOK FROM `useSlotDrag`
 * ----------------------------------------------
 * Moving and resizing are two gestures on the same mark and they must never
 * become one. `useSlotDrag` owns the mark's own surface: a press there travels
 * 3 px and becomes a move. This hook owns one small handle drawn at the mark's
 * TOP-RIGHT corner, a sibling in `.pdf-overlay` rather than a child of the
 * mark (the placed signature is an `<img>`, and the slot box is a `<button>` --
 * neither may contain one), exactly as `SlotToolbar` is. The handle:
 *
 *   * stops `pointerdown` from propagating, so no drag session ever starts;
 *   * captures the pointer itself, so the gesture survives leaving the handle;
 *   * stops `click` / `dblclick`, so the page's click-to-place never fires and
 *     the mark's double-click reset is not triggered either;
 *   * is drawn centred on the corner, never over the middle of the slot --
 *     `tests/ui/_harness.py` presses exactly there, and `elementFromPoint` at
 *     the slot centre must keep returning the slot.
 *
 * WHAT IT COMPUTES
 * ----------------
 * The bottom-left corner is the anchor: it is where the ink stands on its rule
 * (`bottom = line − 1 pt − offset`, `left = bbox.x + dx`), and neither number
 * depends on the size. So the mark grows up and to the right, the aspect ratio
 * is whatever `signatureRect` already fits, and the value written is a single
 * multiplier -- the slot's entry in `slot_scales`, which REPLACES the global
 * `signature_scale` for that slot (never multiplied with it).
 *
 * The pointer's travel becomes a ratio against the size the gesture started
 * from, averaged over the two axes so neither one alone snaps the mark around,
 * then clamped to [0.3, 3.0] and to what still prints inside the page
 * (`slotScaleBounds`). The preview is live; the layout is written once, on
 * release.
 */

interface Session {
  fieldId: string;
  slot: SignatureSlot;
  pointerId: number;
  startX: number;
  startY: number;
  /** CSS pixels the mark occupied when the gesture began; never 0. */
  startWidth: number;
  startHeight: number;
  startScale: number;
  last: number;
  bounds: ScaleRange;
}

interface Options {
  layout: FinalizeLayout;
  update: (next: (current: FinalizeLayout) => FinalizeLayout) => void;
  /** Size of a page in PDF points, or `null` while unknown (then only [0.3, 3.0] applies). */
  pageSizeOf: (page: number | null) => PageSizePt | null;
  enabled?: boolean;
}

export interface ResizeBindings {
  onPointerDown: (event: PointerEvent<Element>) => void;
  onPointerMove: (event: PointerEvent<Element>) => void;
  onPointerUp: (event: PointerEvent<Element>) => void;
  onPointerCancel: (event: PointerEvent<Element>) => void;
  onClick: (event: MouseEvent<Element>) => void;
  onDoubleClick: (event: MouseEvent<Element>) => void;
  onKeyDown: (event: KeyboardEvent<Element>) => void;
}

export interface SlotResize {
  /** The size a slot prints at: the live preview while it is being resized, else the layout. */
  scaleOf: (fieldId: string | null | undefined) => number;
  /** Field id being resized right now, if any. */
  activeId: string | null;
  /** How far this slot may be resized on its page, for the handle's own limits. */
  boundsOf: (slot: SignatureSlot) => ScaleRange;
  /** Handlers for the corner handle. `box` is the mark's box in CSS pixels. */
  bind: (slot: SignatureSlot, box: PxBox) => ResizeBindings;
  /** The − / + of the panel and the arrow keys of the handle, in one place. */
  step: (slot: SignatureSlot, direction: 1 | -1, large?: boolean) => void;
  /** Hand this slot back to the global size slider. */
  clear: (fieldId: string) => void;
}

/** Below this many pixels a start box is treated as unusable for a ratio. */
const MIN_START_PX = 4;

export function useSlotResize({ layout, update, pageSizeOf, enabled = true }: Options): SlotResize {
  const [preview, setPreview] = useState<{ fieldId: string; scale: number } | null>(null);
  const session = useRef<Session | null>(null);

  // Same pattern as `useSlotDrag`: the handlers are built once and read the
  // latest props through a ref, so a pointer move does not rebuild them.
  const latest = useRef({ layout, update, pageSizeOf, enabled });
  useEffect(() => {
    latest.current = { layout, update, pageSizeOf, enabled };
  });

  const scaleOf = useCallback(
    (fieldId: string | null | undefined): number =>
      fieldId && preview && preview.fieldId === fieldId
        ? preview.scale
        : signatureScaleFor(layout, fieldId),
    [preview, layout],
  );

  const boundsFor = useCallback(
    (slot: SignatureSlot, current: FinalizeLayout, sizeOf: Options['pageSizeOf']): ScaleRange => {
      const page = sizeOf(slot.page);
      if (!page || !slot.bbox) return null;
      return slotScaleBounds(
        { kind: slot.kind, bbox: slot.bbox, rule_y: slot.rule_y, field_id: slot.field_id },
        current,
        page,
      );
    },
    [],
  );

  const boundsOf = useCallback(
    (slot: SignatureSlot) => boundsFor(slot, layout, pageSizeOf),
    [boundsFor, layout, pageSizeOf],
  );

  const apply = useCallback(
    (slot: SignatureSlot, next: number) => {
      const { layout: current, update: write, pageSizeOf: sizeOf, enabled: on } = latest.current;
      if (!on) return;
      const scale = clampScaleToRange(next, boundsFor(slot, current, sizeOf));
      write((live) => withSlotScale(live, slot.field_id, scale));
    },
    [boundsFor],
  );

  const step = useCallback(
    (slot: SignatureSlot, direction: 1 | -1, large = false) => {
      const { layout: current } = latest.current;
      const from = signatureScaleFor(current, slot.field_id);
      apply(slot, from + direction * (large ? SLOT_SCALE_STEP_LARGE : SLOT_SCALE_STEP));
    },
    [apply],
  );

  const clear = useCallback((fieldId: string) => {
    const { update: write, enabled: on } = latest.current;
    if (!on) return;
    write((live) => withSlotScale(live, fieldId, null));
  }, []);

  const bind = useCallback(
    (slot: SignatureSlot, box: PxBox): ResizeBindings => ({
      onPointerDown: (event) => {
        // Never let this press reach the mark underneath: that is a move.
        event.stopPropagation();
        const { enabled: on, layout: current, pageSizeOf: sizeOf } = latest.current;
        if (!on || !slot.bbox || slot.kind !== 'signature') return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        const startScale = signatureScaleFor(current, slot.field_id);
        const startWidth = Math.max(box.width, MIN_START_PX);
        const startHeight = Math.max(box.height, MIN_START_PX);
        session.current = {
          fieldId: slot.field_id,
          slot,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          startWidth,
          startHeight,
          startScale,
          last: startScale,
          bounds: boundsFor(slot, current, sizeOf),
        };
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          /* capture is a nicety: the gesture still works without it */
        }
        // The handle starts resizing on the very first pixel (there is no
        // click to protect here), so the preview exists from the press on.
        setPreview({ fieldId: slot.field_id, scale: startScale });
      },
      onPointerMove: (event) => {
        const active = session.current;
        if (!active || active.pointerId !== event.pointerId) return;
        event.preventDefault();
        event.stopPropagation();
        const movedX = event.clientX - active.startX;
        // Screen y grows downwards; dragging UP makes the mark bigger.
        const movedY = active.startY - event.clientY;
        // Both corners of the diagonal count equally, so a mostly-horizontal
        // pull and a mostly-vertical one feel the same and the aspect never
        // changes -- one multiplier is all a signature has.
        const ratio =
          ((active.startWidth + movedX) / active.startWidth +
            (active.startHeight + movedY) / active.startHeight) /
          2;
        const scale = clampScaleToRange(active.startScale * ratio, active.bounds);
        active.last = scale;
        setPreview({ fieldId: active.fieldId, scale });
      },
      onPointerUp: (event) => {
        const active = session.current;
        if (!active || active.pointerId !== event.pointerId) return;
        session.current = null;
        event.stopPropagation();
        try {
          event.currentTarget.releasePointerCapture(event.pointerId);
        } catch {
          /* already released */
        }
        const { update: write, enabled: on } = latest.current;
        if (on) write((live) => withSlotScale(live, active.fieldId, active.last));
        setPreview(null);
      },
      onPointerCancel: (event) => {
        const active = session.current;
        if (!active || active.pointerId !== event.pointerId) return;
        session.current = null;
        setPreview(null);
      },
      // Neither the mark's own click nor the page's click-to-place may hear
      // anything that happened on this handle.
      onClick: (event) => {
        event.stopPropagation();
        event.preventDefault();
      },
      onDoubleClick: (event) => {
        event.stopPropagation();
        event.preventDefault();
      },
      onKeyDown: (event) => {
        const big = event.shiftKey;
        if (event.key === 'ArrowRight' || event.key === 'ArrowUp') {
          event.preventDefault();
          event.stopPropagation();
          step(slot, 1, big);
        } else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') {
          event.preventDefault();
          event.stopPropagation();
          step(slot, -1, big);
        } else if (event.key === 'Escape' || event.key === 'Backspace') {
          event.preventDefault();
          event.stopPropagation();
          clear(slot.field_id);
        }
      },
    }),
    [boundsFor, step, clear],
  );

  return { scaleOf, activeId: preview?.fieldId ?? null, boundsOf, bind, step, clear };
}
