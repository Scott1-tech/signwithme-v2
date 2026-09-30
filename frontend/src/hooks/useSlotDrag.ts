import { useCallback, useEffect, useRef, useState } from 'react';
import type { MouseEvent, PointerEvent } from 'react';
import {
  clampToRange,
  snapOffset,
  slotOffsetBounds,
  withSlotOffset,
  type FinalizeLayout,
  type PageSizePt,
} from '../lib/layout';
import { pxToPt } from '../lib/viewport';
import type { SignatureSlot } from '../types/comparison';

/** Below this many CSS pixels of travel a press is a click, not a drag. */
export const DRAG_THRESHOLD_PX = 3;

export interface SlotOffsets {
  /** Horizontal, PDF points, positive = right. */
  dx: number;
  /** Vertical, PDF points, positive = UP. */
  dy: number;
}

interface Session {
  fieldId: string;
  slot: SignatureSlot;
  pointerId: number;
  startX: number;
  startY: number;
  start: SlotOffsets;
  last: SlotOffsets;
  dragging: boolean;
}

interface Options {
  layout: FinalizeLayout;
  update: (next: (current: FinalizeLayout) => FinalizeLayout) => void;
  /** CSS pixels per PDF point (the viewer's render scale). */
  scale: number;
  /** Size of a page in PDF points, or `null` while unknown (then only ±200 pt applies). */
  pageSizeOf: (page: number | null) => PageSizePt | null;
  enabled?: boolean;
}

export interface DragBindings {
  onPointerDown: (event: PointerEvent<Element>) => void;
  onPointerMove: (event: PointerEvent<Element>) => void;
  onPointerUp: (event: PointerEvent<Element>) => void;
  onPointerCancel: (event: PointerEvent<Element>) => void;
  onClick: (event: MouseEvent<Element>) => void;
  onDoubleClick: (event: MouseEvent<Element>) => void;
}

export interface SlotDrag {
  /** The live offsets of a slot: the drag preview while it moves, else the saved layout. */
  offsetsOf: (fieldId: string) => SlotOffsets;
  /** Field id being dragged right now, if any. */
  draggingId: string | null;
  /** Pointer handlers for anything on the page that moves this slot. */
  bind: (slot: SignatureSlot, onClick?: (event: MouseEvent<Element>) => void) => DragBindings;
}

/**
 * Mouse/touch dragging of signature and date slots, for this contract only.
 *
 * Moves are expressed exactly as the finalize contract v2 wants them: `dx`
 * (positive = right) and `dy` (positive = UP -- screen y grows downwards, so
 * the pointer's travel is negated), snapped to 0.5 pt, clamped to ±200 pt and
 * to the page. The preview is live; the layout is written once, on release.
 * A press that travels less than 3 px stays an ordinary click.
 */
export function useSlotDrag({ layout, update, scale, pageSizeOf, enabled = true }: Options): SlotDrag {
  const [preview, setPreview] = useState<(SlotOffsets & { fieldId: string }) | null>(null);
  const session = useRef<Session | null>(null);
  const suppressClick = useRef(false);

  // Handlers read the latest values through a ref, so their identity does not
  // have to change on every render or every pointer move.
  const latest = useRef({ layout, update, scale, pageSizeOf, enabled });
  useEffect(() => {
    latest.current = { layout, update, scale, pageSizeOf, enabled };
  });

  const offsetsOf = useCallback(
    (fieldId: string): SlotOffsets =>
      preview && preview.fieldId === fieldId
        ? { dx: preview.dx, dy: preview.dy }
        : {
            dx: layout.slot_offsets_x_pt[fieldId] ?? 0,
            dy: layout.slot_offsets_pt[fieldId] ?? 0,
          },
    [preview, layout],
  );

  const bind = useCallback(
    (slot: SignatureSlot, onClick?: (event: MouseEvent<Element>) => void): DragBindings => ({
      onPointerDown: (event) => {
        suppressClick.current = false;
        const { enabled: on, layout: current } = latest.current;
        if (!on || !slot.bbox) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        const start = {
          dx: current.slot_offsets_x_pt[slot.field_id] ?? 0,
          dy: current.slot_offsets_pt[slot.field_id] ?? 0,
        };
        session.current = {
          fieldId: slot.field_id,
          slot,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          start,
          last: start,
          dragging: false,
        };
      },
      onPointerMove: (event) => {
        const active = session.current;
        if (!active || active.pointerId !== event.pointerId || !active.slot.bbox) return;
        const movedX = event.clientX - active.startX;
        const movedY = event.clientY - active.startY;
        if (!active.dragging) {
          if (Math.hypot(movedX, movedY) < DRAG_THRESHOLD_PX) return;
          active.dragging = true;
          try {
            event.currentTarget.setPointerCapture(event.pointerId);
          } catch {
            /* capture is a nicety: the drag still works without it */
          }
        }
        event.preventDefault();

        const { layout: current, scale: renderScale, pageSizeOf: sizeOf } = latest.current;
        const bbox = active.slot.bbox;
        // px -> pt through the render scale (`lib/viewport.ts`); screen y down
        // = offset down (dy is positive UP).
        const page = sizeOf(active.slot.page);
        const bounds = page
          ? slotOffsetBounds({ kind: active.slot.kind, bbox, rule_y: active.slot.rule_y }, current, page)
          : { dx: null, dy: null };
        const dx = clampToRange(snapOffset(active.start.dx + pxToPt(movedX, renderScale)), bounds.dx);
        const dy = clampToRange(snapOffset(active.start.dy - pxToPt(movedY, renderScale)), bounds.dy);

        active.last = { dx, dy };
        setPreview({ fieldId: active.fieldId, dx, dy });
      },
      onPointerUp: (event) => {
        const active = session.current;
        if (!active || active.pointerId !== event.pointerId) return;
        session.current = null;
        if (!active.dragging) return;
        try {
          event.currentTarget.releasePointerCapture(event.pointerId);
        } catch {
          /* already released */
        }
        // The click that follows this release belongs to the drag, not to the slot.
        suppressClick.current = true;
        window.setTimeout(() => {
          suppressClick.current = false;
        }, 0);
        const { dx, dy } = active.last;
        latest.current.update((current) => withSlotOffset(current, active.fieldId, { dx, dy }));
        setPreview(null);
      },
      onPointerCancel: (event) => {
        const active = session.current;
        if (!active || active.pointerId !== event.pointerId) return;
        session.current = null;
        setPreview(null);
      },
      onClick: (event) => {
        // Nothing on a slot may fall through to the page's click-to-place.
        event.stopPropagation();
        if (suppressClick.current) {
          suppressClick.current = false;
          event.preventDefault();
          return;
        }
        onClick?.(event);
      },
      onDoubleClick: (event) => {
        event.stopPropagation();
        if (!latest.current.enabled) return;
        latest.current.update((current) =>
          withSlotOffset(current, slot.field_id, { dx: 0, dy: 0 }),
        );
      },
    }),
    [],
  );

  return { offsetsOf, draggingId: preview?.fieldId ?? null, bind };
}
