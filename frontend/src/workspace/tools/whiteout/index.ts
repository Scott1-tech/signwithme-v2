import { WHITEOUT_NOTICE } from '../../../lib/annotationKinds/whiteout';
import { whiteoutTool } from '../drawing';

/**
 * Whiteout (W, W5): drag a box -- an opaque white box over the page (Shift: a
 * square). It HIDES what is under it on the printed page; it does not remove
 * it from the file (`WHITEOUT_NOTICE`, said here and once on the first use).
 *
 * On screen the box has a thin dashed grey edge (white on white paper would
 * be invisible to whoever placed it); the PDF gets the plain white box. A
 * signature placed where a whiteout is prints ON TOP of it (server z-order:
 * whiteout → signatures → the rest), and the preview draws it the same way.
 *
 * No colour, no stroke, no size: the PropertiesBar greys them out.
 */
export default whiteoutTool({
  label: 'Whiteout',
  glyph: '◻',
  hint: `Drag to cover part of the page with an opaque white box (Shift: a square). ${WHITEOUT_NOTICE}`,
  shortcut: 'W',
  group: 'whiteout',
  order: 10,
  // F2: «Whiteout ▾ Redact» -- a split button once Redact is there (with the
  // document editor); alone it stays a plain button.
  menu: 'whiteout',
});
