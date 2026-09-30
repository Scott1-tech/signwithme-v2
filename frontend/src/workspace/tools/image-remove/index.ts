import type { ToolDefinition } from '../types';

/**
 * Delete original image (F2, Images ▾): a frame over every picture that is
 * PART OF the PDF (`GET …/pages/{uid}/images`); a click removes it from the
 * file for real -- an `image.remove` op of the edit session (review re-opens
 * on Save). Nothing to do with the image annotations (those are Delete).
 * The frames are the workspace's `PageImageLayer`; the tool itself only arms it.
 */
const imageRemove: ToolDefinition = {
  id: 'image-remove',
  label: 'Delete original image',
  glyph: '⌫',
  hint: 'Point at a picture of the document and click to remove it from the file',
  group: 'images',
  order: 30,
  menu: 'images',
  cursor: 'pointer',
  input: 'none',
  placesOnClick: false,
  requiresEditor: true,
  properties: [],
};

export default imageRemove;
