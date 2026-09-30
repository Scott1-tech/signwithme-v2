import { formatForContract } from '../../../components/DatePicker';
import { makeTextualAnnotation } from '../../../lib/annotations';
import { CHOOSE_DATE_FIRST, focusDateInput } from '../../../lib/signing';
import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Date: no inline box -- the date was chosen on the toolbar (`ann-date`)
 * before the click, so the click is the whole gesture. It stays a normal run
 * afterwards and can be retyped by double-clicking it.
 *
 * There is no default date (see `lib/signing.ts`): with the toolbar's box
 * still empty the click places nothing, says so, and puts the focus there.
 */
const date: ToolDefinition = {
  id: 'date',
  label: 'Date',
  glyph: '📅',
  hint: 'Click the page to write the date beside it',
  shortcut: 'D',
  group: 'text',
  order: 30,
  menu: 'text',
  cursor: 'text',
  input: 'click',
  placesOnClick: true,
  properties: ['font', 'style', 'size', 'date', 'color'],
  defaultColor: 'black',
  onPageClick(ctx, pt) {
    if (!ctx.props.date) {
      ctx.notify(`${CHOOSE_DATE_FIRST}: pick it in the toolbar’s Date box, then click the page.`);
      focusDateInput('ann-date');
      return;
    }
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    const id = ctx.newId();
    ctx.annotations.add(
      makeTextualAnnotation('date', {
        id,
        page: ctx.page,
        x_pt: spot.x,
        y_pt: spot.y,
        text: formatForContract(ctx.props.date),
        font: ctx.props.font,
        size_pt: ctx.props.size,
        color: ctx.props.color,
        bold: ctx.props.bold,
        italic: ctx.props.italic,
      }),
    );
    ctx.selection.selectAnnotation(id);
  },
};

export default date;
