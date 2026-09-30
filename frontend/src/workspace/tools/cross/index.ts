import { makeMarkAnnotation } from '../../../lib/annotations';
import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/** Cross: the tick's twin -- one click, centred on the point. */
const cross: ToolDefinition = {
  id: 'cross',
  label: 'Cross',
  glyph: '✖',
  hint: 'Click the page to drop a cross, centred there',
  shortcut: 'X',
  group: 'forms',
  order: 20,
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
      makeMarkAnnotation('cross', {
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

export default cross;
