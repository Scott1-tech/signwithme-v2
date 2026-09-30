import { placementSpot } from '../shared';
import type { ToolDefinition } from '../types';

/**
 * Picture / stamp (M): a click asks for a PNG or JPEG (≤ 2 MB, checked here
 * first), uploads it ONCE (`POST .../annotation-assets` -- a server write, so
 * the tool is off with the reason on a contract nobody approved yet: the
 * server answers 409 there), and places it with its top-left at the click, at
 * its natural size scaled down to fit (aspect kept). The annotation that
 * points at the asset is local and undoable; the upload is not.
 *
 * `slotsStepAside`: what it places is a local annotation, so -- unlike the
 * Signature tool -- a click over a slot box lands on the page.
 */
const image: ToolDefinition = {
  id: 'image',
  label: 'Image',
  glyph: '🖼',
  hint: 'Click the page to place a picture or stamp (PNG / JPEG, up to 2 MB)',
  shortcut: 'M',
  group: 'images',
  order: 10,
  menu: 'images',
  cursor: 'copy',
  input: 'click',
  placesOnClick: true,
  serverWrite: true,
  slotsStepAside: true,
  properties: [],
  onPageClick(ctx, pt) {
    if (ctx.gate.blocked) {
      ctx.notify(ctx.gate.reason ?? 'Images can only be added to a contract that can be signed.');
      return;
    }
    const spot = placementSpot(ctx, pt);
    if (!spot) return;
    ctx.pickImage(spot);
  },
};

export default image;
