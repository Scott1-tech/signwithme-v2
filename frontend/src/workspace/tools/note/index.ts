import { clampNoteAnchor } from '../../../lib/annotationKinds/note';
import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Sticky note (N): a click puts the note's icon there (its top-left corner)
 * and opens the note's text box -- several lines, Ctrl+Enter or a click
 * elsewhere saves, Esc cancels. In the PDF it is a real `/Text` annotation
 * (icon + popup, contract v5), default yellow. The text is checked against
 * the standard fonts' character set BEFORE it is kept (the server's rule).
 */
const note: ToolDefinition = {
  id: 'note',
  label: 'Note',
  glyph: '🗒',
  hint: 'Click the page to add a sticky note',
  shortcut: 'N',
  group: 'annotate',
  order: 50,
  menu: 'annotate',
  menuSection: 'Comment',
  cursor: 'copy',
  input: 'click',
  placesOnClick: true,
  properties: ['color'],
  defaultColor: 'yellow',
  onPageClick(ctx, pt) {
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    const at = clampNoteAnchor(spot, ctx.pageSize);
    ctx.openNote({ id: null, page: ctx.page, x_pt: at.x, y_pt: at.y, color: ctx.props.color, value: '' });
  },
};

export default note;
