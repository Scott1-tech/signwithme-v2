import { useEffect, useRef, useState, type PointerEvent } from 'react';
import type { PageSizePt } from '../lib/layout';
import { clampPointOn } from '../lib/annotationKinds/shared';
import { pxPointToPt } from '../lib/viewport';
import type { Gesture, Point } from '../workspace/tools/types';

/**
 * The page's pointer surface for the DRAG tools of W4 -- ink, rect, ellipse,
 * line, arrow (plan §2.4 a: `.ws-capture`, the FIRST child of `.pdf-overlay`).
 *
 * It exists only while such a tool is armed (the workspace then also carries
 * `workspace--drawing`, which makes every hit box, handle, slot and free
 * signature above it step aside, so a drag that starts over one of them still
 * draws). On a turned page the rotation shield sits above it, so nothing can
 * be drawn there -- exactly like the click-to-place tools.
 *
 * ONE GESTURE
 * -----------
 *  * press (left button / pen / touch): the point, in PDF points of THIS page,
 *    clamped to it, goes to the armed tool's `beginGesture`; the pointer is
 *    captured, so a drag that leaves the page keeps drawing -- clamped to the
 *    page edge;
 *  * move: `gesture.move(pt, event)` (Shift is read from the event) and the
 *    live `Preview` is redrawn -- in this component's own state, so only THIS
 *    page's overlay renders while the pointer moves (the W2 memo rule);
 *  * release: `gesture.end(pt)` -- the tool commits ONE undo step (or nothing,
 *    for a click);
 *  * Esc (or a lost capture / pointercancel): `gesture.cancel()`, nothing kept.
 */

interface Props {
  pageNumber: number;
  scale: number;
  pageSize: PageSizePt | null;
  cursor: string;
  /** The armed tool's `beginGesture` on this page (`null` = it refused). */
  begin: (page: number, pt: Point) => Gesture | null;
}

export function ToolCaptureLayer({ pageNumber, scale, pageSize, cursor, begin }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const live = useRef<{ gesture: Gesture; pointerId: number } | null>(null);
  const [gesture, setGesture] = useState<Gesture | null>(null);
  const [, setTick] = useState(0);

  const pointOf = (event: { clientX: number; clientY: number }): Point => {
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    const pt = pxPointToPt({ x: event.clientX - rect.left, y: event.clientY - rect.top }, scale);
    return clampPointOn(pt, pageSize);
  };

  const finish = (how: 'end' | 'cancel', event?: PointerEvent<HTMLDivElement>) => {
    const current = live.current;
    if (!current) return;
    live.current = null;
    setGesture(null);
    if (event) {
      try {
        event.currentTarget.releasePointerCapture(current.pointerId);
      } catch {
        /* already released */
      }
    }
    if (how === 'end' && event) {
      const pt = pointOf(event);
      current.gesture.move(pt, event.nativeEvent);
      current.gesture.end(pt);
    } else {
      current.gesture.cancel();
    }
  };

  // Esc drops the gesture in flight (and nothing else hears that Esc).
  useEffect(() => {
    if (!gesture) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !live.current) return;
      event.preventDefault();
      event.stopPropagation();
      const current = live.current;
      live.current = null;
      setGesture(null);
      try {
        rootRef.current?.releasePointerCapture(current.pointerId);
      } catch {
        /* already released */
      }
      current.gesture.cancel();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [gesture]);

  const Preview = gesture?.Preview;

  return (
    <div
      ref={rootRef}
      className="ws-capture"
      data-testid={`ws-capture-${pageNumber}`}
      data-drawing={gesture ? 'true' : undefined}
      style={{ cursor }}
      onPointerDown={(event) => {
        if (live.current) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const started = begin(pageNumber, pointOf(event));
        if (!started) return;
        live.current = { gesture: started, pointerId: event.pointerId };
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          /* capture is a nicety: the gesture still works inside the page */
        }
        setGesture(started);
      }}
      onPointerMove={(event) => {
        const current = live.current;
        if (!current || current.pointerId !== event.pointerId) return;
        event.preventDefault();
        current.gesture.move(pointOf(event), event.nativeEvent);
        setTick((value) => value + 1);
      }}
      onPointerUp={(event) => {
        if (!live.current || live.current.pointerId !== event.pointerId) return;
        finish('end', event);
      }}
      onPointerCancel={(event) => {
        if (!live.current || live.current.pointerId !== event.pointerId) return;
        finish('cancel', event);
      }}
      // A click never reaches the page (nothing here places on click).
      onClick={(event) => event.stopPropagation()}
    >
      {Preview && (
        <svg className="ws-capture__preview" aria-hidden="true">
          <Preview scale={scale} />
        </svg>
      )}
    </div>
  );
}
