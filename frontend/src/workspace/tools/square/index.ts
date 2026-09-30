import { makeMarkAnnotation } from '../../../lib/annotations';
import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Filled square (Q, F2, Forms ▾): a v7 `square_mark` CENTRED on the click
 * (how one aims at a box on a form), `size_pt` across, filled in the colour.
 */
const square: ToolDefinition = {
  id: 'square',
  label: 'Filled square',
  glyph: '■',
  hint: 'Click the page to drop a filled square, centred there',
  shortcut: 'Q',
  group: 'forms',
  order: 30,
  menu: 'forms',
  cursor: 'crosshair',
  input: 'click',
  placesOnClick: true,
  requiresEditor: true,
  properties: ['size', 'color'],
  defaultColor: 'black',
  onPageClick(ctx, pt) {
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    const id = ctx.newId();
    ctx.annotations.add(
      makeMarkAnnotation('square_mark', {
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

export default square;
