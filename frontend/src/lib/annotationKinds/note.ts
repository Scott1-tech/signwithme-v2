import type { PageSizePt } from '../layout';
import {
  clampPointOn,
  finite,
  groupKey,
  isAnnotationColor,
  pageExtent,
  tidy,
  type AnnotationColor,
  type AnnotationCommon,
  type BoxPt,
  type SanitizeBase,
} from './shared';

/**
 * `note` (contract v5, W4): a sticky note -- a REAL PDF `/Text` annotation
 * (icon + popup), not ink on the page. `(x_pt, y_pt)` is the icon's TOP-LEFT
 * corner; the icon is `NOTE_ICON_PT` square (`pdf_builder.NOTE_ICON_PT`),
 * drawn by every viewer below-right of that point. The text is 1…1000
 * characters of WinAnsi (the standard fonts' set), line breaks allowed;
 * default colour yellow.
 *
 * The server only needs the anchor on the page; here the anchor is kept far
 * enough in that the whole icon is on it too, so what the reader clicks on
 * paper is never half off the sheet.
 */

export const NOTE_ICON_PT = 20;
export const MAX_NOTE_TEXT = 1000;
export const NOTE_DEFAULT_COLOR: AnnotationColor = 'yellow';
/** The face the server checks a note's text against (`pdf_builder.ANNOTATION_FONT`). */
export const NOTE_FONT = 'Helvetica' as const;

export interface NoteAnnotation extends AnnotationCommon {
  kind: 'note';
  x_pt: number;
  y_pt: number;
  text: string;
  color: AnnotationColor;
}

export interface NoteRequest {
  id: string;
  kind: 'note';
  page: number;
  x_pt: number;
  y_pt: number;
  text: string;
  color: AnnotationColor;
  group_id?: string;
}

/** Line breaks as `\n` only (a pasted `\r\n` would be a control character to the server). */
export const normalizeNoteText = (text: string): string =>
  text.replace(/\r\n?/g, '\n').normalize('NFC');

/** Where a note's anchor may go: the whole icon on the page. */
export function clampNoteAnchor(point: { x: number; y: number }, page: PageSizePt | null): { x: number; y: number } {
  const { width, height } = pageExtent(page);
  const inside = clampPointOn(point, page);
  return {
    x: tidy(Math.min(inside.x, Math.max(0, width - NOTE_ICON_PT))),
    y: tidy(Math.min(inside.y, Math.max(0, height - NOTE_ICON_PT))),
  };
}

export function makeNote(
  base: { id: string; page: number; x: number; y: number; text: string; color?: AnnotationColor; group_id?: string },
  page: PageSizePt | null,
): NoteAnnotation | null {
  const text = normalizeNoteText(base.text).slice(0, MAX_NOTE_TEXT);
  if (text.trim() === '') return null;
  const at = clampNoteAnchor({ x: base.x, y: base.y }, page);
  const made: NoteAnnotation = {
    id: base.id,
    kind: 'note',
    page: base.page,
    x_pt: at.x,
    y_pt: at.y,
    text,
    color: base.color ?? NOTE_DEFAULT_COLOR,
  };
  if (base.group_id) made.group_id = base.group_id;
  return made;
}

export function sanitizeNote({ id, page, group_id, source }: SanitizeBase): NoteAnnotation | null {
  const x = finite(source.x_pt);
  const y = finite(source.y_pt);
  if (x === null || y === null || typeof source.text !== 'string') return null;
  return makeNote(
    {
      id,
      page,
      x,
      y,
      text: source.text,
      color: isAnnotationColor(source.color) ? source.color : NOTE_DEFAULT_COLOR,
      group_id,
    },
    null,
  );
}

export const noteForRequest = (item: NoteAnnotation): NoteRequest => ({
  id: item.id,
  kind: 'note',
  page: item.page,
  x_pt: tidy(item.x_pt),
  y_pt: tidy(item.y_pt),
  text: normalizeNoteText(item.text),
  color: item.color,
  ...groupKey(item),
});

export const noteBounds = (item: { x_pt: number; y_pt: number }): BoxPt => ({
  x: item.x_pt,
  y: item.y_pt,
  w: NOTE_ICON_PT,
  h: NOTE_ICON_PT,
});

export const sameNote = (a: NoteAnnotation, b: NoteAnnotation): boolean => a.text === b.text && a.color === b.color;
