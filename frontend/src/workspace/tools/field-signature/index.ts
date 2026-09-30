import { fieldTool } from '../../forms/fieldTools';

/** F2b: Forms ▾ → «Signature box» (a `field.add` of type `signature` in the edit session). */
export default fieldTool({
  id: 'field-signature',
  type: 'signature',
  label: 'Signature box',
  glyph: '✍',
  hint: 'Drag on the page to draw an empty signature field',
  order: 105,
});
