import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Add text (F2, EDITOR-V2-PLAN §7.2): a v7 TEXT BOX (`textbox` markup) --
 * several lines, wrapped at the box's width, aligned, with its own line
 * spacing; moved and sized by its eight handles, duplicated, deleted. The
 * click is the box's top-left corner; the editor opens there (Enter saves,
 * Shift+Enter is a new line, Esc cancels, a paste is plain text). Only on a
 * server with the document editor: without it Add text is the W2 run.
 */
const DEFAULT_WIDTH_PT = 200;

const textbox: ToolDefinition = {
  id: 'textbox',
  label: 'Add text',
  glyph: 'T',
  hint: 'Click the page to add a text box (Enter saves, Shift+Enter: new line)',
  shortcut: 'T',
  group: 'text',
  order: 10,
  menu: 'text',
  cursor: 'text',
  input: 'click',
  placesOnClick: true,
  requiresEditor: true,
  properties: ['font', 'style', 'size', 'color', 'textbox'],
  defaultColor: 'black',
  onPageClick(ctx, pt) {
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    const pageWidth = ctx.pageSize?.width ?? spot.x + DEFAULT_WIDTH_PT;
    const width = Math.max(24, Math.min(DEFAULT_WIDTH_PT, pageWidth - spot.x - 4));
    ctx.openTextbox({
      id: null,
      page: ctx.page,
      x_pt: spot.x,
      y_pt: spot.y,
      w_pt: width,
      value: '',
      font: ctx.props.boxFont,
      size_pt: Math.min(72, Math.max(6, ctx.props.size)),
      color: ctx.props.color,
      bold: ctx.props.bold,
      italic: ctx.props.italic,
      align: ctx.props.v7.align,
      line_height: ctx.props.v7.lineHeight,
    });
  },
};

export default textbox;
