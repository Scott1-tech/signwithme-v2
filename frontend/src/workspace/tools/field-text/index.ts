import { fieldTool } from '../../forms/fieldTools';

/** F2b: Forms ▾ → «Text field» (a `field.add` of type `text` in the edit session). */
export default fieldTool({
  id: 'field-text',
  type: 'text',
  label: 'Text field',
  glyph: '▭',
  hint: 'Drag on the page to draw a one-line text field (a click makes one of the usual size)',
  order: 100,
});
