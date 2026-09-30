import { memo, useMemo } from 'react';
import { isWhiteout, type Annotation } from '../lib/annotations';
import { isFreeWhiteout } from '../lib/annotationKinds/whiteout';
import { ptToPx } from '../lib/viewport';

interface Props {
  pageNumber: number;
  /** This page's annotations (with any live gesture applied); only the whiteouts are drawn. */
  items: readonly Annotation[] | undefined;
  scale: number;
}

/**
 * The whiteouts of one page, drawn UNDER the slot layer and the free
 * signatures (W5).
 *
 * The PDF builder prints whiteouts first, then every signature (slot or free),
 * then the other annotations (plan §7-4, backend v5 §8.1: "a whiteout never
 * hides a placed signature"). Before W5 the whiteouts were the bottom of the
 * annotation SVG, which sits ABOVE the signatures -- the preview showed a
 * signature covered that the PDF prints on top. Now they are their own layer,
 * right after the drag tools' capture surface and before `IssueLayer` /
 * `SlotLayer` / `FreeSignatureLayer`, so the paint order on screen is the
 * print order.
 *
 * Drawing only (no pointer events, `.swm-ann-layer`): the hit box, the handles
 * and the selection frame of a whiteout stay in `AnnotationLayer`, above
 * everything, so a whiteout under a slot is still selectable.
 *
 * A FREE whiteout (the Whiteout tool's) gets a thin dashed grey edge ON SCREEN
 * (`swm-ann__whiteout--free`) -- white on white paper would be invisible to the
 * person who placed it. The edge is CSS only; the request has no such key and
 * the PDF gets the plain white box. The whiteout of an edited line (WE) keeps
 * its plain look: the new text on top is what the user sees.
 */
export const WhiteoutLayer = memo(function WhiteoutLayer({ pageNumber, items, scale }: Props) {
  const whiteouts = useMemo(() => (items ?? []).filter(isWhiteout), [items]);
  if (whiteouts.length === 0) return null;
  return (
    <svg
      className="swm-ann-layer swm-ann-layer--whiteout"
      data-testid={`ann-whiteouts-${pageNumber}`}
      aria-hidden="true"
    >
      {whiteouts.map((item) => (
        <rect
          key={item.id}
          className={`swm-ann__whiteout${isFreeWhiteout(item) ? ' swm-ann__whiteout--free' : ''}`}
          data-testid={`ann-whiteout-${item.id}`}
          data-ann-id={item.id}
          data-box-pt={`${item.x_pt},${item.y_pt},${item.w_pt},${item.h_pt}`}
          x={ptToPx(item.x_pt, scale)}
          y={ptToPx(item.y_pt, scale)}
          width={ptToPx(item.w_pt, scale)}
          height={ptToPx(item.h_pt, scale)}
        />
      ))}
    </svg>
  );
});
