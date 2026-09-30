import { makeMarkAnnotation } from '../../../lib/annotations';
import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Check: one click drops a tick CENTRED on the point -- the click lands in the
 * middle of the tick, which is how one aims at a tick-box.
 */
const check: ToolDefinition = {
  id: 'check',
  label: 'Check',
  glyph: '✔',
  hint: 'Click the page to drop a tick, centred there',
  shortcut: 'K',
  group: 'forms',
  order: 10,
  menu: 'forms',
  cursor: 'crosshair',
  input: 'click',
  placesOnClick: true,
  properties: ['size', 'color'],
  defaultColor: 'black',
  onPageClick(ctx, pt) {
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    const id = ctx.newId();
    ctx.annotations.add(
      makeMarkAnnotation('check', {
        id,
        page: ctx.page,
        x_pt: spot.x,
        y_pt: spot.y,
        size_pt: ctx.props.markSize,
        color: ctx.props.color,
      }),
    );
    ctx.selection.selectAnnotation(id);
  },
};

export default check;
