import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Text: the click is the left end of the new run's BASELINE; an inline box
 * opens there and the run is committed on Enter / blur (`commitInline`). An
 * empty box is a cancel.
 */
const text: ToolDefinition = {
  id: 'text',
  label: 'Add text',
  glyph: 'T',
  hint: 'Click the page and type',
  shortcut: 'T',
  group: 'text',
  order: 10,
  menu: 'text',
  cursor: 'text',
  input: 'click',
  placesOnClick: true,
  properties: ['font', 'style', 'size', 'color'],
  defaultColor: 'black',
  // F2: with the document editor, Add text is the v7 text box (`textbox/`).
  hiddenWithEditor: true,
  onPageClick(ctx, pt) {
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    ctx.openInline({
      id: null,
      kind: 'text',
      page: ctx.page,
      x_pt: spot.x,
      y_pt: spot.y,
      font: ctx.props.font,
      bold: ctx.props.bold,
      italic: ctx.props.italic,
      size_pt: ctx.props.size,
      color: ctx.props.color,
      value: '',
    });
  },
};

export default text;
