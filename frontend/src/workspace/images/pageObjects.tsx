import { useEffect, useRef, useState } from 'react';
import { documentApi, type DocumentOp, type PageImageOut, type Rect } from '../../api/document';
import { frameLayerStyle, ptToPx, UNROTATED, type PageFrame } from '../../lib/viewport';

/**
 * F2: the page's OWN objects the session changes that are not text:
 *
 *  * `PageImageLayer` -- with «Delete original image» armed, a frame over
 *    every picture of the PDF (`GET …/pages/{uid}/images`); hover shows it,
 *    a click is an `image.remove` op. A picture already removed in the draft
 *    is shown struck through («Removed when you save»).
 *  * `PendingOpsLayer` -- what the draft will do, drawn over the page until
 *    it is saved: a redaction as the black box it becomes, a removed picture
 *    as a hatched box. Each with its own ✕ (the op goes, one undo step).
 *
 * ROTFIX: both speak the ENGINE's space (unrotated page); on a page with its
 * own `/Rotate` the layer is turned onto the box by `frameLayerStyle`.
 */

export function usePageImages(contractId: string | undefined, versionNo: number | null, uids: readonly string[], active: boolean) {
  const [images, setImages] = useState<Map<string, PageImageOut[]>>(new Map());
  const asked = useRef(new Set<string>());
  useEffect(() => {
    asked.current = new Set();
    setImages(new Map());
  }, [contractId, versionNo]);
  useEffect(() => {
    if (!active || !contractId || versionNo === null) return;
    uids.forEach((uid) => {
      const key = `${versionNo}|${uid}`;
      if (asked.current.has(key)) return;
      asked.current.add(key);
      documentApi
        .pageImages(contractId, versionNo, uid)
        .then((list) => setImages((current) => new Map(current).set(uid, Array.isArray(list) ? list : [])))
        .catch(() => setImages((current) => new Map(current).set(uid, [])));
    });
  }, [active, contractId, versionNo, uids]);
  return images;
}

const box = (rect: Rect, scale: number) => ({
  left: ptToPx(rect.x_pt, scale),
  top: ptToPx(rect.y_pt, scale),
  width: ptToPx(rect.w_pt, scale),
  height: ptToPx(rect.h_pt, scale),
});

export function PageImageLayer({
  page,
  scale,
  frame = null,
  images,
  removed,
  onRemove,
}: {
  page: number;
  scale: number;
  frame?: PageFrame | null;
  images: PageImageOut[];
  removed: ReadonlySet<number>;
  onRemove: (image: PageImageOut) => void;
}) {
  return (
    <div className="ws-page-images" data-testid={`page-images-${page}`} data-count={images.length} data-space="unrotated" style={frameLayerStyle(frame, UNROTATED, scale)}>
      {images
        .filter((image) => !removed.has(image.xref))
        .map((image) => (
          <button
            key={image.xref}
            type="button"
            className="ws-page-image"
            data-testid={`page-image-${page}-${image.xref}`}
            data-xref={image.xref}
            aria-label="Remove this picture from the document (on Save)"
            title="Delete this picture of the original document — it is removed from the file when you save"
            style={box(image.bbox, scale)}
            onClick={(event) => {
              event.stopPropagation();
              onRemove(image);
            }}
          />
        ))}
    </div>
  );
}

export function PendingOpsLayer({
  page,
  scale,
  frame = null,
  ops,
  onDrop,
}: {
  page: number;
  scale: number;
  frame?: PageFrame | null;
  ops: DocumentOp[];
  onDrop: (op: DocumentOp) => void;
}) {
  if (ops.length === 0) return null;
  return (
    <div className="ws-pending-ops" data-testid={`pending-ops-${page}`} data-space="unrotated" style={frameLayerStyle(frame, UNROTATED, scale)}>
      {ops.map((op) => {
        if (op.kind === 'redact') {
          return op.rects.map((rect, index) => (
            <span
              key={`${op.id}-${index}`}
              className="ws-redact-pending"
              data-testid={`redact-${op.id}`}
              data-rect-pt={`${rect.x_pt},${rect.y_pt},${rect.w_pt},${rect.h_pt}`}
              title="Redaction: removed from the file when you save"
              style={box(rect, scale)}
            >
              {index === 0 && (
                <button
                  type="button"
                  className="ws-pending-ops__drop"
                  data-testid={`redact-drop-${op.id}`}
                  aria-label="Undo this redaction"
                  title="Undo this redaction"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDrop(op);
                  }}
                >
                  ×
                </button>
              )}
            </span>
          ));
        }
        if (op.kind === 'image.remove') {
          return (
            <span
              key={op.id}
              className="ws-image-removed"
              data-testid={`image-removed-${op.xref}`}
              title="This picture is removed from the file when you save"
              style={box(op.bbox, scale)}
            >
              <span className="ws-image-removed__label">Removed when you save</span>
              <button
                type="button"
                className="ws-pending-ops__drop"
                data-testid={`image-removed-drop-${op.xref}`}
                aria-label="Keep this picture"
                title="Keep this picture"
                onClick={(event) => {
                  event.stopPropagation();
                  onDrop(op);
                }}
              >
                ×
              </button>
            </span>
          );
        }
        return null;
      })}
    </div>
  );
}
