import {
  ANNOTATION_COLOR_HEX,
  MAX_ANNOTATIONS,
  MAX_INK_POINTS_TOTAL,
  SHAPE_FILL_ALPHA,
  WHITEOUT_NOTICE,
  inkPointCount,
  isInk,
  makeWhiteout,
  snapPt,
  type Annotation,
} from '../../lib/annotations';
import { makeInk, splitInk } from '../../lib/annotationKinds/ink';
import { arrowHead, makeLine, snapTo45, type LineKind } from '../../lib/annotationKinds/line';
import { makeShape, type ShapeKind } from '../../lib/annotationKinds/shape';
import { pageExtent, type BoxPt } from '../../lib/annotationKinds/shared';
import { dedupe, simplifyPath } from '../../lib/pathSimplify';
import type { LineV7, ShapeV7 } from '../../lib/annotationKinds/v7';
import type { Gesture, Point, ToolContext, ToolDefinition, ToolGroup, ToolId, ToolMenu } from './types';

/**
 * The drag tools of W4 -- freehand ink, rectangle, ellipse, line, arrow --
 * built from three factories. Not a tool folder (the registry only globs
 * `./<id>/index.ts`): each folder is one call with its own name, glyph and
 * letter.
 *
 * EVERY GESTURE
 * -------------
 *  * starts on a press over the page (`overlay/ToolCaptureLayer.tsx`), in PDF
 *    points already clamped to that page; the live drawing is the gesture's
 *    `Preview`, drawn by the capture layer of THAT page only;
 *  * commits exactly once, on release, through the store (`add` / `addMany`):
 *    one gesture = one undo step; a release that drew nothing (a click, a
 *    line shorter than 1 pt) commits nothing;
 *  * is dropped whole by Esc (`cancel`);
 *  * is refused up front, out loud, when the list already holds 500.
 *
 * Coordinates snap to the half-point grid like every other placement (not
 * ink: a freehand stroke keeps its own shape, thinned by RDP at 0.5 pt).
 */

const INK_MIN_STEP_PT = 0.2;
/** A press that travels less than this (both axes) draws no shape: it was a click. */
const CLICK_PT = 2;

interface Meta {
  label: string;
  glyph: string;
  hint: string;
  shortcut?: string;
  order: number;
  group?: ToolGroup;
  menu?: ToolMenu;
  /** F1a: the heading inside the drop-down. */
  menuSection?: string;
  /** F2: another id for the same factory (Freehand highlight is an ink tool). */
  id?: ToolId;
  /** F2: the ink is a translucent highlighter (`ink2 freehand_highlight`). */
  highlighter?: boolean;
}

/**
 * F2: the v7 look a new drawing gets from the PropertiesBar's «Style» -- only
 * with the document editor, and only when it says something the legacy kind
 * cannot (so an ordinary shape keeps its v5 wire byte for byte).
 */
function shapeLook(ctx: ToolContext): ShapeV7 | undefined {
  if (!ctx.editor) return undefined;
  const { dash, fillColor, opacity } = ctx.props.v7;
  if (dash === 'solid' && fillColor === null && opacity >= 1) return undefined;
  return { dash, fill_color: fillColor, opacity };
}

function lineLook(ctx: ToolContext, kind: LineKind): LineV7 | undefined {
  if (!ctx.editor) return undefined;
  const { dash, opacity } = ctx.props.v7;
  const heads = kind === 'arrow' ? ctx.props.v7.heads : 'none';
  if (dash === 'solid' && opacity >= 1 && heads === (kind === 'arrow' ? 'end' : 'none')) return undefined;
  return { dash, opacity, heads };
}

/** Refuse a new drawing out loud when the list is full. */
function roomFor(ctx: ToolContext, count = 1): boolean {
  if (ctx.annotations.items.length + count > MAX_ANNOTATIONS) {
    ctx.notify(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
    return false;
  }
  return true;
}

function startGesture(ctx: ToolContext): boolean {
  if (!roomFor(ctx)) return false;
  ctx.selection.selectAnnotation(null);
  // Safe mid-press since 2026-09-27: the note floats over the pages
  // (`EditorNote`), so clearing it no longer slides the page under the pointer.
  ctx.notify(null);
  return true;
}

const snap = (pt: Point): Point => ({ x: snapPt(pt.x), y: snapPt(pt.y) });

/* -------------------------------------------------------------------------- */
/* Rect / ellipse                                                              */
/* -------------------------------------------------------------------------- */

/** The box between the press and the pointer; Shift = a square (inside the page). */
export function dragBox(start: Point, end: Point, square: boolean, page: ToolContext['pageSize']): BoxPt {
  let dx = end.x - start.x;
  let dy = end.y - start.y;
  if (square) {
    const { width, height } = pageExtent(page);
    const sx = dx < 0 ? -1 : 1;
    const sy = dy < 0 ? -1 : 1;
    const room = Math.min(sx > 0 ? width - start.x : start.x, sy > 0 ? height - start.y : start.y);
    const side = Math.min(Math.max(Math.abs(dx), Math.abs(dy)), room);
    dx = sx * side;
    dy = sy * side;
  }
  return {
    x: Math.min(start.x, start.x + dx),
    y: Math.min(start.y, start.y + dy),
    w: Math.abs(dx),
    h: Math.abs(dy),
  };
}

export function shapeTool(kind: ShapeKind, meta: Meta): ToolDefinition {
  return {
    id: kind,
    label: meta.label,
    glyph: meta.glyph,
    hint: meta.hint,
    shortcut: meta.shortcut,
    group: meta.group ?? 'shapes',
    order: meta.order,
    menu: meta.menu,
    menuSection: meta.menuSection,
    cursor: 'crosshair',
    input: 'drag',
    placesOnClick: false,
    properties: ['color', 'stroke', 'fill'],
    defaultColor: 'black',
    beginGesture(ctx, pt): Gesture | null {
      if (!startGesture(ctx)) return null;
      const start = snap(pt);
      let end = start;
      let square = false;
      const box = () => dragBox(start, end, square, ctx.pageSize);
      const { stroke, fill, color } = ctx.props;
      return {
        move(next, event) {
          end = snap(next);
          square = event.shiftKey;
        },
        end(next) {
          end = snap(next);
          const drawn = box();
          if (drawn.w < CLICK_PT && drawn.h < CLICK_PT) return;
          const id = ctx.newId();
          const made = makeShape(kind, { id, page: ctx.page, box: drawn, stroke, color, fill }, ctx.pageSize);
          const look = shapeLook(ctx);
          ctx.annotations.add(look ? { ...made, fill: false, v7: look } : made);
          ctx.selection.selectAnnotation(id);
        },
        cancel() {
          /* nothing was stored */
        },
        Preview: ({ scale }) => {
          const drawn = box();
          const common = {
            className: `ws-capture__shape ws-capture__shape--${kind}`,
            'data-testid': 'capture-preview',
            'data-kind': kind,
            'data-box-pt': `${drawn.x},${drawn.y},${drawn.w},${drawn.h}`,
            stroke: ANNOTATION_COLOR_HEX[color],
            strokeWidth: stroke * scale,
            fill: fill ? ANNOTATION_COLOR_HEX[color] : 'none',
            fillOpacity: fill ? SHAPE_FILL_ALPHA : undefined,
          };
          return kind === 'rect' ? (
            <rect {...common} x={drawn.x * scale} y={drawn.y * scale} width={drawn.w * scale} height={drawn.h * scale} />
          ) : (
            <ellipse
              {...common}
              cx={(drawn.x + drawn.w / 2) * scale}
              cy={(drawn.y + drawn.h / 2) * scale}
              rx={(drawn.w / 2) * scale}
              ry={(drawn.h / 2) * scale}
            />
          );
        },
      };
    },
  };
}

/* -------------------------------------------------------------------------- */
/* Whiteout (W5)                                                               */
/* -------------------------------------------------------------------------- */

/** Set once the first whiteout has said what it is (`WHITEOUT_NOTICE`). */
export const WHITEOUT_NOTICE_KEY = 'swm.whiteout.noticed';

function whiteoutNoticeSeen(): boolean {
  try {
    return localStorage.getItem(WHITEOUT_NOTICE_KEY) !== null;
  } catch {
    return false;
  }
}

function rememberWhiteoutNotice(): void {
  try {
    localStorage.setItem(WHITEOUT_NOTICE_KEY, '1');
  } catch {
    /* private mode: it is simply said again next visit */
  }
}

/**
 * The Whiteout tool (W5): the rect's drag (`dragBox`, Shift = a square), but
 * what it commits is a `whiteout` -- opaque white, no stroke, no colour, no
 * fill choice. The live preview is what the placed box looks like on screen:
 * white with a thin dashed grey edge (the edge is a screen aid only; the PDF
 * gets the plain white box). The very first whiteout in this browser says,
 * once, that it only HIDES what is under it (`WHITEOUT_NOTICE`).
 */
export function whiteoutTool(meta: Meta): ToolDefinition {
  return {
    id: 'whiteout',
    label: meta.label,
    glyph: meta.glyph,
    hint: meta.hint,
    shortcut: meta.shortcut,
    group: meta.group ?? 'whiteout',
    order: meta.order,
    menu: meta.menu,
    menuSection: meta.menuSection,
    cursor: 'crosshair',
    input: 'drag',
    placesOnClick: false,
    properties: [],
    beginGesture(ctx, pt): Gesture | null {
      if (!startGesture(ctx)) return null;
      const start = snap(pt);
      let end = start;
      let square = false;
      const box = () => dragBox(start, end, square, ctx.pageSize);
      return {
        move(next, event) {
          end = snap(next);
          square = event.shiftKey;
        },
        end(next) {
          end = snap(next);
          const drawn = box();
          if (drawn.w < CLICK_PT && drawn.h < CLICK_PT) return;
          const id = ctx.newId();
          ctx.annotations.add(makeWhiteout({ id, page: ctx.page, ...drawn }, ctx.pageSize));
          ctx.selection.selectAnnotation(id);
          if (!whiteoutNoticeSeen()) {
            rememberWhiteoutNotice();
            ctx.notify(WHITEOUT_NOTICE);
          }
        },
        cancel() {
          /* nothing was stored */
        },
        Preview: ({ scale }) => {
          const drawn = box();
          return (
            <rect
              className="ws-capture__whiteout"
              data-testid="capture-preview"
              data-kind="whiteout"
              data-box-pt={`${drawn.x},${drawn.y},${drawn.w},${drawn.h}`}
              x={drawn.x * scale}
              y={drawn.y * scale}
              width={drawn.w * scale}
              height={drawn.h * scale}
            />
          );
        },
      };
    },
  };
}

/* -------------------------------------------------------------------------- */
/* Line / arrow                                                                */
/* -------------------------------------------------------------------------- */

export function lineTool(kind: LineKind, meta: Meta): ToolDefinition {
  return {
    id: kind,
    label: meta.label,
    glyph: meta.glyph,
    hint: meta.hint,
    shortcut: meta.shortcut,
    group: meta.group ?? 'shapes',
    order: meta.order,
    menu: meta.menu,
    menuSection: meta.menuSection,
    cursor: 'crosshair',
    input: 'drag',
    placesOnClick: false,
    properties: ['color', 'stroke'],
    defaultColor: 'black',
    beginGesture(ctx, pt): Gesture | null {
      if (!startGesture(ctx)) return null;
      const start = snap(pt);
      let end = start;
      let snapped = false;
      const target = () => (snapped ? snapTo45(start, end, ctx.pageSize) : end);
      const { stroke, color } = ctx.props;
      return {
        move(next, event) {
          end = snap(next);
          snapped = event.shiftKey;
        },
        end(next) {
          end = snap(next);
          const id = ctx.newId();
          const made = makeLine(kind, { id, page: ctx.page, from: start, to: target(), stroke, color }, ctx.pageSize);
          // Shorter than 1 pt: a click, not a line -- nothing is stored.
          if (!made) return;
          const look = lineLook(ctx, kind);
          ctx.annotations.add(look ? { ...made, v7: look } : made);
          ctx.selection.selectAnnotation(id);
        },
        cancel() {
          /* nothing was stored */
        },
        Preview: ({ scale }) => {
          const to = target();
          const hex = ANNOTATION_COLOR_HEX[color];
          const length = Math.hypot(to.x - start.x, to.y - start.y);
          if (length === 0) return null;
          const head = kind === 'arrow' ? arrowHead(start.x, start.y, to.x, to.y, stroke) : null;
          const shaftEnd = head ? head.base : [to.x, to.y];
          return (
            <g
              className={`ws-capture__line ws-capture__line--${kind}`}
              data-testid="capture-preview"
              data-kind={kind}
              data-line-pt={`${start.x},${start.y},${to.x},${to.y}`}
            >
              {(shaftEnd[0] !== start.x || shaftEnd[1] !== start.y) && (
                <line
                  x1={start.x * scale}
                  y1={start.y * scale}
                  x2={shaftEnd[0] * scale}
                  y2={shaftEnd[1] * scale}
                  stroke={hex}
                  strokeWidth={stroke * scale}
                  strokeLinecap="butt"
                />
              )}
              {head && (
                <polygon
                  points={[[to.x, to.y], head.left, head.right]
                    .map(([x, y]) => `${x * scale},${y * scale}`)
                    .join(' ')}
                  fill={hex}
                />
              )}
            </g>
          );
        },
      };
    },
  };
}

/* -------------------------------------------------------------------------- */
/* Freehand ink                                                                */
/* -------------------------------------------------------------------------- */

export function inkTool(meta: Meta): ToolDefinition {
  const highlighter = Boolean(meta.highlighter);
  return {
    id: meta.id ?? 'ink',
    label: meta.label,
    glyph: meta.glyph,
    hint: meta.hint,
    shortcut: meta.shortcut,
    group: meta.group ?? 'shapes',
    order: meta.order,
    menu: meta.menu,
    menuSection: meta.menuSection,
    cursor: 'crosshair',
    input: 'drag',
    placesOnClick: false,
    properties: ['color', 'stroke'],
    defaultColor: highlighter ? 'yellow' : 'black',
    requiresEditor: highlighter || undefined,
    beginGesture(ctx, pt): Gesture | null {
      if (!startGesture(ctx)) return null;
      const points: Point[] = [{ ...pt }];
      const { color } = ctx.props;
      // A highlighter is a marker, not a pen: never thinner than 8 pt.
      const stroke = highlighter ? Math.max(8, ctx.props.stroke) : ctx.props.stroke;
      const opacity = ctx.props.v7.opacity;
      const inkLook =
        highlighter
          ? { opacity: opacity < 1 ? opacity : null, freehand_highlight: true }
          : ctx.editor && opacity < 1
            ? { opacity, freehand_highlight: false }
            : undefined;
      const add = (next: Point) => {
        const last = points[points.length - 1];
        if (Math.hypot(next.x - last.x, next.y - last.y) >= INK_MIN_STEP_PT) points.push({ ...next });
      };
      return {
        move(next) {
          add(next);
        },
        end(next) {
          add(next);
          // Thinned (RDP, 0.5 pt), then cut to the server's limits: a path of
          // ≤ 2000 points, an element of ≤ 20 paths / 4000 points. A stroke
          // too long for one element becomes several -- still ONE step.
          const simplified = dedupe(simplifyPath(points));
          const elements = splitInk([simplified]);
          const existing = ctx.annotations.items.reduce(
            (sum, item: Annotation) => sum + (isInk(item) ? inkPointCount(item) : 0),
            0,
          );
          const adding = elements.reduce((sum, paths) => sum + paths.reduce((n, path) => n + Math.max(2, path.length), 0), 0);
          if (existing + adding > MAX_INK_POINTS_TOTAL) {
            ctx.notify(
              `Freehand drawing on this contract is limited to ${MAX_INK_POINTS_TOTAL.toLocaleString('en-US')} points ` +
                `(${existing.toLocaleString('en-US')} used, this stroke needs ${adding.toLocaleString('en-US')}). ` +
                'The stroke was not kept — delete some drawings or draw a shorter stroke.',
            );
            return;
          }
          if (!roomFor(ctx, elements.length)) return;
          const taken = new Set(ctx.annotations.items.map((item) => item.id));
          const made = elements
            .map((paths) => {
              let id = ctx.newId();
              while (taken.has(id)) id = ctx.newId();
              taken.add(id);
              const ink = makeInk({ id, page: ctx.page, paths, stroke, color }, ctx.pageSize);
              return ink && inkLook ? { ...ink, v7: inkLook } : ink;
            })
            .filter((item): item is NonNullable<typeof item> => item !== null);
          if (made.length === 0) return;
          if (made.length === 1) ctx.annotations.add(made[0]);
          else ctx.annotations.addMany(made, 'Draw freehand');
          ctx.selection.selectAnnotation(made[made.length - 1].id);
        },
        cancel() {
          /* nothing was stored */
        },
        Preview: ({ scale }) => (
          <polyline
            className="ws-capture__ink"
            data-testid="capture-preview"
            data-kind="ink"
            data-point-count={points.length}
            points={points.map((point) => `${(point.x * scale).toFixed(2)},${(point.y * scale).toFixed(2)}`).join(' ')}
            fill="none"
            stroke={ANNOTATION_COLOR_HEX[color]}
            strokeWidth={stroke * scale}
            strokeOpacity={inkLook ? (inkLook.opacity ?? 0.35) : undefined}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ),
      };
    },
  };
}

/* -------------------------------------------------------------------------- */
/* F2: Redact and Links -- a box drag that is not an annotation                 */
/* -------------------------------------------------------------------------- */

function boxTool(
  id: ToolId,
  meta: Meta,
  onBox: (ctx: ToolContext, box: BoxPt) => void,
  look: { className: string; kind: string },
): ToolDefinition {
  return {
    id,
    label: meta.label,
    glyph: meta.glyph,
    hint: meta.hint,
    shortcut: meta.shortcut,
    group: meta.group ?? 'whiteout',
    order: meta.order,
    menu: meta.menu,
    menuSection: meta.menuSection,
    cursor: 'crosshair',
    input: 'drag',
    placesOnClick: false,
    requiresEditor: true,
    // Not an annotation (an op of the edit session): the slot layer stays live.
    properties: [],
    beginGesture(ctx, pt): Gesture | null {
      ctx.selection.selectAnnotation(null);
      ctx.notify(null);
      const start = snap(pt);
      let end = start;
      const box = () => dragBox(start, end, false, ctx.pageSize);
      return {
        move(next) {
          end = snap(next);
        },
        end(next) {
          end = snap(next);
          const drawn = box();
          if (drawn.w < CLICK_PT || drawn.h < CLICK_PT) return;
          onBox(ctx, drawn);
        },
        cancel() {
          /* nothing was made */
        },
        Preview: ({ scale }) => {
          const drawn = box();
          return (
            <rect
              className={look.className}
              data-testid="capture-preview"
              data-kind={look.kind}
              data-box-pt={`${drawn.x},${drawn.y},${drawn.w},${drawn.h}`}
              x={drawn.x * scale}
              y={drawn.y * scale}
              width={drawn.w * scale}
              height={drawn.h * scale}
            />
          );
        },
      };
    },
  };
}

/** Redact: the box becomes a `redact` op (real removal, black fill). */
export function redactTool(meta: Meta): ToolDefinition {
  return boxTool('redact', meta, (ctx, box) => ctx.redact(box), {
    className: 'ws-capture__redact',
    kind: 'redact',
  });
}

/** Links: the box opens the link dialog (URL or page). */
export function linkTool(meta: Meta): ToolDefinition {
  return boxTool('links', meta, (ctx, box) => ctx.openLink(box), {
    className: 'ws-capture__link',
    kind: 'links',
  });
}
