import { useCallback, useEffect, useRef, useState } from 'react';
import {
  documentApi,
  type EditorFontFamilyId,
  type Hex,
  type Rect,
  type TextChar,
  type TextModelOut,
  type TextSpanOut,
} from '../../api/document';
import { isIgnorable } from '../../lib/textCheck';

/**
 * F1b: the server's text model of a page, as the editor uses it.
 *
 * THE ONE ADAPTER. Everything the editor knows about the PDF's own text goes
 * through `adaptModel` below; the wire shape (`TextModelOut`, B1) is read
 * nowhere else -- a change of the server's shape (B1.1 merges same-baseline
 * spans into one line) is an edit of this file only.
 *
 * B1.1 (2026-09-29): blocks come in GEOMETRIC order (first-line baseline,
 * then left edge; lines top to bottom), a logical line may hold spans of
 * several source fragments / fonts (`line.text` is the whole line), and a
 * glyph-less server-inserted space is a `synthetic` span: its character takes
 * a real neighbour's style and it is never the block's style sample. One op
 * targets a whole block (all its lines), so two ops never share a line (422
 * `duplicate_target`).
 *
 * Cached by `(version_no, file_hash, page_uid)`, at most `MAX_MODELS` pages
 * (oldest first). Block ids are positional PER VERSION: a commit makes a new
 * version, whose models are fetched again -- an id is never carried over.
 */

export const MAX_MODELS = 10;

export interface TextStyle {
  /** F2: any family of `GET /api/fonts` (B2's substitute map, or the user's pick). */
  family: EditorFontFamilyId;
  size: number;
  color: Hex;
  bold: boolean;
  italic: boolean;
}

export interface EditableChar {
  /** Index in `EditableBlock.text`. */
  index: number;
  c: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  row: number;
}

export interface EditableRow {
  baseline: number;
  x0: number;
  x1: number;
  /** Where the row starts / ends in `EditableBlock.text`. */
  start: number;
  end: number;
}

export interface EditableBlock {
  id: string;
  pageUid: string;
  /** PDF points, top-left, unrotated. */
  bbox: { x: number; y: number; w: number; h: number };
  editable: boolean;
  reason: string | null;
  /** The target: every line of the block, in order. */
  lineIds: string[];
  /** What the server must still find there (lines joined by `\n`, NFC). */
  expectText: string;
  /** What the user sees and edits: the block's rows (lines on one baseline joined) by `\n`. */
  text: string;
  rows: EditableRow[];
  chars: EditableChar[];
  /** The style of each character of `text` (index into `styles`; -1 for a row break). */
  charStyle: number[];
  styles: TextStyle[];
  /** The first span's style: what the editor shows the block in. */
  style: TextStyle;
  /** The original font's NAME (subset prefix stripped), for «Times → Liberation Serif». */
  originalFont: string;
  firstBaseline: number;
  /** Baseline to baseline of the rows (null: one row). */
  rowGap: number | null;
}

export interface PageTextModel {
  pageUid: string;
  versionNo: number;
  fileHash: string;
  width: number;
  height: number;
  rotation: number;
  scanned: boolean;
  blocks: EditableBlock[];
}

/** A character whose style is not known yet (a synthetic span's). */
const PENDING = -2;

/** B1.1 `TextSpanOut.synthetic` (read defensively: older servers do not send it). */
const isSynthetic = (span: TextSpanOut): boolean => (span as TextSpanOut & { synthetic?: boolean }).synthetic === true;

/** A glyph-less synthetic space: the gap between its real neighbours on the line. */
function syntheticBox(spans: readonly TextSpanOut[], index: number, bbox: Rect): [number, number, number, number] {
  const before = spans.slice(0, index).reverse().find((span) => span.chars.length);
  const after = spans.slice(index + 1).find((span) => span.chars.length);
  const x0 = before ? before.chars[before.chars.length - 1][3] : bbox.x_pt;
  const x1 = after ? after.chars[0][1] : Math.max(x0, bbox.x_pt + bbox.w_pt);
  return [x0, bbox.y_pt, Math.max(x0, x1), bbox.y_pt + bbox.h_pt];
}

const styleKey = (style: TextStyle) =>
  `${style.family}|${style.size}|${style.color}|${style.bold ? 1 : 0}|${style.italic ? 1 : 0}`;

/** Wire → editor model. */
export function adaptModel(wire: TextModelOut): PageTextModel {
  const blocks: EditableBlock[] = wire.blocks.map((block) => {
    const styles: TextStyle[] = [];
    const styleIndex = new Map<string, number>();
    const indexOf = (style: TextStyle) => {
      const key = styleKey(style);
      let at = styleIndex.get(key);
      if (at === undefined) {
        at = styles.length;
        styles.push(style);
        styleIndex.set(key, at);
      }
      return at;
    };
    let text = '';
    const chars: EditableChar[] = [];
    const charStyle: number[] = [];
    const rows: EditableRow[] = [];
    let originalFont = '';
    let firstStyle: TextStyle | null = null;
    let lastLineEnd: number | null = null;
    let lastBaseline: number | null = null;
    let lastSize = 0;

    block.lines.forEach((line) => {
      // B1.1: a server-inserted space between two source fragments is a
      // `synthetic` span -- no glyph in the PDF, never a style sample.
      const real = line.spans.filter((span) => !isSynthetic(span));
      const firstSpan = real[0] ?? line.spans[0];
      const baseline = firstSpan ? firstSpan.baseline : line.bbox.y_pt + line.bbox.h_pt;
      const size = firstSpan ? firstSpan.size_pt : line.bbox.h_pt;
      const sameRow = lastBaseline !== null && Math.abs(baseline - lastBaseline) <= Math.max(0.5, 0.3 * Math.max(size, lastSize));
      if (lastBaseline !== null) {
        if (sameRow) {
          // Two lines of one row (the server's lines can share a baseline):
          // a space where there is a visible gap, nothing where they touch.
          const gap = lastLineEnd !== null ? line.bbox.x_pt - lastLineEnd : 0;
          if (gap > 0.15 * size) {
            const style = charStyle.length ? charStyle[charStyle.length - 1] : 0;
            chars.push({ index: text.length, c: ' ', x0: lastLineEnd ?? line.bbox.x_pt, y0: line.bbox.y_pt, x1: line.bbox.x_pt, y1: line.bbox.y_pt + line.bbox.h_pt, row: rows.length - 1 });
            charStyle.push(style);
            text += ' ';
          }
        } else {
          rows[rows.length - 1].end = text.length;
          charStyle.push(-1);
          text += '\n';
        }
      }
      if (!sameRow) {
        rows.push({ baseline, x0: line.bbox.x_pt, x1: line.bbox.x_pt + line.bbox.w_pt, start: text.length, end: text.length });
      } else {
        const row = rows[rows.length - 1];
        row.x1 = Math.max(row.x1, line.bbox.x_pt + line.bbox.w_pt);
        row.x0 = Math.min(row.x0, line.bbox.x_pt);
      }
      const lineStart = charStyle.length;
      line.spans.forEach((span, spanIndex) => {
        if (isSynthetic(span)) {
          // Its character(s) take the style of a real neighbour (resolved below).
          const glyphs = span.chars.length
            ? span.chars
            : [[' ', ...syntheticBox(line.spans, spanIndex, line.bbox)] as TextChar];
          glyphs.forEach(([c, x0, y0, x1, y1]) => {
            chars.push({ index: text.length, c, x0, y0, x1, y1, row: rows.length - 1 });
            charStyle.push(PENDING);
            text += c;
          });
          return;
        }
        const style: TextStyle = {
          // B2: the substitution map's family (Calibri → Carlito) when there is one.
          family: span.substitute_family ?? span.family,
          size: span.size_pt,
          color: span.color,
          bold: span.bold,
          italic: span.italic,
        };
        if (!firstStyle) {
          firstStyle = style;
          originalFont = span.font;
        }
        const at = indexOf(style);
        span.chars.forEach(([c, x0, y0, x1, y1]) => {
          // A server before 2026-09-30 sent U+200B & co. (Docusign): never
          // shown, never edited (the current one leaves them out itself).
          if (isIgnorable(c)) return;
          chars.push({ index: text.length, c, x0, y0, x1, y1, row: rows.length - 1 });
          charStyle.push(at);
          text += c;
        });
      });
      // Synthetic characters: the real style before them on the line, else after.
      for (let at = lineStart; at < charStyle.length; at += 1) {
        if (charStyle[at] !== PENDING) continue;
        let pick = -1;
        for (let back = at - 1; back >= lineStart && pick < 0; back -= 1) {
          if (charStyle[back] >= 0) pick = charStyle[back];
        }
        for (let ahead = at + 1; ahead < charStyle.length && pick < 0; ahead += 1) {
          if (charStyle[ahead] >= 0) pick = charStyle[ahead];
        }
        charStyle[at] = pick >= 0 ? pick : 0;
      }
      lastLineEnd = line.bbox.x_pt + line.bbox.w_pt;
      lastBaseline = baseline;
      lastSize = size;
    });
    if (rows.length) rows[rows.length - 1].end = text.length;
    const style: TextStyle = firstStyle ?? {
      family: 'LiberationSans',
      size: 11,
      color: '#000000',
      bold: false,
      italic: false,
    };
    if (!styles.length) styles.push(style);
    return {
      id: block.id,
      pageUid: wire.page_uid,
      bbox: { x: block.bbox.x_pt, y: block.bbox.y_pt, w: block.bbox.w_pt, h: block.bbox.h_pt },
      editable: block.editable && !wire.scanned,
      reason: wire.scanned ? 'scanned_page' : block.reason,
      lineIds: block.lines.map((line) => line.id),
      expectText: block.lines.map((line) => line.text).join('\n').normalize('NFC'),
      text,
      rows,
      chars,
      charStyle,
      styles,
      style,
      originalFont,
      firstBaseline: rows[0]?.baseline ?? block.bbox.y_pt + block.bbox.h_pt,
      rowGap: rows.length >= 2 ? rows[1].baseline - rows[0].baseline : null,
    };
  });
  return {
    pageUid: wire.page_uid,
    versionNo: wire.version_no,
    fileHash: wire.file_hash,
    width: wire.width_pt,
    height: wire.height_pt,
    rotation: wire.rotation,
    scanned: wire.scanned,
    blocks,
  };
}

/** Why a block cannot be edited, in words. */
export function reasonText(reason: string | null): string {
  switch (reason) {
    case 'scanned_page':
      return 'This page is a scanned image — its text cannot be edited (OCR is a separate feature).';
    case 'type3_font':
      return 'This text uses a drawn (Type 3) font that cannot be edited in place.';
    case 'rtl_text':
      return 'Right-to-left text cannot be edited in place yet.';
    case 'vertical_or_rotated_text':
      return 'Vertical or rotated text cannot be edited in place.';
    default:
      return 'This text cannot be edited in place.';
  }
}

/**
 * The models of the pages asked for, for one version. `models` holds what
 * has arrived (page uid → model); `request(uid)` asks for one (cached).
 */
export function useServerTextModel(
  contractId: string | undefined,
  versionNo: number | null,
  fileHash: string | null,
) {
  const cache = useRef(new Map<string, PageTextModel>());
  const jobs = useRef(new Map<string, Promise<PageTextModel | null>>());
  const [models, setModels] = useState<ReadonlyMap<string, PageTextModel>>(() => new Map());
  const [errors, setErrors] = useState<ReadonlyMap<string, string>>(() => new Map());
  const versionKey = `${contractId}|${versionNo}|${fileHash}`;
  const keyRef = useRef(versionKey);

  // A new version (or contract): nothing of the old one is reused.
  useEffect(() => {
    keyRef.current = versionKey;
    cache.current.clear();
    jobs.current.clear();
    setModels(new Map());
    setErrors(new Map());
  }, [versionKey]);

  const request = useCallback(
    (pageUid: string): Promise<PageTextModel | null> => {
      if (!contractId || !versionNo) return Promise.resolve(null);
      const cacheKey = `${versionKey}|${pageUid}`;
      const hit = cache.current.get(cacheKey);
      if (hit) {
        cache.current.delete(cacheKey);
        cache.current.set(cacheKey, hit);
        return Promise.resolve(hit);
      }
      const running = jobs.current.get(cacheKey);
      if (running) return running;
      const job = documentApi
        .pageText(contractId, versionNo, pageUid)
        .then((wire) => {
          if (keyRef.current !== versionKey) return null;
          const model = adaptModel(wire);
          if (fileHash && wire.file_hash !== fileHash) return null;
          cache.current.set(cacheKey, model);
          while (cache.current.size > MAX_MODELS) {
            const oldest = cache.current.keys().next().value as string | undefined;
            if (!oldest) break;
            cache.current.delete(oldest);
          }
          setModels(() => {
            const next = new Map<string, PageTextModel>();
            cache.current.forEach((value) => next.set(value.pageUid, value));
            return next;
          });
          return model;
        })
        .catch((cause: unknown) => {
          if (keyRef.current === versionKey) {
            setErrors((current) => {
              const next = new Map(current);
              next.set(pageUid, cause instanceof Error ? cause.message : 'The page text could not be read');
              return next;
            });
          }
          return null;
        })
        .finally(() => {
          jobs.current.delete(cacheKey);
        });
      jobs.current.set(cacheKey, job);
      return job;
    },
    [contractId, fileHash, versionKey, versionNo],
  );

  return { models, errors, request };
}
