import { clampBoxOn } from '../../../lib/annotationKinds/shared';
import { sanitizeStamp, stampBox } from '../../../lib/annotationKinds/v7';
import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Stamp (F2, Images ▾): places the stamp «New Stamp» made (a preset word or
 * a short text, its colour, a border, and a date line ONLY if one was typed --
 * never «now», 2026-09-25) with its top-left at the click. A v7 `stamp`.
 */
const stamp: ToolDefinition = {
  id: 'stamp',
  label: 'Stamp',
  glyph: '⎘',
  hint: 'Click the page to place the stamp (make a new one with «New Stamp…»)',
  group: 'images',
  order: 20,
  menu: 'images',
  cursor: 'copy',
  input: 'click',
  placesOnClick: true,
  requiresEditor: true,
  properties: [],
  onPageClick(ctx, pt) {
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    const spec = ctx.props.stamp;
    const size = stampBox(spec);
    const box = clampBoxOn({ x: spot.x, y: spot.y, w: size.w, h: size.h }, ctx.pageSize);
    const id = ctx.newId();
    const made = sanitizeStamp({
      id,
      page: ctx.page,
      source: {
        x_pt: box.x,
        y_pt: box.y,
        w_pt: box.w,
        h_pt: box.h,
        preset: spec.text ? null : spec.preset,
        text: spec.text,
        color: spec.color,
        border: spec.border,
        date_text: spec.date_text,
      },
    });
    if (!made) {
      ctx.notify('This stamp has no label: make one with «New Stamp…» in Images ▾.');
      return;
    }
    ctx.annotations.add(made);
    ctx.selection.selectAnnotation(id);
  },
};

export default stamp;
