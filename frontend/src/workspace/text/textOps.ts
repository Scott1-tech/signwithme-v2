import {
  TEXT_SIZE_MAX_PT,
  TEXT_SIZE_MIN_PT,
  type Align,
  type ContentOp,
  type DocumentOp,
  isTextOp,
  type FitMode,
  type Rect,
  type TextDeleteOp,
  type TextReplaceOp,
  type TextRunStyle,
} from '../../api/document';
import { EDITOR_FONT_METRICS } from '../../lib/editorFonts';
import { stripIgnorable } from '../../lib/textCheck';
import type { EditableBlock, TextStyle } from './useServerTextModel';

/**
 * F1b: pure helpers between the editor and the draft's op list.
 *
 * One op per edited block (`id` kept across re-edits: «bir nishonga qayta
 * tahrir = o'sha op almashtiriladi»). A block's op targets ALL its lines,
 * with the text the server must still find there (`expect_text`).
 */

export interface StyleOverride {
  family?: TextStyle['family'];
  size?: number;
  color?: TextStyle['color'];
  bold?: boolean;
  italic?: boolean;
}

export interface BoxPt {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const SIZE_PRESETS = [8, 9, 10, 11, 12, 14, 18, 24, 30, 36, 48, 60, 72];

export const clampTextSize = (size: number) =>
  Math.min(TEXT_SIZE_MAX_PT, Math.max(TEXT_SIZE_MIN_PT, Math.round(size * 2) / 2));

const tidy = (value: number) => Math.round(value * 1000) / 1000;

export const toRect = (box: BoxPt): Rect => ({
  x_pt: tidy(box.x),
  y_pt: tidy(box.y),
  w_pt: tidy(Math.max(0.5, box.w)),
  h_pt: tidy(Math.max(0.5, box.h)),
});

export const fromRect = (rect: Rect): BoxPt => ({ x: rect.x_pt, y: rect.y_pt, w: rect.w_pt, h: rect.h_pt });

const withOverride = (style: TextStyle, override: StyleOverride | null): TextStyle =>
  override
    ? {
        family: override.family ?? style.family,
        size: override.size ?? style.size,
        color: override.color ?? style.color,
        bold: override.bold ?? style.bold,
        italic: override.italic ?? style.italic,
      }
    : style;

const runStyle = (style: TextStyle): Omit<TextRunStyle, 'text'> => ({
  font: style.family,
  size_pt: clampTextSize(style.size),
  color: style.color,
  ...(style.bold ? { bold: true } : {}),
  ...(style.italic ? { italic: true } : {}),
});

/**
 * The runs of the new text: characters kept from the original (common prefix
 * and suffix) keep their own style; typed characters take the style of the
 * place they were typed at. A style picked on the toolbar applies to the
 * whole block.
 */
export function buildRuns(block: EditableBlock, text: string, override: StyleOverride | null): TextRunStyle[] {
  const old = block.text;
  let prefix = 0;
  while (prefix < old.length && prefix < text.length && old[prefix] === text[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < old.length - prefix &&
    suffix < text.length - prefix &&
    old[old.length - 1 - suffix] === text[text.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const styleAtOld = (index: number): number => {
    for (let at = Math.min(index, old.length - 1); at >= 0; at -= 1) {
      if (block.charStyle[at] >= 0) return block.charStyle[at];
    }
    for (let at = Math.max(0, index); at < old.length; at += 1) {
      if (block.charStyle[at] >= 0) return block.charStyle[at];
    }
    return 0;
  };
  const middleStyle = styleAtOld(prefix < old.length ? prefix : old.length - 1);
  const styleOf = (index: number): number => {
    if (index < prefix) return styleAtOld(index);
    if (index >= text.length - suffix) return styleAtOld(old.length - (text.length - index));
    return middleStyle;
  };
  const runs: TextRunStyle[] = [];
  let currentKey = '';
  for (let index = 0; index < text.length; index += 1) {
    const style = withOverride(block.styles[styleOf(index)] ?? block.style, override);
    const shaped = runStyle(style);
    const key = JSON.stringify(shaped);
    if (runs.length && key === currentKey) {
      runs[runs.length - 1].text += text[index];
    } else {
      runs.push({ text: text[index], ...shaped });
      currentKey = key;
    }
  }
  return runs;
}

/** The target of an op on this block. */
export const targetOf = (block: EditableBlock) => ({
  block_id: block.id,
  line_ids: [...block.lineIds],
  expect_text: block.expectText,
  bbox: toRect(block.bbox),
});

export interface EditResult {
  text: string;
  override: StyleOverride | null;
  /** The box the user set (moved / widened), or the auto-grown box; null = the block's own. */
  box: BoxPt | null;
  fit: FitMode;
  /** F2: the block's alignment / line spacing as the toolbar set them (null = as it was). */
  align?: Align | null;
  lineHeight?: number | null;
}

/** The op for an edit, or null when the block is back to what it was. */
export function opFor(id: string, block: EditableBlock, edit: EditResult): ContentOp | null {
  // Ignorable zero-width characters are never sent (the server leaves them out too).
  const text = stripIgnorable(edit.text.normalize('NFC'));
  if (text.trim() === '') {
    const op: TextDeleteOp = { id, kind: 'text.delete', page_uid: block.pageUid, target: targetOf(block) };
    return op;
  }
  const overrideSaysSomething =
    edit.override !== null &&
    Object.entries(edit.override).some(
      ([key, value]) => value !== undefined && value !== (block.style as unknown as Record<string, unknown>)[key === 'family' ? 'family' : key],
    );
  const layoutSaysSomething = Boolean(edit.align) || Boolean(edit.lineHeight);
  if (text === block.text && !overrideSaysSomething && !edit.box && edit.fit === 'wrap' && !layoutSaysSomething) return null;
  const op: TextReplaceOp = {
    id,
    kind: 'text.replace',
    page_uid: block.pageUid,
    target: targetOf(block),
    runs: buildRuns(block, text, overrideSaysSomething ? edit.override : null),
    fit: edit.fit,
  };
  if (edit.box) op.box = toRect(edit.box);
  if (block.lineIds.length > 1) {
    const size = Math.max(...block.styles.map((style) => style.size));
    const ratio = block.rowGap && size > 0 ? block.rowGap / size : 1.2;
    op.line_height = Math.round(Math.min(3, Math.max(0.8, ratio)) * 1000) / 1000;
  }
  // F2: the floating toolbar's alignment and line spacing.
  if (edit.align) op.align = edit.align;
  if (edit.lineHeight) op.line_height = Math.round(Math.min(3, Math.max(0.8, edit.lineHeight)) * 1000) / 1000;
  return op;
}

/** Put (or replace, same id) / remove the op of one block. */
export function withBlockOp(list: readonly DocumentOp[], blockKey: BlockKey, op: ContentOp | null): DocumentOp[] {
  // F2: the list also holds page / link / redact / image ops (no `target`).
  const rest = list.filter(
    (item) => !(isTextOp(item) && item.page_uid === blockKey.pageUid && item.target.block_id === blockKey.blockId),
  );
  return op ? [...rest, op] : rest;
}

export interface BlockKey {
  pageUid: string;
  blockId: string;
}

export const opOfBlock = (list: readonly DocumentOp[], key: BlockKey): ContentOp | undefined =>
  list.find(
    (item): item is ContentOp => isTextOp(item) && item.page_uid === key.pageUid && item.target.block_id === key.blockId,
  );

/** What an op makes of its block, for the editor (text, style, box, fit). */
export function editOf(block: EditableBlock, op: ContentOp | undefined): EditResult {
  if (!op) return { text: block.text, override: null, box: null, fit: 'wrap' };
  if (op.kind === 'text.delete') return { text: '', override: null, box: null, fit: 'wrap' };
  const first = op.runs[0];
  const override: StyleOverride = {
    family: first.font,
    size: first.size_pt,
    color: first.color,
    bold: Boolean(first.bold),
    italic: Boolean(first.italic),
  };
  return {
    text: op.runs.map((run) => run.text).join(''),
    override,
    box: op.box ? fromRect(op.box) : null,
    fit: op.fit ?? 'wrap',
    align: op.align ?? null,
    lineHeight: null,
  };
}

/** Text index nearest to a point (PDF points): the row by baseline, then the nearest char boundary. */
export function offsetAt(block: EditableBlock, x: number, y: number): number {
  if (!block.rows.length) return block.text.length;
  let row = 0;
  let best = Infinity;
  block.rows.forEach((candidate, index) => {
    const distance = Math.abs(candidate.baseline - (y + 0.3 * block.style.size));
    if (distance < best) {
      best = distance;
      row = index;
    }
  });
  const chars = block.chars.filter((char) => char.row === row);
  if (!chars.length) return block.rows[row].start;
  let offset = chars[0].index;
  let distance = Math.abs(x - chars[0].x0);
  chars.forEach((char) => {
    const before = Math.abs(x - char.x0);
    if (before < distance) {
      distance = before;
      offset = char.index;
    }
    const after = Math.abs(x - char.x1);
    if (after < distance) {
      distance = after;
      offset = char.index + 1;
    }
  });
  return offset;
}

/** [start, end) of the word around `offset`. */
export function wordAt(text: string, offset: number): [number, number] {
  const isWord = (c: string | undefined) => c !== undefined && /[\p{L}\p{N}_'’-]/u.test(c);
  let start = offset;
  let end = offset;
  if (!isWord(text[start]) && isWord(text[start - 1])) start -= 1;
  while (start > 0 && isWord(text[start - 1])) start -= 1;
  end = Math.max(end, start);
  while (end < text.length && isWord(text[end])) end += 1;
  return [start, end];
}

/** [start, end) of the row around `offset`. */
export function rowAt(text: string, offset: number): [number, number] {
  const start = text.lastIndexOf('\n', Math.max(0, offset - 1)) + 1;
  const next = text.indexOf('\n', offset);
  return [start, next < 0 ? text.length : next];
}

/**
 * Line-box metrics of the Liberation faces (hhea ascender / descender in em),
 * what the browser lays a line out with: where the baseline of the editor's
 * first line is, so it sits exactly on the PDF's.
 */
export const FACE_METRICS: Record<TextStyle['family'], { ascent: number; descent: number }> = {
  ...EDITOR_FONT_METRICS,
  LiberationSans: { ascent: 0.905, descent: 0.212 },
  LiberationSerif: { ascent: 0.891, descent: 0.216 },
  LiberationMono: { ascent: 0.833, descent: 0.3 },
};

/** Top of the first line box (pt) for a baseline, font size and line height (pt). */
export function lineTopFor(baseline: number, family: TextStyle['family'], size: number, lineHeight: number): number {
  const { ascent, descent } = FACE_METRICS[family];
  const halfLeading = (lineHeight - (ascent + descent) * size) / 2;
  return baseline - halfLeading - ascent * size;
}

/** Hex colours offered for a text edit: the palette (no white) and the block's own. */
export const TEXT_COLORS: ReadonlyArray<{ name: string; hex: `#${string}` }> = [
  { name: 'Black', hex: '#000000' },
  { name: 'Blue', hex: '#1D4ED8' },
  { name: 'Red', hex: '#DC2626' },
  { name: 'Green', hex: '#16A34A' },
  { name: 'Orange', hex: '#EA580C' },
  { name: 'Purple', hex: '#7E22CE' },
];
