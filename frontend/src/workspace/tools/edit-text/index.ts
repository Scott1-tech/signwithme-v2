import type { AnnotationFont } from '../../../lib/annotations';
import type { TextRun } from '../../../lib/textRuns';
import type { ToolDefinition } from '../types';

/**
 * Edit text v2 (F1b, EDITOR-V2-PLAN §7 «Mavjud matnni tahrirlash»): change
 * the words that are ALREADY in the PDF -- for real.
 *
 * The page's text is the SERVER's text model (PyMuPDF blocks → lines →
 * chars, `GET …/versions/{no}/pages/{uid}/text`), drawn as hover frames by
 * `workspace/text/TextBlockLayer.tsx`; a click opens `TextBlockEditor` on the
 * block, and the commit is a `text.replace` (or `text.delete`) CONTENT
 * operation of the edit session's draft -- the server removes the old glyphs
 * and sets the new text in the file itself. There is no white box anywhere:
 * the WE tool of 2026-09-27 (whiteout + text on top) is gone (user decision
 * «oq quti TAQIQ»); edits made with it are still drawn as legacy markup and
 * can be turned into real edits from Objects («Convert to real edit»).
 *
 * The tool's own click handling lives in the workspace (the layer is not
 * an annotation tool). When the server has no document editor (an older
 * server, or a PDF it cannot open) the tool still shows the page's lines
 * (the pdf.js layer: search and markup keep their one geometry source) but a
 * click only says why nothing can be edited -- it never falls back to a
 * white box.
 */

/** Liberation family for a pdf.js family shape (kept for callers that map a run). */
export function familyFor(run: Pick<TextRun, 'fontFamily'>): AnnotationFont {
  if (run.fontFamily === 'serif') return 'LiberationSerif';
  if (run.fontFamily === 'monospace') return 'LiberationMono';
  return 'LiberationSans';
}

export const EDIT_TEXT_UNAVAILABLE =
  'Editing the document’s own text needs the server’s document editor, which is not available for this ' +
  'contract. Nothing was changed.';

const editText: ToolDefinition = {
  id: 'edit-text',
  label: 'Edit text',
  glyph: '✎',
  hint: 'Click a block of the document’s text to change it (Edit PDF session)',
  shortcut: 'E',
  group: 'text',
  order: 20,
  menu: 'text',
  cursor: 'text',
  input: 'text-run',
  placesOnClick: false,
  needsTextLayer: true,
  properties: [],
  onTextRun(ctx) {
    ctx.notify(EDIT_TEXT_UNAVAILABLE);
  },
};

export default editText;
