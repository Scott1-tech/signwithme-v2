import { useMemo } from 'react';
import type { PageHit } from '../hooks/usePdfSearch';
import type { PageSize } from '../hooks/usePdfViewer';
import { ptRectToPxBox, rotationTransform, type Quarter } from '../lib/viewport';

/**
 * The search marks for one page.
 *
 * Deliberately **below** `.pdf-overlay` and completely inert:
 *
 * * it is rendered as the page's own child *before* the overlay, so the slot
 *   boxes, the placed signatures and the dates all paint on top of it -- a
 *   highlight can never hide the thing the reader is about to sign;
 * * nothing in here takes a pointer event (`pointer-events: none` on the
 *   layer and on every mark), so dragging a slot, clicking one, or
 *   click-to-place all behave exactly as they do with the search closed;
 * * it draws in the page's own unrotated point space and, when the reader has
 *   turned the page, is transformed by the very same affine map as the
 *   overlay -- one map, applied to a layer, rather than per-mark maths that
 *   could drift apart from the overlay's.
 */

interface Props {
  /** Matches on this page, with their index in the full result list. */
  hits: PageHit[];
  /** Index of the match the reader is on, or -1. */
  activeIndex: number;
  /** CSS pixels per PDF point. */
  scale: number;
  /** The reader's quarter turn for this page. */
  rotation: Quarter;
  /** The page's **unrotated** size in CSS pixels. */
  size: PageSize;
}

export function PdfSearchMarks({ hits, activeIndex, scale, rotation, size }: Props) {
  const style = useMemo(() => {
    if (rotation === 0) return undefined;
    return {
      left: 0,
      top: 0,
      right: 'auto' as const,
      bottom: 'auto' as const,
      width: size.width,
      height: size.height,
      transform: rotationTransform(rotation, size.width, size.height),
      transformOrigin: '0 0',
    };
  }, [rotation, size.width, size.height]);

  if (hits.length === 0) return null;

  return (
    <div className="pdf-search-layer" aria-hidden="true" style={style}>
      {hits.map(({ hit, index }) =>
        hit.rects.map((rect, part) => {
          const box = ptRectToPxBox(rect, scale);
          return (
            <span
              key={`${index}-${part}`}
              className={`pdf-search-hit${index === activeIndex ? ' pdf-search-hit--active' : ''}`}
              data-search-index={index}
              style={{
                left: box.left,
                top: box.top,
                width: box.width,
                height: box.height,
              }}
            />
          );
        }),
      )}
    </div>
  );
}
