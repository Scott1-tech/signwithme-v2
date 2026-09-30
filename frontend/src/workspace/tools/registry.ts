import type { ToolDefinition, ToolGroup, ToolId, ToolMenu } from './types';

/**
 * Every tool of the workspace, found by folder (plan §2.5).
 *
 * `import.meta.glob` is resolved by Vite at build time: each
 * `./<id>/index.ts` is bundled and its default export lands here. Adding a
 * tool is therefore adding a folder -- nothing in this file, the toolbar or
 * the workspace is edited. The list is sorted by group, then by `order`.
 *
 * A module that exports something that is not a definition, or a second
 * definition with an id already taken, is a programming error and is thrown
 * at load, not discovered by a user clicking a dead button.
 */

/** F1a: the spec's order (EDITOR-V2-PLAN §7); «Links» joins between Text and Forms in F2. */
export const GROUP_ORDER: readonly ToolGroup[] = [
  'select',
  'text',
  'links',
  'forms',
  'images',
  'sign',
  'whiteout',
  'annotate',
  'shapes',
];

const modules = import.meta.glob<{ default: ToolDefinition }>('./*/index.ts', { eager: true });

function collect(): ToolDefinition[] {
  const seen = new Set<ToolId>();
  const list: ToolDefinition[] = [];
  Object.entries(modules).forEach(([path, module]) => {
    const tool = module.default;
    if (!tool || typeof tool.id !== 'string') {
      throw new Error(`${path} does not default-export a ToolDefinition`);
    }
    if (seen.has(tool.id)) throw new Error(`Two tools claim the id "${tool.id}" (${path})`);
    seen.add(tool.id);
    list.push(tool);
  });
  return list.sort(
    (a, b) =>
      GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group) ||
      a.order - b.order ||
      a.id.localeCompare(b.id),
  );
}

/** All registered tools, in toolbar order. */
export const TOOLS: readonly ToolDefinition[] = collect();

/**
 * F2: the tools on the toolbar for this server -- with the document editor
 * (B2) the v7 / op tools join and Add text becomes the text box; without it
 * the toolbar is exactly F1a's.
 */
export function toolsFor(editor: boolean): readonly ToolDefinition[] {
  return TOOLS.filter((tool) => (editor ? !tool.hiddenWithEditor : !tool.requiresEditor));
}

const BY_ID = new Map<ToolId, ToolDefinition>(TOOLS.map((tool) => [tool.id, tool]));

/** The definition of a registered tool, or `undefined` for one not built yet. */
export const getTool = (id: ToolId): ToolDefinition | undefined => BY_ID.get(id);

/** Always a definition: an unknown id falls back to Select. */
export const toolOrSelect = (id: ToolId): ToolDefinition =>
  BY_ID.get(id) ?? (BY_ID.get('select') as ToolDefinition);

export const isRegisteredTool = (value: unknown): value is ToolId =>
  typeof value === 'string' && BY_ID.has(value as ToolId);

/** The tool a one-letter shortcut arms (case-insensitive), if any. */
export function toolForShortcut(key: string, tools: readonly ToolDefinition[] = TOOLS): ToolDefinition | undefined {
  const letter = key.length === 1 ? key.toUpperCase() : '';
  if (!letter) return undefined;
  return tools.find((tool) => tool.shortcut?.toUpperCase() === letter);
}

/**
 * A tool whose click places something LOCAL (an annotation): the slot layer
 * steps aside for it (`workspace--placing`), so a click on a slot places the
 * annotation there. Select and the server-writing tools keep the slots live.
 * A tool that works on the page's own text (`needsTextLayer`, WE) steps them
 * aside too: a line under a slot box must still be reachable.
 */
export const slotsStepAsideFor = (tool: ToolDefinition): boolean =>
  tool.id !== 'select' &&
  (tool.placesOnClick || Boolean(tool.needsTextLayer) || tool.input === 'drag') &&
  (!tool.serverWrite || Boolean(tool.slotsStepAside));

/** A tool that draws by dragging on the page (W4: `ToolCaptureLayer`). */
export const drawsByDragging = (tool: ToolDefinition): boolean =>
  tool.input === 'drag' && typeof tool.beginGesture === 'function';

/** The drop-down buttons of the toolbar (W4, F1a: the spec's menus), in words. */
export const TOOL_MENUS: Record<ToolMenu, { label: string; hint: string }> = {
  text: { label: 'Text', hint: 'Add new text, edit the document’s own text, or write a date' },
  forms: { label: 'Forms', hint: 'Tick or cross a box on the form' },
  images: { label: 'Images', hint: 'Place a picture or a stamp' },
  sign: { label: 'Sign', hint: 'Place a signature or your initials' },
  whiteout: {
    label: 'Whiteout',
    hint: 'Cover part of the page with an opaque white box, or redact it for real (Redact)',
  },
  annotate: {
    label: 'Annotate',
    hint: 'Highlight, underline or strike out the page’s text, draw freehand, or add a sticky note',
  },
  shapes: { label: 'Shapes', hint: 'Draw a rectangle, an ellipse, a line or an arrow' },
};
