import type { SignatureImageEntry } from '../hooks/useSignatureImages';
import type { SlotDrag } from '../hooks/useSlotDrag';
import type { SlotResize } from '../hooks/useSlotResize';
import { formatForContract } from '../components/DatePicker';
import { SlotResizeHandle } from '../components/SlotResizeHandle';
import { SlotToolbar } from '../components/SlotToolbar';
import {
  DATE_FONT_CSS,
  dateBaseline,
  hasOwnScale,
  signatureRect,
  slotBoxRect,
  type FinalizeLayout,
} from '../lib/layout';
import { ptRectToPxBox, ptToPx } from '../lib/viewport';
import type { SignatureSlot } from '../types/comparison';
import type { Signature } from '../types/contract';
import type { BBox } from '../types/field';

interface Props {
  pageNumber: number;
  sigSlotsByPage: ReadonlyMap<number, SignatureSlot[]>;
  dateSlotsByPage: ReadonlyMap<number, SignatureSlot[]>;
  /** Signatures sitting in a slot, by page. Free ones are `FreeSignatureLayer`'s. */
  signaturesByPage: ReadonlyMap<number, Signature[]>;
  slotById: ReadonlyMap<string, SignatureSlot>;
  dateValues: Record<string, string>;
  signedFieldIds: ReadonlySet<string>;
  signatureForSlot: (fieldId: string) => Signature | null;
  signatureImages: ReadonlyMap<string, SignatureImageEntry>;
  layout: FinalizeLayout;
  drag: SlotDrag;
  resize: SlotResize;
  /** CSS pixels per PDF point, from the viewer. */
  scale: number;
  focusedSlotId: string | null;
  /** The slot the mini panel is about, if any (on whichever page). */
  toolsTarget: SignatureSlot | null;
  busy: boolean;
  blocked: boolean;
  /** A press on an unsigned signature slot opens the chooser (the Signature
   *  tool); otherwise it only focuses the slot (Select). */
  pressOpensChooser: boolean;
  onStartSlot: (slot: SignatureSlot) => void;
  onReplaceSlot: (slot: SignatureSlot) => Promise<void>;
  onRemoveSignature: (signatureId: string) => Promise<boolean>;
  onDateChange: (fieldId: string, value: string) => void;
  onFocusSlot: (fieldId: string) => void;
  onShowTools: (fieldId: string) => void;
  onHideTools: () => void;
}

/**
 * Everything drawn on ONE page of the document.
 *
 * The viewer lays every page out at once and calls this for each of them, so
 * nothing in here may ask which page is "current": `pageNumber` is the only
 * page this fragment knows about, and `lib/viewport.ts` keeps the maths
 * inside it. The fragment's children become direct children of that page's
 * `.pdf-overlay`.
 */
export function SlotLayer({
  pageNumber,
  sigSlotsByPage,
  dateSlotsByPage,
  signaturesByPage,
  slotById,
  dateValues,
  signedFieldIds,
  signatureForSlot,
  signatureImages,
  layout,
  drag,
  resize,
  scale,
  focusedSlotId,
  toolsTarget,
  busy,
  blocked,
  pressOpensChooser,
  onStartSlot: startSlot,
  onReplaceSlot: replaceSlot,
  onRemoveSignature: removeSignature,
  onDateChange: setDateValue,
  onFocusSlot: focusSlot,
  onShowTools: showSlotTools,
  onHideTools: hideSlotTools,
}: Props) {
  /** Pointer/focus handlers that put a mark's slot under the mini panel. */
  const toolsHandlers = (fieldId: string) => ({
    onPointerEnter: () => showSlotTools(fieldId),
    onPointerLeave: hideSlotTools,
    onFocus: () => showSlotTools(fieldId),
    onBlur: hideSlotTools,
  });

  /**
   * The box one signature mark occupies on its page, in CSS pixels: the exact
   * rectangle drawn below (`signatureRect`), so the corner handle sits on the
   * ink that is really there and not on the slot's raw bbox. Its bottom-left
   * corner is the anchor the resize keeps still.
   */
  const markBoxFor = (slot: SignatureSlot) => {
    const { dx, dy } = drag.offsetsOf(slot.field_id);
    const placed = signatureForSlot(slot.field_id);
    const image = placed ? signatureImages.get(placed.id) : undefined;
    const loaded = image && typeof image === 'object' ? image : null;
    return ptRectToPxBox(
      signatureRect(
        slot.bbox as BBox,
        slot.rule_y,
        layout.signature_offset_pt + dy,
        resize.scaleOf(slot.field_id),
        loaded,
        dx,
      ),
      scale,
    );
  };

  const slotsOnPage = sigSlotsByPage.get(pageNumber) ?? [];
  const datesOnPage = dateSlotsByPage.get(pageNumber) ?? [];
  const signaturesOnPage = signaturesByPage.get(pageNumber) ?? [];
  if (slotsOnPage.length === 0 && datesOnPage.length === 0 && signaturesOnPage.length === 0) {
    return null;
  }
  const filledDates = datesOnPage.filter((slot) => dateValues[slot.field_id]);

  return (
    <>
      {/* Detected signature slots: clicking one places the signature at the
          template's coordinates rather than wherever the pointer landed. */}
      {slotsOnPage.map((slot) => {
        const signed = signedFieldIds.has(slot.field_id);
        const { dx, dy } = drag.offsetsOf(slot.field_id);
        const rect = slotBoxRect(
          slot.bbox as BBox,
          slot.rule_y,
          layout.signature_offset_pt + dy,
          dx,
        );
        return (
          <button
            key={slot.field_id}
            type="button"
            className={`slot slot--draggable${signed ? ' slot--filled' : ''}${drag.draggingId === slot.field_id ? ' is-dragging' : ''}${focusedSlotId === slot.field_id ? ' slot--active' : ''}`}
            style={ptRectToPxBox(rect, scale)}
            title={`${slot.label} — drag to move, double-click to reset`}
            data-field-id={slot.field_id}
            data-slot-kind="signature"
            data-left-pt={rect.x.toFixed(2)}
            data-bottom-pt={rect.bottom.toFixed(2)}
            {...drag.bind(slot, () => {
              // Only the Signature tool signs on a press; with Select a press
              // picks the slot (its card, its mini panel) and nothing more.
              if (signed || !pressOpensChooser) {
                focusSlot(slot.field_id);
                return;
              }
              startSlot(slot);
            })}
            {...toolsHandlers(slot.field_id)}
          >
            {/* A signed slot shows the real image (drawn below), not a badge. */}
            {signed ? (
              <span className="visually-hidden">{`${slot.label}: signed`}</span>
            ) : (
              <span className="slot__label">{slot.label}</span>
            )}
          </button>
        );
      })}

      {/* Date slots: a preview of what will print there, or a reminder that
          nothing has been chosen yet. Clicking jumps to the date picker. */}
      {datesOnPage.map((slot) => {
        const value = dateValues[slot.field_id];
        const { dx, dy } = drag.offsetsOf(slot.field_id);
        const rect = slotBoxRect(slot.bbox as BBox, slot.rule_y, layout.date_offset_pt + dy, dx);
        return (
          <button
            key={slot.field_id}
            type="button"
            className={`slot slot--date slot--draggable${value ? ' slot--filled' : ''}${drag.draggingId === slot.field_id ? ' is-dragging' : ''}${focusedSlotId === slot.field_id ? ' slot--active' : ''}`}
            style={ptRectToPxBox(rect, scale)}
            title={`${slot.label} — drag to move, double-click to reset`}
            data-field-id={slot.field_id}
            data-slot-kind="date"
            data-left-pt={rect.x.toFixed(2)}
            data-bottom-pt={rect.bottom.toFixed(2)}
            {...drag.bind(slot, () => focusSlot(slot.field_id))}
            {...toolsHandlers(slot.field_id)}
          >
            {/* A filled date is drawn as real text below, in the chosen font. */}
            {value ? (
              <span className="visually-hidden">{`${slot.label}: ${formatForContract(value)}`}</span>
            ) : (
              <span className="slot__label">Date needed</span>
            )}
          </button>
        );
      })}

      {/* Placed signatures, as they will print: the server-processed image,
          fitted into bbox × size and standing on the rule (shared contract
          §3). Until the image arrives a dashed box marks the spot. */}
      {signaturesOnPage.map((signature) => {
        const slot = signature.field_id ? slotById.get(signature.field_id) : undefined;
        const anchor = slot && slot.kind === 'signature' && slot.bbox ? slot : null;
        const bbox = anchor?.bbox ?? signature.bbox;
        // Free placements take no per-slot offset at all (no dx, no dy).
        const { dx, dy } = anchor ? drag.offsetsOf(anchor.field_id) : { dx: 0, dy: 0 };
        const offset = layout.signature_offset_pt + dy;
        const image = signatureImages.get(signature.id);
        const loaded = image && typeof image === 'object' ? image : null;
        // A slot with a size of its own uses it (live while its corner handle
        // is being dragged); a free placement only ever gets the global one.
        const rect = signatureRect(
          bbox,
          anchor?.rule_y,
          offset,
          resize.scaleOf(anchor?.field_id),
          loaded,
          dx,
        );
        const style = ptRectToPxBox(rect, scale);
        const movable = anchor
          ? {
              ...drag.bind(anchor, () => focusSlot(anchor.field_id)),
              ...toolsHandlers(anchor.field_id),
              title: `${anchor.label} — drag to move, double-click to reset`,
            }
          : {};
        const dragClass = anchor
          ? ` placed-signature--draggable${drag.draggingId === anchor.field_id ? ' is-dragging' : ''}`
          : '';
        return loaded ? (
          <img
            key={signature.id}
            className={`placed-signature${dragClass}`}
            src={loaded.url}
            alt=""
            draggable={false}
            style={style}
            data-signature-id={signature.id}
            data-field-id={signature.field_id ?? ''}
            data-left-pt={rect.x.toFixed(2)}
            data-bottom-pt={rect.bottom.toFixed(2)}
            {...movable}
          />
        ) : (
          <div
            key={signature.id}
            className={`placed-signature placed-signature--${image === 'failed' ? 'failed' : 'pending'}${dragClass}`}
            style={style}
            data-signature-id={signature.id}
            data-field-id={signature.field_id ?? ''}
            data-left-pt={rect.x.toFixed(2)}
            data-bottom-pt={rect.bottom.toFixed(2)}
            {...movable}
          >
            <span className="slot__label">
              {image === 'failed' ? 'Signature preview unavailable' : 'Loading signature…'}
            </span>
          </div>
        );
      })}

      {/* Chosen dates as text on their baseline, in the chosen font and size.
          One SVG per page: an overlay is positioned against its own page. */}
      {filledDates.length > 0 && (
        <svg className="placed-dates" aria-hidden="true">
          {filledDates.map((slot) => {
            const value = dateValues[slot.field_id];
            const bbox = slot.bbox as BBox;
            const { dx, dy } = drag.offsetsOf(slot.field_id);
            const baseline = dateBaseline(bbox, slot.rule_y, layout.date_offset_pt + dy);
            const x = bbox.x + dx;
            return (
              <text
                key={slot.field_id}
                className={`placed-date--draggable${drag.draggingId === slot.field_id ? ' is-dragging' : ''}`}
                x={ptToPx(x, scale)}
                y={ptToPx(baseline, scale)}
                style={{
                  fontFamily: DATE_FONT_CSS[layout.date_font],
                  fontWeight: layout.date_bold ? 700 : 400,
                  fontStyle: layout.date_italic ? 'italic' : 'normal',
                }}
                fontSize={ptToPx(layout.date_font_size, scale)}
                data-font={layout.date_font}
                data-bold={layout.date_bold ? 'true' : undefined}
                data-italic={layout.date_italic ? 'true' : undefined}
                data-field-id={slot.field_id}
                data-x-pt={x.toFixed(2)}
                data-baseline-pt={baseline.toFixed(2)}
                {...drag.bind(slot, () => focusSlot(slot.field_id))}
                {...toolsHandlers(slot.field_id)}
              >
                {formatForContract(value)}
              </text>
            );
          })}
        </svg>
      )}

      {/* The mini panel for the slot under the pointer. Drawn last so it sits
          above the marks, and positioned clear of its slot's box -- see the
          header of `SlotToolbar.tsx` for why that is not negotiable. */}
      {toolsTarget && toolsTarget.page === pageNumber && (
        <SlotToolbar
          slot={toolsTarget}
          box={ptRectToPxBox(
            slotBoxRect(
              toolsTarget.bbox as BBox,
              toolsTarget.rule_y,
              (toolsTarget.kind === 'signature'
                ? layout.signature_offset_pt
                : layout.date_offset_pt) + drag.offsetsOf(toolsTarget.field_id).dy,
              drag.offsetsOf(toolsTarget.field_id).dx,
            ),
            scale,
          )}
          signed={signedFieldIds.has(toolsTarget.field_id)}
          dateValue={dateValues[toolsTarget.field_id] ?? ''}
          busy={busy}
          blocked={blocked}
          onSign={() => startSlot(toolsTarget)}
          onReplace={() => void replaceSlot(toolsTarget)}
          onRemove={() => {
            const placed = signatureForSlot(toolsTarget.field_id);
            if (placed) void removeSignature(placed.id);
          }}
          onDateChange={(value) => setDateValue(toolsTarget.field_id, value)}
          onEnter={() => showSlotTools(toolsTarget.field_id)}
          onLeave={hideSlotTools}
          hasOwnSize={hasOwnScale(layout, toolsTarget.field_id)}
          onResetSize={() => resize.clear(toolsTarget.field_id)}
        />
      )}

      {/* An unsigned slot has nothing on the page that changes size, so the
          gesture draws what the signature will fill. A signed one needs no
          ghost: its own image is already the live preview. */}
      {toolsTarget &&
        toolsTarget.page === pageNumber &&
        toolsTarget.kind === 'signature' &&
        resize.activeId === toolsTarget.field_id &&
        !signedFieldIds.has(toolsTarget.field_id) && (
          <div
            className="slot-resize-ghost"
            aria-hidden="true"
            style={markBoxFor(toolsTarget)}
            data-field-id={toolsTarget.field_id}
          />
        )}

      {/* The corner grip of the slot the panel is about. Top-right, outside
          the maths: the bottom-left corner it grows from never moves. */}
      {toolsTarget && toolsTarget.page === pageNumber && toolsTarget.kind === 'signature' && (
        <SlotResizeHandle
          slot={toolsTarget}
          box={markBoxFor(toolsTarget)}
          scale={resize.scaleOf(toolsTarget.field_id)}
          bounds={resize.boundsOf(toolsTarget)}
          own={hasOwnScale(layout, toolsTarget.field_id)}
          active={resize.activeId === toolsTarget.field_id}
          bindings={resize.bind(toolsTarget, markBoxFor(toolsTarget))}
          onEnter={() => showSlotTools(toolsTarget.field_id)}
          onLeave={hideSlotTools}
        />
      )}
    </>
  );
}
