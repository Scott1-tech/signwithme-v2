import { redactTool } from '../drawing';

/**
 * Redact (F2, «Whiteout ▾ Redact»): drag a box -- what is under it is
 * PERMANENTLY REMOVED from the file (text, image pixels, line art), not just
 * covered: a `redact` op of the edit session, black where it was. Whiteout
 * only hides; Redact deletes.
 */
export default redactTool({
  label: 'Erase',
  glyph: '⌫',
  hint: 'Drag a box around a line, text or picture: it is removed from the PDF itself and nothing is painted over it',
  group: 'whiteout',
  order: 20,
  menu: 'whiteout',
});
