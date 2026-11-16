import { formatForContract } from '../components/DatePicker';
import type { SignatureImageEntry } from '../hooks/useSignatureImages';
import { DATE_FONT_CSS, dateBaseline, signatureRect, type FinalizeLayout } from '../lib/layout';
import type { SignatureSlot } from '../types/comparison';
import type { Signature } from '../types/contract';
import type { BBox } from '../types/field';

/**
 * One mark the PDF editor draws over a page, in PDF points with a top-left
 * origin on the unrotated page (the app's own coordinates). The editor turns
 * them into pixels with its page viewport (`editor/js/sign-overlays.js`).
 */
export type EditorOverlay =
  | {
      kind: 'signature';
      key: string;
      page: number;
      x: number;
      y: number;
      width: number;
      height: number;
      /** Blob URL of the signature image (same origin as the editor), or null while loading. */
      src: string | null;
    }
  | {
      kind: 'date';
      key: string;
      page: number;
      x: number;
      /** The text baseline. */
      y: number;
      text: string;
      fontSize: number;
      fontFamily: string;
      bold: boolean;
      italic: boolean;
    };

interface Input {
  signatures: readonly Signature[];
  signatureImages: ReadonlyMap<string, SignatureImageEntry>;
  slotById: ReadonlyMap<string, SignatureSlot>;
  dateSlots: readonly SignatureSlot[];
  dateValues: Record<string, string>;
  layout: FinalizeLayout;
  offsetsOf: (fieldId: string) => { dx: number; dy: number };
  scaleOf: (fieldId: string | undefined) => number;
}

/** The placed signatures and filled dates, where the signing view draws them (`SlotLayer`). */
export function buildEditorOverlays(input: Input): EditorOverlay[] {
  const { layout, offsetsOf, scaleOf } = input;
  const overlays: EditorOverlay[] = [];

  input.signatures.forEach((signature) => {
    const slot = signature.field_id ? input.slotById.get(signature.field_id) : undefined;
    const anchor = slot && slot.kind === 'signature' && slot.bbox ? slot : null;
    const image = input.signatureImages.get(signature.id);
    const loaded = image && typeof image === 'object' ? image : null;
    let rect: { x: number; y: number; width: number; height: number };
    if (anchor) {
      const { dx, dy } = offsetsOf(anchor.field_id);
      rect = signatureRect(
        anchor.bbox as BBox,
        anchor.rule_y,
        layout.signature_offset_pt + dy,
        scaleOf(anchor.field_id),
        loaded,
        dx,
      );
    } else {
      rect = signature.bbox;
    }
    overlays.push({
      kind: 'signature',
      key: `sig:${signature.id}`,
      page: signature.page_number,
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
      src: loaded ? loaded.url : null,
    });
  });

  input.dateSlots.forEach((slot) => {
    const value = input.dateValues[slot.field_id];
    if (!value || !slot.bbox || !slot.page) return;
    const bbox = slot.bbox as BBox;
    const { dx, dy } = offsetsOf(slot.field_id);
    overlays.push({
      kind: 'date',
      key: `date:${slot.field_id}`,
      page: slot.page,
      x: bbox.x + dx,
      y: dateBaseline(bbox, slot.rule_y, layout.date_offset_pt + dy),
      text: formatForContract(value),
      fontSize: layout.date_font_size,
      fontFamily: DATE_FONT_CSS[layout.date_font],
      bold: Boolean(layout.date_bold),
      italic: Boolean(layout.date_italic),
    });
  });

  return overlays;
}
