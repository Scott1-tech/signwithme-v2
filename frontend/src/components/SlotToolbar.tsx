import type { PxBox } from '../lib/viewport';
import type { SignatureSlot } from '../types/comparison';
import '../styles/signing.css';

/**
 * The little panel that appears beside a slot on the page.
 *
 * WHY IT NEVER FIGHTS THE DRAG
 * ----------------------------
 * `useSlotDrag` turns a press on the slot box into a move once the pointer has
 * travelled 3 px, captures the pointer, and resets the slot on a double-click.
 * All of that starts on the slot's own surface, so this panel must never sit on
 * top of it:
 *
 *   * it is anchored to the slot box but drawn entirely OUTSIDE it -- above it,
 *     or below it when the slot is within `FLIP_BELOW_PX` of the top of the
 *     page (see `.slot-tools--above/--below` in `styles/signing.css`);
 *   * it is a sibling of the slot inside `.pdf-overlay`, not a child of it, so
 *     it cannot swallow a press meant for the slot;
 *   * it stops `pointerdown` / `click` / `dblclick` from bubbling, so a press
 *     on a button here starts no drag and -- just as important -- never reaches
 *     the page's click-to-place handler, which would drop a free signature;
 *   * the caller hides it outright while that slot is being dragged.
 *
 * The consequence to keep in mind when changing the layout: the centre of the
 * slot box must stay clickable. `tests/ui/_harness.py` presses exactly there.
 */

/** A slot whose top edge is nearer the page top than this gets its panel below. */
const FLIP_BELOW_PX = 44;

interface Props {
  slot: SignatureSlot;
  /** The slot's own box on this page, CSS pixels, page-local. */
  box: PxBox;
  /** A signature is already placed on this (signature) slot. */
  signed: boolean;
  /** ISO value of this (date) slot; empty string when nothing is chosen. */
  dateValue: string;
  busy: boolean;
  /** Signing is blocked (not approved / critical issues outstanding). */
  blocked: boolean;
  onSign: () => void;
  onReplace: () => void;
  onRemove: () => void;
  onDateChange: (value: string) => void;
  onEnter: () => void;
  onLeave: () => void;
  /** This signature slot was resized with its corner handle (contract v3). */
  hasOwnSize?: boolean;
  /** Give that slot back to the contract-wide `Signature size` slider. */
  onResetSize?: () => void;
}

export function SlotToolbar({
  slot,
  box,
  signed,
  dateValue,
  busy,
  blocked,
  onSign,
  onReplace,
  onRemove,
  onDateChange,
  onEnter,
  onLeave,
  hasOwnSize = false,
  onResetSize,
}: Props) {
  const below = box.top < FLIP_BELOW_PX;
  const disabled = busy || blocked;
  // Only offered once the corner handle has actually been used: an "auto size"
  // button on a slot that is already automatic says nothing.
  const sizeReset = slot.kind === 'signature' && hasOwnSize && onResetSize ? (
    <>
      <span className="slot-tools__divider" aria-hidden="true" />
      <button
        type="button"
        className="slot-tools__button"
        aria-label={`${slot.label}: use the global signature size`}
        title="Back to the contract-wide signature size"
        disabled={busy}
        onClick={onResetSize}
      >
        Auto size
      </button>
    </>
  ) : null;

  return (
    <div
      className={`slot-tools slot-tools--${below ? 'below' : 'above'}`}
      style={{ left: box.left, top: below ? box.top + box.height : box.top }}
      data-slot-tools={slot.field_id}
      role="toolbar"
      aria-label={`${slot.label}: quick actions`}
      onPointerEnter={onEnter}
      onPointerLeave={onLeave}
      onFocusCapture={onEnter}
      onBlurCapture={onLeave}
      // Nothing that happens in here is a gesture on the document.
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
    >
      {slot.kind === 'signature' ? (
        signed ? (
          <>
            <button
              type="button"
              className="slot-tools__button"
              aria-label={`${slot.label}: replace signature`}
              disabled={disabled || !slot.bbox}
              onClick={onReplace}
            >
              Replace
            </button>
            <span className="slot-tools__divider" aria-hidden="true" />
            <button
              type="button"
              className="slot-tools__button slot-tools__button--danger"
              aria-label={`${slot.label}: remove signature`}
              disabled={busy}
              onClick={onRemove}
            >
              Remove
            </button>
            {sizeReset}
          </>
        ) : (
          <>
            <button
              type="button"
              className="slot-tools__button slot-tools__button--primary"
              aria-label={`${slot.label}: sign`}
              disabled={disabled || !slot.bbox}
              onClick={onSign}
            >
              Sign
            </button>
            {sizeReset}
          </>
        )
      ) : (
        <>
          <input
            type="date"
            className="slot-tools__date"
            aria-label={`${slot.label}: set date`}
            value={dateValue}
            disabled={busy}
            onChange={(event) => onDateChange(event.target.value)}
          />
          {/* No "Today" shortcut: a date is only ever the one picked in the
              box above (no automatic date -- see `lib/signing.ts`). */}
          {dateValue && (
            <button
              type="button"
              className="slot-tools__button slot-tools__button--danger"
              aria-label={`${slot.label}: clear date`}
              disabled={busy}
              onClick={() => onDateChange('')}
            >
              Clear
            </button>
          )}
        </>
      )}
    </div>
  );
}
