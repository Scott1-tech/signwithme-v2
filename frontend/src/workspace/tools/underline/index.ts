import { markupTool } from '../markup';

/** Underline (U): select text on the page; a bar along the bottom of each line. See `../markup.ts`. */
export default markupTool('underline', {
  label: 'Underline',
  glyph: 'U',
  hint: 'Select text on the page to underline it',
  shortcut: 'U',
  order: 20,
});
