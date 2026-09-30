import { useEffect, useRef } from 'react';
import type { TextLayerHandle } from '../../hooks/usePdfViewer';
import { clampBox } from '../../lib/annotations';
import { ensureFontLoaded, type FontFamily } from '../../lib/fonts';
import type { PageSizePt } from '../../lib/layout';
import { advanceWithin, type TextRun } from '../../lib/textRuns';
import type { RectPt } from '../tools/types';

/**
 * The browser's text selection, over the pdf.js text layer, into boxes in PDF
 * points (plan §3, W3) -- what highlight / underline / strikeout are made of.
 *
 * WHERE EACH NUMBER COMES FROM
 * ----------------------------
 * The spans pdf.js lays over the page are set in a FALLBACK font (the PDF's
 * own is not a web font) and stretched to the right width, so their glyph
 * boxes are close but not exact -- and their height is the fallback font's,
 * not the line's. So the selection is read as CHARACTERS, not as pixels:
 *
 *  * which span, and which characters of it: the selection's range over each
 *    text node (`startOffset` / `endOffset`); the span is the node's parent,
 *    `TextLayerHandle.spanIndexOf(span)` names the pdf.js item, and the run
 *    built from that item (`lib/textRuns.ts`, the ONE geometry source) gives
 *    the rest;
 *  * vertically: the run's INK box -- ascent above to descent below the
 *    baseline, the same box an edited line's whiteout covers;
 *  * horizontally: the run's own start and advance width, split at the
 *    selected characters in the metric-compatible Liberation face of the
 *    run's family (Helvetica / Times / Courier widths), scaled so the whole
 *    run is exactly its PDF width. Leading / trailing blanks are not marked.
 *
 * Then, per page: boxes of one LINE are merged (their vertical extents overlap
 * by half the smaller one, or their tops are < 2 pt apart) -- unless a gap
 * wider than the line is tall separates them (two columns stay two boxes);
 * each box is clamped into the page, ≥ 1 pt on both axes. The tool splits a
 * page of more than 50 boxes into several annotations.
 *
 * A selection that crosses a page break yields one entry PER PAGE; the
 * workspace commits them together, one undo step. The browser's selection is
 * cleared afterwards -- the mark replaces it.
 */

/** A page's drawn text layer: the spans' container and what pdf.js said about them. */
export interface TextLayerEntry {
  container: HTMLDivElement;
  handle: TextLayerHandle;
}

/** One page's boxes out of a selection. */
export interface PageSelection {
  page: number;
  rects: RectPt[];
}

/** Two boxes whose tops are closer than this are on one line (plan §3). */
export const SAME_LINE_PT = 2;

const tidy = (value: number) => Math.round(value * 100) / 100;

const runsByItem = new WeakMap<TextLayerHandle, Map<number, TextRun>>();
function runOfSpan(handle: TextLayerHandle, span: Element): TextRun | undefined {
  let map = runsByItem.get(handle);
  if (!map) {
    map = new Map();
    handle.runs.forEach((run) => {
      if (typeof run.item === 'number') map!.set(run.item, run);
    });
    runsByItem.set(handle, map);
  }
  const index = handle.spanIndexOf(span);
  return index < 0 ? undefined : map.get(index);
}

/** How far into `run` its first `count` characters reach, in PDF points. */
export const advanceOf = (run: TextRun, count: number): number =>
  advanceWithin(run.text, run.width, run.fontFamily, run.bold, run.italic, count);

const isBlank = (char: string | undefined) => char !== undefined && /\s/u.test(char);

/** Every selected piece of every span of one page's layer, as raw boxes. */
function boxesIn(range: Range, layer: TextLayerEntry): RectPt[] {
  const boxes: RectPt[] = [];
  const walker = document.createTreeWalker(layer.container, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text;
    if (!range.intersectsNode(text)) continue;
    const span = text.parentElement;
    if (!span) continue;
    const run = runOfSpan(layer.handle, span);
    if (!run) continue;
    const limit = Math.min(text.length, run.text.length);
    let start = text === range.startContainer ? range.startOffset : 0;
    let end = text === range.endContainer ? range.endOffset : text.length;
    start = Math.max(0, Math.min(start, limit));
    end = Math.max(0, Math.min(end, limit));
    while (start < end && isBlank(run.text[start])) start += 1;
    while (end > start && isBlank(run.text[end - 1])) end -= 1;
    if (end <= start) continue;
    const left = run.x + advanceOf(run, start);
    const right = run.x + advanceOf(run, end);
    if (!(right > left)) continue;
    boxes.push({ x: left, y: run.y, w: right - left, h: run.height });
  }
  return boxes;
}

const sameLine = (a: RectPt, b: RectPt) => {
  if (Math.abs(a.y - b.y) < SAME_LINE_PT) return true;
  const overlap = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return overlap >= 0.5 * Math.min(a.h, b.h);
};

/**
 * Boxes of one line merged into one (plan §3), top to bottom, left to right --
 * except across a gap wider than the line is tall (a column gutter).
 */
export function mergeLineBoxes(boxes: readonly RectPt[]): RectPt[] {
  const sorted = [...boxes].sort((a, b) => a.y - b.y || a.x - b.x);
  const lines: RectPt[][] = [];
  sorted.forEach((box) => {
    const line = lines.find((entry) => sameLine(entry[0], box));
    if (line) line.push(box);
    else lines.push([box]);
  });
  const merged: RectPt[] = [];
  lines.forEach((line) => {
    line.sort((a, b) => a.x - b.x);
    let current: RectPt | null = null;
    line.forEach((box) => {
      if (current) {
        const gap = box.x - (current.x + current.w);
        if (gap <= Math.max(current.h, box.h)) {
          const right = Math.max(current.x + current.w, box.x + box.w);
          const top = Math.min(current.y, box.y);
          const bottom = Math.max(current.y + current.h, box.y + box.h);
          current = { x: current.x, y: top, w: right - current.x, h: bottom - top };
          return;
        }
        merged.push(current);
      }
      current = { ...box };
    });
    if (current) merged.push(current);
  });
  return merged.sort((a, b) => a.y - b.y || a.x - b.x);
}

/**
 * The selection's boxes, page by page (only the pages whose text layer it
 * touches), merged per line and clamped into the page. Pure apart from reading
 * the DOM ranges, so the checks can pin it.
 */
export function selectionToPages(
  ranges: readonly Range[],
  layers: ReadonlyMap<number, TextLayerEntry>,
  pageSizeOf: (page: number) => PageSizePt | null,
): PageSelection[] {
  const result: PageSelection[] = [];
  [...layers.keys()].sort((a, b) => a - b).forEach((page) => {
    const layer = layers.get(page);
    if (!layer || !layer.container.isConnected) return;
    const raw: RectPt[] = [];
    ranges.forEach((range) => {
      if (range.intersectsNode(layer.container)) raw.push(...boxesIn(range, layer));
    });
    if (raw.length === 0) return;
    const size = pageSizeOf(page);
    const rects = mergeLineBoxes(raw).map((box) => {
      const clamped = clampBox(box, size);
      return { x: tidy(clamped.x), y: tidy(clamped.y), w: tidy(clamped.w), h: tidy(clamped.h) };
    });
    if (rects.length > 0) result.push({ page, rects });
  });
  return result;
}

/** Every face a run can be measured in (loaded up front, while the tool is armed). */
const FACES: Array<[FontFamily, boolean, boolean]> = (
  ['LiberationSans', 'LiberationSerif', 'LiberationMono'] as FontFamily[]
).flatMap((family) => [
  [family, false, false] as [FontFamily, boolean, boolean],
  [family, true, false] as [FontFamily, boolean, boolean],
  [family, false, true] as [FontFamily, boolean, boolean],
  [family, true, true] as [FontFamily, boolean, boolean],
]);
const loadFaces = () => Promise.all(FACES.map(([family, bold, italic]) => ensureFontLoaded(family, bold, italic)));

interface Options {
  /** A `text-selection` tool is armed. */
  active: boolean;
  /** page -> its drawn text layer (kept by the workspace from `PdfTextLayer onReady`). */
  layers: ReadonlyMap<number, TextLayerEntry>;
  pageSizeOf: (page: number) => PageSizePt | null;
  /** The boxes of a finished selection, one entry per page (never empty). */
  onSelect: (pages: PageSelection[]) => void;
}

/**
 * While a text-selection tool is armed: every time a pointer is released (a
 * drag, a double-click on a word), a selection that touches a page's text
 * layer is turned into boxes (`selectionToPages`), handed to `onSelect`, and
 * cleared. A selection anywhere else (the side panel, a form field) is not
 * touched.
 */
export function useTextSelection({ active, layers, pageSizeOf, onSelect }: Options): void {
  const latest = useRef({ layers, pageSizeOf, onSelect });
  latest.current = { layers, pageSizeOf, onSelect };

  useEffect(() => {
    if (!active) return undefined;
    void loadFaces();
    let alive = true;
    const finish = async () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;
      const ranges: Range[] = [];
      for (let index = 0; index < selection.rangeCount; index += 1) {
        ranges.push(selection.getRangeAt(index).cloneRange());
      }
      const touched = [...latest.current.layers.values()].some((layer) =>
        ranges.some((range) => range.intersectsNode(layer.container)),
      );
      if (!touched) return;
      // The split of a run is measured in its Liberation face: it has to be in.
      await loadFaces();
      if (!alive) return;
      const pages = selectionToPages(ranges, latest.current.layers, latest.current.pageSizeOf);
      window.getSelection()?.removeAllRanges();
      if (pages.length > 0) latest.current.onSelect(pages);
    };
    const onUp = () => {
      // After the browser has finished the selection this release ends.
      window.setTimeout(() => void finish(), 0);
    };
    document.addEventListener('pointerup', onUp);
    return () => {
      alive = false;
      document.removeEventListener('pointerup', onUp);
    };
  }, [active]);
}
