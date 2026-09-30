import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, PointerEvent } from 'react';
import {
  NUDGE_STEP,
  NUDGE_STEP_LARGE,
  SIZE_STEP,
  SIZE_STEP_LARGE,
  clampAnchor,
  clampSize,
  hasBoxHandles,
  isImage,
  isLine,
  isShape,
  isSized,
  isWhiteout,
  moveAnnotation,
  resizeAnnotation,
  snapPt,
  type Annotation,
} from '../lib/annotations';
import { moveLineEnd, snapTo45 } from '../lib/annotationKinds/line';
import { resizeBox, type BoxHandle } from '../lib/annotationKinds/resize';
import type { PageSizePt } from '../lib/layout';
import { pxToPt, type PxBox } from '../lib/viewport';
import type { AnnotationStore } from './useAnnotations';

/** A handle of a W4 drawing: one of a box's eight, or one end of a line. */
export type ShapeHandle = BoxHandle | 'start' | 'end';

/**
 * The geometry a handle drag produces (pure, so the keyboard and the pointer
 * share it). `dx` / `dy` in PDF points since the press; `free` = Shift
 * (an image's ratio let go, a rect / ellipse made square, a line end snapped
 * to 45°).
 */
export function reshapeByHandle(
  start: Annotation,
  handle: ShapeHandle,
  dx: number,
  dy: number,
  shift: boolean,
  page: PageSizePt | null,
): Annotation {
  if (isLine(start) && (handle === 'start' || handle === 'end')) {
    const moving = handle === 'start' ? { x: start.x1_pt, y: start.y1_pt } : { x: start.x2_pt, y: start.y2_pt };
    const fixed = handle === 'start' ? { x: start.x2_pt, y: start.y2_pt } : { x: start.x1_pt, y: start.y1_pt };
    let target = { x: snapPt(moving.x + dx), y: snapPt(moving.y + dy) };
    if (shift) target = snapTo45(fixed, target, page);
    return moveLineEnd(start, handle, target, page);
  }
  // W5: a free whiteout resizes like a rect (Shift on a corner: a square); a
  // grouped one (an edited line) has no handles at all.
  if (hasBoxHandles(start) && handle !== 'start' && handle !== 'end') {
    const box = resizeBox(
      { x: start.x_pt, y: start.y_pt, w: start.w_pt, h: start.h_pt },
      handle,
      snapPt(dx),
      snapPt(dy),
      { aspect: isImage(start) && !shift, square: (isShape(start) || isWhiteout(start)) && shift },
      page,
    );
    if (box.x === start.x_pt && box.y === start.y_pt && box.w === start.w_pt && box.h === start.h_pt) return start;
    return { ...start, x_pt: box.x, y_pt: box.y, w_pt: box.w, h_pt: box.h };
  }
  return start;
}

/**
 * The two pointer gestures an annotation has: **move** (press its own box and
 * drag) and **resize** (press the one small corner handle and drag).
 *
 * Deliberately built the same way as `useSlotDrag` + `useSlotResize`, because
 * the hazards are the same ones:
 *
 *  * a press that travels less than `DRAG_THRESHOLD_PX` is a *click* -- that is
 *    how a mark is selected -- so the drag only starts, and the pointer is only
 *    captured, once the threshold is crossed;
 *  * the click that follows a real drag belongs to the drag: it is swallowed,
 *    or releasing over the page would place a second annotation;
 *  * the handle stops `pointerdown` from propagating, so a resize can never
 *    become a move of the mark underneath;
 *  * the preview is live and local, and the store is written exactly **once**,
 *    on release -- one undo step per gesture, not one per pointer move.
 *
 * Everything is clamped to the page (`clampPoint`) and to the kind's own size
 * range (`clampSize`: 6…48 pt for a run of text or a date, 6…72 pt for a tick
 * or a cross) here rather than at finalize: the server answers 422 for a
 * coordinate outside its page, and a refusal the user cannot see coming is not
 * a limit, it is a bug.
 *
 * W4 adds a third gesture, the HANDLES of a drawing (`bindShapeHandle`): the
 * eight of a rect / ellipse / image box and the two ends of a line / arrow,
 * with the geometry in `reshapeByHandle` (pure; `lib/annotationKinds/
 * resize.ts` for boxes). The live preview is now the whole annotation as the
 * gesture has it (`Preview.annotation`), whatever the kind -- a markup, an ink
 * or a line moves every point along. One undo step per handle drag.
 *
 * Both gestures are written once for every kind. A mark's box is square and
 * centred on its point where a run's is measured from its glyphs, but that
 * difference lives entirely in `AnnotationLayer`: by the time a box reaches
 * `bindHandle` it is CSS pixels, and the ratio maths below neither knows nor
 * cares which kind produced it.
 */

/** What a handle step is called in the history (the Undo button's title). */
const reshapeLabel = (next: Annotation): string =>
  isLine(next) ? 'Move line end' : isWhiteout(next) ? 'Resize whiteout' : 'Resize shape';

/** A whiteout and a markup have no size; everything else does. */
const sizeOf0 = (annotation: Annotation): number => (isSized(annotation) ? annotation.size_pt : 0);

/** The whiteout of an edited line (a grouped whiteout) never moves. */
export const isLocked = (annotation: Annotation): boolean =>
  isWhiteout(annotation) && Boolean(annotation.group_id);

/** Below this many CSS pixels of travel a press is a click, not a drag. */
export const DRAG_THRESHOLD_PX = 3;
/** A start box smaller than this cannot carry a ratio. */
const MIN_START_PX = 4;

interface MoveSession {
  id: string;
  annotation: Annotation;
  pointerId: number;
  startX: number;
  startY: number;
  /** Baseline point the gesture started from, PDF points. */
  from: { x: number; y: number };
  last: { x: number; y: number };
  dragging: boolean;
  /** A grouped whiteout: the press is a gesture, but nothing moves. */
  locked: boolean;
}

interface ResizeSession {
  id: string;
  annotation: Annotation;
  pointerId: number;
  startX: number;
  startY: number;
  startWidth: number;
  startHeight: number;
  startSize: number;
  last: number;
}

/** A W4 handle drag in progress (8 box handles, 2 line ends). */
interface ShapeSession {
  id: string;
  annotation: Annotation;
  handle: ShapeHandle;
  pointerId: number;
  startX: number;
  startY: number;
  last: Annotation;
}

/** The annotation as the live gesture has it (moved, resized, reshaped). */
interface Preview {
  id: string;
  annotation: Annotation;
}

interface Options {
  store: AnnotationStore;
  /** CSS pixels per PDF point (the viewer's render scale). */
  scale: number;
  pageSizeOf: (page: number | null) => PageSizePt | null;
  enabled?: boolean;
}

export interface MarkBindings {
  onPointerDown: (event: PointerEvent<Element>) => void;
  onPointerMove: (event: PointerEvent<Element>) => void;
  onPointerUp: (event: PointerEvent<Element>) => void;
  onPointerCancel: (event: PointerEvent<Element>) => void;
  onClick: (event: MouseEvent<Element>) => void;
  onDoubleClick: (event: MouseEvent<Element>) => void;
}

export interface HandleBindings {
  onPointerDown: (event: PointerEvent<Element>) => void;
  onPointerMove: (event: PointerEvent<Element>) => void;
  onPointerUp: (event: PointerEvent<Element>) => void;
  onPointerCancel: (event: PointerEvent<Element>) => void;
  onClick: (event: MouseEvent<Element>) => void;
  onDoubleClick: (event: MouseEvent<Element>) => void;
}

export interface AnnotationEdit {
  /** An annotation with the live gesture applied, if it is the one being moved. */
  resolve: (annotation: Annotation) => Annotation;
  draggingId: string | null;
  resizingId: string | null;
  bindMark: (
    annotation: Annotation,
    on: { select: () => void; edit: () => void },
  ) => MarkBindings;
  bindHandle: (annotation: Annotation, box: PxBox) => HandleBindings;
  /**
   * W4: one of the eight handles of a rect / ellipse / image, or one end of a
   * line / arrow. One undo step per drag; arrows on a focused handle move it
   * by 0.5 pt (Shift: 2 pt), one step per press.
   */
  bindShapeHandle: (
    annotation: Annotation,
    handle: ShapeHandle,
  ) => HandleBindings & { onKeyDown: (event: KeyboardEvent<Element>) => void };
  /** Arrow-key move, in PDF points (dy positive = DOWN, as the y axis runs). */
  nudge: (annotation: Annotation, dx: number, dy: number) => void;
  /** One press of − / + on the size, or an arrow key on the corner handle. */
  stepSize: (annotation: Annotation, direction: 1 | -1, large?: boolean) => void;
}

export function useAnnotationEdit({
  store,
  scale,
  pageSizeOf,
  enabled = true,
}: Options): AnnotationEdit {
  const [preview, setPreview] = useState<Preview | null>(null);
  // `ShapeSession` (W4 handles) is declared next to its bindings below.
  const move = useRef<MoveSession | null>(null);
  const resize = useRef<ResizeSession | null>(null);
  const suppressClick = useRef(false);
  const [resizing, setResizing] = useState<string | null>(null);

  // The handlers are built once and read the latest props through a ref, so a
  // pointer move does not rebuild every binding on the page.
  const latest = useRef({ store, scale, pageSizeOf, enabled });
  useEffect(() => {
    latest.current = { store, scale, pageSizeOf, enabled };
  });

  const resolve = useCallback(
    (annotation: Annotation): Annotation => {
      if (!preview || preview.id !== annotation.id) return annotation;
      // A grouped whiteout is locked (never previewed as moved); anything else
      // is shown exactly as the gesture has it -- a markup / ink / line moved
      // as a whole, a box reshaped by its handle.
      if (isWhiteout(annotation) && annotation.group_id) return annotation;
      return preview.annotation;
    },
    [preview],
  );

  const nudge = useCallback((annotation: Annotation, dx: number, dy: number) => {
    const { enabled: on, pageSizeOf: sizeOf, store: current } = latest.current;
    if (!on) return;
    const point = clampAnchor(
      annotation,
      { x: snapPt(annotation.x_pt + dx), y: snapPt(annotation.y_pt + dy) },
      sizeOf(annotation.page),
    );
    current.move(annotation.id, point.x, point.y);
  }, []);

  const stepSize = useCallback((annotation: Annotation, direction: 1 | -1, large = false) => {
    const { enabled: on, store: current } = latest.current;
    if (!on || !isSized(annotation)) return;
    current.resize(
      annotation.id,
      clampSize(
        annotation.size_pt + direction * (large ? SIZE_STEP_LARGE : SIZE_STEP),
        annotation.kind,
      ),
    );
  }, []);

  const bindMark = useCallback(
    (annotation: Annotation, on: { select: () => void; edit: () => void }): MarkBindings => ({
      onPointerDown: (event) => {
        suppressClick.current = false;
        if (!latest.current.enabled) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        // A press selects straight away: the mark the user is about to drag is
        // the mark the toolbar should already be describing.
        on.select();
        // The whiteout of an edited line is LOCKED where the line is (plan
        // §7-6): it can be selected (which selects its group), never dragged.
        // Its press still owns the gesture (captured, click swallowed), so a
        // drag that ends over the paper does not read as a click on the paper.
        move.current = {
          id: annotation.id,
          annotation,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          from: { x: annotation.x_pt, y: annotation.y_pt },
          last: { x: annotation.x_pt, y: annotation.y_pt },
          dragging: false,
          locked: isLocked(annotation),
        };
      },
      onPointerMove: (event) => {
        const active = move.current;
        if (!active || active.pointerId !== event.pointerId) return;
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
        if (active.locked) return;
        const { scale: renderScale, pageSizeOf: sizeOf } = latest.current;
        const point = clampAnchor(
          active.annotation,
          {
            x: snapPt(active.from.x + pxToPt(movedX, renderScale)),
            y: snapPt(active.from.y + pxToPt(movedY, renderScale)),
          },
          sizeOf(active.annotation.page),
        );
        active.last = point;
        setPreview({ id: active.id, annotation: moveAnnotation(active.annotation, point.x, point.y) });
      },
      onPointerUp: (event) => {
        const active = move.current;
        if (!active || active.pointerId !== event.pointerId) return;
        move.current = null;
        if (!active.dragging) return;
        try {
          event.currentTarget.releasePointerCapture(event.pointerId);
        } catch {
          /* already released */
        }
        // The click that follows this release belongs to the drag.
        suppressClick.current = true;
        window.setTimeout(() => {
          suppressClick.current = false;
        }, 0);
        if (active.locked) return;
        setPreview(null);
        latest.current.store.move(active.id, active.last.x, active.last.y);
      },
      onPointerCancel: (event) => {
        const active = move.current;
        if (!active || active.pointerId !== event.pointerId) return;
        move.current = null;
        setPreview(null);
      },
      onClick: (event) => {
        // Nothing on a mark may fall through to the page's click-to-place.
        event.stopPropagation();
        if (suppressClick.current) {
          suppressClick.current = false;
          event.preventDefault();
          return;
        }
        on.select();
      },
      onDoubleClick: (event) => {
        event.stopPropagation();
        event.preventDefault();
        if (!latest.current.enabled) return;
        on.edit();
      },
    }),
    [],
  );

  const bindHandle = useCallback(
    (annotation: Annotation, box: PxBox): HandleBindings => ({
      onPointerDown: (event) => {
        // Never let this press reach the mark underneath: that is a move.
        event.stopPropagation();
        if (!latest.current.enabled || !isSized(annotation)) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        resize.current = {
          id: annotation.id,
          annotation,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          startWidth: Math.max(box.width, MIN_START_PX),
          startHeight: Math.max(box.height, MIN_START_PX),
          startSize: sizeOf0(annotation),
          last: sizeOf0(annotation),
        };
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          /* capture is a nicety: the gesture still works without it */
        }
        setResizing(annotation.id);
        setPreview({ id: annotation.id, annotation });
      },
      onPointerMove: (event) => {
        const active = resize.current;
        if (!active || active.pointerId !== event.pointerId) return;
        event.preventDefault();
        event.stopPropagation();
        const movedX = event.clientX - active.startX;
        // Screen y grows downwards and the handle is at the TOP-right corner:
        // dragging up makes the mark bigger. What never moves is the stored
        // point -- a run's baseline, a mark's centre -- exactly as a
        // signature's corner handle leaves its own anchor alone.
        const movedY = active.startY - event.clientY;
        const ratio =
          ((active.startWidth + movedX) / active.startWidth +
            (active.startHeight + movedY) / active.startHeight) /
          2;
        const size = clampSize(active.startSize * ratio, active.annotation.kind);
        active.last = size;
        setPreview({ id: active.id, annotation: resizeAnnotation(active.annotation, size) });
      },
      onPointerUp: (event) => {
        const active = resize.current;
        if (!active || active.pointerId !== event.pointerId) return;
        resize.current = null;
        event.stopPropagation();
        try {
          event.currentTarget.releasePointerCapture(event.pointerId);
        } catch {
          /* already released */
        }
        setResizing(null);
        setPreview(null);
        latest.current.store.resize(active.id, active.last);
      },
      onPointerCancel: (event) => {
        const active = resize.current;
        if (!active || active.pointerId !== event.pointerId) return;
        resize.current = null;
        setResizing(null);
        setPreview(null);
      },
      // Neither the mark's own click nor the page's click-to-place may hear
      // anything that happened on this handle.
      onClick: (event) => {
        event.stopPropagation();
        event.preventDefault();
      },
      onDoubleClick: (event) => {
        event.stopPropagation();
        event.preventDefault();
      },
    }),
    [],
  );

  const shape = useRef<ShapeSession | null>(null);

  const bindShapeHandle = useCallback(
    (annotation: Annotation, handle: ShapeHandle) => ({
      onPointerDown: (event: PointerEvent<Element>) => {
        // Never let this press reach the drawing underneath: that is a move.
        event.stopPropagation();
        if (!latest.current.enabled) return;
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        shape.current = {
          id: annotation.id,
          annotation,
          handle,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          last: annotation,
        };
        try {
          event.currentTarget.setPointerCapture(event.pointerId);
        } catch {
          /* capture is a nicety: the gesture still works without it */
        }
        setResizing(annotation.id);
        setPreview({ id: annotation.id, annotation });
      },
      onPointerMove: (event: PointerEvent<Element>) => {
        const active = shape.current;
        if (!active || active.pointerId !== event.pointerId) return;
        event.preventDefault();
        event.stopPropagation();
        const { scale: renderScale, pageSizeOf: sizeOf } = latest.current;
        const next = reshapeByHandle(
          active.annotation,
          active.handle,
          pxToPt(event.clientX - active.startX, renderScale),
          pxToPt(event.clientY - active.startY, renderScale),
          event.shiftKey,
          sizeOf(active.annotation.page),
        );
        active.last = next;
        setPreview({ id: active.id, annotation: next });
      },
      onPointerUp: (event: PointerEvent<Element>) => {
        const active = shape.current;
        if (!active || active.pointerId !== event.pointerId) return;
        shape.current = null;
        event.stopPropagation();
        try {
          event.currentTarget.releasePointerCapture(event.pointerId);
        } catch {
          /* already released */
        }
        setResizing(null);
        setPreview(null);
        // ONE step for the whole drag (nothing at all when it ended where it began).
        if (active.last !== active.annotation) {
          latest.current.store.reshape(active.id, active.last, reshapeLabel(active.last));
        }
      },
      onPointerCancel: (event: PointerEvent<Element>) => {
        const active = shape.current;
        if (!active || active.pointerId !== event.pointerId) return;
        shape.current = null;
        setResizing(null);
        setPreview(null);
      },
      onClick: (event: MouseEvent<Element>) => {
        event.stopPropagation();
        event.preventDefault();
      },
      onDoubleClick: (event: MouseEvent<Element>) => {
        event.stopPropagation();
        event.preventDefault();
      },
      onKeyDown: (event: KeyboardEvent<Element>) => {
        const step = event.shiftKey ? NUDGE_STEP_LARGE : NUDGE_STEP;
        const delta: Record<string, [number, number]> = {
          ArrowLeft: [-step, 0],
          ArrowRight: [step, 0],
          ArrowUp: [0, -step],
          ArrowDown: [0, step],
        };
        const move = delta[event.key];
        if (!move || !latest.current.enabled) return;
        event.preventDefault();
        event.stopPropagation();
        const { pageSizeOf: sizeOf, store: current } = latest.current;
        const live = current.get(annotation.id) ?? annotation;
        const next = reshapeByHandle(live, handle, move[0], move[1], false, sizeOf(live.page));
        if (next !== live) current.reshape(live.id, next, reshapeLabel(next));
      },
    }),
    [],
  );

  return {
    resolve,
    draggingId: preview && !resizing ? preview.id : null,
    resizingId: resizing,
    bindMark,
    bindHandle,
    bindShapeHandle,
    nudge,
    stepSize,
  };
}
