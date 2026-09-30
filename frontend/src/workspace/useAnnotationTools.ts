import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react';
import type { InlineEditor, NoteEditor } from '../components/AnnotationLayer';
import { assetsApi, describeAssetError } from '../api/assets';
import { useAnnotationEdit } from '../hooks/useAnnotationEdit';
import { useAnnotations } from '../hooks/useAnnotations';
import {
  MAX_ANNOTATIONS,
  MAX_MARKUP_RECTS,
  MAX_TEXT_LENGTH,
  NUDGE_STEP,
  NUDGE_STEP_LARGE,
  clampAnchor,
  moveAnnotation,
  clampAnnotationsToPages,
  clampSize,
  colorOf,
  groupSelectionOrder,
  isDrawKind,
  isMarkKind,
  isMarkup,
  isMarkupKind,
  isNote,
  isShape,
  isSized,
  isStroked,
  isTextual,
  makeAnnotationId,
  makeMarkupAnnotation,
  makeTextualAnnotation,
  sameRects,
  type Annotation,
  type AnnotationColor,
  type AnnotationFont,
  type AnnotationKind,
} from '../lib/annotations';
import { makeImage, placeImageBox } from '../lib/annotationKinds/image';
import { MAX_NOTE_TEXT, NOTE_FONT, makeNote, normalizeNoteText } from '../lib/annotationKinds/note';
import { checkImageFile } from '../lib/imageFile';
import type { SearchHit } from '../hooks/usePdfSearch';
import type { AnnotationStore } from '../hooks/useAnnotations';
import type { PageSizePt } from '../lib/layout';
import { checkPrintable, findUndrawable, normalizeText } from '../lib/textCheck';
import type { BBox } from '../types/field';
import type { TextboxEditorState } from '../components/V7Drawings';
import type { EditorFontFamilyId } from '../api/document';
import {
  clampOpacity,
  makeTextbox,
  restyleAll,
  restyleRuns,
  textboxNeededHeight,
  textboxText,
  type TextboxAnnotation,
} from '../lib/annotationKinds/v7';
import { isFontFamily } from '../lib/fonts';
import type { V7Props } from './tools/types';
import type { DocumentHistory } from './state/useDocumentHistory';
import type { SelectionApi } from './state/useSelection';
import { colorForTool, useToolState } from './state/useToolState';
import { drawsByDragging, getTool, slotsStepAsideFor, toolOrSelect } from './tools/registry';
import type { Point, RectPt, TextRun, ToolContext, ToolGate, ToolId } from './tools/types';

/**
 * Why a note's text cannot be kept, or `null`. A note is a PDF `/Text`
 * annotation: the server checks it against the standard fonts' character set
 * (WinAnsi), line breaks allowed -- so the refusal names the character and
 * says what to do, without offering a font (a note has none).
 */
export function noteRefusal(text: string): string | null {
  if (text.length > MAX_NOTE_TEXT) {
    return `A note holds at most ${MAX_NOTE_TEXT} characters (this one has ${text.length}).`;
  }
  const found = findUndrawable(text.replace(/\n/g, ' '), NOTE_FONT);
  if (!found) return null;
  const hex = found.codePoint.toString(16).toUpperCase().padStart(4, '0');
  const shown = /[\p{Cc}\p{Cf}\p{Mn}\p{Me}]/u.test(found.char) ? '' : ` "${found.char}"`;
  return (
    `A note cannot carry${shown} (U+${hex}) at position ${found.index} — notes use the PDF’s ` +
    'standard character set (Western European text only). Remove or replace that character.'
  );
}

interface Options {
  contractId: string | undefined;
  /** The tool the workspace opens with (plan §2.5: `/sign` → signature, else select). */
  initialTool: ToolId;
  scale: number;
  pageSizeOf: (page: number | null) => PageSizePt | null;
  selection: SelectionApi;
  /** The workspace's one history (annotations are its `annotations` channel). */
  history: DocumentHistory;
  gate: ToolGate;
  /** The Signature tool's click: open the chooser for a free placement there. */
  onSignatureClick: (page: number, suggested: BBox) => void;
  /** W5: the Initials tool's click: open the chooser for initials centred there. */
  onInitialsClick: (page: number, at: Point) => void;
  setNote: (note: string | null) => void;
  /** F1b: false once the annotations live on the server (see `useAnnotations`). */
  persistLocal?: boolean;
  /** F2: the server has the document editor (markup v7 and ops). */
  editor?: boolean;
  /** F2: a Redact drag landed: a `redact` op of the edit session. */
  onRedact?: (page: number, rect: RectPt) => void;
  /** F2: a Links drag landed: the link dialog for a new link there. */
  onOpenLink?: (page: number, rect: RectPt) => void;
  /** F2: the Image tool's click -- true when the caller placed something itself (a gallery pick). */
  onPickImage?: (page: number, at: Point) => boolean;
}

/** F2: the annotation kind a tool describes in the PropertiesBar. */
const TOOL_KIND: Partial<Record<ToolId, AnnotationKind>> = {
  textbox: 'textbox',
  square: 'square_mark',
  stamp: 'stamp',
  'highlight-ink': 'ink',
};

/** The whiteout over an edited line reaches this far past the line's ink box, each side. */
export const WHITEOUT_MARGIN_PT = 0.5;

/**
 * The editor half of the workspace: the annotation list, the armed tool and
 * what the next annotation is placed with, the inline text box, the page
 * click (dispatched to the armed tool's `onPageClick` -- see
 * `workspace/tools/`), the text-layer click (`onTextRun`, WE), and the window
 * keyboard.
 *
 * W2: the per-tool `if` chain that used to be `handleCanvasClick` lives in
 * the tool folders now; Undo/Redo is the workspace's one history, so Ctrl+Z
 * also takes back a slot drag. What is *selected* is the workspace's single
 * selection, so the keys below only act on an annotation when an annotation
 * is what is selected -- with a slot or an issue picked, Delete and the arrow
 * keys do nothing at all.
 *
 * WE: an "edit existing text" is a whiteout + text GROUP. Selecting either
 * member selects both (text first -- the toolbar describes it, the arrows move
 * it); Delete takes the group; the commit of the edit box is ONE step.
 * Before anything is committed, the text is normalised to NFC and checked
 * against the chosen face (`lib/textCheck.ts`) -- a character the PDF cannot
 * print is refused right there, by name, with the fix.
 */
export function useAnnotationTools({
  contractId,
  initialTool,
  scale,
  pageSizeOf,
  selection,
  history,
  gate,
  onSignatureClick,
  onInitialsClick,
  setNote,
  persistLocal = true,
  editor = false,
  onRedact,
  onOpenLink,
  onPickImage,
}: Options) {
  const store = useAnnotations(contractId, history, persistLocal);
  const edit = useAnnotationEdit({ store, scale, pageSizeOf });

  // W5: a stored annotation is pulled onto its page once the page's size is
  // known (`clampAnnotationsToPages`) -- the app's own correction, NOT an
  // undo step (W2b), said once. Every later change runs it again and finds
  // nothing (the same array comes back), so it costs one pass over the list.
  const clampSaid = useRef(false);
  const { items: storedItems, replace: replaceItems } = store;
  useEffect(() => {
    const { items: clamped, moved } = clampAnnotationsToPages(storedItems, pageSizeOf);
    if (moved === 0) return;
    replaceItems(clamped);
    if (clampSaid.current) return;
    clampSaid.current = true;
    setNote(
      `${moved} ${moved === 1 ? 'annotation was' : 'annotations were'} moved to stay on the page ` +
        '(stored for a page of another size, or before the page was measured).',
    );
  }, [storedItems, pageSizeOf, replaceItems, setNote]);
  const toolState = useToolState(initialTool);
  const {
    toolId,
    tool,
    props,
    setToolId,
    setFont,
    setStyle,
    setSize,
    setMarkSize,
    setStroke,
    setFill,
    setDate,
    setColorFor,
    setV7,
    setBoxFont,
    setStamp,
  } = toolState;

  const { annotationId: selectedId, annotationIds: selectedIds, selectAnnotation, selectAnnotations } =
    selection;

  // The inline box is mirrored into a ref because it is committed from a blur,
  // which can run in the same batch as the click that replaces it -- reading
  // the state variable there would read the value of the previous render.
  const [inline, setInline] = useState<InlineEditor | null>(null);
  const inlineRef = useRef<InlineEditor | null>(null);
  const openInline = useCallback((next: InlineEditor | null) => {
    inlineRef.current = next;
    setInline(next);
  }, []);

  // The sticky-note box (W4), mirrored into a ref for the same reason.
  const [noteEditor, setNoteEditor] = useState<NoteEditor | null>(null);
  const noteRef = useRef<NoteEditor | null>(null);
  const openNote = useCallback((next: NoteEditor | null) => {
    noteRef.current = next;
    setNoteEditor(next);
  }, []);

  // F2: the text box editor (mirrored into a ref for the same reason).
  const [boxEditor, setBoxEditor] = useState<TextboxEditorState | null>(null);
  const boxRef = useRef<TextboxEditorState | null>(null);
  const openTextbox = useCallback((next: TextboxEditorState | null) => {
    boxRef.current = next;
    setBoxEditor(next);
  }, []);

  /** Select an annotation -- and, for a group member, the whole group (text first). */
  const selectGroup = useCallback(
    (id: string | null) => {
      if (!id) {
        selectAnnotation(null);
        return;
      }
      const ids = groupSelectionOrder(store.items, id);
      if (ids.length > 1) selectAnnotations(ids);
      else selectAnnotation(id);
    },
    [store.items, selectAnnotation, selectAnnotations],
  );

  const selected = store.get(selectedId);
  /** What the toolbar's boxes currently describe: the open box, the selection, else the armed tool. */
  const activeKind: AnnotationKind = inline
    ? inline.kind
    : noteEditor
      ? 'note'
      : boxEditor
        ? 'textbox'
        : selected
          ? selected.kind
          : TOOL_KIND[toolId]
            ? (TOOL_KIND[toolId] as AnnotationKind)
            : isMarkKind(toolId) || isMarkupKind(toolId) || isDrawKind(toolId) || toolId === 'whiteout'
              ? (toolId as AnnotationKind)
              : toolId === 'date'
                ? 'date'
                : // F2: with the editor, «text» is the text box (Add text).
                  editor
                  ? 'textbox'
                  : 'text';
  const armedSize = isMarkKind(activeKind) ? props.markSize : props.size;
  /** The tool whose colour the palette shows: the selection's kind, else the armed tool (Select → Text). */
  const colorTool = toolOrSelect(
    selected && selected.kind !== 'whiteout'
      ? (selected.kind as ToolId)
      : tool.properties.includes('color')
        ? tool.id
        : 'text',
  );
  const activeColor: AnnotationColor = inline
    ? (inline.color ?? 'black')
    : boxEditor
      ? boxEditor.color
    : noteEditor
      ? noteEditor.color
      : selected
        ? colorOf(selected)
        : colorForTool(colorTool, toolState.colors);
  /** A tool with no colour (Signature, Image) greys the palette out, and says why; so does a whiteout / an image. */
  const colorDisabled =
    !inline &&
    !noteEditor &&
    !boxEditor &&
    (selected
      ? selected.kind === 'whiteout' || selected.kind === 'image'
      : tool.id !== 'select' && !tool.properties.includes('color'));
  /** W4: the stroke width / fill the boxes show -- the selection's, else the next drawing's. */
  const activeStroke = selected && isStroked(selected) ? selected.stroke_pt : props.stroke;
  const activeFill = selected && isShape(selected) ? selected.fill : props.fill;

  /** What the font / B / I boxes show: the open box, the selected run, else the next run. */
  const activeFont: AnnotationFont = inline
    ? inline.font
    : selected && isTextual(selected)
      ? selected.font
      : props.font;
  const activeBold = inline
    ? Boolean(inline.bold)
    : selected && isTextual(selected)
      ? Boolean(selected.bold)
      : props.bold;
  const activeItalic = inline
    ? Boolean(inline.italic)
    : selected && isTextual(selected)
      ? Boolean(selected.italic)
      : props.italic;
  const selectedBox = selected && selected.kind === 'textbox' ? (selected as TextboxAnnotation) : null;
  const activeSize = inline
    ? inline.size_pt
    : boxEditor
      ? boxEditor.size_pt
      : selectedBox
        ? (selectedBox.runs[0]?.size_pt ?? props.size)
        : selected && isSized(selected)
          ? selected.size_pt
          : armedSize;
  /** F2: the text box's family (any of `GET /api/fonts`). */
  const activeBoxFont: EditorFontFamilyId = boxEditor
    ? boxEditor.font
    : selectedBox
      ? (selectedBox.runs[0]?.font ?? props.boxFont)
      : props.boxFont;
  /** F2: the v7 look the boxes show -- the selection's, else the next drawing's. */
  const activeV7: V7Props & { rotation: number } = (() => {
    const base = { ...props.v7, rotation: 0 };
    if (boxEditor) return { ...base, align: boxEditor.align, lineHeight: boxEditor.line_height };
    if (!selected) return base;
    const look = (selected as { v7?: Record<string, unknown> }).v7;
    if (selected.kind === 'textbox') {
      const box = selected as TextboxAnnotation;
      return { ...base, align: box.align, lineHeight: box.line_height, opacity: box.opacity ?? 1, rotation: box.rotation_deg ?? 0 };
    }
    if (selected.kind === 'stamp') {
      const stamp = selected as { opacity?: number; rotation_deg?: number };
      return { ...base, opacity: stamp.opacity ?? 1, rotation: stamp.rotation_deg ?? 0 };
    }
    if (look) {
      return {
        ...base,
        dash: (look.dash as V7Props['dash']) ?? 'solid',
        fillColor: (look.fill_color as AnnotationColor | null) ?? null,
        opacity: typeof look.opacity === 'number' ? look.opacity : 1,
        heads: (look.heads as V7Props['heads']) ?? base.heads,
        rotation: typeof look.rotation_deg === 'number' ? look.rotation_deg : 0,
      };
    }
    // A legacy drawing: what it looks like today in v7 words.
    return {
      ...base,
      dash: 'solid',
      fillColor: isShape(selected) && selected.fill ? selected.color : null,
      opacity: 1,
      heads: selected.kind === 'arrow' ? 'end' : 'none',
      rotation: 0,
    };
  })();

  const setTool = useCallback(
    (next: ToolId) => {
      if (!getTool(next)) return;
      setToolId(next);
      if (next !== 'select') selectAnnotation(null);
    },
    [selectAnnotation, setToolId],
  );

  /* -- the inline text box -------------------------------------------------- */

  const commitInline = useCallback(() => {
    const current = inlineRef.current;
    if (!current) return;
    const value = normalizeText(current.value).trim();
    // Nothing typed, or everything deleted: a cancel. The server refuses an
    // empty run, and an existing one keeps the text it already had.
    if (!value) {
      openInline(null);
      return;
    }
    const bold = Boolean(current.bold);
    const italic = Boolean(current.italic);
    // Refused HERE, before anything is stored: the box stays open, says which
    // character and why, and how to fix it (NFC first, like the server).
    const refusal =
      value.length > MAX_TEXT_LENGTH
        ? `A run of text holds at most ${MAX_TEXT_LENGTH} characters (this one has ${value.length}).`
        : checkPrintable(value, current.font, bold, italic);
    if (refusal) {
      openInline({ ...current, error: refusal });
      setNote(refusal);
      return;
    }
    openInline(null);
    if (current.error) setNote(null);

    // F1b: the WE path (a whiteout + a text over an existing line) is gone
    // -- «oq quti TAQIQ»; a stored group of it is only ever re-typed as the
    // plain text run it is (below), or converted to a real edit from Objects.
    if (current.id) {
      store.setText(current.id, value);
      return;
    }
    const id = makeAnnotationId(store.items.map((item) => item.id));
    store.add(
      makeTextualAnnotation(current.kind, {
        id,
        page: current.page,
        x_pt: current.x_pt,
        y_pt: current.y_pt,
        text: value,
        font: current.font,
        size_pt: current.size_pt,
        color: current.color,
        bold,
        italic,
      }),
    );
    selectAnnotation(id);
  }, [openInline, store, selectAnnotation, selectAnnotations, selectGroup, setNote, pageSizeOf]);

  /* -- F2: the text box editor ------------------------------------------------ */

  const commitTextbox = useCallback(() => {
    const current = boxRef.current;
    if (!current) return;
    const value = current.value.normalize('NFC').replace(/\s+$/u, '');
    if (!value.trim()) {
      openTextbox(null);
      return;
    }
    if (value.length > 2000) {
      openTextbox({ ...current, error: `A text box holds at most 2000 characters (this one has ${value.length}).` });
      return;
    }
    // The Liberation faces are checked here, like every run; a B2 family by the server.
    const refusal = isFontFamily(current.font)
      ? checkPrintable(value.replace(/\n/g, ' '), current.font, current.bold, current.italic)
      : null;
    if (refusal) {
      openTextbox({ ...current, error: refusal });
      setNote(refusal);
      return;
    }
    openTextbox(null);
    if (current.error) setNote(null);
    const style = {
      font: current.font,
      size_pt: current.size_pt,
      color: current.color,
      bold: current.bold,
      italic: current.italic,
    };
    if (current.id) {
      const existing = store.get(current.id);
      if (!existing || existing.kind !== 'textbox') return;
      const box = existing as TextboxAnnotation;
      const runs = restyleRuns(box.runs, value, style);
      const next: TextboxAnnotation = { ...box, runs, align: current.align, line_height: current.line_height };
      const needed = textboxNeededHeight(next);
      const pageHeight = pageSizeOf(box.page)?.height ?? Infinity;
      next.h_pt = Math.min(Math.max(box.h_pt, needed), Math.max(1, pageHeight - box.y_pt));
      store.reshape(box.id, next, 'Edit text box');
      selectAnnotation(box.id);
      return;
    }
    if (store.items.length >= MAX_ANNOTATIONS) {
      setNote(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
      return;
    }
    const id = makeAnnotationId(store.items.map((item) => item.id));
    const made = makeTextbox(
      {
        id,
        page: current.page,
        at: { x: current.x_pt, y: current.y_pt },
        width: current.w_pt,
        runs: restyleRuns([], value, style),
        align: current.align,
        line_height: current.line_height,
        opacity: props.v7.opacity,
      },
      pageSizeOf(current.page),
    );
    store.add(made);
    selectAnnotation(id);
  }, [openTextbox, setNote, store, selectAnnotation, pageSizeOf, props.v7.opacity]);

  const cancelTextbox = useCallback(() => {
    const current = boxRef.current;
    openTextbox(null);
    if (current?.error) setNote(null);
  }, [openTextbox, setNote]);

  // A press anywhere else saves the text box (its own blur does too).
  useEffect(() => {
    if (!boxEditor) return undefined;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target || typeof target.closest !== 'function') return;
      if (
        target.closest(
          '.swm-textbox-editor, .ws-props__group--appearance, .ws-props__group--textbox, .ws-color__palette, .tb-font, .modal',
        )
      ) {
        return;
      }
      if (document.activeElement?.classList.contains('swm-textbox-editor')) return; // its own blur saves
      commitTextbox();
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [boxEditor, commitTextbox]);

  /** Esc: the box goes, nothing is stored, and a refusal it was showing goes with it. */
  const cancelInline = useCallback(() => {
    const current = inlineRef.current;
    openInline(null);
    if (current?.error) setNote(null);
  }, [openInline, setNote]);

  // While a toolbar control (font, size, B, I, colour) holds the focus the box
  // stays open (`AnnotationLayer keepsEditorOpen`); a press anywhere else is
  // "done", exactly like the blur it would otherwise have been.
  useEffect(() => {
    if (!inline) return undefined;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target || typeof target.closest !== 'function') return;
      if (target.closest('.swm-ann-input, .ws-props__group--appearance, .ws-color__palette')) return;
      if (document.activeElement?.classList.contains('swm-ann-input')) return; // its own blur commits
      commitInline();
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [inline, commitInline]);

  /* -- the sticky-note box (W4) --------------------------------------------- */

  const commitNote = useCallback(() => {
    const current = noteRef.current;
    if (!current) return;
    const value = normalizeNoteText(current.value).trim();
    // Nothing written: a cancel (an existing note keeps the text it had).
    if (!value) {
      openNote(null);
      if (current.error) setNote(null);
      return;
    }
    const refusal = noteRefusal(value);
    if (refusal) {
      openNote({ ...current, error: refusal });
      setNote(refusal);
      return;
    }
    openNote(null);
    if (current.error) setNote(null);
    if (current.id) {
      const existing = store.get(current.id);
      if (existing && isNote(existing)) {
        store.reshape(existing.id, { ...existing, text: value, color: current.color }, 'Edit note');
        selectAnnotation(existing.id);
      }
      return;
    }
    if (store.items.length >= MAX_ANNOTATIONS) {
      setNote(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
      return;
    }
    const id = makeAnnotationId(store.items.map((item) => item.id));
    const made = makeNote(
      { id, page: current.page, x: current.x_pt, y: current.y_pt, text: value, color: current.color },
      pageSizeOf(current.page),
    );
    if (!made) return;
    store.add(made);
    selectAnnotation(id);
  }, [openNote, setNote, store, selectAnnotation, pageSizeOf]);

  const cancelNote = useCallback(() => {
    const current = noteRef.current;
    openNote(null);
    if (current?.error) setNote(null);
  }, [openNote, setNote]);

  // A press anywhere else saves the note (its own blur does too; whichever
  // comes first wins -- `commitNote` empties the ref).
  useEffect(() => {
    if (!noteEditor) return undefined;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target || typeof target.closest !== 'function') return;
      if (target.closest('.swm-note-editor, .ws-props__group--appearance, .ws-color__palette')) return;
      if (document.activeElement?.classList.contains('swm-note-editor__input')) return; // its own blur saves
      commitNote();
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [noteEditor, commitNote]);

  /* -- the Image tool (W4) --------------------------------------------------- */

  const imageInputRef = useRef<HTMLInputElement>(null);
  const pendingImage = useRef<{ page: number; at: Point } | null>(null);
  const [uploading, setUploading] = useState(false);
  const latestStore = useRef(store);
  latestStore.current = store;

  const pickOverride = useRef(onPickImage);
  pickOverride.current = onPickImage;
  const pickImage = useCallback((page: number, at: Point) => {
    if (pickOverride.current?.(page, at)) return;
    const input = imageInputRef.current;
    if (!input) return;
    pendingImage.current = { page, at };
    input.value = '';
    input.click();
  }, []);

  /** The file dialog answered: check, upload ONCE, place (one undo step; the upload is not one). */
  const onImageFile = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] ?? null;
      const target = pendingImage.current;
      pendingImage.current = null;
      if (!file || !target || !contractId) return;
      const checked = await checkImageFile(file);
      if (!checked.ok) {
        setNote(checked.error);
        return;
      }
      setUploading(true);
      setNote('Uploading the image…');
      let asset: { asset_id: string; width_px: number; height_px: number };
      try {
        asset = await assetsApi.upload(contractId, checked.dataUrl);
      } catch (cause) {
        setNote(describeAssetError(cause));
        return;
      } finally {
        setUploading(false);
      }
      const current = latestStore.current;
      if (current.items.length >= MAX_ANNOTATIONS) {
        setNote(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
        return;
      }
      const pageSize = pageSizeOf(target.page);
      const natural = {
        width: asset.width_px > 0 ? asset.width_px : checked.width,
        height: asset.height_px > 0 ? asset.height_px : checked.height,
      };
      const id = makeAnnotationId(current.items.map((item) => item.id));
      const made = makeImage(
        { id, page: target.page, asset_id: asset.asset_id, box: placeImageBox(natural, target.at, pageSize) },
        pageSize,
      );
      if (!made) {
        setNote('The server answered with an image id this editor does not understand; nothing was placed.');
        return;
      }
      setNote(null);
      current.add(made);
      selectAnnotation(id);
    },
    [contractId, pageSizeOf, setNote, selectAnnotation],
  );

  const editAnnotation = useCallback(
    (annotation: Annotation) => {
      selectGroup(annotation.id);
      if (annotation.kind === 'textbox') {
        const box = annotation as TextboxAnnotation;
        const first = box.runs[0];
        openTextbox({
          id: box.id,
          page: box.page,
          x_pt: box.x_pt,
          y_pt: box.y_pt,
          w_pt: box.w_pt,
          value: textboxText(box),
          font: first.font,
          size_pt: first.size_pt,
          color: first.color,
          bold: Boolean(first.bold),
          italic: Boolean(first.italic),
          align: box.align,
          line_height: box.line_height,
        });
        return;
      }
      if (isNote(annotation)) {
        openNote({
          id: annotation.id,
          page: annotation.page,
          x_pt: annotation.x_pt,
          y_pt: annotation.y_pt,
          color: annotation.color,
          value: annotation.text,
        });
        return;
      }
      // A tick has no text to retype; double-clicking one simply selects it.
      if (!isTextual(annotation)) return;
      openInline({
        id: annotation.id,
        kind: annotation.kind,
        page: annotation.page,
        x_pt: annotation.x_pt,
        y_pt: annotation.y_pt,
        font: annotation.font,
        size_pt: annotation.size_pt,
        color: annotation.color,
        bold: Boolean(annotation.bold),
        italic: Boolean(annotation.italic),
        value: annotation.text,
      });
    },
    [openInline, openNote, openTextbox, selectGroup],
  );

  /* -- the page itself: the armed tool decides ------------------------------ */

  const contextFor = useCallback(
    (page: number): ToolContext => ({
      page,
      scale,
      pageSize: pageSizeOf(page),
      props,
      annotations: store,
      selection,
      chooser: { openFree: onSignatureClick, openInitials: onInitialsClick },
      gate,
      notify: setNote,
      openInline,
      openNote: (next) => {
        // A note the PDF cannot carry stays open (with its error) rather than
        // being silently replaced by a new one -- its text would be lost.
        if (next && noteRef.current?.error) return;
        openNote(next);
      },
      pickImage: (at) => pickImage(page, at),
      newId: () => makeAnnotationId(store.items.map((item) => item.id)),
      editor,
      openTextbox,
      redact: (rect) => onRedact?.(page, rect),
      openLink: (rect) => onOpenLink?.(page, rect),
    }),
    [
      scale,
      pageSizeOf,
      props,
      store,
      selection,
      onSignatureClick,
      onInitialsClick,
      gate,
      setNote,
      openInline,
      openNote,
      pickImage,
      editor,
      openTextbox,
      onRedact,
      onOpenLink,
    ],
  );

  /** A press of a DRAG tool on a page (W4, `ToolCaptureLayer`). */
  const beginGesture = useCallback(
    (page: number, pt: Point) => tool.beginGesture?.(contextFor(page), pt) ?? null,
    [tool, contextFor],
  );

  const handleCanvasClick = useCallback(
    (page: number, point: { x: number; y: number }, suggested: BBox) => {
      // Whatever was being typed has already been committed by its own blur,
      // which the browser runs on the press that produced this click.
      tool.onPageClick?.(contextFor(page), point, suggested);
    },
    [tool, contextFor],
  );

  /** A line of the text layer was clicked (a `text-run` tool, WE). */
  const handleTextRun = useCallback(
    (page: number, run: TextRun) => {
      tool.onTextRun?.(contextFor(page), run);
    },
    [tool, contextFor],
  );

  /**
   * A text selection landed (a `text-selection` tool, W3): one entry per page
   * it crosses, its boxes already merged per line and clamped
   * (`text/useTextSelection.ts`). The armed tool turns each page's boxes into
   * annotations through a BATCH store, and what it made is committed here as
   * ONE step -- a selection across a page break is one highlight per page, one
   * Ctrl+Z. All or nothing: if the tool refuses any page (the list is full),
   * nothing of the selection is kept and the refusal is said.
   */
  const handleTextSelection = useCallback(
    (pages: ReadonlyArray<{ page: number; rects: RectPt[] }>) => {
      if (!tool.onTextSelection || pages.length === 0) return;
      const items = [...store.items];
      const pending: Annotation[] = [];
      let refusal: string | null = null;
      const batch: AnnotationStore = {
        ...store,
        items,
        add: (annotation) => {
          items.push(annotation);
          pending.push(annotation);
        },
      };
      for (const { page, rects } of pages) {
        tool.onTextSelection({
          ...contextFor(page),
          annotations: batch,
          notify: (note) => {
            if (note) refusal = note;
          },
          newId: () => makeAnnotationId(items.map((item) => item.id)),
        }, rects);
        if (refusal) break;
      }
      if (refusal) {
        setNote(refusal);
        return;
      }
      if (pending.length === 0) return;
      setNote(null);
      if (pending.length === 1) {
        store.add(pending[0]);
        selectAnnotation(pending[0].id);
      } else {
        store.addMany(pending, tool.label);
        selectAnnotation(null);
      }
    },
    [tool, store, contextFor, setNote, selectAnnotation],
  );

  /**
   * «Highlight all matches» (W3, search panel): one highlight per match, from
   * the search's own boxes (no text layer needed), in the Highlight tool's
   * colour, ONE undo step. A match that already carries a highlight with the
   * very same boxes is skipped (pressing it twice adds nothing). If the list
   * would pass `MAX_ANNOTATIONS`, NOTHING is added and the note says how many
   * are left -- a half-highlighted search would be worse than none.
   */
  const highlightMatches = useCallback(
    (hits: readonly SearchHit[]) => {
      const highlight = getTool('highlight');
      if (!highlight || hits.length === 0) return;
      const color = colorForTool(highlight, toolState.colors);
      const taken = new Set(store.items.map((item) => item.id));
      const existing = store.items.filter(isMarkup).filter((item) => item.kind === 'highlight');
      const added: Annotation[] = [];
      hits.forEach((hit) => {
        const boxes = hit.rects.map((rect) => ({ x: rect.x, y: rect.y, w: rect.width, h: rect.height }));
        for (let start = 0; start < boxes.length; start += MAX_MARKUP_RECTS) {
          const id = makeAnnotationId(taken);
          const made = makeMarkupAnnotation(
            'highlight',
            { id, page: hit.page, rects: boxes.slice(start, start + MAX_MARKUP_RECTS), color },
            pageSizeOf(hit.page),
          );
          if (!made) continue;
          if (existing.some((item) => item.page === made.page && sameRects(item.rects, made.rects))) {
            continue;
          }
          taken.add(id);
          added.push(made);
        }
      });
      if (added.length === 0) {
        setNote('Every match is already highlighted.');
        return;
      }
      const room = MAX_ANNOTATIONS - store.items.length;
      if (added.length > room) {
        setNote(
          `Highlighting ${added.length} matches would pass the maximum of ${MAX_ANNOTATIONS} annotations ` +
            `(${Math.max(0, room)} left). Nothing was highlighted — narrow the search or delete some annotations.`,
        );
        return;
      }
      setNote(null);
      store.addMany(added, 'Highlight all matches');
      selectAnnotation(null);
    },
    [store, toolState.colors, pageSizeOf, setNote, selectAnnotation],
  );

  /* -- the PropertiesBar's controls ------------------------------------------ */

  /** A face change on the selected run is refused out loud if the text would not print in it. */
  const refusalFor = useCallback(
    (annotation: Annotation | undefined, font: AnnotationFont, bold: boolean, italic: boolean) =>
      annotation && isTextual(annotation) ? checkPrintable(annotation.text, font, bold, italic) : null,
    [],
  );

  /** F2: restyle the open text box, else the selected one (one step), and the next one. */
  const restyleBox = useCallback(
    (change: { font?: EditorFontFamilyId; size_pt?: number; color?: AnnotationColor; bold?: boolean; italic?: boolean }) => {
      const current = boxRef.current;
      if (current) {
        openTextbox({
          ...current,
          ...(change.font ? { font: change.font } : {}),
          ...(change.size_pt !== undefined ? { size_pt: Math.min(72, Math.max(6, change.size_pt)) } : {}),
          ...(change.color ? { color: change.color } : {}),
          ...(change.bold !== undefined ? { bold: change.bold } : {}),
          ...(change.italic !== undefined ? { italic: change.italic } : {}),
          error: null,
        });
        return true;
      }
      if (selected && selected.kind === 'textbox') {
        const box = selected as TextboxAnnotation;
        const next = { ...box, runs: restyleAll(box.runs, change) };
        const needed = textboxNeededHeight(next);
        store.reshape(box.id, { ...next, h_pt: Math.max(box.h_pt, needed) }, 'Style text box');
        return true;
      }
      return false;
    },
    [openTextbox, selected, store],
  );

  const changeBoxFont = useCallback(
    (font: EditorFontFamilyId) => {
      setBoxFont(font);
      restyleBox({ font });
    },
    [setBoxFont, restyleBox],
  );

  /**
   * F2: the v7 look (dash, fill colour, opacity, heads, rotation, align, line
   * spacing) -- of the open text box / the selection (one step), and of the
   * next drawing. A legacy drawing that gets a v7 look becomes its v7 kind on
   * the wire (`shape2` / `ink2` / `image2`).
   */
  const changeV7 = useCallback(
    (change: Partial<V7Props> & { rotation?: number }) => {
      const { rotation, ...rest } = change;
      setV7(rest);
      const current = boxRef.current;
      if (current) {
        openTextbox({
          ...current,
          ...(rest.align ? { align: rest.align } : {}),
          ...(rest.lineHeight ? { line_height: rest.lineHeight } : {}),
        });
        return;
      }
      if (!selected) return;
      const look = { ...activeV7, ...rest, ...(rotation !== undefined ? { rotation } : {}) };
      const opacity = clampOpacity(look.opacity);
      let next: Annotation | null = null;
      if (selected.kind === 'textbox') {
        const box = selected as TextboxAnnotation;
        const made: TextboxAnnotation = { ...box, align: look.align, line_height: look.lineHeight };
        if (opacity < 1) made.opacity = opacity;
        else delete made.opacity;
        if (look.rotation) made.rotation_deg = look.rotation;
        else delete made.rotation_deg;
        made.h_pt = Math.max(box.h_pt, textboxNeededHeight(made));
        next = made;
      } else if (selected.kind === 'stamp') {
        const stamp = { ...(selected as Annotation & { opacity?: number; rotation_deg?: number }) };
        if (opacity < 1) stamp.opacity = opacity;
        else delete stamp.opacity;
        if (look.rotation) stamp.rotation_deg = look.rotation;
        else delete stamp.rotation_deg;
        next = stamp;
      } else if (isShape(selected)) {
        next = { ...selected, fill: false, v7: { dash: look.dash, fill_color: look.fillColor, opacity } };
      } else if (selected.kind === 'line' || selected.kind === 'arrow') {
        next = {
          ...selected,
          v7: { dash: look.dash, opacity, heads: selected.kind === 'arrow' ? look.heads : look.heads === 'end' ? 'end' : look.heads },
        } as Annotation;
      } else if (selected.kind === 'ink') {
        const ink = selected as Annotation & { v7?: { freehand_highlight: boolean } };
        next = { ...ink, v7: { opacity, freehand_highlight: Boolean(ink.v7?.freehand_highlight) } } as Annotation;
      } else if (selected.kind === 'image') {
        next = { ...selected, v7: { opacity, rotation_deg: look.rotation } } as Annotation;
      }
      if (next) store.reshape(selected.id, next, 'Change style');
    },
    [activeV7, openTextbox, selected, setV7, store],
  );

  /** F2: a copy of the selection, 12 pt down-right, selected; ONE step. */
  const duplicateSelected = useCallback(() => {
    if (!selected || selected.group_id) return;
    if (store.items.length >= MAX_ANNOTATIONS) {
      setNote(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
      return;
    }
    const id = makeAnnotationId(store.items.map((item) => item.id));
    const copy = { ...selected, id } as Annotation;
    const target = clampAnchor(copy, { x: copy.x_pt + 12, y: copy.y_pt + 12 }, pageSizeOf(copy.page));
    store.add(moveAnnotation(copy, target.x, target.y));
    selectAnnotation(id);
  }, [selected, store, selectAnnotation, setNote, pageSizeOf]);

  const changeFont = useCallback(
    (font: AnnotationFont) => {
      const current = inlineRef.current;
      // The edit-text box describes ITS line: the pick is for this edit only.
      if (current?.edit) {
        openInline({ ...current, font, error: null });
        return;
      }
      setFont(font);
      if (selected) {
        const refusal = refusalFor(
          selected,
          font,
          isTextual(selected) && Boolean(selected.bold),
          isTextual(selected) && Boolean(selected.italic),
        );
        if (refusal) setNote(refusal);
        else store.setFont(selected.id, font);
      }
      if (current) openInline({ ...current, font, error: null });
    },
    [openInline, selected, store, setFont, refusalFor, setNote],
  );

  const changeStyle = useCallback(
    (style: { bold?: boolean; italic?: boolean }) => {
      if (restyleBox(style)) {
        setStyle(style);
        return;
      }
      const current = inlineRef.current;
      if (current?.edit) {
        openInline({ ...current, ...style, error: null });
        return;
      }
      setStyle(style);
      if (selected && isTextual(selected)) {
        const bold = style.bold ?? Boolean(selected.bold);
        const italic = style.italic ?? Boolean(selected.italic);
        const refusal = refusalFor(selected, selected.font, bold, italic);
        if (refusal) setNote(refusal);
        else store.setStyle(selected.id, style);
      }
      if (current) openInline({ ...current, ...style, error: null });
    },
    [openInline, selected, store, setStyle, refusalFor, setNote],
  );

  const changeSize = useCallback(
    (size: number) => {
      if (activeKind === 'textbox') {
        setSize(Math.min(48, Math.max(6, size)));
        restyleBox({ size_pt: size });
        return;
      }
      const current = inlineRef.current;
      const value = clampSize(size, activeKind);
      if (current?.edit) {
        openInline({ ...current, size_pt: value });
        return;
      }
      if (isMarkKind(activeKind)) setMarkSize(value);
      else setSize(value);
      if (selected) store.resize(selected.id, value);
      if (current) openInline({ ...current, size_pt: value });
    },
    [activeKind, openInline, selected, store, setMarkSize, setSize],
  );

  const changeColor = useCallback(
    (color: AnnotationColor) => {
      if (boxRef.current) {
        restyleBox({ color });
        setColorFor(colorTool.id, color);
        return;
      }
      const current = inlineRef.current;
      if (current?.edit) {
        openInline({ ...current, color });
        return;
      }
      const note = noteRef.current;
      if (note) {
        // The note box: this note's colour (stored with its text, one step);
        // a NEW note also makes it the Note tool's colour for the next one.
        openNote({ ...note, color });
        if (!note.id) setColorFor('note', color);
        return;
      }
      setColorFor(colorTool.id, color);
      if (selected) store.setColor(selected.id, color);
      if (current) openInline({ ...current, color });
    },
    [colorTool.id, openInline, openNote, selected, store, setColorFor],
  );

  /** W4: the stroke width -- of the selected drawing (one step), and of the next one. */
  const changeStroke = useCallback(
    (stroke: number) => {
      setStroke(stroke);
      if (selected && isStroked(selected)) store.setStroke(selected.id, stroke);
    },
    [selected, store, setStroke],
  );

  /** W4: the translucent fill -- of the selected rect / ellipse (one step), and of the next one. */
  const changeFill = useCallback(
    (fill: boolean) => {
      setFill(fill);
      if (selected && isShape(selected)) store.setFill(selected.id, fill);
    },
    [selected, store, setFill],
  );

  const removeSelected = useCallback(() => {
    if (!selected) return;
    store.remove(selected.id, selected.group_id ? 'Revert edited text' : undefined);
    selectAnnotation(null);
  }, [selected, store, selectAnnotation]);

  /* -- keyboard ------------------------------------------------------------- */

  // On `window`, not on a container: clicking the document moves the focus
  // nowhere in particular, and a shortcut that only works while a button
  // happens to be focused is not a shortcut. Anything typed into a real input
  // (the inline box, the size field, a date field, the review notes) is left
  // entirely alone. The one-letter tool keys live with the toolbar
  // (`toolbar/FloatingToolbar.tsx`).
  const clearSelection = selection.clear;
  const hasSelection = selection.selection !== null;
  const { undo, redo } = history;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = Boolean(
        target?.closest?.('input, textarea, select, [contenteditable="true"]'),
      );
      const mod = event.ctrlKey || event.metaKey;

      if (mod && (event.key === 'z' || event.key === 'Z')) {
        // Inside a text box Ctrl+Z is the browser's own undo of the typing.
        if (typing) return;
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (mod && (event.key === 'y' || event.key === 'Y')) {
        if (typing) return;
        event.preventDefault();
        redo();
        return;
      }
      if (mod || event.altKey || typing) return;
      // A key pressed on the toolbar (Enter on a tool, an arrow between tools)
      // or inside a dialog / pop-over belongs to that control, not to the
      // selected annotation.
      if (target?.closest?.('[role="toolbar"], [role="listbox"], .modal')) return;

      const current = store.get(selectedId);
      if (!current) {
        // Nothing the keys can act on. Escape still lets go of a slot or an
        // issue; Delete and the arrows do nothing -- a server row is removed
        // with its own button, never with a key.
        if (event.key === 'Escape' && hasSelection && !document.querySelector('.modal')) {
          clearSelection();
        }
        return;
      }
      const step = event.shiftKey ? NUDGE_STEP_LARGE : NUDGE_STEP;
      switch (event.key) {
        case 'Delete':
        case 'Backspace':
          event.preventDefault();
          // A group member takes its whole group with it (plan §7-6).
          store.remove(current.id, current.group_id ? 'Revert edited text' : undefined);
          selectAnnotation(null);
          break;
        case 'Escape':
          event.preventDefault();
          selectAnnotation(null);
          break;
        case 'Enter':
        case 'F2':
          event.preventDefault();
          editAnnotation(current);
          break;
        case 'ArrowLeft':
          event.preventDefault();
          edit.nudge(current, -step, 0);
          break;
        case 'ArrowRight':
          event.preventDefault();
          edit.nudge(current, step, 0);
          break;
        case 'ArrowUp':
          event.preventDefault();
          edit.nudge(current, 0, -step);
          break;
        case 'ArrowDown':
          event.preventDefault();
          edit.nudge(current, 0, step);
          break;
        default:
          break;
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [edit, editAnnotation, selectedId, store, selectAnnotation, clearSelection, hasSelection, undo, redo]);

  return {
    store,
    edit,
    tool,
    toolId,
    setTool,
    /** The slot layer steps aside for this tool (`workspace--placing`). */
    placing: slotsStepAsideFor(tool),
    /** A drag tool is armed (W4): every page gets a `ToolCaptureLayer` (`workspace--drawing`). */
    drawing: drawsByDragging(tool),
    beginGesture,
    noteEditor,
    noteRef,
    openNote,
    commitNote,
    cancelNote,
    imageInputRef,
    onImageFile,
    uploading,
    activeStroke,
    activeFill,
    changeStroke,
    changeFill,
    /** The armed tool reads the page's own text (`workspace--text-tools`, WE). */
    textTools: Boolean(tool.needsTextLayer),
    /** ...by SELECTING it (highlight / underline / strikeout, W3): `workspace--text-select`. */
    textSelect: Boolean(tool.needsTextLayer) && tool.input === 'text-selection',
    handleTextSelection,
    highlightMatches,
    props,
    setDate,
    selectedId,
    selectedIds,
    selected,
    selectGroup,
    activeKind,
    armedSize,
    activeSize,
    activeFont,
    activeBold,
    activeItalic,
    activeColor,
    colorDisabled,
    inline,
    inlineRef,
    openInline,
    commitInline,
    cancelInline,
    editAnnotation,
    handleCanvasClick,
    handleTextRun,
    changeFont,
    changeStyle,
    changeSize,
    changeColor,
    removeSelected,
    // F2
    boxEditor,
    boxRef,
    openTextbox,
    commitTextbox,
    cancelTextbox,
    activeBoxFont,
    changeBoxFont,
    activeV7,
    changeV7,
    duplicateSelected,
    setStamp,
  };
}

export type AnnotationTools = ReturnType<typeof useAnnotationTools>;
