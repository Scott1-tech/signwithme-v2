import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, tokenStore } from '../../api/client';
import type { DocumentOp, PageOut } from '../../api/document';
import type { AnnotationStore } from '../../hooks/useAnnotations';
import type { Annotation } from '../../lib/annotations';
import type { EditSession } from '../session/useEditSession';
import { newOpId } from '../session/useEditSession';
import type { DocumentOps } from '../session/useDocumentOps';
import { rotateAnnotation } from '../../lib/markupFrames';
import { normalizeQuarter, type PageFrame, type Quarter } from '../../lib/viewport';

/** The whole draft as the server built it (`draft/preview.pdf`), and the turns it was built with. */
interface DraftView {
  bytes: ArrayBuffer;
  uids: string[];
  rotations: Record<string, number>;
  key: string;
}

/**
 * F2 «Sahifalar» (EDITOR-V2-PLAN §7.4, B2 `page.insert / delete / move /
 * rotate`): the document's page STRUCTURE while it is being edited.
 *
 * WHAT THE VIEWER SHOWS
 * ---------------------
 * With no page op in the draft: the current version, as before. With one:
 * the WHOLE draft as the server builds it (`GET …/draft/preview.pdf`, after
 * the autosave has stored the op), its page order from the `X-Page-Uids`
 * header -- never a guess made here. The reader keeps their place: the page
 * they were on is found again by its uid and scrolled to.
 *
 * WHAT MOVES WITH THE PAGES
 * -------------------------
 * The annotations are kept by page NUMBER (the whole editor speaks numbers),
 * so when the order on screen changes they are renumbered by uid -- not an
 * undo step: undoing the page op renumbers them back. The ones on a deleted
 * page are set aside (by uid) and come back if the page does (Undo); they are
 * not sent while the page is gone. Slots, signatures and review issues are
 * the server's, in the CURRENT version's numbers: `displayPage` maps them.
 */

export interface PageEntry {
  uid: string;
  /** 1-based position on screen. */
  position: number;
  /** Position in the current version, null for an inserted page. */
  base: number | null;
  /** The page's absolute rotation in the draft (0 / 90 / 180 / 270). */
  rotation: number;
}

const randomHex = (length: number) => {
  const bytes = new Uint8Array(length / 2);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
};
export const newPageUid = () => `pg_${randomHex(12)}`;

const isPageOp = (op: DocumentOp) => op.kind.startsWith('page.');

/** The page order the ops make of `base` (the server is the authority; this is for the panel's counts and labels). */
export function simulatePages(base: readonly PageOut[], ops: readonly DocumentOp[]): { uid: string; rotation: number }[] {
  let list = [...base].sort((a, b) => a.position - b.position).map((page) => ({ uid: page.page_uid, rotation: page.rotation }));
  ops.forEach((op) => {
    if (op.kind === 'page.insert') {
      const at = op.after_page_uid === null ? 0 : list.findIndex((page) => page.uid === op.after_page_uid) + 1;
      const next = [...list];
      next.splice(Math.max(0, at), 0, { uid: op.new_page_uid, rotation: 0 });
      list = next;
    } else if (op.kind === 'page.delete') {
      list = list.filter((page) => page.uid !== op.page_uid);
    } else if (op.kind === 'page.move') {
      const moving = list.find((page) => page.uid === op.page_uid);
      if (!moving) return;
      const rest = list.filter((page) => page.uid !== op.page_uid);
      const at = op.after_page_uid === null ? 0 : rest.findIndex((page) => page.uid === op.after_page_uid) + 1;
      rest.splice(Math.max(0, at), 0, moving);
      list = rest;
    } else if (op.kind === 'page.rotate') {
      list = list.map((page) => (page.uid === op.page_uid ? { ...page, rotation: op.rotation } : page));
    }
  });
  return list;
}

async function fetchDraftPdf(contractId: string, revision: number): Promise<{ bytes: ArrayBuffer; uids: string[] }> {
  const token = tokenStore.get();
  const response = await fetch(`/api/contracts/${contractId}/document/draft/preview.pdf?revision=${revision}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: 'no-store',
  });
  if (!response.ok) {
    let code = 'preview_failed';
    let message = `The draft preview could not be built (${response.status})`;
    try {
      const body = (await response.json()) as { error?: string; message?: string };
      code = body.error ?? code;
      message = body.message ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, code, message);
  }
  const header = response.headers.get('X-Page-Uids') ?? '';
  const uids = header
    .split(',')
    .map((uid) => uid.trim())
    .filter(Boolean);
  return { bytes: await response.arrayBuffer(), uids };
}

interface Options {
  contractId: string | undefined;
  basePages: readonly PageOut[];
  session: EditSession;
  ops: DocumentOps;
  store: AnnotationStore;
  setNote: (note: string | null) => void;
  /** The viewer's current page and scroll, for keeping the reader's place. */
  currentPage: number;
  scrollToPage: (page: number) => void;
  /** Identity changes when the viewer has laid out a new document. */
  pageSizesKey: unknown;
}

export function usePageStructure({
  contractId,
  basePages,
  session,
  ops,
  store,
  setNote,
  currentPage,
  scrollToPage,
  pageSizesKey,
}: Options) {
  const pageOps = useMemo(() => session.ops.filter(isPageOp), [session.ops]);
  const pageOpsKey = useMemo(() => JSON.stringify(pageOps), [pageOps]);
  const [draftView, setDraftView] = useState<DraftView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = session.mode === 'edit';
  /**
   * ROTFIX: a commit ends the session BEFORE the new version is loaded. Until
   * it is, the screen keeps the draft it was showing (order, turns, bytes) --
   * otherwise the annotations would be renumbered / turned back to the old
   * version for a moment, and a markup save landing in that moment would
   * write them there.
   */
  const [held, setHeld] = useState<(DraftView & { base: readonly PageOut[] }) | null>(null);
  const draftRef = useRef<DraftView | null>(null);
  draftRef.current = draftView && editing && pageOps.length > 0 ? draftView : null;
  useEffect(() => {
    if (held && held.base !== basePages) setHeld(null);
  }, [basePages, held]);
  const sessionEnded = useCallback(
    (reason: 'commit' | 'cancel' | 'conflict') => {
      const view = draftRef.current;
      setHeld(reason === 'commit' && view ? { ...view, base: basePages } : null);
    },
    [basePages],
  );

  const baseUids = useMemo(
    () => [...basePages].sort((a, b) => a.position - b.position).map((page) => page.page_uid),
    [basePages],
  );
  const shownView: DraftView | null = draftView && editing && pageOps.length > 0 ? draftView : held;
  /** What the viewer shows now, by uid. */
  const shownUids = shownView ? shownView.uids : baseUids;
  /** ROTFIX: each shown page's rotation as displayed (the draft's, else the version's). */
  const baseRotation = useCallback(
    (uid: string): Quarter => normalizeQuarter(basePages.find((page) => page.page_uid === uid)?.rotation ?? 0),
    [basePages],
  );
  const shownRotations = useMemo(() => {
    const map = new Map<string, Quarter>();
    shownUids.forEach((uid) => map.set(uid, shownView ? normalizeQuarter(shownView.rotations[uid] ?? 0) : baseRotation(uid)));
    return map;
  }, [shownUids, shownView, baseRotation]);
  /** The page's UNROTATED size (pt): the version's, else (an inserted page) the one before it. */
  const flatSizeOfUid = useCallback(
    (uid: string, order: readonly string[]): { width: number; height: number } => {
      const at = Math.max(0, order.indexOf(uid));
      for (let index = at; index >= 0; index -= 1) {
        const page = basePages.find((entry) => entry.page_uid === order[index]);
        if (page && page.width_pt > 0 && page.height_pt > 0) return { width: page.width_pt, height: page.height_pt };
      }
      const first = basePages.find((entry) => entry.width_pt > 0 && entry.height_pt > 0);
      return first ? { width: first.width_pt, height: first.height_pt } : { width: 612, height: 792 };
    },
    [basePages],
  );

  // The draft's PDF, after the autosave has the op (the preview is revision-checked).
  const flush = session.flush;
  const revisionRef = session.revisionRef;
  const anchorRef = useRef<string | null>(null);
  useEffect(() => {
    if (!contractId || !editing || pageOps.length === 0) {
      setDraftView(null);
      setError(null);
      return undefined;
    }
    let live = true;
    setLoading(true);
    void (async () => {
      try {
        const saved = await flush();
        if (!live) return;
        if (!saved) throw new Error('The page change could not be saved to the draft');
        const view = await fetchDraftPdf(contractId, revisionRef.current);
        if (!live) return;
        // ROTFIX: the turns this preview was built with (the ops as they were asked for).
        const rotations: Record<string, number> = {};
        simulatePages(basePages, pageOps).forEach((page) => {
          rotations[page.uid] = page.rotation;
        });
        setDraftView({ ...view, rotations, key: pageOpsKey });
        setError(null);
      } catch (cause) {
        if (!live) return;
        setError(cause instanceof Error ? cause.message : 'The draft preview could not be built');
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [contractId, editing, pageOpsKey, pageOps.length, flush, revisionRef]);

  // Keep the reader's page: remembered by uid before the order changes.
  const shownRef = useRef(shownUids);
  const lastShown = useRef(shownUids);
  useEffect(() => {
    shownRef.current = shownUids;
  }, [shownUids]);
  useEffect(() => {
    anchorRef.current = shownRef.current[currentPage - 1] ?? null;
  }, [currentPage]);

  // Renumber the annotations when the order on screen changes (not an undo step).
  // `storeUids` is the order the annotations are numbered in RIGHT NOW: it
  // changes in the same batch as the renumbering, so the markup wire (page →
  // uid) never reads a new order against the old numbers.
  //
  // ROTFIX: the same moment turns them. The annotations are kept in the page
  // AS DISPLAYED; when a page's displayed rotation changes (a `page.rotate` of
  // the draft reaching the screen, its undo, a Cancel) its annotations turn
  // with it (`lib/markupFrames.ts rotateAnnotation`) -- and `storeRotations`,
  // the space the wire converts from, changes in the same batch.
  const parked = useRef(new Map<string, { items: Annotation[]; rotation: Quarter }>());
  const lastRotations = useRef(shownRotations);
  const [storeFrame, setStoreFrame] = useState<{ uids: readonly string[]; rotations: ReadonlyMap<string, Quarter> }>(
    () => ({ uids: shownUids, rotations: shownRotations }),
  );
  useEffect(() => {
    const before = lastShown.current;
    const turnedBefore = lastRotations.current;
    const sameOrder =
      before === shownUids || (before.length === shownUids.length && before.every((uid, index) => uid === shownUids[index]));
    const turned = shownUids.filter((uid) => turnedBefore.has(uid) && turnedBefore.get(uid) !== shownRotations.get(uid));
    lastShown.current = shownUids;
    lastRotations.current = shownRotations;
    if (sameOrder && turned.length === 0) {
      setStoreFrame((current) =>
        current.uids === shownUids && current.rotations === shownRotations ? current : { uids: shownUids, rotations: shownRotations },
      );
      return;
    }
    setStoreFrame({ uids: shownUids, rotations: shownRotations });
    const positionOf = new Map(shownUids.map((uid, index) => [uid, index + 1]));
    const turn = (item: Annotation, uid: string, from: Quarter | undefined): Annotation => {
      const to = shownRotations.get(uid);
      if (from === undefined || to === undefined || from === to) return item;
      const size = flatSizeOfUid(uid, shownUids);
      return rotateAnnotation(item, { rotation: to, width: size.width, height: size.height }, from, to);
    };
    const kept: Annotation[] = [];
    let changed = false;
    store.items.forEach((item) => {
      const uid = before[item.page - 1];
      const next = uid ? positionOf.get(uid) : undefined;
      if (uid && next === undefined) {
        const entry = parked.current.get(uid) ?? { items: [], rotation: turnedBefore.get(uid) ?? 0 };
        parked.current.set(uid, { ...entry, items: [...entry.items, item] });
        changed = true;
        return;
      }
      const moved = uid ? turn(item, uid, turnedBefore.get(uid)) : item;
      if (next !== undefined && next !== item.page) {
        kept.push({ ...moved, page: next } as Annotation);
        changed = true;
        return;
      }
      if (moved !== item) changed = true;
      kept.push(moved);
    });
    parked.current.forEach((entry, uid) => {
      const at = positionOf.get(uid);
      if (at === undefined) return;
      entry.items.forEach((item) => kept.push({ ...turn(item, uid, entry.rotation), page: at } as Annotation));
      parked.current.delete(uid);
      changed = true;
    });
    if (changed) store.replace(kept);
  }, [shownUids, shownRotations, store, flatSizeOfUid]);
  const storeUids = storeFrame.uids;
  /** ROTFIX: page N of the annotations (their numbering) -- displayed turn + unrotated size, for the wire. */
  const storeFrameOf = useCallback(
    (page: number): PageFrame | null => {
      const uid = storeFrame.uids[page - 1];
      if (!uid) return null;
      const size = flatSizeOfUid(uid, storeFrame.uids);
      return { rotation: storeFrame.rotations.get(uid) ?? 0, width: size.width, height: size.height };
    },
    [storeFrame, flatSizeOfUid],
  );

  // Back on the page the reader was on, once the new document is laid out.
  const pendingScroll = useRef<string | null>(null);
  useEffect(() => {
    pendingScroll.current = anchorRef.current;
  }, [shownUids]);
  useEffect(() => {
    const uid = pendingScroll.current;
    if (!uid) return;
    const at = shownRef.current.indexOf(uid);
    pendingScroll.current = null;
    if (at >= 0) scrollToPage(at + 1);
  }, [pageSizesKey, scrollToPage]);

  /* -- the ops -------------------------------------------------------------------------- */

  const simulated = useMemo(() => simulatePages(basePages, pageOps), [basePages, pageOps]);
  const rotationOf = useCallback(
    (uid: string) => simulated.find((page) => page.uid === uid)?.rotation ?? 0,
    [simulated],
  );

  const insertAfter = useCallback(
    (position: number) => {
      const after = position <= 0 ? null : (shownUids[position - 1] ?? null);
      const uid = newPageUid();
      void ops.change('Insert page', (list) => [
        ...list,
        { id: newOpId(), kind: 'page.insert', new_page_uid: uid, after_page_uid: after, size: 'same_as_previous' },
      ]);
      setNote(`A blank page will be inserted after page ${position} when you save.`);
    },
    [ops, setNote, shownUids],
  );

  const remove = useCallback(
    (position: number) => {
      const uid = shownUids[position - 1];
      if (!uid) return;
      if (shownUids.length <= 1) {
        setNote('The last page of a document cannot be deleted.');
        return;
      }
      void ops.change('Delete page', (list) => {
        // A page inserted in this draft: its insert (and every op on it) simply goes.
        if (list.some((op) => op.kind === 'page.insert' && op.new_page_uid === uid)) {
          return list.filter(
            (op) =>
              !(op.kind === 'page.insert' && op.new_page_uid === uid) &&
              !('page_uid' in op && op.page_uid === uid),
          );
        }
        return [...list, { id: newOpId(), kind: 'page.delete', page_uid: uid }];
      });
    },
    [ops, setNote, shownUids],
  );

  const move = useCallback(
    (from: number, toAfter: number) => {
      const uid = shownUids[from - 1];
      if (!uid) return;
      const rest = shownUids.filter((entry) => entry !== uid);
      const afterIndex = Math.max(0, Math.min(rest.length, toAfter));
      const after = afterIndex === 0 ? null : rest[afterIndex - 1];
      if (shownUids.indexOf(uid) === afterIndex) return;
      void ops.change('Move page', (list) => [...list, { id: newOpId(), kind: 'page.move', page_uid: uid, after_page_uid: after }]);
    },
    [ops, shownUids],
  );

  const rotate = useCallback(
    (position: number, delta: 90 | -90) => {
      const uid = shownUids[position - 1];
      if (!uid) return;
      const next = ((((rotationOf(uid) + delta) % 360) + 360) % 360) as 0 | 90 | 180 | 270;
      void ops.change('Rotate page', (list) => [
        ...list.filter((op) => !(op.kind === 'page.rotate' && op.page_uid === uid)),
        { id: newOpId(), kind: 'page.rotate', page_uid: uid, rotation: next },
      ]);
    },
    [ops, rotationOf, shownUids],
  );

  /** Page number on screen of a page of the CURRENT version (slots, signatures, issues). */
  const displayPage = useCallback(
    (basePage: number | null | undefined): number | null => {
      if (!basePage) return null;
      if (shownUids === baseUids) return basePage;
      const uid = baseUids[basePage - 1];
      if (!uid) return basePage;
      const at = shownUids.indexOf(uid);
      return at < 0 ? null : at + 1;
    },
    [baseUids, shownUids],
  );

  const entries = useMemo<PageEntry[]>(
    () =>
      shownUids.map((uid, index) => {
        const base = baseUids.indexOf(uid);
        return { uid, position: index + 1, base: base < 0 ? null : base + 1, rotation: rotationOf(uid) };
      }),
    [baseUids, rotationOf, shownUids],
  );

  return {
    pageOps,
    hasPageOps: pageOps.length > 0,
    /** The order the annotations' page numbers refer to (the markup wire's map). */
    storeUids,
    /** ROTFIX: the annotations' page N -- how it is displayed + its unrotated size (the wire's frame). */
    storeFrameOf,
    /** ROTFIX: the rotation page `uid` has in the CURRENT version (the space of slots / signatures / issues). */
    baseRotation,
    /** ROTFIX: tell the page panel how the session ended (a commit holds the draft's view until the reload). */
    sessionEnded,
    draftSource: shownView ? shownView.bytes : null,
    loading,
    error,
    shownUids,
    entries,
    displayPage,
    insertAfter,
    remove,
    move,
    rotate,
  };
}

export type PageStructure = ReturnType<typeof usePageStructure>;
