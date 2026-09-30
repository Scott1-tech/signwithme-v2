import { MAX_ANNOTATIONS, clampPoint, snapPt } from '../../lib/annotations';
import type { Point, ToolContext } from './types';

/**
 * The common first half of every "click places an annotation" tool (it was
 * the top of `DocumentEditor.handleCanvasClick`): refuse once, out loud, when
 * the list is full; snap the point to the half-point grid and pull it inside
 * the page; let go of whatever was selected and clear the note.
 *
 * Returns the spot, or `null` when nothing may be placed. Not a tool folder
 * (the registry only globs `./<id>/index.ts`), so it is never on the toolbar.
 */
export function placementSpot(ctx: ToolContext, pt: Point): Point | null {
  if (ctx.annotations.items.length >= MAX_ANNOTATIONS) {
    ctx.notify(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
    return null;
  }
  const spot = clampPoint({ x: snapPt(pt.x), y: snapPt(pt.y) }, ctx.pageSize);
  ctx.selection.selectAnnotation(null);
  ctx.notify(null);
  return spot;
}
