import { markupTool } from '../markup';

/**
 * Strikeout (C, "cross out"): select text on the page; a bar through the
 * middle of each line. See `../markup.ts`.
 *
 * Why C: `S` is Signature, `X` the Cross mark and `K` the Check; the plan's
 * letter list (§2.6: V T D K X S I H U P R O L A N M W E) leaves no letter
 * for strikeout, and C -- "cross out", the English verb for it -- is free of
 * every planned tool and of every modifier shortcut (the letters are off
 * with Ctrl / Alt / Meta, so Ctrl+C still copies).
 */
export default markupTool('strikeout', {
  label: 'Strikeout',
  glyph: 'S',
  hint: 'Select text on the page to strike it out',
  shortcut: 'C',
  order: 30,
});
