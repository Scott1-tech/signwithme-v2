import type { ToolDefinition } from '../types';

/**
 * Signature: the one placement that is NOT an annotation. The click opens the
 * chooser for a free placement there, and the row is created server-side the
 * moment an image is confirmed (`field_id: null`) -- so it is a `serverWrite`
 * tool: off while signing is blocked, and not in the undo history.
 *
 * On a slot, the slot's own press opens the chooser for THAT slot (the slot
 * layer stays live for this tool); this handler only sees empty paper.
 */
const signature: ToolDefinition = {
  id: 'signature',
  label: 'Signature',
  glyph: '✍',
  hint: 'Click the page to place a signature there',
  shortcut: 'S',
  group: 'sign',
  order: 10,
  menu: 'sign',
  cursor: 'crosshair',
  input: 'click',
  placesOnClick: true,
  serverWrite: true,
  properties: [],
  onPageClick(ctx, _pt, suggested) {
    if (ctx.gate.blocked) return;
    ctx.selection.selectAnnotation(null);
    ctx.notify(null);
    ctx.chooser.openFree(ctx.page, suggested);
  },
};

export default signature;
