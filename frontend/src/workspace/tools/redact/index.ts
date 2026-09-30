import { redactTool } from '../drawing';

/**
 * Redact (F2, «Whiteout ▾ Redact»): drag a box -- what is under it is
 * PERMANENTLY REMOVED from the file (text, image pixels, line art), not just
 * covered: a `redact` op of the edit session, black where it was. Whiteout
 * only hides; Redact deletes.
 */
export default redactTool({
  label: 'Redact',
  glyph: '▇',
  hint: 'Drag a box: permanently removes the content under it from the file (Whiteout only hides it)',
  group: 'whiteout',
  order: 20,
  menu: 'whiteout',
});
