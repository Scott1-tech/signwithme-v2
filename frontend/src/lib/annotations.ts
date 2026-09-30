import { FONT_CSS, FONT_FAMILIES, FONT_LABEL, isFontFamily, type FontFamily } from './fonts';
import type { PageSizePt } from './layout';
import { normalizeText } from './textCheck';
import {
  COORD_LIMIT,
  DEFAULT_COLOR,
  ID_PATTERN,
  MIN_BOX_PT,
  clamp,
  clampBoxOn,
  clampPointOn,
  clampStroke,
  finite,
  isAnnotationColor,
  tidy,
  type AnnotationColor,
  type AnnotationCommon,
  type BoxPt,
} from './annotationKinds/shared';
import {
  clampInkCorner,
  inkBounds,
  inkForRequest,
  inkPointCount,
  MAX_INK_POINTS_TOTAL,
  sameInk,
  sanitizeInk,
  translateInk,
  type InkAnnotation,
  type InkRequest,
} from './annotationKinds/ink';
import {
  clampBoxCorner,
  isShapeKind,
  keepShapeOn,
  sameShape,
  sanitizeShape,
  shapeBounds,
  shapeForRequest,
  type ShapeAnnotation,
  type ShapeKind,
  type ShapeRequest,
} from './annotationKinds/shape';
import {
  isLineKind,
  lineBounds,
  lineForRequest,
  sameLine,
  sanitizeLine,
  translateLine,
  type LineAnnotation,
  type LineKind,
  type LineRequest,
} from './annotationKinds/line';
import {
  NOTE_DEFAULT_COLOR,
  clampNoteAnchor,
  noteBounds,
  noteForRequest,
  sameNote,
  sanitizeNote,
  type NoteAnnotation,
  type NoteRequest,
} from './annotationKinds/note';
import {
  imageBounds,
  imageForRequest,
  sameImage,
  sanitizeImage,
  type ImageAnnotation,
  type ImageRequest,
} from './annotationKinds/image';
import {
  boxOf as v7BoxOf,
  readImageV7,
  readInkV7,
  readLineV7,
  readShapeV7,
  restyleAll,
  sameStamp,
  sameTextbox,
  sanitizeStamp,
  sanitizeTextbox,
  stampForRequest,
  textboxForRequest,
  type StampAnnotation,
  type TextboxAnnotation,
} from './annotationKinds/v7';
import {
  clampWhiteout,
  sameWhiteout,
  sanitizeWhiteout,
  whiteoutBounds,
  whiteoutForRequest,
  type WhiteoutAnnotation,
  type WhiteoutRequest,
} from './annotationKinds/whiteout';

export {
  ANNOTATION_COLORS,
  ANNOTATION_COLOR_HEX,
  ANNOTATION_COLOR_LABEL,
  DEFAULT_COLOR,
  DEFAULT_STROKE_PT,
  GRID_PT,
  ID_PATTERN,
  MIN_BOX_PT,
  SHAPE_FILL_ALPHA,
  STROKE_MAX_PT,
  STROKE_MIN_PT,
  STROKE_STEP_PT,
  clampStroke,
  isAnnotationColor,
  snapPt,
} from './annotationKinds/shared';
export type { AnnotationColor, BoxPt } from './annotationKinds/shared';
export type { InkAnnotation, InkPoint } from './annotationKinds/ink';
export type { ShapeAnnotation, ShapeKind } from './annotationKinds/shape';
export type { LineAnnotation, LineKind } from './annotationKinds/line';
export type { NoteAnnotation } from './annotationKinds/note';
export type { ImageAnnotation } from './annotationKinds/image';
export type { WhiteoutAnnotation } from './annotationKinds/whiteout';
export { WHITEOUT_NOTICE, isFreeWhiteout, makeWhiteout } from './annotationKinds/whiteout';
export { isShapeKind } from './annotationKinds/shape';
export { isLineKind } from './annotationKinds/line';
export { MAX_INK_POINTS_TOTAL, inkPointCount } from './annotationKinds/ink';

/**
 * Free annotations on a contract -- what the full-screen editor puts on the
 * page that is not a template signature and not a template date.
 *
 * Mirrors the optional `annotations` array of `POST /api/contracts/{id}/finalize`
 * (shared contract v4, extended by v5 and v6). The same three rules as
 * `lib/layout.ts` hold here:
 *
 * * **PDF points, top-left origin.** The same space `bbox` and `rule_y` speak,
 *   so `lib/viewport.ts` is the only place a coordinate meets the render scale.
 *   The point means a different thing per kind, and that difference is the
 *   whole of the geometry contract:
 *     - `text` / `date`  -> the **left end of the baseline** of the run;
 *     - `check` / `cross` -> the **centre** of the mark;
 *     - `whiteout`        -> the **top-left corner** of its box (`w_pt`/`h_pt`).
 * * **The server stays the authority.** Every limit below is re-checked there
 *   and a value outside it is a 422; what this module is for is making sure the
 *   browser never builds such a request in the first place.
 * * **Anything stored may be rubbish.** `localStorage` is hand-editable and
 *   outlives the code that wrote it, so nothing is read back without going
 *   through `sanitizeAnnotations`, which drops what it cannot make sense of
 *   rather than throwing.
 *
 * v6 (WE): six font families (`lib/fonts.ts`), optional `bold` / `italic`
 * (present only when `true` -- a run nobody bolded is the v5 entry byte for
 * byte), the `whiteout` kind and `group_id`. "Edit existing text" is a
 * **group**: one `whiteout` over the original line and one `text` on its
 * baseline, sharing a `group_id` (plan §7-6) -- placed, deleted and undone as
 * one, the whiteout locked where the line was, the text free to move.
 *
 * W3: the three MARKUP kinds -- `highlight`, `underline`, `strikeout` -- are
 * one or more boxes over the page's own text (`rects`, one per line, 1…50),
 * made from a text selection (`workspace/text/useTextSelection.ts`) or from the
 * search's matches. They carry `color` ALWAYS (their default is not black: a
 * highlight starts yellow, the other two red), and a derived anchor `x_pt` /
 * `y_pt` = the top-left of their rects' bounding box, so moving one is moving
 * a point like every other kind. The anchor is never stored as a source of
 * truth (it is recomputed from `rects` on every read) and never sent.
 *
 * W4: the DRAWING kinds -- `ink`, `rect`, `ellipse`, `line`, `arrow`, `note`,
 * `image` -- live in `lib/annotationKinds/<kind>.ts` (see `shared.ts` there
 * for why only they were split out); this file dispatches to them. They carry
 * `color` always (like a markup: black is a choice for them, and a note's
 * default is yellow), `stroke_pt` where they have one, and -- ink and line --
 * a DERIVED anchor `x_pt` / `y_pt` (the bounding box's top-left) that is never
 * sent, so a move is a move of a point like every other kind.
 *
 * W5: `whiteout` moved to `lib/annotationKinds/whiteout.ts` (the Whiteout
 * tool draws FREE ones -- moved and resized by eight handles, `hasBoxHandles`;
 * an edited line's grouped one stays locked). `clampAnnotationsToPages` is
 * the clamp a stored list gets once its pages' sizes are known.
 *
 * WHAT IS *NOT* HERE: the free signature (and the initial, W5: a small one). That is a row on the server
 * (`POST /api/contracts/{id}/signatures` with `field_id: null`), not a value in
 * this list -- see `overlay/FreeSignatureLayer.tsx` (`SIGNATURE_LOCK_NOTE`) for why it therefore cannot be
 * dragged and is not a step of the annotation history.
 */

/** The kinds this build draws and sends. */
export const ANNOTATION_KINDS = [
  'text',
  'date',
  'check',
  'cross',
  'whiteout',
  'highlight',
  'underline',
  'strikeout',
  'ink',
  'rect',
  'ellipse',
  'line',
  'arrow',
  'note',
  'image',
  // F2: markup v7 (B2) -- only drawn and sent where the server has the editor.
  'textbox',
  'stamp',
  'square_mark',
] as const;
export type AnnotationKind = (typeof ANNOTATION_KINDS)[number];
export type { TextboxAnnotation, StampAnnotation, TextboxRun } from './annotationKinds/v7';

/** The kinds W4 draws (`lib/annotationKinds/*`). */
export const DRAW_KINDS = ['ink', 'rect', 'ellipse', 'line', 'arrow', 'note', 'image'] as const;
export type DrawKind = (typeof DRAW_KINDS)[number];
export const isDrawKind = (kind: unknown): kind is DrawKind =>
  typeof kind === 'string' && (DRAW_KINDS as readonly string[]).includes(kind);
/** The kinds with a `stroke_pt`. */
export const isStrokeKind = (kind: unknown): kind is 'ink' | ShapeKind | LineKind =>
  kind === 'ink' || isShapeKind(kind) || isLineKind(kind);

/** The two that are drawn as a glyph centred on their point. */
export const MARK_KINDS = ['check', 'cross', 'square_mark'] as const;
export type MarkKind = (typeof MARK_KINDS)[number];

/** The three drawn over the page's own text, one box per line (W3). */
export const MARKUP_KINDS = ['highlight', 'underline', 'strikeout'] as const;
export type MarkupKind = (typeof MARKUP_KINDS)[number];

/** The two that are drawn as a run of text on its baseline. */
export type TextualKind = 'text' | 'date';

/** The six families the server accepts (contract v6, `lib/fonts.ts`). */
export type AnnotationFont = FontFamily;
export const ANNOTATION_FONTS = FONT_FAMILIES;
export const ANNOTATION_FONT_CSS = FONT_CSS;
export const ANNOTATION_FONT_LABEL = FONT_LABEL;
export const DEFAULT_FONT: AnnotationFont = 'Helvetica';

/**
 * Size limits (contract v4). A run of text stops at 48 pt; a mark goes to 72,
 * because a tick has to be able to fill a tick-box on a scanned form and those
 * are routinely larger than any line of type on the same page.
 */
export const SIZE_MIN = 6;
export const SIZE_MAX = 48;
export const MARK_SIZE_MIN = 6;
export const MARK_SIZE_MAX = 72;
export const DEFAULT_SIZE = 11;
/** A tick placed at 11 pt would be a speck; this is the size a box wants. */
export const DEFAULT_MARK_SIZE = 16;
/*
 * The palette (contract v5, `pdf_builder.py:177`) lives in
 * `annotationKinds/shared.ts` and is re-exported above. For the four v4 kinds
 * `black` is the default and is never stored or sent: an annotation without
 * `color` prints byte-for-byte as it did under v4, so a user who never touches
 * the palette sends exactly the v4 body (plan §8).
 */

/** The colour an annotation prints in (absent = black; a whiteout is always white, an image has none). */
export const colorOf = (
  item: { color?: AnnotationColor } | { kind: 'whiteout' } | { kind: 'image' } | TextboxAnnotation,
): AnnotationColor => {
  // F2: a text box speaks with its (first) run's colour.
  if ('runs' in item && Array.isArray(item.runs)) return item.runs[0]?.color ?? DEFAULT_COLOR;
  return ('color' in item ? item.color : undefined) ?? DEFAULT_COLOR;
};

/**
 * What a markup kind prints in when nobody chose (`pdf_builder.py`): a
 * highlight is yellow, underline and strikeout red. A markup annotation always
 * carries its colour explicitly -- black is a real choice for it, not "absent".
 */
export const MARKUP_DEFAULT_COLOR: Record<MarkupKind, AnnotationColor> = {
  highlight: 'yellow',
  underline: 'red',
  strikeout: 'red',
};
/** Highlight fill opacity, normal blend (`pdf_builder.HIGHLIGHT_ALPHA`). */
export const HIGHLIGHT_ALPHA = 0.35;
/** Underline / strikeout bar: `max(MIN, h × RATIO)` pt (`pdf_builder.markup_line_thickness`). */
export const MARKUP_LINE_MIN_PT = 0.75;
export const MARKUP_LINE_RATIO = 0.08;
/** One markup annotation covers at most this many boxes (`MAX_MARKUP_RECTS`). */
export const MAX_MARKUP_RECTS = 50;

/** The bar thickness of an underline / strikeout over a box `h` points tall. */
export const markupLineThickness = (h: number) => Math.max(MARKUP_LINE_MIN_PT, h * MARKUP_LINE_RATIO);

/**
 * The box one markup rect actually prints (PDF points, top-left origin):
 * a highlight fills the rect; an underline is a bar whose LOWER edge is the
 * rect's lower edge; a strikeout is the same bar centred on the rect's middle.
 */
export function markupInk(kind: MarkupKind, rect: MarkupRect): MarkupRect {
  if (kind === 'highlight') return rect;
  const thickness = markupLineThickness(rect.h_pt);
  const top =
    kind === 'underline'
      ? rect.y_pt + rect.h_pt - thickness
      : rect.y_pt + (rect.h_pt - thickness) / 2;
  return { x_pt: rect.x_pt, y_pt: top, w_pt: rect.w_pt, h_pt: thickness };
}

/**
 * The server refuses a longer list, or a longer run; the browser never builds
 * one. v5 raised the list from 200 to 500 (one highlight run is one element).
 */
export const MAX_ANNOTATIONS = 500;
export const MAX_TEXT_LENGTH = 200;

/*
 * Everything lands on the same half-point grid (`GRID_PT`, re-exported from
 * `annotationKinds/shared.ts`): the arrow keys step on it, a drag snaps to it,
 * and a click places on it. One grid means a mark nudged with the keyboard and
 * the same mark dragged with the mouse can hold exactly the same position, and
 * it keeps float noise out of the stored JSON.
 */
/** One arrow-key press; Shift takes the large step. */
export const NUDGE_STEP = 0.5;
export const NUDGE_STEP_LARGE = 2;
/** One press of − / + (or an arrow on the corner handle), in points. */
export const SIZE_STEP = 1;
export const SIZE_STEP_LARGE = 4;

interface AnnotationBase extends AnnotationCommon {
  x_pt: number;
  y_pt: number;
  size_pt: number;
  /** v5, optional. Never `'black'`: black is the absence of the key. */
  color?: AnnotationColor;
}

/** What a run of text is set in, besides its family (contract v6). */
interface TextStyle {
  text: string;
  font: AnnotationFont;
  /** Only ever `true`; not bold is the absence of the key (v5 body unchanged). */
  bold?: true;
  italic?: true;
}

/** A run typed by hand. `x_pt` / `y_pt` is the left end of its baseline. */
export interface TextAnnotation extends AnnotationBase, TextStyle {
  kind: 'text';
}

/**
 * A date dropped by the date tool. Identical to a text run in every respect
 * the server cares about -- the kind is kept apart so the PDF builder (and a
 * later audit) can tell "the signer typed this" from "the signer dated it
 * here", and so the tool can re-offer the chosen date rather than a blank box.
 */
export interface DateAnnotation extends AnnotationBase, TextStyle {
  kind: 'date';
}

/**
 * A tick or a cross. No `text` and no `font` at all: the glyph is the kind, so
 * carrying either would be a second, contradictable source of truth.
 * `x_pt` / `y_pt` is the **centre**, and `size_pt` the side of its square box.
 */
export interface MarkAnnotation extends AnnotationBase {
  kind: MarkKind;
}

/*
 * The opaque white box (v5) -- `WhiteoutAnnotation` -- lives in
 * `annotationKinds/whiteout.ts` since W5 (its tool made it a kind of its own:
 * `make`, `sanitize`, `forRequest`, `same`, `clamp`), re-exported above.
 */

/** One box of a markup annotation, PDF points, top-left origin (contract v5 `rects[]`). */
export interface MarkupRect {
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
}

/**
 * Highlight / underline / strikeout (v5, W3): one box per line of the text it
 * marks. `x_pt` / `y_pt` is DERIVED -- the top-left corner of the boxes'
 * bounding box -- so a move is a translation of every box; it is recomputed
 * from `rects` on every read and never sent. `color` is always present (see
 * `MARKUP_DEFAULT_COLOR`).
 */
export interface MarkupAnnotation extends AnnotationCommon {
  kind: MarkupKind;
  rects: MarkupRect[];
  color: AnnotationColor;
  x_pt: number;
  y_pt: number;
}

export type Annotation =
  | TextAnnotation
  | DateAnnotation
  | MarkAnnotation
  | WhiteoutAnnotation
  | MarkupAnnotation
  | InkAnnotation
  | ShapeAnnotation
  | LineAnnotation
  | NoteAnnotation
  | ImageAnnotation
  | TextboxAnnotation
  | StampAnnotation;
export type TextualAnnotation = TextAnnotation | DateAnnotation;
/** Everything with a `size_pt`: runs and marks (nothing else). */
export type SizedAnnotation = TextAnnotation | DateAnnotation | MarkAnnotation;
/** Everything with a `stroke_pt` (W4). */
export type StrokedAnnotation = InkAnnotation | ShapeAnnotation | LineAnnotation;
/** Everything drawn by a W4 tool. */
export type DrawAnnotation = InkAnnotation | ShapeAnnotation | LineAnnotation | NoteAnnotation | ImageAnnotation;

export const isMarkKind = (kind: unknown): kind is MarkKind =>
  kind === 'check' || kind === 'cross' || kind === 'square_mark';
/** F2: the two v7 boxes of their own. */
export const isTextbox = (item: Annotation): item is TextboxAnnotation => item.kind === 'textbox';
export const isStamp = (item: Annotation): item is StampAnnotation => item.kind === 'stamp';
export const isV7Box = (item: Annotation): item is TextboxAnnotation | StampAnnotation =>
  item.kind === 'textbox' || item.kind === 'stamp';
export const isTextualKind = (kind: unknown): kind is TextualKind =>
  kind === 'text' || kind === 'date';
export const isMarkupKind = (kind: unknown): kind is MarkupKind =>
  kind === 'highlight' || kind === 'underline' || kind === 'strikeout';

/** A mark: drawn centred, sized to 72, and carrying neither text nor font. */
export const isMark = (item: Annotation): item is MarkAnnotation => isMarkKind(item.kind);
/** A run: drawn on its baseline, sized to 48, and carrying text and a font. */
export const isTextual = (item: Annotation): item is TextualAnnotation =>
  isTextualKind(item.kind);
export const isWhiteout = (item: Annotation): item is WhiteoutAnnotation =>
  item.kind === 'whiteout';
export const isMarkup = (item: Annotation): item is MarkupAnnotation => isMarkupKind(item.kind);
export const isSized = (item: Annotation): item is SizedAnnotation =>
  isTextualKind(item.kind) || isMarkKind(item.kind);
/** A kind with no size and no font (the PropertiesBar greys both out). */
export const isBoxKind = (kind: AnnotationKind): boolean => kind === 'whiteout' || isMarkupKind(kind);
/** W4 kinds. */
export const isInk = (item: Annotation): item is InkAnnotation => item.kind === 'ink';
export const isShape = (item: Annotation): item is ShapeAnnotation => isShapeKind(item.kind);
export const isLine = (item: Annotation): item is LineAnnotation => isLineKind(item.kind);
export const isNote = (item: Annotation): item is NoteAnnotation => item.kind === 'note';
export const isImage = (item: Annotation): item is ImageAnnotation => item.kind === 'image';
export const isStroked = (item: Annotation): item is StrokedAnnotation => isStrokeKind(item.kind);
export const isDraw = (item: Annotation): item is DrawAnnotation => isDrawKind(item.kind);
/** A box the user resizes by 8 handles (rect, ellipse, image). */
export const isResizableBox = (
  item: Annotation,
): item is ShapeAnnotation | ImageAnnotation | TextboxAnnotation | StampAnnotation =>
  isShapeKind(item.kind) || item.kind === 'image' || item.kind === 'textbox' || item.kind === 'stamp';
/**
 * A box with the eight handles (W5): a rect / ellipse / image, and a FREE
 * whiteout (the Whiteout tool's). The whiteout of an edited line is locked
 * where the line is (plan §7-6) and has none.
 */
export const hasBoxHandles = (
  item: Annotation,
): item is ShapeAnnotation | ImageAnnotation | WhiteoutAnnotation | TextboxAnnotation | StampAnnotation =>
  isResizableBox(item) || (isWhiteout(item) && !item.group_id);

/**
 * The geometric box of an annotation in PDF points, for every kind whose box
 * is a definition rather than a measurement (a run of text is measured by the
 * browser -- `null` here).
 */
export function annotationBounds(item: Annotation): BoxPt | null {
  switch (item.kind) {
    case 'whiteout':
      return whiteoutBounds(item);
    case 'highlight':
    case 'underline':
    case 'strikeout':
      return rectsBounds(item.rects);
    case 'ink':
      return inkBounds(item);
    case 'rect':
    case 'ellipse':
      return shapeBounds(item);
    case 'line':
    case 'arrow':
      return lineBounds(item);
    case 'note':
      return noteBounds(item);
    case 'image':
      return imageBounds(item);
    case 'textbox':
    case 'stamp':
      return v7BoxOf(item);
    case 'check':
    case 'cross':
    case 'square_mark':
      return { x: item.x_pt - item.size_pt / 2, y: item.y_pt - item.size_pt / 2, w: item.size_pt, h: item.size_pt };
    default:
      return null;
  }
}

/** The bounding box of a list of rects (`x`, `y`, `w`, `h`; zeros for none). */
export function rectsBounds(rects: readonly MarkupRect[]): { x: number; y: number; w: number; h: number } {
  if (rects.length === 0) return { x: 0, y: 0, w: 0, h: 0 };
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  rects.forEach((rect) => {
    left = Math.min(left, rect.x_pt);
    top = Math.min(top, rect.y_pt);
    right = Math.max(right, rect.x_pt + rect.w_pt);
    bottom = Math.max(bottom, rect.y_pt + rect.h_pt);
  });
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/** The inclusive [min, max] this kind's size lives in. */
export function sizeLimits(kind: AnnotationKind): [number, number] {
  if (kind === 'textbox') return [6, 72];
  return isMarkKind(kind) ? [MARK_SIZE_MIN, MARK_SIZE_MAX] : [SIZE_MIN, SIZE_MAX];
}

/** The size this kind starts at when nothing else is armed. */
export const defaultSizeFor = (kind: AnnotationKind) =>
  kind === 'square_mark' ? 10 : isMarkKind(kind) ? DEFAULT_MARK_SIZE : DEFAULT_SIZE;

/** A size into its kind's range, on the same grid every coordinate uses. */
export function clampSize(value: number, kind: AnnotationKind = 'text'): number {
  const [min, max] = sizeLimits(kind);
  return tidy(clamp(Math.round(value / 0.5) * 0.5, min, max));
}

/**
 * A point pulled inside its page, so the server never sees a coordinate it
 * would answer 422 to. With no page size yet (the document is still opening)
 * only the absolute ceiling applies -- the drag that produced it will be
 * clamped again the moment the size arrives.
 */
export const clampPoint = clampPointOn;

/**
 * A box pulled inside its page (both corners), never smaller than `MIN_BOX_PT`.
 * Two decimals, like every other coordinate.
 */
export const clampBox = clampBoxOn;

/* -------------------------------------------------------------------------- */
/* Pure edits (written per kind, so the union never has to be widened by a     */
/* spread the compiler cannot narrow)                                          */
/* -------------------------------------------------------------------------- */

/**
 * A markup annotation whose boxes are translated so their bounding box starts
 * at (`x_pt`, `y_pt`). The anchor is then read back from the moved boxes, so
 * it can never disagree with them by a rounding.
 */
function translateMarkup(item: MarkupAnnotation, x_pt: number, y_pt: number): MarkupAnnotation {
  const dx = x_pt - item.x_pt;
  const dy = y_pt - item.y_pt;
  if (dx === 0 && dy === 0) return item;
  const rects = item.rects.map((rect) => ({
    x_pt: tidy(rect.x_pt + dx),
    y_pt: tidy(rect.y_pt + dy),
    w_pt: rect.w_pt,
    h_pt: rect.h_pt,
  }));
  const bounds = rectsBounds(rects);
  return { ...item, rects, x_pt: tidy(bounds.x), y_pt: tidy(bounds.y) };
}

/**
 * New point, in PDF points. Baseline for a run, centre for a mark, corner for
 * a whiteout, the bounding box's corner for a markup (every box moves along).
 */
export function moveAnnotation(item: Annotation, x_pt: number, y_pt: number): Annotation {
  switch (item.kind) {
    case 'whiteout':
      return { ...item, x_pt, y_pt };
    case 'check':
    case 'cross':
      return { ...item, x_pt, y_pt };
    case 'highlight':
    case 'underline':
    case 'strikeout':
      return translateMarkup(item, x_pt, y_pt);
    case 'ink':
      return translateInk(item, x_pt, y_pt);
    case 'line':
    case 'arrow':
      return translateLine(item, x_pt, y_pt);
    case 'rect':
    case 'ellipse':
    case 'note':
    case 'image':
    case 'textbox':
    case 'stamp':
      return item.x_pt === x_pt && item.y_pt === y_pt ? item : { ...item, x_pt, y_pt };
    default:
      return { ...item, x_pt, y_pt };
  }
}

/** New stroke width (0.5…12 pt) of an ink / shape / line; anything else untouched. */
export function restrokeAnnotation(item: Annotation, stroke_pt: number): Annotation {
  if (!isStroked(item)) return item;
  const next = clampStroke(stroke_pt);
  return item.stroke_pt === next ? item : { ...item, stroke_pt: next };
}

/** A rect / ellipse filled (translucent, alpha 0.2) or not; anything else untouched. */
export function refillAnnotation(item: Annotation, fill: boolean): Annotation {
  if (!isShape(item)) return item;
  return item.fill === fill ? item : { ...item, fill };
}

/** New size, clamped to this kind's own range. A whiteout and a markup have none. */
export function resizeAnnotation(item: Annotation, size_pt: number): Annotation {
  if (!isSized(item)) return item;
  const size = clampSize(size_pt, item.kind);
  return isMark(item) ? { ...item, size_pt: size } : { ...item, size_pt: size };
}

/** New text. A mark has none, so it comes back untouched rather than widened. */
export function retypeAnnotation(item: Annotation, text: string): Annotation {
  return isTextual(item) ? { ...item, text } : item;
}

/** New font. Same rule as `retypeAnnotation`. */
export function refontAnnotation(item: Annotation, font: AnnotationFont): Annotation {
  return isTextual(item) ? { ...item, font } : item;
}

/**
 * Bold / italic on a run. `false` REMOVES the key, so a run nobody bolded is
 * the v5 shape exactly. Returns the original when nothing changes.
 */
export function restyleAnnotation(
  item: Annotation,
  style: { bold?: boolean; italic?: boolean },
): Annotation {
  if (!isTextual(item)) return item;
  const bold = style.bold ?? Boolean(item.bold);
  const italic = style.italic ?? Boolean(item.italic);
  if (bold === Boolean(item.bold) && italic === Boolean(item.italic)) return item;
  const copy = { ...item };
  if (bold) copy.bold = true;
  else delete copy.bold;
  if (italic) copy.italic = true;
  else delete copy.italic;
  return copy;
}

/**
 * The same annotation in another colour. Black (or `undefined`) REMOVES the
 * key, so "back to black" leaves exactly the v4 shape behind. Returns the
 * original when nothing changes -- and always for a whiteout, which is white.
 */
export function recolorAnnotation<T extends Annotation>(item: T, color: AnnotationColor | undefined): T {
  if (item.kind === 'whiteout' || item.kind === 'image') return item;
  if (isTextbox(item)) {
    const next = color ?? DEFAULT_COLOR;
    return item.runs.every((run) => run.color === next) ? item : ({ ...item, runs: restyleAll(item.runs, { color: next }) } as T);
  }
  if (isStamp(item)) {
    const next = color ?? 'red';
    return item.color === next ? item : ({ ...item, color: next } as T);
  }
  if (isMarkup(item)) {
    // A markup always names its colour: black is a choice, absent is its default.
    const next = color ?? MARKUP_DEFAULT_COLOR[item.kind];
    return item.color === next ? item : ({ ...item, color: next } as T);
  }
  if (isDraw(item)) {
    // So does every W4 kind (a note's default is yellow, the rest black).
    const drawn = item as InkAnnotation | ShapeAnnotation | LineAnnotation | NoteAnnotation;
    const next = color ?? (drawn.kind === 'note' ? NOTE_DEFAULT_COLOR : DEFAULT_COLOR);
    return drawn.color === next ? item : ({ ...item, color: next } as T);
  }
  const sized = item as SizedAnnotation;
  const next = color && color !== DEFAULT_COLOR ? color : undefined;
  if (sized.color === next) return item;
  const copy = { ...sized };
  if (next) copy.color = next;
  else delete copy.color;
  return copy as T;
}

/** A fresh run of either textual kind, built so the discriminant stays narrow. */
export function makeTextualAnnotation(
  kind: TextualKind,
  base: {
    id: string;
    page: number;
    x_pt: number;
    y_pt: number;
    text: string;
    font: AnnotationFont;
    size_pt: number;
    color?: AnnotationColor;
    bold?: boolean;
    italic?: boolean;
    group_id?: string;
  },
): TextualAnnotation {
  const { color, bold, italic, group_id, ...rest } = base;
  const made: TextualAnnotation =
    kind === 'date' ? { ...rest, kind: 'date' } : { ...rest, kind: 'text' };
  if (group_id) made.group_id = group_id;
  return restyleAnnotation(recolorAnnotation(made, color), { bold, italic }) as TextualAnnotation;
}

/** A fresh mark of either kind. */
export function makeMarkAnnotation(
  kind: MarkKind,
  base: {
    id: string;
    page: number;
    x_pt: number;
    y_pt: number;
    size_pt: number;
    color?: AnnotationColor;
  },
): MarkAnnotation {
  const { color, ...rest } = base;
  const made: MarkAnnotation =
    kind === 'cross'
      ? { ...rest, kind: 'cross' }
      : kind === 'square_mark'
        ? { ...rest, kind: 'square_mark' }
        : { ...rest, kind: 'check' };
  return recolorAnnotation(made, color);
}

/**
 * A fresh markup annotation. Every box is clamped to the page (≥ `MIN_BOX_PT`
 * on both axes), at most `MAX_MARKUP_RECTS` are kept, and the colour is the
 * kind's own default unless one is given. `null` when no box is left.
 */
export function makeMarkupAnnotation(
  kind: MarkupKind,
  base: {
    id: string;
    page: number;
    rects: ReadonlyArray<{ x: number; y: number; w: number; h: number }>;
    color?: AnnotationColor;
    group_id?: string;
  },
  page: PageSizePt | null,
): MarkupAnnotation | null {
  const rects: MarkupRect[] = [];
  for (const rect of base.rects) {
    if (rects.length >= MAX_MARKUP_RECTS) break;
    if (![rect.x, rect.y, rect.w, rect.h].every((value) => Number.isFinite(value))) continue;
    if (rect.w <= 0 || rect.h <= 0) continue;
    const box = clampBox(rect, page);
    rects.push({ x_pt: box.x, y_pt: box.y, w_pt: box.w, h_pt: box.h });
  }
  if (rects.length === 0) return null;
  const bounds = rectsBounds(rects);
  const made: MarkupAnnotation = {
    id: base.id,
    kind,
    page: base.page,
    rects,
    color: base.color ?? MARKUP_DEFAULT_COLOR[kind],
    x_pt: tidy(bounds.x),
    y_pt: tidy(bounds.y),
  };
  if (base.group_id) made.group_id = base.group_id;
  return made;
}

/** The size of what an anchor carries along: a box's own, a markup's bounding box, else nothing. */
function anchoredSize(item: Annotation): { w: number; h: number } | null {
  if (isWhiteout(item)) return { w: item.w_pt, h: item.h_pt };
  if (isMarkup(item)) {
    const bounds = rectsBounds(item.rects);
    return { w: bounds.w, h: bounds.h };
  }
  return null;
}

/**
 * Where `item`'s anchor may go on this page: a point for a run or a mark
 * (`clampPoint`), and for a box -- a whiteout, a markup's bounding box -- a
 * corner that keeps the WHOLE box inside the page (the server refuses any box
 * that leaves it: 422 `annotation_outside_page`).
 */
export function clampAnchor(
  item: Annotation,
  point: { x: number; y: number },
  page: PageSizePt | null,
): { x: number; y: number } {
  // W4: the anchor carries the whole drawing along (every ink point, both
  // line ends, the whole box, the whole note icon).
  switch (item.kind) {
    case 'ink':
      return clampInkCorner(item, point, page);
    case 'line':
    case 'arrow': {
      const bounds = lineBounds(item);
      return clampBoxCorner({ w: bounds.w, h: bounds.h }, point, page);
    }
    case 'rect':
    case 'ellipse':
    case 'image':
    case 'textbox':
    case 'stamp':
      return clampBoxCorner({ w: item.w_pt, h: item.h_pt }, point, page);
    case 'note':
      return clampNoteAnchor(point, page);
    default:
      break;
  }
  const size = anchoredSize(item);
  if (!size) return clampPoint(point, page);
  const width = page && page.width > 0 ? page.width : COORD_LIMIT;
  const height = page && page.height > 0 ? page.height : COORD_LIMIT;
  return {
    x: tidy(clamp(point.x, 0, Math.max(0, width - size.w))),
    y: tidy(clamp(point.y, 0, Math.max(0, height - size.h))),
  };
}

/** The same clamp, applied to an annotation. Returns the original when nothing moved. */
export function clampAnnotation(annotation: Annotation, page: PageSizePt | null): Annotation {
  if (isResizableBox(annotation)) return keepShapeOn(annotation, page);
  if (isInk(annotation) || isLine(annotation) || isNote(annotation)) {
    const { x, y } = clampAnchor(annotation, { x: annotation.x_pt, y: annotation.y_pt }, page);
    return x === annotation.x_pt && y === annotation.y_pt ? annotation : moveAnnotation(annotation, x, y);
  }
  if (isMarkup(annotation)) {
    const { x, y } = clampAnchor(annotation, { x: annotation.x_pt, y: annotation.y_pt }, page);
    return x === annotation.x_pt && y === annotation.y_pt ? annotation : moveAnnotation(annotation, x, y);
  }
  if (isWhiteout(annotation)) return clampWhiteout(annotation, page);
  const { x, y } = clampPoint({ x: annotation.x_pt, y: annotation.y_pt }, page);
  if (x === annotation.x_pt && y === annotation.y_pt) return annotation;
  return moveAnnotation(annotation, x, y);
}

/**
 * W5: the whole list pulled onto its pages -- the clamp that had no caller.
 *
 * Every placement and every gesture already clamps to the page it knows, but
 * an annotation READ BACK (`swm.annotations.<id>`) was only held to the
 * absolute ceiling (`COORD_LIMIT`): one stored before its page's size was
 * known, or written for a page of another size, stayed off the page and the
 * server answered the finalize with 422 `annotation_outside_page`. This runs
 * once the sizes are known (and again on any later change, where it finds
 * nothing to do). An entry whose page size is not known yet is left alone.
 *
 * Returns the SAME array when nothing moved, and how many entries did.
 */
export function clampAnnotationsToPages(
  items: readonly Annotation[],
  pageSizeOf: (page: number | null) => PageSizePt | null,
): { items: Annotation[]; moved: number } {
  let moved = 0;
  const next = items.map((item) => {
    const size = pageSizeOf(item.page);
    if (!size) return item;
    const clamped = clampAnnotation(item, size);
    if (clamped !== item) moved += 1;
    return clamped;
  });
  return { items: moved === 0 ? (items as Annotation[]) : next, moved };
}

/* -------------------------------------------------------------------------- */
/* Identity                                                                    */
/* -------------------------------------------------------------------------- */

let sequence = 0;

/**
 * A fresh id that satisfies the server's charset and is not already taken.
 * Time plus a counter: two annotations placed in the same millisecond still
 * differ, and the value stays short enough to read in a stored blob.
 */
export function makeAnnotationId(taken: Iterable<string> = [], prefix = 'a'): string {
  const used = taken instanceof Set ? taken : new Set(taken);
  for (let attempt = 0; attempt < 1000; attempt += 1) {
    sequence += 1;
    const id = `${prefix}${Date.now().toString(36)}${sequence.toString(36)}`;
    if (ID_PATTERN.test(id) && !used.has(id)) return id;
  }
  // Unreachable in practice; a valid id still has to come back.
  return `${prefix}${Math.random().toString(36).slice(2, 12)}`;
}

/* -------------------------------------------------------------------------- */
/* Groups (plan §7-6)                                                          */
/* -------------------------------------------------------------------------- */

/**
 * Every id in the group `id` belongs to (itself included), in list order.
 * An annotation outside any group is a group of one.
 */
export function groupIdsOf(items: readonly Annotation[], id: string | null): string[] {
  if (!id) return [];
  const item = items.find((entry) => entry.id === id);
  if (!item) return [];
  if (!item.group_id) return [item.id];
  return items.filter((entry) => entry.group_id === item.group_id).map((entry) => entry.id);
}

/** The group's members, the TEXT first (the one the toolbar describes and the arrows move). */
export function groupSelectionOrder(items: readonly Annotation[], id: string): string[] {
  const ids = groupIdsOf(items, id);
  if (ids.length <= 1) return ids.length ? ids : [id];
  const members = ids.map((memberId) => items.find((entry) => entry.id === memberId)!);
  const primary = members.find((member) => !isWhiteout(member)) ?? members[0];
  return [primary.id, ...ids.filter((memberId) => memberId !== primary.id)];
}

/** An "edit existing text" group, as the Objects list and the edit tool see it. */
export interface EditGroup {
  groupId: string;
  page: number;
  whiteout: WhiteoutAnnotation;
  text: TextualAnnotation | null;
}

/** Every whiteout + text group, in list order of the whiteout. */
export function editGroups(items: readonly Annotation[]): EditGroup[] {
  const groups: EditGroup[] = [];
  items.forEach((item) => {
    if (!isWhiteout(item) || !item.group_id) return;
    const text =
      items.find(
        (entry): entry is TextualAnnotation => entry.group_id === item.group_id && isTextual(entry),
      ) ?? null;
    groups.push({ groupId: item.group_id, page: item.page, whiteout: item, text });
  });
  return groups;
}

/** A fresh group id (same charset as an annotation id). */
export const makeGroupId = (items: readonly Annotation[]) =>
  makeAnnotationId(
    items.map((item) => item.group_id).filter((value): value is string => Boolean(value)),
    'g',
  );

/* -------------------------------------------------------------------------- */
/* Reading anything back                                                       */
/* -------------------------------------------------------------------------- */

function readGroupId(value: unknown): string | undefined {
  return typeof value === 'string' && ID_PATTERN.test(value) ? value : undefined;
}

function sanitizeAnnotation(raw: unknown, taken: ReadonlySet<string>): Annotation | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const source = raw as Record<string, unknown>;

  // F2: the v7 wire kinds that are the W4 kinds with a v7 look come back as
  // those kinds (`shape2` -> rect / ellipse / line / arrow, `ink2` -> ink,
  // `image2` -> image) carrying `v7`; a `v7` already on an entry is kept.
  const v7Kind = typeof source.kind === 'string' ? source.kind : '';
  if (v7Kind === 'textbox' || v7Kind === 'stamp') {
    const id7 = typeof source.id === 'string' ? source.id : '';
    const page7 = finite(source.page);
    if (!ID_PATTERN.test(id7) || taken.has(id7) || page7 === null || !Number.isInteger(page7) || page7 < 1) return null;
    const base7 = { id: id7, page: page7, group_id: readGroupId(source.group_id), source };
    return v7Kind === 'textbox' ? sanitizeTextbox(base7) : sanitizeStamp(base7);
  }
  if (v7Kind === 'shape2' || v7Kind === 'ink2' || v7Kind === 'image2') {
    const converted = fromV7Wire(source);
    return converted ? sanitizeAnnotation(converted, taken) : null;
  }

  // An entry this build cannot DRAW is dropped rather than carried invisibly
  // into a request -- a newer build may write another kind here one day.
  const kind = source.kind;
  if (
    !isMarkKind(kind) &&
    !isTextualKind(kind) &&
    !isMarkupKind(kind) &&
    kind !== 'whiteout' &&
    !isDrawKind(kind)
  ) {
    return null;
  }

  const id = typeof source.id === 'string' ? source.id : '';
  if (!ID_PATTERN.test(id) || taken.has(id)) return null;

  const page = finite(source.page);
  if (page === null || !Number.isInteger(page) || page < 1) return null;

  if (isDrawKind(kind)) {
    const base = { id, page, group_id: readGroupId(source.group_id), source };
    switch (kind) {
      case 'ink': {
        const made = sanitizeInk(base);
        const v7 = readInkV7(source.v7);
        return made && v7 ? { ...made, v7 } : made;
      }
      case 'rect':
      case 'ellipse': {
        const made = sanitizeShape(kind, base);
        const v7 = readShapeV7(source.v7);
        return made && v7 ? { ...made, v7 } : made;
      }
      case 'line':
      case 'arrow': {
        const made = sanitizeLine(kind, base);
        const v7 = readLineV7(source.v7, kind === 'arrow');
        return made && v7 ? { ...made, v7 } : made;
      }
      case 'note':
        return sanitizeNote(base);
      case 'image': {
        const made = sanitizeImage(base);
        const v7 = readImageV7(source.v7);
        return made && v7 ? { ...made, v7 } : made;
      }
      default:
        return null;
    }
  }

  if (isMarkupKind(kind)) {
    // The boxes are the truth; a stored `x_pt` / `y_pt` is ignored and derived again.
    if (!Array.isArray(source.rects)) return null;
    const rects: Array<{ x: number; y: number; w: number; h: number }> = [];
    for (const raw of source.rects as unknown[]) {
      if (!raw || typeof raw !== 'object') continue;
      const box = raw as Record<string, unknown>;
      const rx = finite(box.x_pt);
      const ry = finite(box.y_pt);
      const rw = finite(box.w_pt);
      const rh = finite(box.h_pt);
      if (rx === null || ry === null || rw === null || rh === null) continue;
      if (rw < MIN_BOX_PT || rh < MIN_BOX_PT) continue;
      rects.push({ x: rx, y: ry, w: rw, h: rh });
    }
    return makeMarkupAnnotation(
      kind,
      {
        id,
        page,
        rects,
        color: isAnnotationColor(source.color) ? source.color : undefined,
        group_id: readGroupId(source.group_id),
      },
      null,
    );
  }

  const group_id = readGroupId(source.group_id);
  if (kind === 'whiteout') return sanitizeWhiteout({ id, page, group_id, source });

  const x = finite(source.x_pt);
  const y = finite(source.y_pt);
  if (x === null || y === null) return null;

  const base = {
    id,
    page,
    x_pt: tidy(clamp(x, 0, COORD_LIMIT)),
    y_pt: tidy(clamp(y, 0, COORD_LIMIT)),
    // Only a palette NAME survives; anything else (a hex, `white`, a typo) is
    // read as black -- i.e. the key is simply not there.
    color: isAnnotationColor(source.color) ? source.color : undefined,
  };

  if (isMarkKind(kind)) {
    // No text, no font: a stored entry that carries them is simply read
    // without them, because for a mark the glyph IS the kind.
    const mark = makeMarkAnnotation(kind, {
      ...base,
      size_pt: clampSize(finite(source.size_pt) ?? DEFAULT_MARK_SIZE, kind),
    });
    if (group_id) mark.group_id = group_id;
    return mark;
  }

  const text = typeof source.text === 'string' ? source.text.slice(0, MAX_TEXT_LENGTH) : '';
  // The server refuses an empty run, and an invisible annotation is worse than
  // no annotation: the user would drag something they cannot see.
  if (text.trim() === '') return null;

  const font = isFontFamily(source.font) ? source.font : DEFAULT_FONT;

  return makeTextualAnnotation(kind, {
    ...base,
    text,
    font,
    size_pt: clampSize(finite(source.size_pt) ?? DEFAULT_SIZE, kind),
    // Strict: only the boolean `true` means bold (the server's StrictBool).
    bold: source.bold === true,
    italic: source.italic === true,
    group_id,
  });
}

/**
 * Coerce anything -- a stored blob, a hand-edited entry, a response from a
 * newer build -- into a list this build can draw and the server would accept.
 * Never throws: a corrupt entry is simply not in the result.
 */
export function sanitizeAnnotations(raw: unknown): Annotation[] {
  if (!Array.isArray(raw)) return [];
  const items: Annotation[] = [];
  const taken = new Set<string>();
  // Ink points of the whole list (the server's per-request 20 000): an ink
  // entry that would pass it is dropped, like any other entry the server
  // would refuse.
  let inkPoints = 0;
  for (const entry of raw) {
    if (items.length >= MAX_ANNOTATIONS) break;
    const annotation = sanitizeAnnotation(entry, taken);
    if (!annotation) continue;
    if (isInk(annotation)) {
      const count = inkPointCount(annotation);
      if (inkPoints + count > MAX_INK_POINTS_TOTAL) continue;
      inkPoints += count;
    }
    taken.add(annotation.id);
    items.push(annotation);
  }
  return items;
}

/* -------------------------------------------------------------------------- */
/* Storage                                                                     */
/* -------------------------------------------------------------------------- */

export const annotationsStorageKey = (contractId: string) => `swm.annotations.${contractId}`;

/** Best-effort, exactly like `loadLayout`: private mode or rubbish yields none. */
export function loadAnnotations(contractId: string | undefined): Annotation[] {
  if (!contractId) return [];
  try {
    const stored = localStorage.getItem(annotationsStorageKey(contractId));
    return stored ? sanitizeAnnotations(JSON.parse(stored) as unknown) : [];
  } catch {
    return [];
  }
}

export function saveAnnotations(contractId: string | undefined, items: readonly Annotation[]): void {
  if (!contractId) return;
  try {
    // An empty list is the absence of the key, not `[]`: nothing is left behind
    // for a later build (or another contract's debugging) to trip over.
    if (items.length === 0) localStorage.removeItem(annotationsStorageKey(contractId));
    else localStorage.setItem(annotationsStorageKey(contractId), JSON.stringify(items));
  } catch {
    /* private mode: the annotations simply do not persist */
  }
}

/* -------------------------------------------------------------------------- */
/* Pure list helpers (the store commits these)                                 */
/* -------------------------------------------------------------------------- */

export const findAnnotation = (items: readonly Annotation[], id: string | null) =>
  id ? items.find((item) => item.id === id) : undefined;

/** Two lists of boxes, box for box. */
export function sameRects(a: readonly MarkupRect[], b: readonly MarkupRect[]): boolean {
  return (
    a.length === b.length &&
    a.every(
      (rect, index) =>
        rect.x_pt === b[index].x_pt &&
        rect.y_pt === b[index].y_pt &&
        rect.w_pt === b[index].w_pt &&
        rect.h_pt === b[index].h_pt,
    )
  );
}

/** Everything that would make two entries print differently. */
function sameAnnotation(a: Annotation, b: Annotation): boolean {
  if (a.kind !== b.kind || a.page !== b.page) return false;
  if (a.x_pt !== b.x_pt || a.y_pt !== b.y_pt || a.group_id !== b.group_id) return false;
  if (isTextbox(a) || isTextbox(b)) return isTextbox(a) && isTextbox(b) && sameTextbox(a, b);
  if (isStamp(a) || isStamp(b)) return isStamp(a) && isStamp(b) && sameStamp(a, b);
  if (isDraw(a) || isDraw(b)) {
    if (a.kind !== b.kind) return false;
    // F2: a v7 look is part of what prints.
    const v7a = (a as { v7?: unknown }).v7;
    const v7b = (b as { v7?: unknown }).v7;
    if (JSON.stringify(v7a ?? null) !== JSON.stringify(v7b ?? null)) return false;
    if (isInk(a) && isInk(b)) return sameInk(a, b);
    if (isShape(a) && isShape(b)) return sameShape(a, b);
    if (isLine(a) && isLine(b)) return sameLine(a, b);
    if (isNote(a) && isNote(b)) return sameNote(a, b);
    if (isImage(a) && isImage(b)) return sameImage(a, b);
    return false;
  }
  if (isMarkup(a) || isMarkup(b)) {
    return isMarkup(a) && isMarkup(b) && a.color === b.color && sameRects(a.rects, b.rects);
  }
  if (isWhiteout(a) || isWhiteout(b)) {
    return isWhiteout(a) && isWhiteout(b) && sameWhiteout(a, b);
  }
  const sa = a as MarkAnnotation | TextualAnnotation;
  const sb = b as MarkAnnotation | TextualAnnotation;
  if (sa.size_pt !== sb.size_pt || colorOf(sa) !== colorOf(sb)) return false;
  if (isTextual(a) && isTextual(b)) {
    return (
      a.text === b.text &&
      a.font === b.font &&
      Boolean(a.bold) === Boolean(b.bold) &&
      Boolean(a.italic) === Boolean(b.italic)
    );
  }
  return true;
}

/** Replace the annotation with this id, or append it. Same array when nothing changed. */
export function withAnnotation(items: readonly Annotation[], next: Annotation): Annotation[] {
  const index = items.findIndex((item) => item.id === next.id);
  if (index < 0) {
    if (items.length >= MAX_ANNOTATIONS) return items as Annotation[];
    return [...items, next];
  }
  if (sameAnnotation(items[index], next)) return items as Annotation[];
  const copy = [...items];
  copy[index] = next;
  return copy;
}

/**
 * Append several at once -- all of them or none (a group must never land
 * half-way because the list filled up). Same array when nothing was added.
 */
export function withAnnotations(items: readonly Annotation[], added: readonly Annotation[]): Annotation[] {
  if (added.length === 0) return items as Annotation[];
  if (items.length + added.length > MAX_ANNOTATIONS) return items as Annotation[];
  const taken = new Set(items.map((item) => item.id));
  if (added.some((item) => taken.has(item.id))) return items as Annotation[];
  return [...items, ...added];
}

/** Drop one annotation. Same array when it was not there. */
export function withoutAnnotation(items: readonly Annotation[], id: string): Annotation[] {
  if (!items.some((item) => item.id === id)) return items as Annotation[];
  return items.filter((item) => item.id !== id);
}

/** Drop several. Same array when none of them was there. */
export function withoutAnnotations(items: readonly Annotation[], ids: readonly string[]): Annotation[] {
  const gone = new Set(ids);
  if (!items.some((item) => gone.has(item.id))) return items as Annotation[];
  return items.filter((item) => !gone.has(item.id));
}

/* -------------------------------------------------------------------------- */
/* The request body                                                            */
/* -------------------------------------------------------------------------- */

/**
 * One entry of the finalize body's optional `annotations` array (contract v4,
 * plus v5's optional `color` / `group_id` / `whiteout` and v6's `bold` /
 * `italic`). Every optional key is present only when it says something, so an
 * annotation nobody recoloured, bolded or grouped is the v4 entry byte for byte.
 */
export type AnnotationRequest =
  | {
      id: string;
      kind: TextualKind;
      page: number;
      x_pt: number;
      y_pt: number;
      text: string;
      font: AnnotationFont;
      size_pt: number;
      color?: AnnotationColor;
      bold?: true;
      italic?: true;
      group_id?: string;
    }
  | {
      id: string;
      kind: MarkKind;
      page: number;
      x_pt: number;
      y_pt: number;
      size_pt: number;
      color?: AnnotationColor;
      group_id?: string;
    }
  | WhiteoutRequest
  | {
      id: string;
      kind: MarkupKind;
      page: number;
      rects: MarkupRect[];
      color: AnnotationColor;
      group_id?: string;
    }
  | InkRequest
  | ShapeRequest
  | LineRequest
  | NoteRequest
  | ImageRequest
  // F2: markup v7 (B2). Only ever built where the server has the editor.
  | (Record<string, unknown> & { id: string; kind: string; page: number });

/** `{ color }` for a non-black palette name, else nothing at all. */
const colorKey = (item: SizedAnnotation): { color?: AnnotationColor } =>
  item.color && item.color !== DEFAULT_COLOR && isAnnotationColor(item.color)
    ? { color: item.color }
    : {};

const groupKey = (item: AnnotationCommon): { group_id?: string } =>
  item.group_id && ID_PATTERN.test(item.group_id) ? { group_id: item.group_id } : {};

/**
 * What actually leaves the browser: sanitized once more (the list in hand came
 * from the store, but a stored blob is the same shape and this is the last
 * place before the wire), capped, rounded to the grid, text in NFC.
 *
 * A mark's entry carries **no** `text` and **no** `font` key at all -- not an
 * empty string and not a null. The server's model for a mark has no such
 * field, and an unexpected key is a 422. `bold` / `italic` are sent only as
 * `true`: `false` is the same as leaving the key out, and leaving it out keeps
 * the v5 body identical.
 *
 * A markup entry (W3) is `{id, kind, page, rects, color, group_id?}` -- the
 * boxes and the colour, ALWAYS named; no `x_pt` / `y_pt` (the server's model
 * has none, and an unexpected key is a 422).
 *
 * W4 (every key always present, nothing derived):
 *   ink      `{id, kind, page, paths: [[{x_pt, y_pt}…]…], stroke_pt, color}`
 *   rect / ellipse `{id, kind, page, x_pt, y_pt, w_pt, h_pt, stroke_pt, color, fill}`
 *   line / arrow   `{id, kind, page, x1_pt, y1_pt, x2_pt, y2_pt, stroke_pt, color}`
 *   note     `{id, kind, page, x_pt, y_pt, text, color}`
 *   image    `{id, kind, page, asset_id, x_pt, y_pt, w_pt, h_pt}`
 * (+ `group_id` only in a group).
 */
export function annotationsForRequest(items: readonly Annotation[]): AnnotationRequest[] {
  return sanitizeAnnotations(items as unknown).map((item): AnnotationRequest => {
    // F2: markup v7 (EDITOR-V2-PLAN §3). A W4 kind WITHOUT `v7` is untouched.
    if (isTextbox(item)) return textboxForRequest(item);
    if (isStamp(item)) return stampForRequest(item);
    if (isInk(item) && item.v7) return ink2ForRequest(item);
    if ((isShape(item) || isLine(item)) && item.v7) return shape2ForRequest(item);
    if (isImage(item) && item.v7) return image2ForRequest(item);
    if (isInk(item)) return inkForRequest(item);
    if (isShape(item)) return shapeForRequest(item);
    if (isLine(item)) return lineForRequest(item);
    if (isNote(item)) return noteForRequest(item);
    if (isImage(item)) return imageForRequest(item);
    if (isMarkup(item)) {
      return {
        id: item.id,
        kind: item.kind,
        page: item.page,
        rects: item.rects.map((rect) => ({
          x_pt: tidy(rect.x_pt),
          y_pt: tidy(rect.y_pt),
          w_pt: tidy(rect.w_pt),
          h_pt: tidy(rect.h_pt),
        })),
        color: item.color,
        ...groupKey(item),
      };
    }
    if (isWhiteout(item)) return whiteoutForRequest(item);
    if (isMark(item)) {
      return {
        id: item.id,
        kind: item.kind,
        page: item.page,
        x_pt: tidy(item.x_pt),
        y_pt: tidy(item.y_pt),
        size_pt: tidy(item.size_pt),
        ...colorKey(item),
        ...groupKey(item),
      };
    }
    const textual = item as TextualAnnotation;
    return {
      id: textual.id,
      kind: textual.kind,
      page: textual.page,
      x_pt: tidy(textual.x_pt),
      y_pt: tidy(textual.y_pt),
      text: normalizeText(textual.text),
      font: textual.font,
      size_pt: tidy(textual.size_pt),
      ...colorKey(textual),
      ...(textual.bold === true ? { bold: true as const } : {}),
      ...(textual.italic === true ? { italic: true as const } : {}),
      ...groupKey(textual),
    };
  });
}

/* -------------------------------------------------------------------------- */
/* F2: the v7 wire of the W4 kinds (`shape2`, `ink2`, `image2`)                 */
/* -------------------------------------------------------------------------- */

function ink2ForRequest(item: InkAnnotation): AnnotationRequest {
  const base = inkForRequest(item);
  const v7 = item.v7!;
  return {
    ...base,
    kind: 'ink2',
    ...(v7.opacity !== null ? { opacity: v7.opacity } : {}),
    freehand_highlight: v7.freehand_highlight,
  };
}

function shape2ForRequest(item: ShapeAnnotation | LineAnnotation): AnnotationRequest {
  if (isShape(item)) {
    const v7 = item.v7!;
    return {
      id: item.id,
      kind: 'shape2',
      page: item.page,
      shape: item.kind,
      x_pt: tidy(item.x_pt),
      y_pt: tidy(item.y_pt),
      w_pt: tidy(item.w_pt),
      h_pt: tidy(item.h_pt),
      stroke_color: item.color,
      fill_color: v7.fill_color,
      stroke_pt: clampStroke(item.stroke_pt),
      opacity: v7.opacity,
      dash: v7.dash,
      ...groupKey(item),
    };
  }
  const line = item as LineAnnotation;
  const v7 = line.v7!;
  return {
    id: line.id,
    kind: 'shape2',
    page: line.page,
    shape: line.kind,
    x1_pt: tidy(line.x1_pt),
    y1_pt: tidy(line.y1_pt),
    x2_pt: tidy(line.x2_pt),
    y2_pt: tidy(line.y2_pt),
    stroke_color: line.color,
    stroke_pt: clampStroke(line.stroke_pt),
    opacity: v7.opacity,
    dash: v7.dash,
    heads: v7.heads,
    ...groupKey(line),
  };
}

function image2ForRequest(item: ImageAnnotation): AnnotationRequest {
  const v7 = item.v7!;
  return {
    ...imageForRequest(item),
    kind: 'image2',
    rotation_deg: v7.rotation_deg,
    opacity: v7.opacity,
  };
}

/** A `shape2` / `ink2` / `image2` element as the W4 kind it is, with its `v7`. */
function fromV7Wire(source: Record<string, unknown>): Record<string, unknown> | null {
  const { kind, ...rest } = source;
  if (kind === 'ink2') {
    const { opacity, freehand_highlight, ...ink } = rest;
    return { ...ink, kind: 'ink', v7: { opacity: opacity ?? null, freehand_highlight: freehand_highlight === true } };
  }
  if (kind === 'image2') {
    const { opacity, rotation_deg, ...image } = rest;
    return { ...image, kind: 'image', v7: { opacity: opacity ?? 1, rotation_deg: rotation_deg ?? 0 } };
  }
  if (kind === 'shape2') {
    const { shape, stroke_color, fill_color, opacity, dash, heads, ...geometry } = rest;
    if (shape === 'rect' || shape === 'ellipse') {
      return {
        ...geometry,
        kind: shape,
        color: stroke_color ?? 'black',
        fill: false,
        v7: { fill_color: fill_color ?? null, opacity: opacity ?? 1, dash: dash ?? 'solid' },
      };
    }
    if (shape === 'line' || shape === 'arrow') {
      return {
        ...geometry,
        kind: shape,
        color: stroke_color ?? 'black',
        v7: { opacity: opacity ?? 1, dash: dash ?? 'solid', heads: heads ?? (shape === 'arrow' ? 'end' : 'none') },
      };
    }
  }
  return null;
}
