import type { AnnotationFont } from '../../../lib/annotations';
import type { TextRun } from '../../../lib/textRuns';
import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/** Width of a new text box added by a click outside the document's text. */
const NEW_TEXT_WIDTH_PT = 200;

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
  // One tool for both, like Sejda: a click on a line of the document edits
  // that line (the block layer takes the click); a click anywhere else -- empty
  // paper, a ruled line, a "____" blank -- adds NEW text there, as a text box.
  placesOnClick: true,
  needsTextLayer: true,
  properties: ['font', 'style', 'size', 'color', 'textbox'],
  defaultColor: 'black',
  onTextRun(ctx) {
    ctx.notify(EDIT_TEXT_UNAVAILABLE);
  },
  onPageClick(ctx, pt) {
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    const pageWidth = ctx.pageSize?.width ?? spot.x + NEW_TEXT_WIDTH_PT;
    const size = Math.min(72, Math.max(6, ctx.props.size));
    ctx.openTextbox({
      id: null,
      page: ctx.page,
      x_pt: spot.x,
      // The click is where the text should SIT (on the line), so the box
      // starts one line above it.
      y_pt: Math.max(0, spot.y - size),
      w_pt: Math.max(24, Math.min(NEW_TEXT_WIDTH_PT, pageWidth - spot.x - 4)),
      value: '',
      font: ctx.props.boxFont,
      size_pt: size,
      color: ctx.props.color,
      bold: ctx.props.bold,
      italic: ctx.props.italic,
      align: ctx.props.v7.align,
      line_height: ctx.props.v7.lineHeight,
    });
  },
};

export default editText;
