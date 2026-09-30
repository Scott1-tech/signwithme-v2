import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { isTextOp, type Align, type DocumentOp, type FitMode, type Hex } from '../../api/document';
import type { EditorDocument } from '../../hooks/useContractDocument';
import type { PdfViewerState } from '../../hooks/usePdfViewer';
import type { AnnotationStore } from '../../hooks/useAnnotations';
import {
  ANNOTATION_COLOR_HEX,
  MAX_ANNOTATIONS,
  MAX_TEXT_LENGTH,
  clampSize,
  makeAnnotationId,
  makeTextualAnnotation,
  type AnnotationColor,
  type EditGroup,
} from '../../lib/annotations';
import { LIBERATION_OF, isFontFamily } from '../../lib/fonts';
import { measureEditorText } from '../../lib/editorFonts';
import { canonical } from '../../lib/markupWire';
import { checkPrintable, stripIgnorable } from '../../lib/textCheck';
import { mapPointPt, UNROTATED } from '../../lib/viewport';
import type { EditSession } from '../session/useEditSession';
import { newOpId } from '../session/useEditSession';
import { fetchPreview } from './previewFetch';
import { TextBlockEditor, type EditorHandle } from './TextBlockEditor';
import { TextBlockLayer, type OverflowNotice, type PendingEdit } from './TextBlockLayer';
import { TextFloatingToolbar } from './TextFloatingToolbar';
import {
  editOf,
  offsetAt,
  opFor,
  opOfBlock,
  rowAt,
  withBlockOp,
  wordAt,
  type BoxPt,
  type StyleOverride,
} from './textOps';
import { useServerTextModel, type EditableBlock, type TextStyle } from './useServerTextModel';

/**
 * F1b: the whole «edit the PDF's own text» interaction, for the workspace.
 *
 * Owns the open editor, the server previews painted over the pages
 * (`viewer.setPageOverride`), the masks, the overflow notices; the op list
 * itself is the session's (`session.changeOps`: one `'ops'` history step and
 * one autosave per block commit).
 *
 * PREVIEW = EXPORT. What the page shows of an edit is the SERVER's one-page
 * preview (the same engine path as the commit), never a client imitation:
 *  * editor open  → `preview?hide=<block>` (the page without that block's
 *    glyphs, every other op applied), the editor's DOM text over it;
 *  * editor closed → `preview` of the page once the draft revision holding
 *    the edit is saved;
 *  * in between → the new text in the server's face over a mask of the
 *    paper's median colour (`pending`) -- on the screen only, never sent.
 */

interface OpenEdit {
  token: number;
  page: number;
  block: EditableBlock;
  opId: string;
  initialText: string;
  selection: [number, number];
  override: StyleOverride | null;
  box: BoxPt | null;
  userBox: boolean;
  fit: FitMode;
  /** F2: the toolbar's alignment / line spacing for this block (null = as it was). */
  align: Align | null;
  lineHeight: number | null;
  mask: string | null;
  previewReady: boolean;
  moving: boolean;
}

interface Installed {
  digest: string;
  hide: string | null;
}

interface Options {
  contractId: string | undefined;
  session: EditSession;
  editor: EditorDocument;
  viewer: PdfViewerState;
  /** Edit text is armed (the layer is shown). */
  armed: boolean;
  pageSizeOf: (page: number | null) => { width: number; height: number } | null;
  store: AnnotationStore;
  selectAnnotation: (id: string | null) => void;
  setNote: (note: string | null) => void;
  /** F2: «Link» on the floating toolbar -- a link over the edited block's box. */
  onLink?: (page: number, pageUid: string, box: { x: number; y: number; w: number; h: number }) => void;
}

const keyOf = (uid: string, blockId: string) => `${uid}|${blockId}`;

function nearestPalette(hex: string): AnnotationColor {
  const parse = (value: string) => [1, 3, 5].map((at) => parseInt(value.slice(at, at + 2), 16));
  const [r, g, b] = parse(hex.length === 7 ? hex : '#000000');
  let best: AnnotationColor = 'black';
  let distance = Infinity;
  (Object.keys(ANNOTATION_COLOR_HEX) as AnnotationColor[]).forEach((name) => {
    const [pr, pg, pb] = parse(ANNOTATION_COLOR_HEX[name]);
    const d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2;
    if (d < distance) {
      distance = d;
      best = name;
    }
  });
  return best;
}

export function useTextEditing({
  contractId,
  session,
  editor,
  viewer,
  armed,
  pageSizeOf,
  store,
  selectAnnotation,
  setNote,
  onLink,
}: Options) {
  const onLinkRef = useRef(onLink);
  onLinkRef.current = onLink;
  /** ROTFIX: the blocks are the engine's -- the page they live on is the UNROTATED one. */
  const { frameOf } = viewer;
  const flatSizeOf = useCallback(
    (page: number) => {
      const frame = frameOf(page);
      return frame ? { width: frame.width, height: frame.height } : pageSizeOf(page);
    },
    [frameOf, pageSizeOf],
  );
  const doc = editor.doc;
  const versionNo = doc?.current_version.version_no ?? null;
  const fileHash = doc?.current_version.file_hash ?? null;
  const models = useServerTextModel(contractId, versionNo, fileHash);
  const available = editor.status === 'ready' && Boolean(doc);
  const editing = session.mode === 'edit';

  const uidOf = useCallback(
    (page: number) => editor.pages.find((entry) => entry.position === page)?.page_uid ?? null,
    [editor.pages],
  );
  const pageOf = useCallback(
    (uid: string) => editor.pages.find((entry) => entry.page_uid === uid)?.position ?? null,
    [editor.pages],
  );

  const [open, setOpenState] = useState<OpenEdit | null>(null);
  const openRef = useRef<OpenEdit | null>(null);
  const setOpen = useCallback((next: OpenEdit | null) => {
    openRef.current = next;
    setOpenState(next);
  }, []);
  const [liveText, setLiveText] = useState('');
  const handleRef = useRef<EditorHandle | null>(null);
  const tokenRef = useRef(0);

  const installedRef = useRef(new Map<string, Installed>());
  const [installedVersion, setInstalledVersion] = useState(0);
  const bumpInstalled = () => setInstalledVersion((value) => value + 1);
  const fetchingRef = useRef(new Map<string, string>());
  const masksRef = useRef(new Map<string, string>());
  const [previewOverflow, setPreviewOverflow] = useState<
    Record<string, { linesOver: number; neededH: number | null; collision: boolean }>
  >({});

  /* -- the models of the pages on screen ------------------------------------ */

  const active = armed && available;
  useEffect(() => {
    if (!active) return;
    viewer.windowPages.forEach((page) => {
      const uid = uidOf(page);
      if (uid) void models.request(uid);
    });
  }, [active, viewer.windowPages, uidOf, models]);

  /* -- ops by page ------------------------------------------------------------ */

  const opsByPage = useMemo(() => {
    // F2: every op that belongs to one page (a text edit, a redaction, a
    // removed image, a link) changes that page's preview; page moves do not.
    const map = new Map<string, DocumentOp[]>();
    session.ops.forEach((op) => {
      if (!('page_uid' in op) || op.kind.startsWith('page.')) return;
      const list = map.get(op.page_uid);
      if (list) list.push(op);
      else map.set(op.page_uid, [op]);
    });
    return map;
  }, [session.ops]);
  const digestOf = useCallback((uid: string) => canonical(opsByPage.get(uid) ?? []), [opsByPage]);

  /* -- previews of the closed edits ------------------------------------------ */

  const clearAllOverrides = useCallback(() => {
    installedRef.current.forEach((_value, uid) => {
      const page = pageOf(uid);
      if (page) void viewer.setPageOverride(page, null);
    });
    installedRef.current.clear();
    fetchingRef.current.clear();
    bumpInstalled();
  }, [pageOf, viewer]);

  // The session ended: the pages are the document's again (a commit replaces
  // the whole source anyway; a cancel does not).
  const wasEditing = useRef(editing);
  useEffect(() => {
    if (wasEditing.current && !editing) {
      setOpen(null);
      clearAllOverrides();
      setPreviewOverflow({});
    }
    wasEditing.current = editing;
  }, [editing, clearAllOverrides, setOpen]);

  useEffect(() => {
    if (!editing || !contractId) return;
    const openUid = openRef.current?.block.pageUid ?? null;
    const pages = new Set<string>([...opsByPage.keys(), ...installedRef.current.keys()]);
    pages.forEach((uid) => {
      if (uid === openUid) return;
      const page = pageOf(uid);
      if (!page) return;
      const installed = installedRef.current.get(uid);
      if (!opsByPage.has(uid)) {
        if (installed) {
          installedRef.current.delete(uid);
          void viewer.setPageOverride(page, null).then(() => bumpInstalled());
        }
        return;
      }
      if (session.status !== 'saved') return;
      const want = digestOf(uid);
      if (installed && installed.digest === want && !installed.hide) return;
      const revision = session.revision;
      const ticket = `${want}@${revision}`;
      if (fetchingRef.current.get(uid) === ticket) return;
      fetchingRef.current.set(uid, ticket);
      fetchPreview(contractId, uid, revision)
        .then((bytes) => {
          if (fetchingRef.current.get(uid) !== ticket) return false;
          return viewer.setPageOverride(page, bytes);
        })
        .then((ok) => {
          if (ok && fetchingRef.current.get(uid) === ticket) {
            installedRef.current.set(uid, { digest: want, hide: null });
            fetchingRef.current.delete(uid);
            bumpInstalled();
          }
        })
        .catch((cause: unknown) => {
          fetchingRef.current.delete(uid);
          if (cause instanceof ApiError && cause.code === 'text_overflow') {
            const opId = cause.details.op_id as string | undefined;
            if (opId) {
              setPreviewOverflow((current) => ({
                ...current,
                [opId]: {
                  linesOver: Number(cause.details.lines_over) || 0,
                  neededH: typeof cause.details.needed_h_pt === 'number' ? (cause.details.needed_h_pt as number) : null,
                  collision: cause.details.reason === 'collision',
                },
              }));
            }
          }
        });
    });
    // `installedVersion` re-runs this once an override landed.
  }, [editing, contractId, opsByPage, session.status, session.revision, digestOf, pageOf, viewer, installedVersion, open]);

  // A new op list: the preview-time overflow notices are re-decided by the next preview.
  useEffect(() => {
    setPreviewOverflow({});
  }, [session.ops]);

  /* -- the editor --------------------------------------------------------------- */

  const styleOf = useCallback(
    (edit: OpenEdit): TextStyle => ({ ...edit.block.style, ...(edit.override ?? {}) }) as TextStyle,
    [],
  );

  /** The box a commit sends: the user's, else grown to fit one row of text, else none. */
  const boxFor = useCallback(
    (edit: OpenEdit, text: string): BoxPt | null => {
      if (edit.userBox && edit.box) return edit.box;
      const block = edit.block;
      if (text.includes('\n') || block.rows.length > 1) return edit.box;
      const style = styleOf(edit);
      const width = measureEditorText(text, style.family, style.bold, style.italic, style.size);
      if (width <= block.bbox.w - 0.5) return edit.box;
      const pageWidth = flatSizeOf(edit.page)?.width ?? block.bbox.x + width + 2;
      return { ...block.bbox, w: Math.min(width + 1, Math.max(block.bbox.w, pageWidth - block.bbox.x - 1)) };
    },
    [flatSizeOf, styleOf],
  );

  const commitOpen = useCallback(
    (text?: string): boolean => {
      const current = openRef.current;
      if (!current) return true;
      // Zero-width format characters (U+200B ... -- a pasted Docusign line
      // carries them) are not printed: left out, never a refusal.
      const value = stripIgnorable((text ?? handleRef.current?.read() ?? current.initialText).normalize('NFC'));
      const style = styleOf(current);
      // The Liberation faces are checked here; a B2 family by the server (422 `undrawable_character`).
      const refusal =
        value.trim() && isFontFamily(style.family)
          ? checkPrintable(value.replace(/\n/g, ' '), style.family, style.bold, style.italic)
          : null;
      if (refusal) {
        setNote(refusal);
        handleRef.current?.focus();
        return false;
      }
      const op = opFor(current.opId, current.block, {
        text: value,
        override: current.override,
        box: boxFor(current, value),
        fit: current.fit,
        align: current.align,
        lineHeight: current.lineHeight,
      });
      const key = { pageUid: current.block.pageUid, blockId: current.block.id };
      session.changeOps(op?.kind === 'text.delete' ? 'Delete text' : 'Edit text', (list) => withBlockOp(list, key, op));
      setOpen(null);
      setNote(null);
      void session.flush();
      return true;
    },
    [boxFor, session, setNote, setOpen, styleOf],
  );

  const cancelOpen = useCallback(() => {
    setOpen(null);
  }, [setOpen]);

  const openBlock = useCallback(
    (page: number, block: EditableBlock, point: { x: number; y: number } | null, clicks: number) => {
      if (!contractId || !block.editable) return;
      if (openRef.current && !commitOpen()) return;
      const key = { pageUid: block.pageUid, blockId: block.id };
      const op = opOfBlock(session.opsRef.current, key);
      const edit = editOf(block, op);
      const text = edit.text;
      const offset = op || !point ? text.length : Math.min(text.length, offsetAt(block, point.x, point.y));
      const selection: [number, number] =
        clicks >= 3 ? rowAt(text, offset) : clicks === 2 ? wordAt(text, offset) : [offset, offset];
      const maskKey = keyOf(block.pageUid, block.id);
      const sampled = viewer.overriddenPages.has(page) ? null : viewer.sampleColor(page, block.bbox);
      if (sampled) masksRef.current.set(maskKey, sampled);
      tokenRef.current += 1;
      const token = tokenRef.current;
      setLiveText(text);
      setOpen({
        token,
        page,
        block,
        opId: op?.id ?? newOpId(),
        initialText: text,
        selection,
        override: edit.override,
        box: edit.box,
        userBox: Boolean(edit.box),
        fit: edit.fit,
        align: edit.align ?? null,
        lineHeight: edit.lineHeight ?? null,
        mask: masksRef.current.get(maskKey) ?? sampled,
        previewReady: false,
        moving: false,
      });
      // The page without this block, every other edit applied.
      void session
        .flush()
        .then(() => fetchPreview(contractId, block.pageUid, session.revisionRef.current, block.id))
        .then((bytes) => (openRef.current?.token === token ? viewer.setPageOverride(page, bytes) : false))
        .then((ok) => {
          if (!ok || openRef.current?.token !== token) return;
          installedRef.current.set(block.pageUid, { digest: 'hide', hide: block.id });
          bumpInstalled();
          const current = openRef.current;
          if (current) setOpen({ ...current, previewReady: true });
        })
        .catch(() => {
          /* the mask stays: the editor still works over it */
        });
    },
    [commitOpen, contractId, session, setOpen, viewer],
  );

  // A press anywhere outside the editor and its toolbar is «done».
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target || typeof target.closest !== 'function') return;
      if (target.closest('.tb-editor-frame, .tb-toolbar, .tb-block, .tb-overflow__panel')) return;
      commitOpen();
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [open, commitOpen]);

  // Leaving the tool / the session closes the editor (committing it).
  useEffect(() => {
    if ((!armed || !editing) && openRef.current) commitOpen();
  }, [armed, editing, commitOpen]);

  const changeStyle = useCallback(
    (change: StyleOverride) => {
      const current = openRef.current;
      if (!current) return;
      setOpen({ ...current, override: { ...(current.override ?? {}), ...change } });
      // The caret goes back to the text -- unless the user is typing in the size box.
      window.setTimeout(() => {
        const active = document.activeElement as HTMLElement | null;
        if (active?.closest('.tb-toolbar') && active.tagName === 'INPUT') return;
        handleRef.current?.focus();
      }, 0);
    },
    [setOpen],
  );

  const deleteOpen = useCallback(() => {
    const current = openRef.current;
    if (!current) return;
    commitOpen('');
  }, [commitOpen]);

  const duplicateOpen = useCallback(() => {
    const current = openRef.current;
    if (!current) return;
    const value = handleRef.current?.read() ?? current.initialText;
    const style = styleOf(current);
    const box = boxFor(current, value) ?? current.block.bbox;
    if (!commitOpen(value)) return;
    if (store.items.length >= MAX_ANNOTATIONS) {
      setNote(`This contract already has the maximum of ${MAX_ANNOTATIONS} annotations.`);
      return;
    }
    const text = value.split('\n')[0].slice(0, MAX_TEXT_LENGTH).trim();
    if (!text) return;
    const size = clampSize(style.size, 'text');
    const baseline = current.block.firstBaseline + (box.y - current.block.bbox.y);
    const id = makeAnnotationId(store.items.map((item) => item.id));
    const color = nearestPalette(style.color);
    // ROTFIX: the block is the engine's (unrotated); a text annotation is on the page as displayed.
    const frame = frameOf(current.page);
    const flatAt = { x: box.x + 8, y: Math.min((flatSizeOf(current.page)?.height ?? baseline + 40) - 2, baseline + size * 1.6) };
    const at = frame && frame.rotation !== 0 ? mapPointPt(flatAt, frame, UNROTATED, frame.rotation) : flatAt;
    store.add(
      makeTextualAnnotation('text', {
        id,
        page: current.page,
        x_pt: at.x,
        y_pt: at.y,
        text,
        font: isFontFamily(style.family) ? LIBERATION_OF[style.family] : 'LiberationSans',
        size_pt: size,
        color: color === 'black' ? undefined : color,
        bold: style.bold,
        italic: style.italic,
      }),
    );
    selectAnnotation(id);
    setNote('Duplicated as a new text annotation, selected below the original (it is markup, not part of the page’s text).');
  }, [boxFor, commitOpen, flatSizeOf, frameOf, selectAnnotation, setNote, store, styleOf]);

  const fitOp = useCallback(
    (opId: string, fit: FitMode) => {
      session.changeOps(fit === 'shrink' ? 'Shrink text to fit' : 'Allow text overlap', (list) =>
        list.map((op) => (op.id === opId && op.kind === 'text.replace' ? { ...op, fit } : op)),
      );
      void session.flush();
    },
    [session],
  );

  /* -- «Convert to real edit» (a legacy WE group) ------------------------------ */

  const convertGroup = useCallback(
    async (group: EditGroup): Promise<boolean> => {
      if (!group.text) return false;
      const uid = uidOf(group.whiteout.page);
      if (!uid || !contractId) return false;
      if (!editing) {
        const started = await session.start();
        if (!started) return false;
      }
      const model = await models.request(uid);
      if (!model) {
        setNote('The page’s text could not be read; nothing was converted.');
        return false;
      }
      const w = group.whiteout;
      const overlap = (block: EditableBlock) => {
        const x = Math.max(0, Math.min(block.bbox.x + block.bbox.w, w.x_pt + w.w_pt) - Math.max(block.bbox.x, w.x_pt));
        const y = Math.max(0, Math.min(block.bbox.y + block.bbox.h, w.y_pt + w.h_pt) - Math.max(block.bbox.y, w.y_pt));
        return x * y;
      };
      const block = [...model.blocks].filter((item) => item.editable).sort((a, b) => overlap(b) - overlap(a))[0];
      if (!block || overlap(block) <= 0) {
        setNote('No editable text of the page is under this edit; it stays as it is.');
        return false;
      }
      const text = group.text;
      const colorHex = (text.color ? ANNOTATION_COLOR_HEX[text.color] : '#000000') as Hex;
      const op = opFor(opOfBlock(session.opsRef.current, { pageUid: uid, blockId: block.id })?.id ?? newOpId(), block, {
        text: text.text,
        override: {
          family: LIBERATION_OF[text.font],
          size: text.size_pt,
          color: colorHex,
          bold: Boolean(text.bold),
          italic: Boolean(text.italic),
        },
        box: null,
        fit: 'wrap',
      });
      session.changeOps('Convert to real edit', (list) => withBlockOp(list, { pageUid: uid, blockId: block.id }, op));
      store.remove(text.id, 'Convert to real edit');
      void session.flush();
      setNote('Converted: the line is now edited in the document itself (the white box is gone). Save changes to keep it.');
      return true;
    },
    [contractId, editing, models, session, setNote, store, uidOf],
  );

  /* -- what the page draws -------------------------------------------------------- */

  const overflowByOp = useMemo(() => {
    const map = new Map<string, { linesOver: number; neededH: number | null; collision: boolean }>();
    session.warnings.forEach((warning) => {
      if (warning.code !== 'text_overflow' || typeof warning.op_id !== 'string') return;
      const op = session.ops.find((item) => item.id === warning.op_id);
      if (op?.kind === 'text.replace' && op.fit === 'overflow') return;
      map.set(warning.op_id, {
        linesOver: Number(warning.lines_over) || 0,
        neededH: typeof warning.needed_h_pt === 'number' ? (warning.needed_h_pt as number) : null,
        collision: warning.reason === 'collision' || (Number(warning.collisions) || 0) > 0,
      });
    });
    Object.entries(previewOverflow).forEach(([opId, value]) => map.set(opId, value));
    return map;
  }, [previewOverflow, session.ops, session.warnings]);

  const renderLayer = useCallback(
    (page: number): ReactNode => {
      const uid = uidOf(page);
      if (!uid) return null;
      const model = models.models.get(uid) ?? null;
      const pageOps = opsByPage.get(uid) ?? [];
      const installed = installedRef.current.get(uid);
      const current = open && open.page === page ? open : null;
      const upToDate = installed && !installed.hide && installed.digest === digestOf(uid);
      const pending: PendingEdit[] = [];
      const overflow: OverflowNotice[] = [];
      if (model) {
        const byId = new Map(model.blocks.map((block) => [block.id, block]));
        pageOps.forEach((op) => {
          if (!isTextOp(op)) return;
          const block = byId.get(op.target.block_id);
          if (!block || block.id === current?.block.id) return;
          const edit = editOf(block, op);
          if (!upToDate) pending.push({ block, edit, mask: masksRef.current.get(keyOf(uid, block.id)) ?? null });
          const info = overflowByOp.get(op.id);
          if (info) {
            overflow.push({ opId: op.id, block, box: edit.box ?? block.bbox, ...info });
          }
        });
        // A block the override hides but no op or editor shows: its own text, until the page is refreshed.
        if (installed?.hide && installed.hide !== current?.block.id && !pageOps.some((op) => isTextOp(op) && op.target.block_id === installed.hide)) {
          const block = byId.get(installed.hide);
          if (block) pending.push({ block, edit: editOf(block, undefined), mask: null });
        }
      }
      let editorNode: ReactNode = null;
      let openMask: { box: BoxPt; color: string } | null = null;
      if (current) {
        const style = styleOf(current);
        const box = boxFor(current, liveText) ?? current.box ?? current.block.bbox;
        const size = flatSizeOf(page) ?? { width: model?.width ?? 612, height: model?.height ?? 792 };
        if (!current.previewReady && current.mask) openMask = { box: current.block.bbox, color: current.mask };
        const scale = viewer.scale;
        editorNode = (
          <>
            <TextBlockEditor
              key={current.token}
              block={current.block}
              scale={scale}
              turn={frameOf(page)?.rotation ?? 0}
              initialText={current.initialText}
              initialSelection={current.selection}
              style={style}
              box={box}
              moving={current.moving}
              pageWidth={size.width}
              pageHeight={size.height}
              onText={setLiveText}
              onCommit={(text) => void commitOpen(text)}
              onCancel={cancelOpen}
              onToggle={(which) => changeStyle({ [which]: !style[which] })}
              onBox={(next) => {
                const latest = openRef.current;
                if (latest) setOpen({ ...latest, box: next, userBox: true });
              }}
              handleRef={handleRef}
            />
            <TextFloatingToolbar
              style={style}
              originalFont={current.block.originalFont}
              substitute={current.block.style.family}
              anchor={{ left: box.x * scale, top: box.y * scale, bottom: (box.y + Math.max(box.h, current.block.bbox.h)) * scale }}
              moving={current.moving}
              onStyle={changeStyle}
              onMove={() => {
                const latest = openRef.current;
                if (latest) setOpen({ ...latest, moving: !latest.moving, box: latest.box ?? boxFor(latest, liveText) ?? latest.block.bbox });
              }}
              onDuplicate={duplicateOpen}
              onDelete={deleteOpen}
              onDone={() => window.setTimeout(() => handleRef.current?.focus(), 0)}
              align={current.align ?? 'left'}
              lineHeight={current.lineHeight}
              onAlign={(align) => {
                const latest = openRef.current;
                if (latest) setOpen({ ...latest, align });
              }}
              onLineHeight={(lineHeight) => {
                const latest = openRef.current;
                if (latest) setOpen({ ...latest, lineHeight });
              }}
              onLink={
                onLinkRef.current
                  ? () => {
                      const latest = openRef.current;
                      if (!latest) return;
                      const at = boxFor(latest, liveText) ?? latest.box ?? latest.block.bbox;
                      onLinkRef.current?.(latest.page, latest.block.pageUid, at);
                    }
                  : undefined
              }
            />
          </>
        );
      }
      return (
        <TextBlockLayer
          pageNumber={page}
          scale={viewer.scale}
          frame={frameOf(page)}
          model={model}
          error={models.errors.get(uid) ?? null}
          editedBlocks={new Set(pageOps.filter(isTextOp).map((op) => op.target.block_id))}
          openBlockId={current?.block.id ?? null}
          pending={pending}
          overflow={overflow}
          openMask={openMask}
          onOpen={(block, point, clicks) => {
            if (!editing) {
              // A click on the text starts the edit session (= «Edit PDF»), when allowed.
              if (session.capabilities && !session.capabilities.can_edit) {
                setNote('This document cannot be edited by you now (see the reason next to «Edit PDF»).');
                return;
              }
              void session.start().then((ok) => {
                if (ok) openBlock(page, block, point, clicks);
              });
              return;
            }
            openBlock(page, block, point, clicks);
          }}
          onFit={fitOp}
          onKeepEditing={(block) => openBlock(page, block, null, 1)}
        >
          {editorNode}
        </TextBlockLayer>
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `installedVersion` says the overrides moved
    [
      boxFor, cancelOpen, changeStyle, commitOpen, deleteOpen, digestOf, duplicateOpen, editing, fitOp, liveText,
      models.errors, models.models, open, openBlock, opsByPage, overflowByOp, flatSizeOf, frameOf, setNote, setOpen,
      styleOf, uidOf, viewer.scale, installedVersion, session,
    ],
  );

  return {
    available,
    renderLayer,
    open,
    commitOpen,
    convertGroup,
    uidOf,
    pageOf,
    models,
  };
}

export type TextEditing = ReturnType<typeof useTextEditing>;
