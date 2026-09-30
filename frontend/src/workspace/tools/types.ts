import type { FC } from 'react';
import type { InlineEditor, NoteEditor } from '../../components/AnnotationLayer';
import type { AnnotationStore } from '../../hooks/useAnnotations';
import type { EditorFontFamilyId } from '../../api/document';
import type { AnnotationColor, AnnotationFont } from '../../lib/annotations';
import type { PageSizePt } from '../../lib/layout';
import type { BBox } from '../../types/field';
import type { SelectionApi } from '../state/useSelection';

/**
 * The tool registry's contract (plan §2.5, W2).
 *
 * A tool is ONE folder, `workspace/tools/<id>/index.ts`, whose default export
 * is a `ToolDefinition`. `registry.ts` finds every such folder with
 * `import.meta.glob`, so adding a tool never edits a shared file: the toolbar,
 * the one-letter shortcut, the cursor, the slot layer stepping aside and the
 * page-click dispatch all read the definition.
 *
 * `ToolId` already names every tool the plan has (W3-W5), so a later phase
 * only adds its folder; the registry lists whatever folders exist today.
 */

export type ToolId =
  | 'select'
  | 'text'
  | 'date'
  | 'check'
  | 'cross'
  | 'signature'
  | 'initials'
  | 'highlight'
  | 'underline'
  | 'strikeout'
  | 'ink'
  | 'rect'
  | 'ellipse'
  | 'line'
  | 'arrow'
  | 'note'
  | 'image'
  | 'whiteout'
  | 'edit-text'
  // F2 (EDITOR-V2-PLAN §7.2): only on a server with the document editor (B2).
  | 'textbox'
  | 'links'
  | 'square'
  | 'stamp'
  | 'image-remove'
  | 'redact'
  | 'highlight-ink'
  // F2b (EDITOR-V2-PLAN §7.4): interactive form fields (Forms ▾, `workspace/forms/`).
  | 'field-text'
  | 'field-multiline'
  | 'field-dropdown'
  | 'field-radio'
  | 'field-checkbox'
  | 'field-signature'
  | 'field-detect';

/** How a tool takes its input from the page. */
export type ToolInput = 'none' | 'click' | 'drag' | 'text-selection' | 'text-run';

/**
 * F1a (EDITOR-V2-PLAN §7 «Toolbar»): the Sejda menus, left to right:
 *
 *   Select | Text ▼ | (Links, F2) | Forms ▼ | Images ▼ | Sign ▼ | Whiteout |
 *   Annotate ▼ | Shapes ▼ | Undo | Redo
 *
 * A tool that declares a `menu` shares ONE split button on the toolbar
 * (`toolgroup-<menu>`): the main half shows (and arms) the member used last,
 * its ▾ opens the list. `tool-<id>` stays on every member, inside the list.
 * Members of one menu must be neighbours in the registry order -- which they
 * are, because a tool's `group` IS its menu (see `ToolGroup`).
 *
 * `whiteout` is a menu name already (spec: «Whiteout W (▾ Redact)»), but no
 * tool declares it yet: until Redact exists Whiteout is a plain button. A
 * menu nobody declares is simply not drawn -- never an empty or dead button.
 */
export type ToolMenu = 'text' | 'forms' | 'images' | 'sign' | 'whiteout' | 'annotate' | 'shapes';

/**
 * Toolbar groups, drawn in this order (see `GROUP_ORDER` in `registry.ts`):
 * Select alone, then one group per menu. A tool's group is where it stands;
 * its `menu` (usually the same name) whether it shares a drop-down there.
 */
export type ToolGroup = 'select' | ToolMenu | 'links';

/** What the PropertiesBar offers while this tool is armed (`style` = bold / italic). */
export type ToolProperty = 'color' | 'stroke' | 'fill' | 'font' | 'style' | 'size' | 'date' | 'v7' | 'textbox';

/** A point in PDF points, top-left origin (the space every annotation speaks). */
export interface Point {
  x: number;
  y: number;
}

/** A rectangle in PDF points, top-left origin (W3's text selections). */
export interface RectPt {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * One run of the pdf.js text layer, in unrotated PDF points (plan §3) -- or,
 * for a `text-run` tool, one merged LINE of them. Defined with the geometry
 * it comes from, in `lib/textRuns.ts`.
 */
import type { TextRun } from '../../lib/textRuns';

export type { TextRun };

/**
 * What the next annotation is placed with. One value per property, shared by
 * every tool that has it -- except the colour, which is per tool (a highlight
 * starts yellow, a text run black).
 */
export interface ToolProps {
  font: AnnotationFont;
  /** v6: the face of the next run / date. */
  bold: boolean;
  italic: boolean;
  /** A run of text / a date (6…48 pt). */
  size: number;
  /** A tick / a cross (6…72 pt): 11 pt would be a speck. */
  markSize: number;
  /** ISO `yyyy-mm-dd`, what the Date tool writes. */
  date: string;
  /** The armed tool's colour (resolved from the per-tool map). */
  color: AnnotationColor;
  /** W4: stroke width of the next ink / shape / line (0.5…12 pt). */
  stroke: number;
  /** W4: the next rect / ellipse gets a translucent fill. */
  fill: boolean;
  /** F2 (markup v7): what the next shape / line / ink / image / text box is drawn with. */
  v7: V7Props;
  /** F2: the stamp the Stamp tool places (set in «New Stamp»). */
  stamp: StampSpec;
  /** F2: the text box's family (any of `GET /api/fonts`) -- `font` stays the legacy run's. */
  boxFont: EditorFontFamilyId;
}

/** F2: the v7 look of the next drawing (EDITOR-V2-PLAN §3 «Markup», `shape2` / `ink2` / `image2`). */
export interface V7Props {
  dash: 'solid' | 'dash' | 'dot';
  /** null = no fill. */
  fillColor: AnnotationColor | null;
  opacity: number;
  heads: 'none' | 'start' | 'end' | 'both';
  align: 'left' | 'center' | 'right' | 'justify';
  lineHeight: number;
}

/** F2: a stamp as «New Stamp» made it. `date_text` is typed, never «now» (2026-09-25). */
export interface StampSpec {
  preset: string | null;
  text: string | null;
  date_text: string | null;
  color: AnnotationColor;
  border: boolean;
}

/** The signing gate, as a tool sees it. */
export interface ToolGate {
  blocked: boolean;
  reason: string | null;
}

/** Everything a tool may touch when the page is clicked. */
export interface ToolContext {
  page: number;
  scale: number;
  pageSize: PageSizePt | null;
  props: ToolProps;
  annotations: AnnotationStore;
  selection: SelectionApi;
  /**
   * The signature chooser; `openFree` places a free signature at `bbox`,
   * `openInitials` (W5) an initial centred on `at` (its box is fitted to the
   * chosen image, inside 60 × 24 pt).
   */
  chooser: { openFree(page: number, bbox: BBox): void; openInitials(page: number, at: Point): void };
  gate: ToolGate;
  /** The editor's note line (`editor-note`); `null` clears it. */
  notify(note: string | null): void;
  /** Open (or close) the inline text box. */
  openInline(editor: InlineEditor | null): void;
  /** W4: open (or close) the sticky-note editor. */
  openNote(editor: NoteEditor | null): void;
  /**
   * W4: ask for a picture (the file dialog), upload it, and place it with its
   * top-left at `at` on this page. Everything after the dialog is async and
   * reports through `notify`.
   */
  pickImage(at: Point): void;
  /** A fresh annotation id that is not taken yet. */
  newId(): string;
  /** F2: the server has the document editor (markup v7, ops). */
  editor: boolean;
  /** F2: open (or close) the text box editor. */
  openTextbox(editor: import('../../components/V7Drawings').TextboxEditorState | null): void;
  /** F2: a real redaction of this box (a `redact` op of the edit session). */
  redact(rect: RectPt): void;
  /** F2: a new link over this box (opens the link dialog). */
  openLink(rect: RectPt): void;
}

/** A pointer gesture in progress (W4's drag tools). */
export interface Gesture {
  move(pt: Point, ev: PointerEvent): void;
  end(pt: Point): void;
  cancel(): void;
  Preview: FC<{ scale: number }>;
}

export interface ToolDefinition {
  id: ToolId;
  /** On the button (hidden below 1024 px and in the vertical/collapsed panel). */
  label: string;
  /** One character, drawn in every orientation. */
  glyph: string;
  /** What the click does, in words -- title and accessible name. */
  hint: string;
  /** One letter, no modifier (`aria-keyshortcuts`); off inside inputs. */
  shortcut?: string;
  group: ToolGroup;
  /** Order inside the group. */
  order: number;
  /** CSS cursor over an interactive page. */
  cursor: string;
  input: ToolInput;
  /** A click on the page does something (`pdf-page--interactive`). */
  placesOnClick: boolean;
  needsTextLayer?: boolean;
  /**
   * Writes to the server (a signature, an initial, an uploaded image): off
   * while signing is blocked, with the reason, and not in the undo history.
   * A tool WITHOUT it that places something makes the slot layer step aside
   * (`workspace--placing`) so its click lands on the page, not on a slot.
   */
  serverWrite?: boolean;
  /**
   * The slot layer steps aside for this tool even though it writes to the
   * server (W4 image: the upload is a server write, but what it PLACES is a
   * local annotation, so a click over a slot must still land on the page).
   */
  slotsStepAside?: boolean;
  /** W4: this tool lives in a drop-down on the toolbar (see `ToolMenu`). */
  menu?: ToolMenu;
  /**
   * F1a: the light-grey heading this tool sits under inside its drop-down
   * («Text markup», «Freehand», …). A heading is drawn where it changes.
   */
  menuSection?: string;
  properties: ToolProperty[];
  /**
   * F2: the tool needs the server's document editor (B2 markup v7 / ops):
   * it is not on the toolbar at all without it (no dead button).
   */
  requiresEditor?: boolean;
  /** F2: the legacy tool this one replaces once the editor is there (Add text → text box). */
  hiddenWithEditor?: boolean;
  /** Default colour for this tool (`properties` includes `'color'`). */
  defaultColor?: AnnotationColor;
  onPageClick?(ctx: ToolContext, pt: Point, suggested: BBox): void;
  /**
   * A drag tool (`input: 'drag'`, W4): the press, in PDF points (already
   * clamped to the page). `null` = nothing to draw (the list is full; the
   * tool has said why through `ctx.notify`). The returned gesture commits ONE
   * step on `end` and nothing on `cancel` (Esc).
   */
  beginGesture?(ctx: ToolContext, pt: Point): Gesture | null;
  onTextSelection?(ctx: ToolContext, rects: RectPt[]): void;
  onTextRun?(ctx: ToolContext, run: TextRun): void;
}
