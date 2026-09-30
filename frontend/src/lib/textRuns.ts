import * as pdfjs from 'pdfjs-dist';
import { measureTextPt, type FontFamily } from './fonts';

/**
 * The page's own text, as geometry (plan §3).
 *
 * ONE SOURCE OF GEOMETRY
 * ----------------------
 * `runGeometry` is the only place a pdf.js text item becomes a box. The search
 * (`usePdfViewer.getPageText`) and the text layer (`renderTextLayer` → the
 * edit-text tool) both go through it, so a search mark and a line the user
 * edits can never disagree about where a word is.
 *
 * Every box is in PDF points, TOP-LEFT origin, with the page's own `/Rotate`
 * applied and the reader's quarter turn NOT applied -- the space `lib/layout.ts`
 * and every annotation speak.
 */

/** The bits of a pdf.js `TextItem` this module reads. */
export interface TextItemLike {
  str: string;
  transform: number[];
  width: number;
  height: number;
  fontName: string;
  hasEOL?: boolean;
}

/** The bits of a pdf.js text style this module reads. */
export interface TextStyleLike {
  fontFamily?: string;
  ascent?: number;
  descent?: number;
  vertical?: boolean;
}

export interface RunGeometry {
  /** Left end of the baseline. */
  x: number;
  baseline: number;
  /** Advance width of the run. */
  width: number;
  /** The em size (the hypotenuse of the text matrix's vertical column). */
  fontSize: number;
  /** `baseline - fontSize`: the box the SEARCH has always drawn. */
  top: number;
}

/**
 * Where one item sits. For horizontal text `tx` is `[size, 0, 0, -size, x,
 * baselineY]`: the hypotenuse is the font size and `tx[5]` the baseline.
 *
 * Known limit (unchanged from the search): the box is axis-aligned, so a run
 * that is itself rotated gets the right start and the wrong extent.
 */
export function runGeometry(item: TextItemLike, viewportTransform: number[]): RunGeometry {
  const tx = pdfjs.Util.transform(viewportTransform, item.transform);
  const fontSize = Math.hypot(tx[2], tx[3]) || Math.abs(item.height) || 0;
  return { x: tx[4], baseline: tx[5], width: item.width, fontSize, top: tx[5] - fontSize };
}

export type RunFontFamily = 'sans-serif' | 'serif' | 'monospace';

/** The metric-compatible face a family shape is measured in (Helvetica / Times / Courier widths). */
export const LIBERATION_FOR_FAMILY: Record<RunFontFamily, FontFamily> = {
  'sans-serif': 'LiberationSans',
  serif: 'LiberationSerif',
  monospace: 'LiberationMono',
};

/**
 * How far into a run of `text`, `width` points wide, its first `count`
 * characters reach (W3). A pdf.js item only knows its WHOLE width; the split
 * inside it is measured in the Liberation face of its family -- the same
 * advance widths as Helvetica / Times / Courier -- and scaled so the whole
 * run is exactly its PDF width. A face that is none of those is still split
 * in proportion to real glyph widths, which beats counting characters.
 * Used by the text selection (markup tools) and by the search's boxes.
 */
export function advanceWithin(
  text: string,
  width: number,
  family: RunFontFamily,
  bold: boolean,
  italic: boolean,
  count: number,
): number {
  const length = text.length;
  if (count <= 0 || length === 0) return 0;
  if (count >= length) return width;
  const face = LIBERATION_FOR_FAMILY[family];
  const whole = measureTextPt(text, face, bold, italic, 100);
  if (!(whole > 0)) return (width * count) / length;
  return (measureTextPt(text.slice(0, count), face, bold, italic, 100) * width) / whole;
}

/**
 * One run of the pdf.js text layer (plan §3). For a merged LINE (see
 * `mergeRunsIntoLines`) the same shape, with `runs` listing its items.
 *
 * `y` / `height` are the INK box -- ascent above the baseline to descent below
 * it -- which is what a whiteout has to cover (descenders included).
 */
export interface TextRun {
  index: number;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  baseline: number;
  fontSize: number;
  fontFamily: RunFontFamily;
  bold: boolean;
  italic: boolean;
  /** pdf.js' internal font id; runs of one line share it. */
  fontName?: string;
  /**
   * Which pdf.js text item this run is, counting every item that has a `str`
   * (empty ones too) -- exactly the index of its `<span>` in the text layer's
   * `textDivs`, so `TextLayerHandle.spanIndexOf(span)` finds the run (W3).
   */
  item?: number;
  /** For a merged line: the indexes of the runs it was built from. */
  runs?: number[];
}

/** What pdf.js knows about a font face (its `commonObjs` entry). */
export interface FontFaceInfo {
  name: string;
  bold: boolean;
  italic: boolean;
}

const BOLD_NAME = /Bold|Black|Heavy|Semibold|Demi/i;
const ITALIC_NAME = /Italic|Oblique/i;
const SERIF_NAME = /Times|Serif|Roman|Georgia|Garamond|Cambria|Book|Palatino|Minion/i;
const SANS_NAME = /Sans|Arial|Helvetica|Verdana|Calibri|Tahoma|Segoe/i;
const MONO_NAME = /Courier|Mono|Consol/i;

/** A face's name (e.g. `ABCDEF+Arial-BoldMT`) into bold / italic. */
export function faceFromName(name: string | undefined | null, flags?: { bold?: boolean; italic?: boolean; black?: boolean }): FontFaceInfo {
  const clean = name ?? '';
  return {
    name: clean,
    bold: BOLD_NAME.test(clean) || Boolean(flags?.bold) || Boolean(flags?.black),
    italic: ITALIC_NAME.test(clean) || Boolean(flags?.italic),
  };
}

/** Which of the three family shapes a run is set in. */
export function familyOf(style: TextStyleLike | undefined, face: FontFaceInfo | undefined): RunFontFamily {
  const name = face?.name ?? '';
  if (MONO_NAME.test(name)) return 'monospace';
  if (SANS_NAME.test(name)) return 'sans-serif';
  if (SERIF_NAME.test(name)) return 'serif';
  const family = (style?.fontFamily ?? '').toLowerCase();
  if (family.includes('mono')) return 'monospace';
  if (family.includes('serif') && !family.includes('sans')) return 'serif';
  return 'sans-serif';
}

/** Fallback ascent / descent (fractions of the em) when pdf.js has none. */
const DEFAULT_ASCENT = 0.9;
const DEFAULT_DESCENT = -0.22;

function inkExtent(style: TextStyleLike | undefined): { ascent: number; descent: number } {
  const ascent = style?.ascent;
  const descent = style?.descent;
  return {
    ascent:
      typeof ascent === 'number' && ascent >= 0.5 && ascent <= 1.3 ? ascent : DEFAULT_ASCENT,
    descent:
      typeof descent === 'number' && descent <= 0 && descent >= -0.6 ? descent : DEFAULT_DESCENT,
  };
}

/**
 * The page's runs, one per non-empty pdf.js item, in content-stream order.
 * Vertical and rotated items are skipped: the edit tool draws horizontal text.
 */
export function buildRuns(
  items: ReadonlyArray<TextItemLike | { type?: string }>,
  styles: Readonly<Record<string, TextStyleLike>>,
  viewportTransform: number[],
  faces: ReadonlyMap<string, FontFaceInfo>,
): TextRun[] {
  const runs: TextRun[] = [];
  let itemIndex = -1;
  items.forEach((raw) => {
    if (!('str' in raw)) return;
    // pdf.js' TextLayer makes one span per item that has a `str` (an empty
    // one included), in this order: this counter IS the span's index.
    itemIndex += 1;
    const item = raw as TextItemLike;
    if (item.str.length === 0) return;
    const style = styles[item.fontName];
    if (style?.vertical) return;
    const tx = pdfjs.Util.transform(viewportTransform, item.transform);
    // Rotated text (b or c non-zero) is not a line the edit tool can redraw.
    if (Math.abs(tx[1]) > 1e-3 || Math.abs(tx[2]) > 1e-3) return;
    const geometry = runGeometry(item, viewportTransform);
    if (!(geometry.fontSize > 0)) return;
    const face = faces.get(item.fontName);
    const { ascent, descent } = inkExtent(style);
    const top = geometry.baseline - ascent * geometry.fontSize;
    const bottom = geometry.baseline - descent * geometry.fontSize;
    runs.push({
      index: runs.length,
      text: item.str,
      x: geometry.x,
      y: top,
      width: geometry.width,
      height: bottom - top,
      baseline: geometry.baseline,
      fontSize: geometry.fontSize,
      fontFamily: familyOf(style, face),
      bold: face?.bold ?? false,
      italic: face?.italic ?? false,
      fontName: item.fontName,
      item: itemIndex,
    });
  });
  return runs;
}

/** How far apart two baselines may be and still be one line (× font size). */
export const SAME_BASELINE = 0.5;
/** The widest gap between two runs of one line (× font size). */
export const MAX_GAP = 0.6;
/** A gap wider than this (× font size) is a space between words. */
const SPACE_GAP = 0.15;

const sameStyle = (a: TextRun, b: TextRun) =>
  a.fontFamily === b.fontFamily &&
  a.bold === b.bold &&
  a.italic === b.italic &&
  Math.abs(a.fontSize - b.fontSize) <= Math.max(0.5, 0.1 * a.fontSize);

/**
 * pdf.js hands out words and fragments; a person edits LINES. Runs that share
 * a baseline (|Δ| ≤ 0.5 × size), a style (family, bold, italic, size) and are
 * close (gap ≤ 0.6 × size, a small overlap allowed) become one line -- the
 * unit the edit-text tool frames on hover and opens on click (Sejda does the
 * same). A visible gap between two runs that carry no space of their own
 * becomes one space in the line's text.
 *
 * Whitespace-only runs never start a line and never widen one: pdf.js hands
 * out the gap between two columns as ONE whitespace item as wide as the gap,
 * and letting it stretch the line would glue the columns together. A blank
 * run only says "there is a space here" to the next word.
 */
export function mergeRunsIntoLines(runs: readonly TextRun[]): TextRun[] {
  const lines: Array<TextRun & { right: number; bottom: number; runs: number[] }> = [];
  runs.forEach((run) => {
    const blank = run.text.trim() === '';
    if (blank) return;
    let target: (typeof lines)[number] | undefined;
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      const line = lines[index];
      if (!sameStyle(line, run)) continue;
      if (Math.abs(line.baseline - run.baseline) > SAME_BASELINE * line.fontSize) continue;
      const gap = run.x - line.right;
      if (gap > MAX_GAP * line.fontSize || gap < -0.25 * line.fontSize) continue;
      target = line;
      break;
    }
    if (!target) {
      lines.push({
        ...run,
        index: lines.length,
        right: run.x + run.width,
        bottom: run.y + run.height,
        runs: [run.index],
      });
      return;
    }
    const gap = run.x - target.right;
    const needsSpace =
      gap > SPACE_GAP * target.fontSize && !/\s$/.test(target.text) && !/^\s/.test(run.text);
    target.text += (needsSpace ? ' ' : '') + run.text;
    target.right = Math.max(target.right, run.x + run.width);
    target.y = Math.min(target.y, run.y);
    target.bottom = Math.max(target.bottom, run.y + run.height);
    target.runs.push(run.index);
  });
  return lines.map(({ right, bottom, ...line }) => {
    const text = line.text.replace(/\s+$/u, '');
    return {
      ...line,
      text,
      width: right - line.x,
      height: bottom - line.y,
    };
  });
}
