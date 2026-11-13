import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import type { SignatureImageEntry } from '../hooks/useSignatureImages';
import { freeSignatureRect, type FinalizeLayout } from '../lib/layout';
import { ptRectToPxBox } from '../lib/viewport';
import type { Signature } from '../types/contract';
import type { BBox } from '../types/field';

/** A resized box is kept within this many times its size when the drag began. */
const RESIZE_MIN_FACTOR = 0.3;
const RESIZE_MAX_FACTOR = 4;
/** Smallest width a signature may be given, in PDF points. */
const RESIZE_MIN_WIDTH_PT = 12;

/** Said in one place, so the interface and the report cannot drift apart. */
export const SIGNATURE_LOCK_NOTE =
  'A placed signature is stored on the server, not in this browser: it cannot be dragged, ' +
  'and Undo does not reach it. Remove it and place it again to move it.';

interface Props {
  /** Free signatures (no template slot) on this page. */
  signatures: Signature[];
  /** Their images, by signature id, as `useSignatureImages` holds them. */
  signatureImages: ReadonlyMap<string, SignatureImageEntry>;
  scale: number;
  /** The one picked from the Objects list, if it is on this page. */
  selectedId: string | null;
  /** True while a signature request is in flight: its button must not repeat. */
  busy: boolean;
  onRemove: (signatureId: string) => void;
  /**
   * The corner handle: called once, on release, with the new box (top-left
   * corner unchanged, proportions kept). Absent: no handle is drawn.
   */
  onResize?: (signatureId: string, bbox: BBox) => void;
  /** W5: which of these rows are initials (drawn apart, `data-signature-kind="initials"`). */
  initialIds?: ReadonlySet<string>;
  /**
   * The contract-wide signature offset and size: the PDF builder applies both
   * to a free signature too (`freeSignatureRect`), so the preview does.
   */
  layout?: Pick<FinalizeLayout, 'signature_offset_pt' | 'signature_scale'>;
}

const NO_INITIALS: ReadonlySet<string> = new Set();
const NEUTRAL_LAYOUT: Pick<FinalizeLayout, 'signature_offset_pt' | 'signature_scale'> = {
  signature_offset_pt: 0,
  signature_scale: 1,
};
const fmt = (value: number) => value.toFixed(2);

/**
 * Free signatures: rows created by `POST /api/contracts/{id}/signatures` with
 * `field_id: null`, drawn as `.swm-sig` -- the ONE way a free signature is
 * drawn in the workspace (W1; the signing page's `.placed-signature` for free
 * placements is gone, `.placed-signature` now only ever means "in a slot").
 *
 * Server rows: drawn, removable, and deliberately not draggable -- moving one
 * would mean deleting and re-placing it behind the user's back, which an audit
 * trail must not be asked to explain. Nor are they on the undo stack.
 *
 * WHERE THE INK IS DRAWN (fix 2026-09-27)
 * ---------------------------------------
 * The `.swm-sig` box is the row's own bbox (hover frame, selection, the
 * button under it). The picture inside it is NOT centred in that box any more
 * (`object-fit: contain` put it up to 6 pt above where the PDF prints it): it
 * is placed at `freeSignatureRect` -- the PDF builder's rule for a free
 * signature, ink bottom and all -- so what the page shows is what prints.
 */
export function FreeSignatureLayer({
  signatures,
  signatureImages,
  scale,
  selectedId,
  busy,
  onRemove,
  onResize,
  initialIds = NO_INITIALS,
  layout = NEUTRAL_LAYOUT,
}: Props) {
  // The box being resized, live while the corner is held; saved on release.
  const [live, setLive] = useState<{ id: string; bbox: BBox } | null>(null);
  const start = useRef<{ id: string; bbox: BBox; x: number } | null>(null);

  const beginResize = (event: ReactPointerEvent<HTMLElement>, signature: Signature) => {
    if (busy || !onResize) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    start.current = { id: signature.id, bbox: signature.bbox, x: event.clientX };
    setLive({ id: signature.id, bbox: signature.bbox });
  };
  const moveResize = (event: ReactPointerEvent<HTMLElement>) => {
    const from = start.current;
    if (!from) return;
    event.stopPropagation();
    const ratio = from.bbox.height / from.bbox.width;
    const wanted = from.bbox.width + (event.clientX - from.x) / scale;
    const width = Math.min(
      from.bbox.width * RESIZE_MAX_FACTOR,
      Math.max(RESIZE_MIN_WIDTH_PT, from.bbox.width * RESIZE_MIN_FACTOR, wanted),
    );
    const tidy = (value: number) => Math.round(value * 100) / 100;
    setLive({ id: from.id, bbox: { ...from.bbox, width: tidy(width), height: tidy(width * ratio) } });
  };
  const endResize = (event: ReactPointerEvent<HTMLElement>) => {
    const from = start.current;
    if (!from) return;
    event.stopPropagation();
    start.current = null;
    const result = live && live.id === from.id ? live.bbox : null;
    setLive(null);
    if (result && onResize && (result.width !== from.bbox.width || result.height !== from.bbox.height)) {
      onResize(from.id, result);
    }
  };

  return (
    <>
      {signatures.map((original) => {
        // While its corner is held, a signature is drawn at the live size.
        const signature =
          live && live.id === original.id ? { ...original, bbox: live.bbox } : original;
        const box = ptRectToPxBox(signature.bbox, scale);
        const image = signatureImages.get(signature.id);
        const selected = signature.id === selectedId;
        // W5: an initial is a small free signature (see `lib/initials.ts`).
        const initials = initialIds.has(signature.id);
        const loaded = typeof image === 'object' ? image : null;
        // The ink as the PDF prints it, relative to the box (whose 1 px border
        // is `--swm-sig-border`: an absolute child is placed from inside it).
        const ink = loaded ? freeSignatureRect(signature.bbox, layout, loaded) : null;
        return (
          <div
            key={signature.id}
            className={`swm-sig${initials ? ' swm-sig--initials' : ''}${selected ? ' is-selected' : ''}`}
            style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
            data-testid={`ann-sig-${signature.id}`}
            data-signature-id={signature.id}
            data-signature-kind={initials ? 'initials' : 'signature'}
            data-page={signature.page_number}
            data-x-pt={signature.bbox.x.toFixed(2)}
            data-y-pt={signature.bbox.y.toFixed(2)}
            data-w-pt={signature.bbox.width.toFixed(2)}
            data-h-pt={signature.bbox.height.toFixed(2)}
            title={SIGNATURE_LOCK_NOTE}
            // Nothing that happens on a signature may reach the page's
            // click-to-place underneath it.
            onClick={(event) => event.stopPropagation()}
          >
            {loaded && ink ? (
              <img
                className="swm-sig__image"
                src={loaded.url}
                alt={initials ? 'Initials placed on this page' : 'Signature placed on this page'}
                style={{
                  left: `calc(${(ink.x - signature.bbox.x) * scale}px - var(--swm-sig-border))`,
                  top: `calc(${(ink.y - signature.bbox.y) * scale}px - var(--swm-sig-border))`,
                  width: ink.width * scale,
                  height: ink.height * scale,
                }}
                // The printed geometry, in PDF points (the checks read these).
                data-rect-pt={`${fmt(ink.x)},${fmt(ink.y)},${fmt(ink.width)},${fmt(ink.height)}`}
                data-ink-bottom-pt={fmt(ink.bottom)}
              />
            ) : (
              <span className="swm-sig__pending" aria-hidden="true">
                {image === 'failed' ? 'image unavailable' : '…'}
              </span>
            )}
            <button
              type="button"
              className="swm-sig__remove"
              data-testid={`ann-sig-remove-${signature.id}`}
              disabled={busy}
              aria-label={
                initials
                  ? `Remove these initials. ${SIGNATURE_LOCK_NOTE}`
                  : `Remove this signature and place it again. ${SIGNATURE_LOCK_NOTE}`
              }
              title={SIGNATURE_LOCK_NOTE}
              onClick={(event) => {
                event.stopPropagation();
                onRemove(signature.id);
              }}
            >
              {initials ? 'Remove' : 'Remove and place again'}
            </button>
            {onResize && (
              <span
                className="swm-sig__resize"
                role="slider"
                tabIndex={-1}
                aria-label="Drag to resize this signature"
                aria-valuenow={Math.round(signature.bbox.width)}
                title="Drag to resize"
                data-testid={`ann-sig-resize-${signature.id}`}
                onPointerDown={(event) => beginResize(event, original)}
                onPointerMove={moveResize}
                onPointerUp={endResize}
                onPointerCancel={endResize}
                onClick={(event) => event.stopPropagation()}
              />
            )}
          </div>
        );
      })}
    </>
  );
}
