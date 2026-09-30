import { formatScale, type ScaleRange } from '../lib/layout';
import type { PxBox } from '../lib/viewport';
import type { ResizeBindings } from '../hooks/useSlotResize';
import type { SignatureSlot } from '../types/comparison';
import '../styles/signing.css';

/**
 * The corner grip that resizes one signature (D7).
 *
 * WHERE IT SITS -- and why that is not negotiable
 * ----------------------------------------------
 * It is centred on the mark's TOP-RIGHT corner and is a sibling of the mark
 * inside `.pdf-overlay`, never a child of it. Two consequences the rest of the
 * page depends on:
 *
 *   * the BOTTOM-LEFT corner carries no chrome at all, which is the corner the
 *     placement maths anchors on (`data-left-pt` / `data-bottom-pt`), and
 *   * the middle of the slot box stays the slot's own surface, so
 *     `useSlotDrag`'s 3 px threshold, its pointer capture and the double-click
 *     reset all keep working -- `tests/ui/_harness.py` presses exactly there.
 *
 * Everything that makes the press a resize rather than a move lives in
 * `hooks/useSlotResize.ts`; this file is the button and its badge.
 */

interface Props {
  slot: SignatureSlot;
  /** The mark's box on this page, CSS pixels, page-local. */
  box: PxBox;
  /** The size it prints at right now (live while dragging). */
  scale: number;
  /** What still fits on the page, for the title and the disabled ends. */
  bounds: ScaleRange;
  /** This slot has a size of its own, i.e. the global slider does not move it. */
  own: boolean;
  /** Being dragged right now. */
  active: boolean;
  disabled?: boolean;
  bindings: ResizeBindings;
  onEnter: () => void;
  onLeave: () => void;
}

export function SlotResizeHandle({
  slot,
  box,
  scale,
  bounds,
  own,
  active,
  disabled,
  bindings,
  onEnter,
  onLeave,
}: Props) {
  const percent = formatScale(scale);
  const limit = bounds ? ` (up to ${formatScale(bounds[1])} on this page)` : '';
  return (
    <button
      type="button"
      className={`slot-resize${active ? ' is-resizing' : ''}${own ? ' slot-resize--own' : ''}`}
      style={{ left: box.left + box.width, top: box.top }}
      data-slot-resize={slot.field_id}
      data-field-id={slot.field_id}
      data-scale={scale.toFixed(2)}
      aria-label={`${slot.label}: resize signature`}
      title={`${slot.label}: drag to resize (${percent})${limit}. The bottom-left corner stays put.`}
      disabled={disabled}
      onPointerEnter={onEnter}
      onPointerLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      {...bindings}
    >
      <span className="slot-resize__grip" aria-hidden="true" />
      <span className="slot-resize__value" data-testid={`slot-resize-value-${slot.field_id}`}>
        {percent}
      </span>
    </button>
  );
}
