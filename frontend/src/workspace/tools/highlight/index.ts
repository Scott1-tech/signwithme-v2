import { markupTool } from '../markup';

/** Highlight (H): select text on the page; it is marked yellow (alpha 0.35). See `../markup.ts`. */
export default markupTool('highlight', {
  label: 'Highlight',
  glyph: 'H',
  hint: 'Select text on the page to highlight it',
  shortcut: 'H',
  order: 10,
});
