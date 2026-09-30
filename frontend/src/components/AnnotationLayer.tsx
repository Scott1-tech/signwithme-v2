import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FocusEvent } from 'react';
import {
  ANNOTATION_COLOR_HEX,
  ANNOTATION_FONT_CSS,
  HIGHLIGHT_ALPHA,
  SHAPE_FILL_ALPHA,
  annotationBounds,
  colorOf,
  hasBoxHandles,
  inkPointCount,
  isDraw,
  isImage,
  isInk,
  isLine,
  isMark,
  isMarkup,
  isNote,
  isResizableBox,
  isShape,
  isSized,
  isStroked,
  isTextual,
  isTextbox,
  isStamp,
  isV7Box,
  isWhiteout,
  markupInk,
  type Annotation,
  type AnnotationColor,
  type AnnotationFont,
  type DrawAnnotation,
  type LineAnnotation,
  type MarkKind,
  type MarkupAnnotation,
  type MarkupRect,
  type NoteAnnotation,
  type TextualKind,
  type WhiteoutAnnotation,
} from '../lib/annotations';
import { inkPathData } from '../lib/annotationKinds/ink';
import { arrowHead } from '../lib/annotationKinds/line';
import { MAX_NOTE_TEXT, NOTE_ICON_PT } from '../lib/annotationKinds/note';
import { BOX_HANDLES, HANDLE_AT, HANDLE_CURSOR } from '../lib/annotationKinds/resize';
import { LIBERATION_OF, ensureFontLoaded, measureTextPt, useFontEpoch } from '../lib/fonts';
import { isLocked, type AnnotationEdit } from '../hooks/useAnnotationEdit';
import type { AssetImageEntry } from '../hooks/useAnnotationAssets';
import { ptToPx, type PxBox } from '../lib/viewport';
import { dashArray, inkOpacity, v7Head } from '../lib/annotationKinds/v7';
import { TextboxEditor, renderStamp, renderTextbox, type TextboxEditorState } from './V7Drawings';

/**
 * One page's annotations, drawn and made editable.
 *
 * TWO LAYERS, ON PURPOSE
 * ----------------------
 * The **drawing** is SVG -- a `<text>` on its baseline for a run, a stroked
 * outline centred on its point for a mark, a white `<rect>` for a whiteout.
 * That is the only honest preview of what the PDF builder will produce,
 * because `x_pt` / `y_pt` IS a baseline (or IS a centre) and an HTML box's
 * baseline depends on font metrics the browser will not tell us about.
 * Whiteouts are not drawn here (W5): the PDF prints them under everything
 * else, the signatures included (plan §7-4), so they are their own layer
 * below the slots (`overlay/WhiteoutLayer.tsx`) -- a whiteout never hides the
 * text that replaces what it covers, nor a signature placed on it. Their hit
 * boxes (and a free one's eight handles) are still here, on top.
 *
 * The **interaction** is ordinary HTML on top of it: a `<button>` covering the
 * measured box of each annotation, plus one corner handle for the selected
 * one. That is what makes an annotation reachable by keyboard and nameable to a
 * screen reader -- an SVG `<text>` is neither. The SVG layer itself takes no
 * pointer events at all, so a click on empty paper still reaches the page
 * underneath and places the next annotation.
 *
 * WHERE THE BOXES COME FROM, AND WHY IT IS NOT ONE ANSWER
 * ------------------------------------------------------
 * A run's box is **measured** (`getBBox()` after every render that could have
 * moved it -- and again once its font face has loaded, `useFontEpoch`):
 * nobody can say how wide a string in Times at 14 pt is without asking the
 * browser, so the frame follows a drag, a resize and a zoom exactly.
 *
 * A mark's box is **computed**: it is a square of `size_pt` centred on the
 * point, and that is a definition rather than a measurement. Computing it also
 * keeps the frame honest about the *stroke*, which `getBBox()` leaves out --
 * measuring a tick would hand back a box narrower than the ink it contains.
 * A whiteout's box is its own rectangle.
 *
 * MARKUP (W3)
 * -----------
 * A highlight / underline / strikeout is drawn exactly as the PDF builder
 * prints it (`markupInk`): a highlight fills each box with its colour at
 * alpha 0.35 (normal blend), an underline is a bar of `max(0.75, h × 0.08)` pt
 * along the bottom of each box, a strikeout the same bar through its middle.
 * They are drawn right above the whiteouts and below every run and mark (plan
 * §2.4 b). Its hit surface is one button PER BOX (the first carries the
 * annotation's testid and data): a two-line highlight is not clickable in the
 * empty corners of its bounding box, where other text lives. No corner grip --
 * a markup has no size; it moves as a whole.
 *
 * GROUPS (WE)
 * -----------
 * The whiteout + text of an edited line are one group: pressing either
 * selects both (`selectedIds`), the whiteout is locked (no grab cursor, no
 * drag, no grip), the text moves and resizes like any run.
 *
 * DRAWINGS (W4)
 * -------------
 * Ink, rect, ellipse, line, arrow and image are drawn above the markups and
 * below the runs and marks, in list order (the PDF builder prints them in
 * request order after the signatures); the stroke is centred on the geometry,
 * ink has round caps / joins, a line / arrow butt caps, an arrow a filled head
 * of 6 × 3 × stroke (`arrowHead`, the builder's own formula), a shape's fill
 * is the colour at alpha 0.2, an image fills its box exactly. A note is a
 * `/Text` annotation the viewer draws ON TOP of the page, so its icon is drawn
 * last; selecting it shows its text (`note-popup-<id>`). A rect / ellipse /
 * image has eight handles (`ann-handle-<dir>`), a line / arrow two
 * (`ann-handle-start` / `-end`); ink and notes move whole. A line's hit box
 * lies ALONG the line (rotated), not over its bounding box, so the empty
 * corners of a diagonal stay clickable.
 *
 * WHAT IS NOT AN ANNOTATION
 * -------------------------
 * The free signature is a **row on the server**, placed through the unchanged
 * `POST /api/contracts/{id}/signatures` with `field_id: null`. Since W1 it is
 * drawn by `overlay/FreeSignatureLayer.tsx` (`.swm-sig`), not here: it has no
 * hit surface, no corner handle and no place in the undo history -- see
 * `SIGNATURE_LOCK_NOTE` there.
 */

/** Breathing room around an annotation's own box, in CSS pixels. */
const HIT_PAD = 3;
/** The corner handle's side, in CSS pixels. Mirrored in `editor.css`. */
const HANDLE_PX = 12;
/**
 * Where an HTML input's first line sits relative to its top edge, as a
 * fraction of the font size. Only the *editing* box is positioned with it --
 * what is committed is drawn by the SVG above, on the exact baseline.
 */
const INPUT_ASCENT = 0.82;
/**
 * The edit-text box sits EXACTLY on the line it replaces: its baseline, in em
 * from the top of the text box, for each Liberation family at `line-height:
 * 1.2` (half-leading + ascent, from the fonts' own hhea metrics), plus the
 * border and padding in pixels (`.swm-ann-input--edit`).
 */
const EDIT_BASELINE_EM = { LiberationSans: 0.9465, LiberationSerif: 0.9375, LiberationMono: 0.8665 };
const EDIT_INSET_PX = 2;
const EDIT_LEFT_PX = 5;
/** Tolerance before "wider than the original line" is said, in points. */
const WIDER_TOLERANCE_PT = 0.5;

/**
 * Each mark, drawn inside a box of side 1 centred on (0, 0), so one `scale()`
 * of `size_pt x renderScale` puts it exactly where the contract says: centred
 * on its point, `size_pt` across. Deliberately a path and not a glyph -- "✔"
 * is a different shape in every font, and half of them do not have it at all.
 */
const MARK_PATH: Record<MarkKind, string> = {
  check: 'M -0.36 0.03 L -0.12 0.30 L 0.36 -0.30',
  cross: 'M -0.30 -0.30 L 0.30 0.30 M 0.30 -0.30 L -0.30 0.30',
  // F2 (Forms ▸ Filled square, v7 `square_mark`): FILLED, side = size_pt.
  square_mark: 'M -0.5 -0.5 H 0.5 V 0.5 H -0.5 Z',
};
/** Stroke width as a fraction of the side; scales with the mark. */
const MARK_STROKE = 0.14;

/** The line an edit-text box replaces (unrotated PDF points). */
export interface EditTextLine {
  /** Left end of the baseline. */
  x: number;
  baseline: number;
  /** The ink box: what the whiteout covers (before its 0.5 pt margin). */
  y: number;
  width: number;
  height: number;
  /** The line as the PDF has it. */
  text: string;
}

/** What makes an inline box an "edit existing text" one (WE). */
export interface EditTextSession {
  line: EditTextLine;
  /** The group being re-edited, or `null` for a first edit of this line. */
  groupId: string | null;
  /** What the tool picked for the line; a commit that changes nothing adds nothing. */
  auto: { text: string; font: AnnotationFont; bold: boolean; italic: boolean; size_pt: number; color?: AnnotationColor };
}

/** The inline text box: a run being typed for the first time, or retyped. */
export interface InlineEditor {
  /** The annotation being retyped, or `null` while a new run is being typed. */
  id: string | null;
  /** Which textual kind is being committed -- a typed run, or a dated one. */
  kind: TextualKind;
  page: number;
  x_pt: number;
  y_pt: number;
  font: AnnotationFont;
  size_pt: number;
  /** v5 colour the committed run will carry (absent = black). */
  color?: AnnotationColor;
  /** v6 face. */
  bold?: boolean;
  italic?: boolean;
  value: string;
  /** Set by the edit-text tool (WE): the box replaces an existing line. */
  edit?: EditTextSession;
  /** Why the last commit was refused (a character the font cannot print). */
  error?: string | null;
}

/** The sticky-note text box (W4): a new note being written, or one being retyped. */
export interface NoteEditor {
  /** The note being retyped, or `null` for a new one. */
  id: string | null;
  page: number;
  /** The icon's top-left corner (PDF points). */
  x_pt: number;
  y_pt: number;
  color: AnnotationColor;
  value: string;
  /** Why the last save was refused (a character the PDF cannot carry, too long). */
  error?: string | null;
}

interface Props {
  pageNumber: number;
  /** Annotations on this page, with any live gesture already applied. */
  items: Annotation[];
  scale: number;
  selectedId: string | null;
  /** Every selected id (a group selects all its members). */
  selectedIds?: readonly string[];
  /** The inline box, when it belongs to this page. */
  editor: InlineEditor | null;
  /** The note box (W4), when it belongs to this page. */
  noteEditor?: NoteEditor | null;
  /** The pictures `image` annotations point at (W4). */
  assets?: ReadonlyMap<string, AssetImageEntry>;
  /**
   * The gesture bindings (stable) and, ON THE PAGE WHERE A GESTURE RUNS ONLY,
   * which annotation is being dragged / resized. Every other page gets nulls
   * and so keeps its memoised render while a mark moves elsewhere (W2).
   */
  edit: Pick<
    AnnotationEdit,
    'bindMark' | 'bindHandle' | 'bindShapeHandle' | 'stepSize' | 'draggingId' | 'resizingId'
  >;
  onSelect: (id: string) => void;
  onEdit: (annotation: Annotation) => void;
  onEditorChange: (value: string) => void;
  onEditorCommit: () => void;
  onEditorCancel: () => void;
  onNoteChange?: (value: string) => void;
  onNoteCommit?: () => void;
  onNoteCancel?: () => void;
  /** F2: the text box editor, when it belongs to this page. */
  boxEditor?: TextboxEditorState | null;
  onBoxChange?: (value: string) => void;
  onBoxCommit?: () => void;
  onBoxCancel?: () => void;
}

const NO_ASSETS: ReadonlyMap<string, AssetImageEntry> = new Map();
/** A line's hit box is at least this thick (CSS pixels), whatever its stroke. */
const LINE_HIT_PX = 10;
/** The side of a box handle (CSS pixels). Mirrored in `editor.css`. */
const BOX_HANDLE_PX = 10;

/** A line's hit box: a bar along it, rotated about its start. */
function lineHitBox(item: LineAnnotation, scale: number) {
  const x1 = ptToPx(item.x1_pt, scale);
  const y1 = ptToPx(item.y1_pt, scale);
  const x2 = ptToPx(item.x2_pt, scale);
  const y2 = ptToPx(item.y2_pt, scale);
  const length = Math.hypot(x2 - x1, y2 - y1);
  const thick = Math.max(LINE_HIT_PX, ptToPx(item.stroke_pt, scale)) + HIT_PAD * 2;
  return {
    left: x1 - HIT_PAD,
    top: y1 - thick / 2,
    width: length + HIT_PAD * 2,
    height: thick,
    angle: Math.atan2(y2 - y1, x2 - x1),
  };
}

const sameBoxes = (a: Record<string, PxBox>, b: Record<string, PxBox>): boolean => {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((key) => {
    const one = a[key];
    const two = b[key];
    return (
      two !== undefined &&
      Math.abs(one.left - two.left) < 0.5 &&
      Math.abs(one.top - two.top) < 0.5 &&
      Math.abs(one.width - two.width) < 0.5 &&
      Math.abs(one.height - two.height) < 0.5
    );
  });
};

/** One markup box, in CSS pixels, with the hit padding. */
function markupRectBox(rect: MarkupRect, scale: number): PxBox {
  return {
    left: ptToPx(rect.x_pt, scale) - HIT_PAD,
    top: ptToPx(rect.y_pt, scale) - HIT_PAD,
    width: ptToPx(rect.w_pt, scale) + HIT_PAD * 2,
    height: ptToPx(rect.h_pt, scale) + HIT_PAD * 2,
  };
}

/** The boxes of a markup, as the JSON `data-ann-rects` carries (PDF points). */
const rectsJson = (item: MarkupAnnotation) =>
  JSON.stringify(item.rects.map((rect) => [rect.x_pt, rect.y_pt, rect.w_pt, rect.h_pt]));

/** A mark's square, in CSS pixels relative to the page. A definition, not a measurement. */
function markBox(item: Annotation, scale: number): PxBox {
  if (isWhiteout(item)) return whiteoutBox(item, scale);
  if (isMarkup(item)) return markupRectBox(item.rects[0], scale);
  if (isLine(item)) {
    const bar = lineHitBox(item, scale);
    return { left: bar.left, top: bar.top, width: bar.width, height: bar.height };
  }
  if (isDraw(item) || isV7Box(item)) {
    // Ink, shapes, images, notes, text boxes, stamps: their own box, plus the stroke and the pad.
    const bounds = annotationBounds(item)!;
    const reach = isStroked(item) ? ptToPx(item.stroke_pt, scale) / 2 : 0;
    return {
      left: ptToPx(bounds.x, scale) - reach - HIT_PAD,
      top: ptToPx(bounds.y, scale) - reach - HIT_PAD,
      width: ptToPx(bounds.w, scale) + reach * 2 + HIT_PAD * 2,
      height: ptToPx(bounds.h, scale) + reach * 2 + HIT_PAD * 2,
    };
  }
  const side = ptToPx(item.size_pt, scale);
  return {
    left: ptToPx(item.x_pt, scale) - side / 2 - HIT_PAD,
    top: ptToPx(item.y_pt, scale) - side / 2 - HIT_PAD,
    width: side + HIT_PAD * 2,
    height: side + HIT_PAD * 2,
  };
}

/** A whiteout's own rectangle, in CSS pixels. */
function whiteoutBox(item: WhiteoutAnnotation, scale: number): PxBox {
  return {
    left: ptToPx(item.x_pt, scale),
    top: ptToPx(item.y_pt, scale),
    width: ptToPx(item.w_pt, scale),
    height: ptToPx(item.h_pt, scale),
  };
}

/** What a screen reader is told an annotation is. */
function describe(item: Annotation): string {
  switch (item.kind) {
    case 'check':
      return 'Check mark';
    case 'cross':
      return 'Cross mark';
    case 'whiteout':
      return item.group_id ? 'Whiteout over the edited line' : 'Whiteout';
    case 'highlight':
    case 'underline':
    case 'strikeout': {
      const name = { highlight: 'Highlight', underline: 'Underline', strikeout: 'Strikeout' }[item.kind];
      const lines = item.rects.length;
      return `${name} (${lines} line${lines === 1 ? '' : 's'})`;
    }
    case 'date':
      return `Date annotation: ${item.text}`;
    case 'ink': {
      const strokes = item.paths.length;
      return `Freehand drawing (${strokes} stroke${strokes === 1 ? '' : 's'})`;
    }
    case 'rect':
      return `Rectangle${item.fill ? ' (filled)' : ''}`;
    case 'ellipse':
      return `Ellipse${item.fill ? ' (filled)' : ''}`;
    case 'line':
      return 'Line';
    case 'arrow':
      return 'Arrow';
    case 'note':
      return `Sticky note: ${item.text}`;
    case 'image':
      return 'Image';
    case 'square_mark':
      return 'Filled square';
    case 'textbox':
      return `Text box: ${item.runs.map((run) => run.text).join('')}`;
    case 'stamp':
      return `Stamp: ${item.text ?? item.preset?.toUpperCase() ?? ''}${item.date_text ? ` (${item.date_text})` : ''}`;
    default:
      return `${item.group_id ? 'Edited text' : 'Text annotation'}: ${item.text}`;
  }
}

/** The tooltip on an annotation's hit surface. */
function hint(item: Annotation): string {
  if (isWhiteout(item)) {
    return item.group_id
      ? 'Whiteout over the original line — it stays in place; Delete removes the edit'
      : 'Whiteout — drag to move, handles resize it (Shift: square). The dashed edge is only on screen: ' +
          'the PDF gets a plain white box, and the text under it stays in the file.';
  }
  if (isMarkup(item)) return `${describe(item)} — drag to move, Delete removes it`;
  if (isNote(item)) return 'Sticky note — drag to move, double-click to edit its text';
  if (isInk(item)) return `${describe(item)} — drag to move, Delete removes it`;
  if (isLine(item)) return `${describe(item)} — drag to move, drag an end to reshape (Shift: 45°)`;
  if (isImage(item)) return 'Image — drag to move, handles resize it (Shift: free ratio)';
  if (isShape(item)) return `${describe(item)} — drag to move, handles resize it (Shift: square)`;
  if (isTextbox(item)) return 'Text box — drag to move, handles resize it, double-click (or Enter) to edit its text';
  if (isStamp(item)) return 'Stamp — drag to move, handles resize it';
  return isMark(item)
    ? `${describe(item)} — drag to move, corner grip to resize`
    : `${item.text} — drag to move, double-click to edit`;
}

/** Does moving the focus here keep the inline box open (the font/size/colour controls)? */
const keepsEditorOpen = (target: EventTarget | null): boolean =>
  target instanceof Element && Boolean(target.closest('.ws-props__group--appearance, .ws-color__palette'));

export function AnnotationLayer({
  pageNumber,
  items,
  scale,
  selectedId,
  selectedIds,
  editor,
  noteEditor = null,
  assets = NO_ASSETS,
  edit,
  onSelect,
  onEdit,
  onEditorChange,
  onEditorCommit,
  onEditorCancel,
  onNoteChange,
  onNoteCommit,
  onNoteCancel,
  boxEditor = null,
  onBoxChange,
  onBoxCommit,
  onBoxCancel,
}: Props) {
  const textRefs = useRef(new Map<string, SVGTextElement>());
  const [boxes, setBoxes] = useState<Record<string, PxBox>>({});
  const inputRef = useRef<HTMLInputElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const fontEpoch = useFontEpoch();

  const runs = useMemo(() => items.filter(isTextual), [items]);
  const marks = useMemo(() => items.filter(isMark), [items]);
  const markups = useMemo(() => items.filter(isMarkup), [items]);
  /** W4 drawings, in list order (notes apart: the viewer draws them on top). */
  const drawings = useMemo(
    () => items.filter((item): item is DrawAnnotation => isDraw(item) && !isNote(item)),
    [items],
  );
  const notes = useMemo(() => items.filter(isNote), [items]);
  /** F2: the v7 boxes (text box, stamp), in list order, over the drawings. */
  const boxes7 = useMemo(() => items.filter(isV7Box), [items]);
  /**
   * Hit surfaces, bottom to top: markups first -- a highlight lies over text
   * that a run or a mark may also sit on, and that one must stay clickable --
   * then everything else in list order.
   */
  const hitOrder = useMemo(
    () => [...items.filter(isMarkup), ...items.filter((item) => !isMarkup(item))],
    [items],
  );
  const selectedSet = useMemo(
    () => new Set(selectedIds && selectedIds.length ? selectedIds : selectedId ? [selectedId] : []),
    [selectedIds, selectedId],
  );

  // Ask for every face on the page up front: a run measured before its face
  // has arrived would get a fallback font's box.
  useEffect(() => {
    runs.forEach((item) => void ensureFontLoaded(item.font, Boolean(item.bold), Boolean(item.italic)));
  }, [runs]);
  useEffect(() => {
    if (editor) void ensureFontLoaded(editor.font, Boolean(editor.bold), Boolean(editor.italic));
  }, [editor?.font, editor?.bold, editor?.italic]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ref callbacks keep their identity, or every run would detach and re-attach
  // on each render -- and the measurement below would never settle.
  const refFor = useMemo(() => {
    const map = new Map<string, (element: SVGTextElement | null) => void>();
    runs.forEach((item) => {
      map.set(item.id, (element) => {
        if (element) textRefs.current.set(item.id, element);
        else textRefs.current.delete(item.id);
      });
    });
    return map;
  }, [runs]);

  // Measure after the browser has laid the text out but before it paints, so a
  // frame is never one render behind the run it belongs to. `sameBoxes` is what
  // stops the state write from feeding itself. Marks are not in here: their box
  // is computed below, from the same two numbers that drew them. `fontEpoch`
  // re-measures once a face has finished loading.
  useLayoutEffect(() => {
    const next: Record<string, PxBox> = {};
    runs.forEach((item) => {
      const element = textRefs.current.get(item.id);
      if (!element) return;
      try {
        const box = element.getBBox();
        next[item.id] = {
          left: box.x - HIT_PAD,
          top: box.y - HIT_PAD,
          width: box.width + HIT_PAD * 2,
          height: box.height + HIT_PAD * 2,
        };
      } catch {
        /* a node that is not rendered has no box; it will be measured again */
      }
    });
    setBoxes((current) => (sameBoxes(current, next) ? current : next));
  }, [runs, scale, fontEpoch]);

  const boxOf = (item: Annotation): PxBox | undefined =>
    isTextual(item) ? boxes[item.id] : markBox(item, scale);

  // The inline box takes the focus the moment it opens, and an existing run
  // arrives selected so typing replaces it -- the usual behaviour of a label
  // one has just double-clicked.
  const editorKey = editor ? `${editor.id ?? 'new'}:${editor.page}:${editor.x_pt}:${editor.y_pt}` : '';
  useEffect(() => {
    if (!editorKey) return;
    const element = inputRef.current;
    if (!element) return;
    element.focus();
    element.select();
  }, [editorKey]);

  // A font / size / colour picked on the toolbar while the box is open keeps
  // it open (see `keepsEditorOpen`); once the pick is made, the caret goes
  // back into the box so the user can go on typing. Never out of a number box
  // being typed into.
  const editorStyleKey = editor
    ? `${editor.font}|${editor.size_pt}|${editor.bold ? 1 : 0}|${editor.italic ? 1 : 0}|${editor.color ?? ''}`
    : '';
  useEffect(() => {
    if (!editorStyleKey) return;
    const element = inputRef.current;
    const active = document.activeElement as HTMLElement | null;
    if (!element || active === element) return;
    if (active && active !== document.body) {
      if (!keepsEditorOpen(active)) return;
      if (active instanceof HTMLInputElement) return;
    }
    element.focus({ preventScroll: true });
  }, [editorStyleKey]);

  // Edit-text (WE): is the new text wider than the line it replaces? Said,
  // never refused -- the text simply runs past where the old one ended.
  const wider = useMemo(() => {
    if (!editor?.edit) return false;
    const width = measureTextPt(
      editor.value,
      editor.font,
      Boolean(editor.bold),
      Boolean(editor.italic),
      editor.size_pt,
    );
    return width > editor.edit.line.width + WIDER_TOLERANCE_PT;
    // `fontEpoch`: measured again once the face is in.
  }, [editor?.edit, editor?.value, editor?.font, editor?.bold, editor?.italic, editor?.size_pt, fontEpoch]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = selectedId ? items.find((item) => item.id === selectedId) : undefined;
  const selectedBox = selected && isSized(selected) ? boxOf(selected) : undefined;

  // The note box takes the focus when it opens (and puts the caret at the end).
  const noteKey = noteEditor ? `${noteEditor.id ?? 'new'}:${noteEditor.page}:${noteEditor.x_pt}:${noteEditor.y_pt}` : '';
  useEffect(() => {
    if (!noteKey) return;
    const element = noteRef.current;
    if (!element) return;
    element.focus();
    element.setSelectionRange(element.value.length, element.value.length);
  }, [noteKey]);
  // A colour picked on the toolbar while it is open: the caret goes back in.
  const noteColor = noteEditor?.color ?? '';
  useEffect(() => {
    if (!noteColor) return;
    const element = noteRef.current;
    const active = document.activeElement as HTMLElement | null;
    if (!element || active === element) return;
    if (active && active !== document.body && !keepsEditorOpen(active)) return;
    element.focus({ preventScroll: true });
  }, [noteColor]);
  const onNoteBlur = (event: FocusEvent<HTMLTextAreaElement>) => {
    const next = event.relatedTarget;
    if (keepsEditorOpen(next)) return;
    if (next instanceof Element && next.closest('.swm-note-editor')) return;
    onNoteCommit?.();
  };

  const editorLeft = editor
    ? editor.edit
      ? ptToPx(editor.x_pt, scale) - EDIT_LEFT_PX
      : ptToPx(editor.x_pt, scale)
    : 0;
  const editorShift = editor?.edit
    ? `translateY(calc(-${EDIT_BASELINE_EM[LIBERATION_OF[editor.font]]}em - ${EDIT_INSET_PX}px))`
    : `translateY(-${INPUT_ASCENT}em)`;
  const editorWidth = editor
    ? editor.edit
      ? `${Math.ceil(
          Math.max(
            ptToPx(editor.edit.line.width, scale),
            ptToPx(
              measureTextPt(editor.value, editor.font, Boolean(editor.bold), Boolean(editor.italic), editor.size_pt),
              scale,
            ),
          ) +
            EDIT_LEFT_PX * 2 +
            ptToPx(editor.size_pt, scale),
        )}px`
      : `${Math.max(8, editor.value.length + 2)}ch`
    : undefined;

  const onEditorBlur = (event: FocusEvent<HTMLInputElement>) => {
    // The font / size / B / I / colour controls edit the open box: moving the
    // focus to them is not "done".
    if (keepsEditorOpen(event.relatedTarget)) return;
    onEditorCommit();
  };

  return (
    <>
      {/* Drawing only -- `editor.css` gives this layer `pointer-events: none`,
          so empty paper still belongs to the page's click-to-place. */}
      <svg className="swm-ann-layer" data-testid={`ann-layer-${pageNumber}`} aria-hidden="true">
        {/* W5: the whiteouts are NOT drawn here any more -- they are their own
            layer UNDER the slots and the signatures (`overlay/WhiteoutLayer`),
            the order the PDF prints in. Their hit boxes stay below. */}

        {/* Markups (W3) over the whiteouts, under the runs and marks: each box
            exactly as the PDF builder prints it (`markupInk`). */}
        {markups.map((item) => {
          const hex = ANNOTATION_COLOR_HEX[item.color];
          return (
            <g
              key={item.id}
              className={`swm-ann__markup swm-ann__markup--${item.kind}`}
              data-testid={`ann-markup-${item.id}`}
              data-ann-id={item.id}
              data-kind={item.kind}
              data-color={item.color}
              data-ann-rects={rectsJson(item)}
              style={{ fill: hex }}
              fillOpacity={item.kind === 'highlight' ? HIGHLIGHT_ALPHA : 1}
            >
              {item.rects.map((rect, index) => {
                const ink = markupInk(item.kind, rect);
                return (
                  <rect
                    key={index}
                    x={ptToPx(ink.x_pt, scale)}
                    y={ptToPx(ink.y_pt, scale)}
                    width={ptToPx(ink.w_pt, scale)}
                    height={ptToPx(ink.h_pt, scale)}
                    data-ink-pt={`${ink.x_pt},${ink.y_pt},${ink.w_pt},${ink.h_pt}`}
                  />
                );
              })}
            </g>
          );
        })}

        {/* W4 drawings over the markups, under the runs and marks, in list order. */}
        {drawings.map((item) => renderDrawing(item, scale, assets))}

        {/* F2: text boxes and stamps (markup v7), as the engine prints them.
            The one being edited is hidden under its editor. */}
        {boxes7.map((item) =>
          isTextbox(item) ? renderTextbox(item, scale, boxEditor?.id === item.id) : renderStamp(item as never, scale),
        )}

        {runs.map((item) => (
          <text
            key={item.id}
            ref={refFor.get(item.id)}
            className={`swm-ann__text swm-ann__text--${item.kind}`}
            data-testid={`ann-text-${item.id}`}
            data-ann-id={item.id}
            x={ptToPx(item.x_pt, scale)}
            y={ptToPx(item.y_pt, scale)}
            fontSize={ptToPx(item.size_pt, scale)}
            data-color={colorOf(item)}
            style={{
              fontFamily: ANNOTATION_FONT_CSS[item.font],
              fontWeight: item.bold ? 700 : 400,
              fontStyle: item.italic ? 'italic' : 'normal',
              fill: ANNOTATION_COLOR_HEX[colorOf(item)],
            }}
          >
            {item.text}
          </text>
        ))}

        {/* One `translate + scale` per mark: the unit outline above lands
            centred on the point, `size_pt` across, and its stroke scales with
            it. No font is involved anywhere in this. */}
        {marks.map((item) => {
          const centreX = ptToPx(item.x_pt, scale);
          const centreY = ptToPx(item.y_pt, scale);
          const side = ptToPx(item.size_pt, scale);
          return (
            <g
              key={item.id}
              className={`swm-ann__mark swm-ann__mark--${item.kind}`}
              data-testid={`ann-mark-${item.id}`}
              data-ann-id={item.id}
              data-kind={item.kind}
              data-cx={centreX.toFixed(2)}
              data-cy={centreY.toFixed(2)}
              data-side={side.toFixed(2)}
              data-color={colorOf(item)}
              transform={`translate(${centreX} ${centreY}) scale(${side})`}
            >
              {item.kind === 'square_mark' ? (
                // A filled square, no stroke (the engine's `_draw_square_mark`).
                <path d={MARK_PATH[item.kind]} style={{ fill: ANNOTATION_COLOR_HEX[colorOf(item)], stroke: 'none' }} />
              ) : (
                <path
                  d={MARK_PATH[item.kind]}
                  strokeWidth={MARK_STROKE}
                  style={{ stroke: ANNOTATION_COLOR_HEX[colorOf(item)] }}
                />
              )}
            </g>
          );
        })}

        {/* Sticky notes last: the viewer draws a `/Text` icon above the page. */}
        {notes.map((item) => renderNoteIcon(item, scale))}
      </svg>

      {/* One hit surface per annotation: focusable, nameable, draggable. A run's
          exists only once it has been measured, which is the same commit; a
          mark's is there from the first paint, because its box is a definition. */}
      {hitOrder.map((item) => {
        const box = boxOf(item);
        if (!box) return null;
        const isPrimary = item.id === selectedId;
        const isSelected = selectedSet.has(item.id);
        const locked = isLocked(item);
        const className = `swm-ann swm-ann--${item.kind}${isSelected ? ' is-selected' : ''}${
          isSelected && !isPrimary ? ' is-grouped' : ''
        }${locked ? ' is-locked' : ''}${edit.draggingId === item.id ? ' is-dragging' : ''}`;
        const bindings = edit.bindMark(item, {
          select: () => onSelect(item.id),
          edit: () => onEdit(item),
        });
        // A line's hit box lies along it (rotated about its start).
        const bar = isLine(item) ? lineHitBox(item, scale) : null;
        const main = (
          <button
            key={item.id}
            type="button"
            className={className}
            style={{
              left: box.left,
              top: box.top,
              width: box.width,
              height: box.height,
              ...(bar
                ? { transform: `rotate(${bar.angle}rad)`, transformOrigin: `${HIT_PAD}px 50%` }
                : null),
            }}
            {...drawData(item)}
            data-testid={`ann-${item.id}`}
            data-ann-id={item.id}
            data-kind={item.kind}
            data-page={item.page}
            data-selected={isSelected ? 'true' : undefined}
            data-group-id={item.group_id}
            data-locked={locked ? 'true' : undefined}
            data-x-pt={item.x_pt.toFixed(2)}
            data-y-pt={item.y_pt.toFixed(2)}
            data-w-pt={isWhiteout(item) || isResizableBox(item) ? item.w_pt.toFixed(2) : undefined}
            data-h-pt={isWhiteout(item) || isResizableBox(item) ? item.h_pt.toFixed(2) : undefined}
            data-size-pt={isSized(item) ? item.size_pt.toFixed(2) : undefined}
            data-font={isTextual(item) ? item.font : undefined}
            data-bold={isTextual(item) && item.bold ? 'true' : undefined}
            data-italic={isTextual(item) && item.italic ? 'true' : undefined}
            data-color={isWhiteout(item) || isImage(item) ? undefined : colorOf(item)}
            data-ann-rects={isMarkup(item) ? rectsJson(item) : undefined}
            data-rect-count={isMarkup(item) ? item.rects.length : undefined}
            aria-pressed={isSelected}
            title={hint(item)}
            {...bindings}
          >
            <span className="visually-hidden">{describe(item)}</span>
          </button>
        );
        if (!isMarkup(item) || item.rects.length === 1) return main;
        // One more hit box per further line of a markup: the same annotation,
        // the same gestures, out of the tab order (the first one is its stop).
        return [
          main,
          ...item.rects.slice(1).map((rect, index) => {
            const part = markupRectBox(rect, scale);
            return (
              <button
                key={`${item.id}-part-${index + 1}`}
                type="button"
                tabIndex={-1}
                aria-hidden="true"
                className={`${className} swm-ann--part`}
                style={{ left: part.left, top: part.top, width: part.width, height: part.height }}
                data-testid={`ann-${item.id}-part-${index + 1}`}
                data-ann-id={item.id}
                data-kind={item.kind}
                data-page={item.page}
                data-part={index + 1}
                title={hint(item)}
                {...bindings}
              />
            );
          }),
        ];
      })}

      {/* W4: the handles of the selected drawing -- eight on a rect / ellipse /
          image box, the two ends of a line / arrow. Drawn at the GEOMETRY
          (not the padded hit box), one undo step per drag. */}
      {selected && hasBoxHandles(selected) &&
        BOX_HANDLES.map((handle) => {
          const [fx, fy] = HANDLE_AT[handle];
          return (
            <button
              key={`h-${handle}`}
              type="button"
              className={`swm-ann-handle swm-ann-handle--box swm-ann-handle--${handle}${
                edit.resizingId === selected.id ? ' is-active' : ''
              }`}
              style={{
                left: ptToPx(selected.x_pt + selected.w_pt * fx, scale) - BOX_HANDLE_PX / 2,
                top: ptToPx(selected.y_pt + selected.h_pt * fy, scale) - BOX_HANDLE_PX / 2,
                cursor: HANDLE_CURSOR[handle],
              }}
              data-testid={`ann-handle-${handle}`}
              data-ann-id={selected.id}
              data-kind={selected.kind}
              data-handle={handle}
              aria-label={`Resize ${describe(selected)} from the ${HANDLE_NAME[handle]}${
                isImage(selected) ? ' (keeps its proportions; Shift frees them)' : ''
              }. Arrow keys move this edge.`}
              title={`Resize (${HANDLE_NAME[handle]})`}
              {...edit.bindShapeHandle(selected, handle)}
            />
          );
        })}
      {selected && isLine(selected) &&
        (['start', 'end'] as const).map((end) => (
          <button
            key={`h-${end}`}
            type="button"
            className={`swm-ann-handle swm-ann-handle--end${edit.resizingId === selected.id ? ' is-active' : ''}`}
            style={{
              left: ptToPx(end === 'start' ? selected.x1_pt : selected.x2_pt, scale) - BOX_HANDLE_PX / 2,
              top: ptToPx(end === 'start' ? selected.y1_pt : selected.y2_pt, scale) - BOX_HANDLE_PX / 2,
              cursor: 'move',
            }}
            data-testid={`ann-handle-${end}`}
            data-ann-id={selected.id}
            data-kind={selected.kind}
            data-handle={end}
            aria-label={`Move the ${end === 'start' ? 'start' : selected.kind === 'arrow' ? 'head' : 'end'} of the ${selected.kind} (Shift: 45° steps). Arrow keys move it.`}
            title={end === 'start' ? 'Drag the start' : selected.kind === 'arrow' ? 'Drag the head' : 'Drag the end'}
            {...edit.bindShapeHandle(selected, end)}
          />
        ))}

      {/* A selected note shows its text, as the PDF viewer's popup will. */}
      {selected && isNote(selected) && !noteEditor && (
        <div
          className="swm-note-popup"
          data-testid={`note-popup-${selected.id}`}
          role="note"
          style={{
            // Right of the icon, but never past the page's right edge.
            left: `min(${ptToPx(selected.x_pt + NOTE_ICON_PT, scale) + 6}px, calc(100% - 266px))`,
            top: ptToPx(selected.y_pt, scale),
          }}
        >
          {selected.text}
        </div>
      )}

      {/* The corner grip of the selected annotation. Top-right, outside the
          maths: the stored point -- baseline or centre -- is the anchor. A
          whiteout and a markup have no size, so no grip. */}
      {selected && isSized(selected) && selectedBox && (
        <button
          type="button"
          className={`swm-ann-handle${edit.resizingId === selected.id ? ' is-active' : ''}`}
          style={{
            left: selectedBox.left + selectedBox.width - HANDLE_PX / 2,
            top: selectedBox.top - HANDLE_PX / 2,
          }}
          data-testid="ann-handle"
          data-ann-id={selected.id}
          data-kind={selected.kind}
          aria-label={`Resize ${describe(selected)} (${selected.size_pt} pt). Arrow keys change the size.`}
          onKeyDown={(event) => {
            const large = event.shiftKey;
            if (event.key === 'ArrowUp' || event.key === 'ArrowRight') {
              event.preventDefault();
              event.stopPropagation();
              edit.stepSize(selected, 1, large);
            } else if (event.key === 'ArrowDown' || event.key === 'ArrowLeft') {
              event.preventDefault();
              event.stopPropagation();
              edit.stepSize(selected, -1, large);
            }
          }}
          {...edit.bindHandle(selected, selectedBox)}
        />
      )}

      {/* The inline box. Positioned from the baseline with the one approximation
          in this file (`INPUT_ASCENT`): only what is being typed sits there, and
          what is committed is redrawn by the SVG on the exact baseline. The
          edit-text box (WE) is placed exactly over the line it replaces, in the
          line's own face, on a white ground that hides the original. */}
      {editor && (
        <>
          <input
            key={editorKey}
            ref={inputRef}
            type="text"
            className={`swm-ann-input${editor.edit ? ' swm-ann-input--edit' : ''}${
              wider ? ' is-wider' : ''
            }${editor.error ? ' is-invalid' : ''}`}
            data-testid="ann-input"
            data-kind={editor.kind}
            data-mode={editor.edit ? 'edit-text' : 'text'}
            data-font={editor.font}
            data-bold={editor.bold ? 'true' : undefined}
            data-italic={editor.italic ? 'true' : undefined}
            data-size-pt={editor.size_pt}
            data-wider={wider ? 'true' : undefined}
            aria-invalid={editor.error ? true : undefined}
            aria-label={
              editor.edit
                ? 'Edit the text of this line'
                : editor.id
                  ? `Edit ${editor.kind === 'date' ? 'date' : 'text'} annotation`
                  : `New ${editor.kind === 'date' ? 'date' : 'text'} annotation`
            }
            value={editor.value}
            maxLength={200}
            style={{
              left: editorLeft,
              top: ptToPx(editor.y_pt, scale),
              fontFamily: ANNOTATION_FONT_CSS[editor.font],
              fontWeight: editor.bold ? 700 : 400,
              fontStyle: editor.italic ? 'italic' : 'normal',
              color: ANNOTATION_COLOR_HEX[editor.color ?? 'black'],
              fontSize: ptToPx(editor.size_pt, scale),
              transform: editorShift,
              width: editorWidth,
            }}
            onChange={(event) => onEditorChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                event.stopPropagation();
                onEditorCommit();
              } else if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                onEditorCancel();
              }
            }}
            onBlur={onEditorBlur}
            // Nothing typed in here may reach the page's click-to-place.
            onClick={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
            onDoubleClick={(event) => event.stopPropagation()}
          />
          {(editor.error || wider) && (
            <span
              className={`swm-ann-input-note${editor.error ? ' swm-ann-input-note--error' : ''}`}
              data-testid={editor.error ? 'ann-input-error' : 'ann-input-wider'}
              role={editor.error ? 'alert' : 'status'}
              style={{
                left: editorLeft,
                top: ptToPx(editor.y_pt, scale) + ptToPx(editor.size_pt, scale) * 0.45 + 4,
              }}
            >
              {editor.error ?? 'Text is wider than the original line'}
            </span>
          )}
        </>
      )}

      {boxEditor && (
        <TextboxEditor
          editor={boxEditor}
          scale={scale}
          onChange={(value) => onBoxChange?.(value)}
          onCommit={() => onBoxCommit?.()}
          onCancel={() => onBoxCancel?.()}
        />
      )}

      {/* The sticky-note box (W4): several lines; Ctrl+Enter or a click
          elsewhere saves, Esc cancels. Checked for the PDF's character set
          before anything is kept (the error stays on it, and in the note). */}
      {noteEditor && (
        <div
          className={`swm-note-editor${noteEditor.error ? ' is-invalid' : ''}`}
          data-testid="note-editor"
          data-note-id={noteEditor.id ?? undefined}
          style={{
            // Under the icon, but never past the page's right edge (its
            // Save button must stay on the page).
            left: `max(0px, min(${ptToPx(noteEditor.x_pt, scale)}px, calc(100% - 284px)))`,
            top: ptToPx(noteEditor.y_pt + NOTE_ICON_PT, scale) + 4,
          }}
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
          onDoubleClick={(event) => event.stopPropagation()}
        >
          <span
            className="swm-note-editor__swatch"
            style={{ background: ANNOTATION_COLOR_HEX[noteEditor.color] }}
            aria-hidden="true"
          />
          <textarea
            key={noteKey}
            ref={noteRef}
            className="swm-note-editor__input"
            data-testid="note-input"
            rows={4}
            maxLength={MAX_NOTE_TEXT}
            aria-label={noteEditor.id ? 'Edit the text of this note' : 'Text of the new note'}
            aria-invalid={noteEditor.error ? true : undefined}
            aria-describedby="swm-note-hint"
            value={noteEditor.value}
            onChange={(event) => onNoteChange?.(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                event.stopPropagation();
                onNoteCommit?.();
              } else if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                onNoteCancel?.();
              }
            }}
            onBlur={onNoteBlur}
          />
          <span className="swm-note-editor__bar">
            <span id="swm-note-hint" className="swm-note-editor__hint">
              Ctrl+Enter saves · Esc cancels
            </span>
            <span className="swm-note-editor__count" data-testid="note-count">
              {noteEditor.value.length} / {MAX_NOTE_TEXT}
            </span>
            <button
              type="button"
              className="editor__button swm-note-editor__button"
              data-testid="note-cancel"
              // Keep the focus in the box: its blur would SAVE.
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onNoteCancel?.()}
            >
              Cancel
            </button>
            <button
              type="button"
              className="editor__button swm-note-editor__button swm-note-editor__button--save"
              data-testid="note-save"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onNoteCommit?.()}
            >
              Save
            </button>
          </span>
          {noteEditor.error && (
            <span className="swm-note-editor__error" data-testid="note-input-error" role="alert">
              {noteEditor.error}
            </span>
          )}
        </div>
      )}
    </>
  );
}

const HANDLE_NAME: Record<string, string> = {
  nw: 'top-left corner',
  n: 'top edge',
  ne: 'top-right corner',
  e: 'right edge',
  se: 'bottom-right corner',
  s: 'bottom edge',
  sw: 'bottom-left corner',
  w: 'left edge',
};

/** The geometry a check reads off a drawing's hit box (PDF points, as stored). */
function drawData(item: Annotation): Record<string, string | number | undefined> {
  if (isInk(item)) {
    return {
      'data-stroke-pt': item.stroke_pt,
      'data-path-count': item.paths.length,
      'data-point-count': inkPointCount(item),
    };
  }
  if (isShape(item)) return { 'data-stroke-pt': item.stroke_pt, 'data-fill': item.fill ? 'true' : 'false' };
  if (isLine(item)) {
    return {
      'data-stroke-pt': item.stroke_pt,
      'data-x1-pt': item.x1_pt.toFixed(2),
      'data-y1-pt': item.y1_pt.toFixed(2),
      'data-x2-pt': item.x2_pt.toFixed(2),
      'data-y2-pt': item.y2_pt.toFixed(2),
    };
  }
  if (isImage(item)) return { 'data-asset-id': item.asset_id };
  if (isNote(item)) return { 'data-text-length': item.text.length };
  return {};
}

/** One W4 drawing, exactly as the PDF builder prints it. */
function renderDrawing(item: DrawAnnotation, scale: number, assets: ReadonlyMap<string, AssetImageEntry>) {
  if (isImage(item)) {
    const entry = assets.get(item.asset_id);
    const common = {
      'data-testid': `ann-image-${item.id}`,
      'data-ann-id': item.id,
      'data-asset-id': item.asset_id,
      'data-state': typeof entry === 'object' ? 'ready' : (entry ?? 'loading'),
      x: ptToPx(item.x_pt, scale),
      y: ptToPx(item.y_pt, scale),
      width: ptToPx(item.w_pt, scale),
      height: ptToPx(item.h_pt, scale),
    };
    // F2 (`image2`): turned about its centre, and translucent.
    const turn = item.v7?.rotation_deg
      ? `rotate(${item.v7.rotation_deg} ${common.x + common.width / 2} ${common.y + common.height / 2})`
      : undefined;
    const look = {
      transform: turn,
      opacity: item.v7 ? item.v7.opacity : undefined,
      'data-rotation': item.v7?.rotation_deg || undefined,
      'data-opacity': item.v7?.opacity,
    };
    return typeof entry === 'object' ? (
      // Fills the box exactly, like the builder (no aspect correction).
      <image key={item.id} {...common} {...look} className="swm-ann__image" href={entry.url} preserveAspectRatio="none" />
    ) : (
      <rect key={item.id} {...common} {...look} className="swm-ann__image swm-ann__image--placeholder" />
    );
  }
  if (isNote(item)) return renderNoteIcon(item, scale);
  const hex = ANNOTATION_COLOR_HEX[item.color];
  const stroke = ptToPx(item.stroke_pt, scale);
  if (isInk(item)) {
    return (
      <path
        key={item.id}
        className="swm-ann__ink"
        data-testid={`ann-ink-${item.id}`}
        data-ann-id={item.id}
        data-color={item.color}
        d={inkPathData(item, scale)}
        fill="none"
        stroke={hex}
        strokeWidth={stroke}
        strokeOpacity={item.v7 ? inkOpacity(item.v7) : undefined}
        data-opacity={item.v7 ? inkOpacity(item.v7) : undefined}
        data-freehand-highlight={item.v7?.freehand_highlight ? 'true' : undefined}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    );
  }
  if (isShape(item) && item.v7) {
    // F2 (`shape2`): an opaque fill colour (or none), a dash, the whole shape's opacity.
    const v7 = item.v7;
    const common = {
      className: `swm-ann__shape swm-ann__shape--${item.kind} swm-ann__shape--v7`,
      'data-testid': `ann-shape-${item.id}`,
      'data-ann-id': item.id,
      'data-color': item.color,
      'data-fill-color': v7.fill_color ?? 'none',
      'data-dash': v7.dash,
      'data-opacity': v7.opacity,
      stroke: hex,
      strokeWidth: stroke,
      strokeDasharray: dashArray(v7.dash, stroke),
      strokeLinecap: v7.dash === 'dot' ? ('round' as const) : ('butt' as const),
      fill: v7.fill_color ? ANNOTATION_COLOR_HEX[v7.fill_color] : 'none',
      opacity: v7.opacity,
    };
    const x = ptToPx(item.x_pt, scale);
    const y = ptToPx(item.y_pt, scale);
    const w = ptToPx(item.w_pt, scale);
    const h = ptToPx(item.h_pt, scale);
    return item.kind === 'rect' ? (
      <rect key={item.id} {...common} x={x} y={y} width={w} height={h} />
    ) : (
      <ellipse key={item.id} {...common} cx={x + w / 2} cy={y + h / 2} rx={w / 2} ry={h / 2} />
    );
  }
  if (isLine(item) && item.v7) {
    // F2 (`shape2` line / arrow): heads at none / start / end / both, dash, opacity.
    const v7 = item.v7;
    const p1: [number, number] = [item.x1_pt, item.y1_pt];
    const p2: [number, number] = [item.x2_pt, item.y2_pt];
    let start = p1;
    let end = p2;
    const heads: Array<[[number, number], [number, number], [number, number]]> = [];
    if (v7.heads === 'end' || v7.heads === 'both') {
      const head = v7Head(p2, p1, item.stroke_pt);
      end = head.base;
      heads.push([p2, head.left, head.right]);
    }
    if (v7.heads === 'start' || v7.heads === 'both') {
      const head = v7Head(p1, p2, item.stroke_pt);
      start = head.base;
      heads.push([p1, head.left, head.right]);
    }
    return (
      <g
        key={item.id}
        className={`swm-ann__line swm-ann__line--${item.kind} swm-ann__line--v7`}
        data-testid={`ann-line-${item.id}`}
        data-ann-id={item.id}
        data-color={item.color}
        data-heads={v7.heads}
        data-dash={v7.dash}
        data-opacity={v7.opacity}
        opacity={v7.opacity}
      >
        <line
          x1={ptToPx(start[0], scale)}
          y1={ptToPx(start[1], scale)}
          x2={ptToPx(end[0], scale)}
          y2={ptToPx(end[1], scale)}
          stroke={hex}
          strokeWidth={stroke}
          strokeDasharray={dashArray(v7.dash, stroke)}
          strokeLinecap={v7.dash === 'dot' ? 'round' : 'butt'}
        />
        {heads.map((points, index) => (
          <polygon
            key={index}
            className="swm-ann__arrow-head"
            points={points.map(([px, py]) => `${ptToPx(px, scale)},${ptToPx(py, scale)}`).join(' ')}
            fill={hex}
          />
        ))}
      </g>
    );
  }
  if (isShape(item)) {
    const common = {
      className: `swm-ann__shape swm-ann__shape--${item.kind}`,
      'data-testid': `ann-shape-${item.id}`,
      'data-ann-id': item.id,
      'data-color': item.color,
      'data-fill': item.fill ? 'true' : 'false',
      stroke: hex,
      strokeWidth: stroke,
      strokeLinejoin: 'miter' as const,
      fill: item.fill ? hex : 'none',
      fillOpacity: item.fill ? SHAPE_FILL_ALPHA : undefined,
    };
    const x = ptToPx(item.x_pt, scale);
    const y = ptToPx(item.y_pt, scale);
    const w = ptToPx(item.w_pt, scale);
    const h = ptToPx(item.h_pt, scale);
    return item.kind === 'rect' ? (
      <rect key={item.id} {...common} x={x} y={y} width={w} height={h} />
    ) : (
      <ellipse key={item.id} {...common} cx={x + w / 2} cy={y + h / 2} rx={w / 2} ry={h / 2} />
    );
  }
  if (isLine(item)) {
    const head =
      item.kind === 'arrow' ? arrowHead(item.x1_pt, item.y1_pt, item.x2_pt, item.y2_pt, item.stroke_pt) : null;
    const shaft = head ? head.base : ([item.x2_pt, item.y2_pt] as [number, number]);
    return (
      <g
        key={item.id}
        className={`swm-ann__line swm-ann__line--${item.kind}`}
        data-testid={`ann-line-${item.id}`}
        data-ann-id={item.id}
        data-color={item.color}
      >
        {(shaft[0] !== item.x1_pt || shaft[1] !== item.y1_pt) && (
          <line
            x1={ptToPx(item.x1_pt, scale)}
            y1={ptToPx(item.y1_pt, scale)}
            x2={ptToPx(shaft[0], scale)}
            y2={ptToPx(shaft[1], scale)}
            stroke={hex}
            strokeWidth={stroke}
            strokeLinecap="butt"
          />
        )}
        {head && (
          <polygon
            className="swm-ann__arrow-head"
            data-head-pt={[[item.x2_pt, item.y2_pt], head.left, head.right]
              .map(([px, py]) => `${px.toFixed(2)},${py.toFixed(2)}`)
              .join(' ')}
            points={[[item.x2_pt, item.y2_pt], head.left, head.right]
              .map(([px, py]) => `${ptToPx(px, scale)},${ptToPx(py, scale)}`)
              .join(' ')}
            fill={hex}
          />
        )}
      </g>
    );
  }
  return null;
}

/** A note's icon: a folded sheet in its colour, `NOTE_ICON_PT` square below-right of its point. */
function renderNoteIcon(item: NoteAnnotation, scale: number) {
  const x = ptToPx(item.x_pt, scale);
  const y = ptToPx(item.y_pt, scale);
  const side = ptToPx(NOTE_ICON_PT, scale);
  const fold = side * 0.3;
  return (
    <g
      key={item.id}
      className="swm-ann__note"
      data-testid={`ann-note-${item.id}`}
      data-ann-id={item.id}
      data-color={item.color}
      transform={`translate(${x} ${y})`}
    >
      <path
        d={`M0 0 H${side - fold} L${side} ${fold} V${side} H0 Z`}
        fill={ANNOTATION_COLOR_HEX[item.color]}
        stroke="rgba(16, 24, 40, 0.55)"
        strokeWidth={1}
      />
      <path d={`M${side - fold} 0 V${fold} H${side}`} fill="none" stroke="rgba(16, 24, 40, 0.55)" strokeWidth={1} />
      {[0.38, 0.55, 0.72].map((at) => (
        <line
          key={at}
          x1={side * 0.2}
          x2={side * 0.8}
          y1={side * at}
          y2={side * at}
          stroke="rgba(16, 24, 40, 0.55)"
          strokeWidth={Math.max(1, side * 0.05)}
        />
      ))}
    </g>
  );
}
