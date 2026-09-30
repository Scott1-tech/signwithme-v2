import { fieldTool } from '../../forms/fieldTools';

/** F2b: Forms ▾ → «Multiline» (a `field.add` of type `multiline` in the edit session). */
export default fieldTool({
  id: 'field-multiline',
  type: 'multiline',
  label: 'Multiline',
  glyph: '▤',
  hint: 'Drag on the page to draw a text field that holds several lines',
  order: 101,
});
