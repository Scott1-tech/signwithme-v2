import type { EditorFontFamilyId } from '../../api/document';
import {
  EDITOR_FONT_METRICS,
  isEditorFamily,
  measureEditorText,
} from '../editorFonts';
import type { PageSizePt } from '../layout';
import {
  DEFAULT_COLOR,
  MIN_BOX_PT,
  clampBoxOn,
  finite,
  groupKey,
  isAnnotationColor,
  tidy,
  type AnnotationColor,
  type AnnotationCommon,
  type BoxPt,
  type SanitizeBase,
} from './shared';

/**
 * F2: the markup v7 kinds (EDITOR-V2-PLAN §3 «Markup», B2
 * `app/schemas/contract.py` `TextboxAnnotationIn` … `SquareMarkAnnotationIn`,
 * drawn by the engine `app/services/pdf_engine/markup.py`).
 *
 * Two are kinds of their own here -- `textbox`, `stamp`. The other four are
 * the W4 kinds with a v7 LOOK: a rect / ellipse / line / arrow with `v7`
 * (dash, fill colour, opacity, heads) travels as `shape2`, an ink with `v7`
 * as `ink2`, an image with `v7` as `image2`, and a filled square is a mark
 * (`square_mark`, centred like a tick). Nothing without `v7` changes its wire:
 * every legacy element is byte for byte what it was.
 *
 * Geometry: PDF points, top-left origin. Opacity 0.05…1. Colours: the closed
 * palette (no white).
 */

export const OPACITY_MIN = 0.05;
export const OPACITY_MAX = 1;
export const TEXTBOX_SIZE_MIN = 6;
export const TEXTBOX_SIZE_MAX = 72;
export const TEXTBOX_MAX_CHARS = 2000;
export const LINE_HEIGHT_MIN = 0.8;
export const LINE_HEIGHT_MAX = 3;
export const LINE_HEIGHTS = [1, 1.15, 1.2, 1.5, 2] as const;
export const STAMP_TEXT_MAX = 40;
export const STAMP_DATE_MAX = 32;
export const STAMP_PRESETS = [
  'approved', 'rejected', 'draft', 'confidential', 'final', 'received', 'void', 'paid', 'reviewed',
] as const;
export const FREEHAND_HIGHLIGHT_OPACITY = 0.35;
export const SQUARE_DEFAULT_SIZE = 10;
/** `ARROW_HEAD_LENGTH_RATIO` / `ARROW_HEAD_HALF_WIDTH_RATIO` of the engine. */
const HEAD_LENGTH = 6;
const HEAD_HALF = 3;

export type Dash = 'solid' | 'dash' | 'dot';
export type Heads = 'none' | 'start' | 'end' | 'both';
export type TextAlign = 'left' | 'center' | 'right' | 'justify';

export const clampOpacity = (value: number): number =>
  Number.isFinite(value) ? tidy(Math.min(OPACITY_MAX, Math.max(OPACITY_MIN, value))) : 1;
export const clampRotation = (value: number): number =>
  Number.isFinite(value) ? tidy(((value % 360) + 360) % 360) : 0;
const isDash = (value: unknown): value is Dash => value === 'solid' || value === 'dash' || value === 'dot';
const isHeads = (value: unknown): value is Heads =>
  value === 'none' || value === 'start' || value === 'end' || value === 'both';
const isAlign = (value: unknown): value is TextAlign =>
  value === 'left' || value === 'center' || value === 'right' || value === 'justify';

/* -- the v7 look of the W4 kinds -------------------------------------------------- */

export interface ShapeV7 {
  fill_color: AnnotationColor | null;
  opacity: number;
  dash: Dash;
}
export interface LineV7 {
  opacity: number;
  dash: Dash;
  heads: Heads;
}
export interface InkV7 {
  /** null = the server's default (0.35 for a freehand highlight, else 1). */
  opacity: number | null;
  freehand_highlight: boolean;
}
export interface ImageV7 {
  rotation_deg: number;
  opacity: number;
}

export function readShapeV7(raw: unknown): ShapeV7 | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const source = raw as Record<string, unknown>;
  return {
    fill_color: isAnnotationColor(source.fill_color) ? source.fill_color : null,
    opacity: clampOpacity(finite(source.opacity) ?? 1),
    dash: isDash(source.dash) ? source.dash : 'solid',
  };
}
export function readLineV7(raw: unknown, arrow: boolean): LineV7 | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const source = raw as Record<string, unknown>;
  return {
    opacity: clampOpacity(finite(source.opacity) ?? 1),
    dash: isDash(source.dash) ? source.dash : 'solid',
    heads: isHeads(source.heads) ? source.heads : arrow ? 'end' : 'none',
  };
}
export function readInkV7(raw: unknown): InkV7 | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const source = raw as Record<string, unknown>;
  const opacity = finite(source.opacity);
  return {
    opacity: opacity === null ? null : clampOpacity(opacity),
    freehand_highlight: source.freehand_highlight === true,
  };
}
export function readImageV7(raw: unknown): ImageV7 | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const source = raw as Record<string, unknown>;
  return {
    rotation_deg: clampRotation(finite(source.rotation_deg) ?? 0),
    opacity: clampOpacity(finite(source.opacity) ?? 1),
  };
}

/** The ink's effective opacity (what the engine draws with). */
export const inkOpacity = (v7: InkV7 | undefined): number =>
  v7 ? (v7.opacity ?? (v7.freehand_highlight ? FREEHAND_HIGHLIGHT_OPACITY : 1)) : 1;

/** An SVG dash array for a stroke of `widthPx` (the engine's `_dashes`). */
export function dashArray(dash: Dash | undefined, widthPx: number): string | undefined {
  if (dash === 'dash') return `${3 * widthPx} ${2 * widthPx}`;
  if (dash === 'dot') return `0 ${2 * widthPx}`;
  return undefined;
}

/** (base, left, right) of an arrow head with its tip at `tip` (the engine's `_head`, PDF points). */
export function v7Head(
  tip: [number, number],
  tail: [number, number],
  stroke: number,
): { base: [number, number]; left: [number, number]; right: [number, number] } {
  const dx = tip[0] - tail[0];
  const dy = tip[1] - tail[1];
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  const headLength = Math.min(HEAD_LENGTH * stroke, length);
  const half = HEAD_HALF * stroke * (headLength / (HEAD_LENGTH * stroke));
  const base: [number, number] = [tip[0] - ux * headLength, tip[1] - uy * headLength];
  return {
    base,
    left: [base[0] - uy * half, base[1] + ux * half],
    right: [base[0] + uy * half, base[1] - ux * half],
  };
}

/* -- textbox ------------------------------------------------------------------------ */

export interface TextboxRun {
  text: string;
  font: EditorFontFamilyId;
  size_pt: number;
  color: AnnotationColor;
  bold?: true;
  italic?: true;
}

export interface TextboxAnnotation extends AnnotationCommon {
  kind: 'textbox';
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
  runs: TextboxRun[];
  align: TextAlign;
  line_height: number;
  rotation_deg?: number;
  opacity?: number;
}

export const clampTextboxSize = (value: number): number =>
  tidy(Math.min(TEXTBOX_SIZE_MAX, Math.max(TEXTBOX_SIZE_MIN, Math.round(value * 2) / 2)));
export const clampLineHeight = (value: number): number =>
  Number.isFinite(value) ? tidy(Math.min(LINE_HEIGHT_MAX, Math.max(LINE_HEIGHT_MIN, value))) : 1.2;

/** The whole text of a box (runs joined). */
export const textboxText = (item: Pick<TextboxAnnotation, 'runs'>): string => item.runs.map((run) => run.text).join('');

/** One run carrying `text` in the style of the box's first run (the editor writes one style per box). */
/** A style change of a text box (booleans may say `false`). */
export interface RunStyleChange {
  font?: EditorFontFamilyId;
  size_pt?: number;
  color?: AnnotationColor;
  bold?: boolean;
  italic?: boolean;
}

export function restyleRuns(runs: readonly TextboxRun[], text: string, change: RunStyleChange = {}): TextboxRun[] {
  const first = runs[0] ?? { text: '', font: 'LiberationSans' as EditorFontFamilyId, size_pt: 11, color: DEFAULT_COLOR };
  const run: TextboxRun = {
    text,
    font: change.font ?? first.font,
    size_pt: clampTextboxSize(change.size_pt ?? first.size_pt),
    color: change.color ?? first.color,
  };
  const bold = change.bold !== undefined ? Boolean(change.bold) : Boolean(first.bold);
  const italic = change.italic !== undefined ? Boolean(change.italic) : Boolean(first.italic);
  if (bold) run.bold = true;
  if (italic) run.italic = true;
  return [run];
}

/** Every run in the same style change (the toolbar speaks for the whole box). */
export function restyleAll(runs: readonly TextboxRun[], change: RunStyleChange): TextboxRun[] {
  return runs.map((run) => {
    const next: TextboxRun = {
      text: run.text,
      font: change.font ?? run.font,
      size_pt: change.size_pt !== undefined ? clampTextboxSize(change.size_pt) : run.size_pt,
      color: change.color ?? run.color,
    };
    const bold = change.bold !== undefined ? change.bold : Boolean(run.bold);
    const italic = change.italic !== undefined ? change.italic : Boolean(run.italic);
    if (bold) next.bold = true;
    if (italic) next.italic = true;
    return next;
  });
}

function readRuns(raw: unknown): TextboxRun[] {
  if (!Array.isArray(raw)) return [];
  const runs: TextboxRun[] = [];
  raw.forEach((entry) => {
    if (!entry || typeof entry !== 'object') return;
    const source = entry as Record<string, unknown>;
    if (typeof source.text !== 'string' || source.text === '') return;
    const run: TextboxRun = {
      text: source.text.slice(0, TEXTBOX_MAX_CHARS),
      font: isEditorFamily(source.font) ? source.font : 'LiberationSans',
      size_pt: clampTextboxSize(finite(source.size_pt) ?? 11),
      color: isAnnotationColor(source.color) ? source.color : DEFAULT_COLOR,
    };
    if (source.bold === true) run.bold = true;
    if (source.italic === true) run.italic = true;
    runs.push(run);
  });
  return runs.slice(0, 50);
}

export function sanitizeTextbox({ id, page, group_id, source }: SanitizeBase): TextboxAnnotation | null {
  const x = finite(source.x_pt);
  const y = finite(source.y_pt);
  const w = finite(source.w_pt);
  const h = finite(source.h_pt);
  if (x === null || y === null || w === null || h === null || w < MIN_BOX_PT || h < MIN_BOX_PT) return null;
  const runs = readRuns(source.runs);
  if (runs.length === 0 || textboxText({ runs }).trim() === '') return null;
  const box = clampBoxOn({ x, y, w, h }, null);
  const made: TextboxAnnotation = {
    id,
    kind: 'textbox',
    page,
    x_pt: box.x,
    y_pt: box.y,
    w_pt: box.w,
    h_pt: box.h,
    runs,
    align: isAlign(source.align) ? source.align : 'left',
    line_height: clampLineHeight(finite(source.line_height) ?? 1.2),
  };
  const rotation = finite(source.rotation_deg);
  if (rotation) made.rotation_deg = clampRotation(rotation);
  const opacity = finite(source.opacity);
  if (opacity !== null && opacity < 1) made.opacity = clampOpacity(opacity);
  if (group_id) made.group_id = group_id;
  return made;
}

export const sameTextbox = (a: TextboxAnnotation, b: TextboxAnnotation): boolean =>
  a.w_pt === b.w_pt &&
  a.h_pt === b.h_pt &&
  a.align === b.align &&
  a.line_height === b.line_height &&
  a.rotation_deg === b.rotation_deg &&
  a.opacity === b.opacity &&
  JSON.stringify(a.runs) === JSON.stringify(b.runs);

export function textboxForRequest(item: TextboxAnnotation) {
  return {
    id: item.id,
    kind: 'textbox' as const,
    page: item.page,
    x_pt: tidy(item.x_pt),
    y_pt: tidy(item.y_pt),
    w_pt: tidy(item.w_pt),
    h_pt: tidy(item.h_pt),
    runs: item.runs.map((run) => ({ ...run, text: run.text.normalize('NFC') })),
    align: item.align,
    line_height: item.line_height,
    ...(item.rotation_deg ? { rotation_deg: item.rotation_deg } : {}),
    ...(item.opacity !== undefined && item.opacity < 1 ? { opacity: item.opacity } : {}),
    ...groupKey(item),
  };
}

/* -- the engine's text-box layout (`markup.layout_textbox`), in the browser ------------ */

export interface LaidPiece {
  text: string;
  run: TextboxRun;
  /** Left edge relative to the line's start, and advance, in points. */
  x: number;
  width: number;
}
export interface LaidLine {
  /** Baseline relative to the box's top, points. */
  baseline: number;
  width: number;
  pieces: LaidPiece[];
  /** Largest size on the line (its descent decides the box's bottom). */
  size: number;
  descent: number;
}

/**
 * Greedy word wrap at the box's width, exactly the engine's: pieces are words
 * and space runs in one style; a wrapped line has no leading space and no
 * trailing space; the first baseline is the line's largest ascender × size
 * below the top, every next one `line_height × size` below the previous.
 */
export function layoutTextbox(item: Pick<TextboxAnnotation, 'runs' | 'w_pt' | 'line_height'>): LaidLine[] {
  type Piece = { text: string; run: TextboxRun; width: number };
  const paragraphs: Piece[][] = [[]];
  item.runs.forEach((run) => {
    run.text.split('\n').forEach((chunk, number) => {
      if (number) paragraphs.push([]);
      let token = '';
      const push = () => {
        if (!token) return;
        paragraphs[paragraphs.length - 1].push({
          text: token,
          run,
          width: measureEditorText(token, run.font, Boolean(run.bold), Boolean(run.italic), run.size_pt),
        });
        token = '';
      };
      for (const char of chunk) {
        if (token && (char === ' ') !== (token[token.length - 1] === ' ')) push();
        token += char;
      }
      push();
    });
  });
  const lines: LaidLine[] = [];
  const firstSize = item.runs[0]?.size_pt ?? 11;
  paragraphs.forEach((paragraph) => {
    const rows: Piece[][] = [];
    let current: Piece[] = [];
    let width = 0;
    paragraph.forEach((piece) => {
      const space = piece.text.startsWith(' ');
      if (current.length && !space && width + piece.width > item.w_pt + 1e-6) {
        rows.push(current);
        current = [];
        width = 0;
      }
      if (!current.length && space) return;
      current.push(piece);
      width += piece.width;
    });
    rows.push(current);
    rows.forEach((raw) => {
      let row = raw;
      while (row.length && row[row.length - 1].text.startsWith(' ')) row = row.slice(0, -1);
      const size = row.length ? Math.max(...row.map((piece) => piece.run.size_pt)) : firstSize;
      const ascent = row.length
        ? Math.max(...row.map((piece) => EDITOR_FONT_METRICS[piece.run.font].ascent * piece.run.size_pt))
        : 0.8 * size;
      const descent = row.length
        ? Math.max(...row.map((piece) => EDITOR_FONT_METRICS[piece.run.font].descent * piece.run.size_pt))
        : 0.2 * size;
      const baseline = lines.length ? lines[lines.length - 1].baseline + size * item.line_height : ascent;
      let x = 0;
      const pieces = row.map((piece) => {
        const laid = { text: piece.text, run: piece.run, x, width: piece.width };
        x += piece.width;
        return laid;
      });
      lines.push({ baseline, width: x, pieces, size, descent });
    });
  });
  return lines;
}

/** The height the laid-out text needs (last baseline + its descent), points. */
export function textboxNeededHeight(item: Pick<TextboxAnnotation, 'runs' | 'w_pt' | 'line_height'>): number {
  const lines = layoutTextbox(item);
  if (!lines.length) return MIN_BOX_PT;
  const last = lines[lines.length - 1];
  return tidy(last.baseline + last.descent);
}

/** A fresh text box at `at` (its top-left), `width` wide, grown to fit its text, on the page. */
export function makeTextbox(
  base: {
    id: string;
    page: number;
    at: { x: number; y: number };
    width: number;
    runs: TextboxRun[];
    align: TextAlign;
    line_height: number;
    opacity?: number;
  },
  page: PageSizePt | null,
): TextboxAnnotation {
  const draft = { runs: base.runs, w_pt: base.width, line_height: base.line_height };
  const height = Math.max(textboxNeededHeight(draft), (base.runs[0]?.size_pt ?? 11) * 1.2);
  const box = clampBoxOn({ x: base.at.x, y: base.at.y, w: base.width, h: height }, page);
  const made: TextboxAnnotation = {
    id: base.id,
    kind: 'textbox',
    page: base.page,
    x_pt: box.x,
    y_pt: box.y,
    w_pt: box.w,
    h_pt: box.h,
    runs: base.runs,
    align: base.align,
    line_height: clampLineHeight(base.line_height),
  };
  if (base.opacity !== undefined && base.opacity < 1) made.opacity = clampOpacity(base.opacity);
  return made;
}

/* -- stamp ------------------------------------------------------------------------------- */

export interface StampAnnotation extends AnnotationCommon {
  kind: 'stamp';
  x_pt: number;
  y_pt: number;
  w_pt: number;
  h_pt: number;
  preset: string | null;
  text: string | null;
  color: AnnotationColor;
  border: boolean;
  /** Typed by the user; never «now» (2026-09-25). null = no date line. */
  date_text: string | null;
  rotation_deg?: number;
  opacity?: number;
}

export const stampLabel = (item: Pick<StampAnnotation, 'preset' | 'text'>): string =>
  item.text ?? (item.preset ? item.preset.toUpperCase() : '');

export function sanitizeStamp({ id, page, group_id, source }: SanitizeBase): StampAnnotation | null {
  const x = finite(source.x_pt);
  const y = finite(source.y_pt);
  const w = finite(source.w_pt);
  const h = finite(source.h_pt);
  if (x === null || y === null || w === null || h === null || w < MIN_BOX_PT || h < MIN_BOX_PT) return null;
  const preset =
    typeof source.preset === 'string' && (STAMP_PRESETS as readonly string[]).includes(source.preset)
      ? source.preset
      : null;
  const text = typeof source.text === 'string' && source.text.trim() ? source.text.slice(0, STAMP_TEXT_MAX) : null;
  if ((preset === null) === (text === null)) return null;
  const date =
    typeof source.date_text === 'string' && source.date_text.trim()
      ? source.date_text.slice(0, STAMP_DATE_MAX)
      : null;
  const box = clampBoxOn({ x, y, w, h }, null);
  const made: StampAnnotation = {
    id,
    kind: 'stamp',
    page,
    x_pt: box.x,
    y_pt: box.y,
    w_pt: box.w,
    h_pt: box.h,
    preset,
    text,
    color: isAnnotationColor(source.color) ? source.color : 'red',
    border: source.border !== false,
    date_text: date,
  };
  const rotation = finite(source.rotation_deg);
  if (rotation) made.rotation_deg = clampRotation(rotation);
  const opacity = finite(source.opacity);
  if (opacity !== null && opacity < 1) made.opacity = clampOpacity(opacity);
  if (group_id) made.group_id = group_id;
  return made;
}

export const sameStamp = (a: StampAnnotation, b: StampAnnotation): boolean =>
  a.w_pt === b.w_pt &&
  a.h_pt === b.h_pt &&
  a.preset === b.preset &&
  a.text === b.text &&
  a.color === b.color &&
  a.border === b.border &&
  a.date_text === b.date_text &&
  a.rotation_deg === b.rotation_deg &&
  a.opacity === b.opacity;

export function stampForRequest(item: StampAnnotation) {
  return {
    id: item.id,
    kind: 'stamp' as const,
    page: item.page,
    x_pt: tidy(item.x_pt),
    y_pt: tidy(item.y_pt),
    w_pt: tidy(item.w_pt),
    h_pt: tidy(item.h_pt),
    ...(item.text !== null ? { text: item.text.normalize('NFC') } : { preset: item.preset }),
    color: item.color,
    border: item.border,
    ...(item.date_text ? { date_text: item.date_text.normalize('NFC') } : {}),
    ...(item.rotation_deg ? { rotation_deg: item.rotation_deg } : {}),
    ...(item.opacity !== undefined && item.opacity < 1 ? { opacity: item.opacity } : {}),
    ...groupKey(item),
  };
}

/** The size a new stamp gets: its label at ~18 pt bold, a date line under it if any. */
export function stampBox(spec: { preset: string | null; text: string | null; date_text: string | null }): { w: number; h: number } {
  const label = stampLabel({ preset: spec.preset, text: spec.text });
  const width = measureEditorText(label, 'LiberationSans', true, false, 18);
  return { w: tidy(Math.max(90, width + 36)), h: spec.date_text ? 56 : 38 };
}

/**
 * The engine's stamp drawing (`markup._draw_stamp`), in points relative to the
 * box: the border, the label (Helvetica-Bold, fitted) and the date line.
 */
export function layoutStamp(item: Pick<StampAnnotation, 'w_pt' | 'h_pt' | 'preset' | 'text' | 'date_text'>) {
  const w = item.w_pt;
  const h = item.h_pt;
  const border = Math.max(1, Math.min(w, h) * 0.06);
  const pad = border * 2.5;
  const label = stampLabel(item);
  const mainH = h * (item.date_text ? 0.62 : 1);
  const unit = measureEditorText(label, 'LiberationSans', true, false, 1) || 1;
  const size = Math.max(4, Math.min(mainH * 0.55, (w - 2 * pad) / unit));
  const textW = unit * size;
  const ascent = 1.07 * size;
  const baseline = item.date_text ? mainH * 0.5 + ascent * 0.36 + pad * 0.3 : (mainH + ascent * 0.72) / 2;
  let date: { text: string; size: number; x: number; baseline: number } | null = null;
  if (item.date_text) {
    const dateUnit = measureEditorText(item.date_text, 'LiberationSans', false, false, 1) || 1;
    const dateSize = Math.max(4, Math.min((h - mainH) * 0.6, (w - 2 * pad) / dateUnit));
    date = {
      text: item.date_text,
      size: dateSize,
      x: (w - dateUnit * dateSize) / 2,
      baseline: h - pad * 0.6 - (h - mainH - dateSize) / 2,
    };
  }
  return {
    border,
    radius: 0.12 * Math.min(w - border, h - border),
    label: { text: label, size, x: (w - textW) / 2, baseline },
    date,
  };
}

export type V7BoxAnnotation = TextboxAnnotation | StampAnnotation;
export const boxOf = (item: { x_pt: number; y_pt: number; w_pt: number; h_pt: number }): BoxPt => ({
  x: item.x_pt,
  y: item.y_pt,
  w: item.w_pt,
  h: item.h_pt,
});
