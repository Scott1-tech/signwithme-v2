import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type RefObject,
} from 'react';

/**
 * Where the workspace's one toolbar sits (plan §2.6), and the gestures that
 * move it.
 *
 * MODES
 * -----
 *  * `docked-top` (default) -- in the document column's flow, above the
 *    viewer, exactly where the old docked toolbar was. The checks click exact
 *    pixels on the page, so the default must keep that layout.
 *  * `floating` -- absolutely positioned inside `.workspace__stage` (the
 *    document column), at `x`/`y` (CSS px, relative to the column's padding
 *    box). `orientation: 'vertical'` + `edge` once it was dropped against the
 *    left or right edge; a right-edge panel is anchored with `right`, so it
 *    stays on the edge when the column changes width.
 *
 * Moving it: press the grip (`toolbar-grip`) and drag -- pointer capture, and
 * a press that travels < 3 px is not a drag. On release: within 24 px of the
 * top edge → docked; of the left / right edge → vertical on that edge;
 * anywhere else → floating, horizontal. At least 48 px of the panel always
 * stays inside the column, also when the column shrinks (ResizeObserver).
 *
 * Keyboard (on the grip): arrows move 10 px (Shift 50), Enter toggles
 * docked / floating.
 *
 * At ≤ 640 px the placement is ignored (not overwritten): the panel is forced
 * to the bottom of the screen and cannot be dragged.
 *
 * Persisted in `swm.toolbar`; anything stored that does not make sense is
 * replaced by the default, field by field.
 */

export type ToolbarMode = 'docked-top' | 'floating';
export type ToolbarOrientation = 'horizontal' | 'vertical';
export type ToolbarEdge = 'left' | 'right' | null;

export interface ToolbarPlacement {
  mode: ToolbarMode;
  x: number;
  y: number;
  orientation: ToolbarOrientation;
  collapsed: boolean;
  /** The edge a vertical panel was dropped on (anchors it there). */
  edge: ToolbarEdge;
}

export const TOOLBAR_KEY = 'swm.toolbar';
/** Distance to an edge that snaps, in CSS px. */
export const SNAP_PX = 24;
/** What of the panel always stays inside the column, in CSS px. */
export const MIN_VISIBLE_PX = 48;
/** Gap to the column's edge of a panel snapped to it. */
export const EDGE_GAP_PX = 8;
/** Grip arrow keys, in CSS px. */
export const KEY_STEP_PX = 10;
export const KEY_STEP_LARGE_PX = 50;
/** Below this many px of travel a press on the grip is not a drag. */
const DRAG_THRESHOLD_PX = 3;
/** At or below this viewport width the panel is forced to the bottom. */
export const NARROW_QUERY = '(max-width: 640px)';

export const DEFAULT_PLACEMENT: ToolbarPlacement = {
  mode: 'docked-top',
  x: 16,
  y: 16,
  orientation: 'horizontal',
  collapsed: false,
  edge: null,
};

const COORD_LIMIT = 10000;

export function sanitizePlacement(raw: unknown): ToolbarPlacement {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return DEFAULT_PLACEMENT;
  const source = raw as Record<string, unknown>;
  const coord = (value: unknown, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value)
      ? Math.max(-COORD_LIMIT, Math.min(COORD_LIMIT, Math.round(value)))
      : fallback;
  const mode: ToolbarMode = source.mode === 'floating' ? 'floating' : 'docked-top';
  const edge: ToolbarEdge = source.edge === 'left' || source.edge === 'right' ? source.edge : null;
  // A docked panel is always horizontal; a vertical one belongs to an edge.
  const orientation: ToolbarOrientation =
    mode === 'floating' && source.orientation === 'vertical' ? 'vertical' : 'horizontal';
  return {
    mode,
    x: coord(source.x, DEFAULT_PLACEMENT.x),
    y: coord(source.y, DEFAULT_PLACEMENT.y),
    orientation,
    collapsed: source.collapsed === true,
    edge: orientation === 'vertical' ? (edge ?? 'left') : null,
  };
}

function loadPlacement(): ToolbarPlacement {
  try {
    const raw = localStorage.getItem(TOOLBAR_KEY);
    return raw ? sanitizePlacement(JSON.parse(raw) as unknown) : DEFAULT_PLACEMENT;
  } catch {
    return DEFAULT_PLACEMENT;
  }
}

function savePlacement(placement: ToolbarPlacement): void {
  try {
    localStorage.setItem(TOOLBAR_KEY, JSON.stringify(placement));
  } catch {
    /* private mode: the placement simply does not persist */
  }
}

interface Size {
  width: number;
  height: number;
}

/** Keep ≥ 48 px of a `w x h` panel inside a `stage`, and never above its top. */
export function clampToStage(x: number, y: number, panel: Size, stage: Size): { x: number; y: number } {
  const minX = MIN_VISIBLE_PX - panel.width;
  const maxX = Math.max(minX, stage.width - MIN_VISIBLE_PX);
  const maxY = Math.max(0, stage.height - MIN_VISIBLE_PX);
  return {
    x: Math.round(Math.min(maxX, Math.max(minX, x))),
    y: Math.round(Math.min(maxY, Math.max(0, y))),
  };
}

interface DragSession {
  pointerId: number;
  startX: number;
  startY: number;
  /** Pointer position inside the panel when pressed. */
  grabX: number;
  grabY: number;
  dragging: boolean;
}

export interface FloatingPanel {
  placement: ToolbarPlacement;
  /** The live position while the grip is being dragged. */
  dragPosition: { x: number; y: number } | null;
  /** ≤ 640 px: bottom sheet, no dragging. */
  narrow: boolean;
  setCollapsed: (collapsed: boolean) => void;
  gripBindings: {
    onPointerDown: (event: PointerEvent<HTMLElement>) => void;
    onPointerMove: (event: PointerEvent<HTMLElement>) => void;
    onPointerUp: (event: PointerEvent<HTMLElement>) => void;
    onPointerCancel: (event: PointerEvent<HTMLElement>) => void;
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  };
  /** The column's inner size, for the overflow maths (0 until measured). */
  stageSize: Size;
}

export function useFloatingPanel(
  stageRef: RefObject<HTMLElement>,
  panelRef: RefObject<HTMLElement>,
): FloatingPanel {
  const [placement, setPlacement] = useState<ToolbarPlacement>(loadPlacement);
  const [dragPosition, setDragPosition] = useState<{ x: number; y: number } | null>(null);
  const [stageSize, setStageSize] = useState<Size>({ width: 0, height: 0 });
  const [narrow, setNarrow] = useState<boolean>(
    () => typeof window !== 'undefined' && window.matchMedia?.(NARROW_QUERY).matches === true,
  );
  const session = useRef<DragSession | null>(null);

  // Written on every change, including the corrections of the clamp below --
  // but not the value it was loaded with (StrictMode runs this effect twice on
  // mount, so a "first run" flag would not do).
  const loaded = useRef(placement);
  useEffect(() => {
    if (placement === loaded.current) return;
    savePlacement(placement);
  }, [placement]);

  useEffect(() => {
    const query = window.matchMedia?.(NARROW_QUERY);
    if (!query) return undefined;
    const onChange = () => setNarrow(query.matches);
    onChange();
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  /** The column's padding box and the panel's box, both in CSS px. */
  const measure = useCallback(() => {
    const stage = stageRef.current;
    const panel = panelRef.current;
    if (!stage || !panel) return null;
    const stageRect = stage.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const originX = stageRect.left + stage.clientLeft - stage.scrollLeft;
    const originY = stageRect.top + stage.clientTop - stage.scrollTop;
    return {
      originX,
      originY,
      stage: { width: stage.clientWidth, height: stage.clientHeight },
      panel: { width: panelRect.width, height: panelRect.height },
      panelX: panelRect.left - originX,
      panelY: panelRect.top - originY,
    };
  }, [stageRef, panelRef]);

  // The column changes size (window resize, the panel below 1024 px, the
  // thumbnails strip): a floating panel is pulled back so it stays reachable.
  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage || typeof ResizeObserver === 'undefined') return undefined;
    const onResize = () => {
      setStageSize((current) =>
        current.width === stage.clientWidth && current.height === stage.clientHeight
          ? current
          : { width: stage.clientWidth, height: stage.clientHeight },
      );
      const box = measure();
      if (!box) return;
      setPlacement((current) => {
        if (current.mode !== 'floating') return current;
        const next = clampToStage(current.x, current.y, box.panel, box.stage);
        return next.x === current.x && next.y === current.y ? current : { ...current, ...next };
      });
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(stage);
    onResize();
    return () => observer.disconnect();
  }, [stageRef, measure]);

  /**
   * Where a panel released at (x, y) ends up: docked, on an edge, or floating.
   * `pointerX` is where the pointer was let go (column px): the RIGHT edge is
   * judged by the pointer, not by the panel's right side -- a horizontal panel
   * is nearly as wide as the column and would otherwise always `touch` it.
   */
  const settle = useCallback(
    (x: number, y: number, pointerX: number, current: ToolbarPlacement): ToolbarPlacement => {
      const box = measure();
      if (!box) return current;
      if (y <= SNAP_PX) {
        return { ...current, mode: 'docked-top', orientation: 'horizontal', edge: null };
      }
      if (x <= SNAP_PX) {
        const clamped = clampToStage(EDGE_GAP_PX, y, box.panel, box.stage);
        return { ...current, mode: 'floating', orientation: 'vertical', edge: 'left', ...clamped };
      }
      if (pointerX >= box.stage.width - SNAP_PX) {
        const clamped = clampToStage(x, y, box.panel, box.stage);
        return { ...current, mode: 'floating', orientation: 'vertical', edge: 'right', ...clamped };
      }
      const clamped = clampToStage(x, y, box.panel, box.stage);
      return { ...current, mode: 'floating', orientation: 'horizontal', edge: null, ...clamped };
    },
    [measure],
  );

  const onPointerDown = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      if (narrow) return;
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      const box = measure();
      if (!box) return;
      event.preventDefault();
      session.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        grabX: event.clientX - box.originX - box.panelX,
        grabY: event.clientY - box.originY - box.panelY,
        dragging: false,
      };
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        /* capture is a nicety: the drag still works without it */
      }
    },
    [measure, narrow],
  );

  const onPointerMove = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      const active = session.current;
      if (!active || active.pointerId !== event.pointerId) return;
      if (!active.dragging) {
        if (Math.hypot(event.clientX - active.startX, event.clientY - active.startY) < DRAG_THRESHOLD_PX) {
          return;
        }
        active.dragging = true;
      }
      event.preventDefault();
      const box = measure();
      if (!box) return;
      const x = event.clientX - box.originX - active.grabX;
      const y = event.clientY - box.originY - active.grabY;
      setDragPosition(clampToStage(x, y, box.panel, box.stage));
    },
    [measure],
  );

  const finish = useCallback(
    (event: PointerEvent<HTMLElement>, commit: boolean) => {
      const active = session.current;
      if (!active || active.pointerId !== event.pointerId) return;
      session.current = null;
      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        /* already released */
      }
      const box = measure();
      if (commit && active.dragging && box) {
        const x = event.clientX - box.originX - active.grabX;
        const y = event.clientY - box.originY - active.grabY;
        const pointerX = event.clientX - box.originX;
        setPlacement((current) => settle(x, y, pointerX, current));
      }
      setDragPosition(null);
    },
    [measure, settle],
  );

  const onPointerUp = useCallback((event: PointerEvent<HTMLElement>) => finish(event, true), [finish]);
  const onPointerCancel = useCallback(
    (event: PointerEvent<HTMLElement>) => finish(event, false),
    [finish],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (narrow) return;
      const step = event.shiftKey ? KEY_STEP_LARGE_PX : KEY_STEP_PX;
      const delta: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
      };
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        event.stopPropagation();
        const box = measure();
        setPlacement((current) => {
          if (current.mode === 'floating') {
            return { ...current, mode: 'docked-top', orientation: 'horizontal', edge: null };
          }
          const at = box
            ? clampToStage(box.panelX + EDGE_GAP_PX * 2, box.panelY + EDGE_GAP_PX * 4, box.panel, box.stage)
            : { x: DEFAULT_PLACEMENT.x, y: DEFAULT_PLACEMENT.y };
          return { ...current, mode: 'floating', orientation: 'horizontal', edge: null, ...at };
        });
        return;
      }
      const move = delta[event.key];
      if (!move) return;
      event.preventDefault();
      event.stopPropagation();
      const box = measure();
      if (!box) return;
      setPlacement((current) => {
        // From wherever it is drawn now (docked, or anchored on the right).
        const next = clampToStage(box.panelX + move[0], box.panelY + move[1], box.panel, box.stage);
        const stillVertical = current.mode === 'floating' && current.orientation === 'vertical';
        return {
          ...current,
          mode: 'floating',
          orientation: stillVertical ? 'vertical' : 'horizontal',
          // A right-anchored panel stays anchored while it only moves up/down;
          // a sideways step anchors it by its left edge from then on.
          edge: stillVertical
            ? current.edge === 'right' && move[0] === 0
              ? 'right'
              : 'left'
            : null,
          ...next,
        };
      });
    },
    [measure, narrow],
  );

  const setCollapsed = useCallback(
    (collapsed: boolean) => setPlacement((current) => ({ ...current, collapsed })),
    [],
  );

  return {
    placement,
    dragPosition,
    narrow,
    setCollapsed,
    gripBindings: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel, onKeyDown },
    stageSize,
  };
}
