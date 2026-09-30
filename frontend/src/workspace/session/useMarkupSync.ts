import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { documentApi, isDocumentError, type MarkupElement } from '../../api/document';
import type { EditorDocument } from '../../hooks/useContractDocument';
import type { AnnotationStore } from '../../hooks/useAnnotations';
import { annotationsStorageKey, loadAnnotations, type Annotation } from '../../lib/annotations';
import { fromWire, sameWire, toWire } from '../../lib/markupWire';
import type { PageFrame } from '../../lib/viewport';

/**
 * F1b (EDITOR-V2-PLAN §3 «Markup», §3.4): the annotations live on the SERVER
 * (`documents.markup`), not only in this browser.
 *
 * FIRST CONTACT -- the localStorage migration, never a silent loss:
 *  * the server has nothing (`revision 0`) and this browser has annotations
 *    → they are PUT as they are; the browser's key is then RENAMED to
 *    `swm.annotations.<cid>.migrated` (kept, never deleted);
 *  * the server has markup and the browser has the same → nothing to ask;
 *  * both have annotations and they DIFFER → one dialog: «Import from this
 *    browser» (the browser's list replaces the server's) or «Keep the
 *    server's» (the browser's list is kept aside under `.migrated`);
 *  * the server has no editor (404, `editor.status === 'unavailable'`) →
 *    the old way: localStorage only, exactly as before F1b.
 *
 * AFTER: every change is PUT (debounced), revision-checked. While an edit
 * session holds the draft the server refuses a markup PUT (409
 * `draft_active`) -- the markup then travels WITH the draft
 * (`session.markupTouched`) and is written by the commit; a Cancel puts the
 * server's markup back.
 */

export const MARKUP_DEBOUNCE_MS = 800;
export const migratedKey = (contractId: string) => `${annotationsStorageKey(contractId)}.migrated`;

export type MarkupSyncStatus = 'local' | 'loading' | 'synced' | 'saving' | 'error' | 'blocked';

export interface MigrationPrompt {
  localCount: number;
  serverCount: number;
  local: MarkupElement[];
  server: MarkupElement[];
  serverRevision: number;
}

interface Options {
  contractId: string | undefined;
  editor: EditorDocument;
  store: AnnotationStore;
  /** An edit session holds the draft: changes travel with it. */
  inSession: boolean;
  onSessionChange: () => void;
  setNote: (note: string | null) => void;
  /**
   * ROTFIX: how the page with this number (the store's numbering) is shown:
   * its rotation as displayed + unrotated size. A v7 element is turned
   * between that and the engine's unrotated page at the wire. Read at call time.
   */
  frameOf?: (page: number) => PageFrame | null;
}

function renameToMigrated(contractId: string) {
  try {
    const raw = localStorage.getItem(annotationsStorageKey(contractId));
    if (raw === null) return;
    localStorage.setItem(migratedKey(contractId), raw);
    localStorage.removeItem(annotationsStorageKey(contractId));
  } catch {
    /* private mode */
  }
}

export function useMarkupSync({ contractId, editor, store, inSession, onSessionChange, setNote, frameOf }: Options) {
  const frameOfRef = useRef(frameOf);
  frameOfRef.current = frameOf;
  const frameAt = useCallback((page: number) => frameOfRef.current?.(page) ?? null, []);
  const [status, setStatus] = useState<MarkupSyncStatus>(editor.status === 'unavailable' ? 'local' : 'loading');
  const [prompt, setPrompt] = useState<MigrationPrompt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const revisionRef = useRef(0);
  /** The wire the server has (as far as we know). */
  const serverWireRef = useRef<MarkupElement[] | null>(null);
  const timerRef = useRef(0);
  const savingRef = useRef(false);
  const againRef = useRef(false);
  const storeRef = useRef(store);
  storeRef.current = store;
  const inSessionRef = useRef(inSession);
  inSessionRef.current = inSession;
  const sessionChangeRef = useRef(onSessionChange);
  sessionChangeRef.current = onSessionChange;

  const pages = editor.pages;
  // F2: read at CALL time -- a save that answers after the pages were
  // renumbered (a page op, a commit) must map the annotations as they are
  // numbered now, never with the order of the render that started it.
  const pagesRef = useRef(pages);
  pagesRef.current = pages;
  const uidOf = useCallback(
    (page: number) => pagesRef.current.find((p) => p.position === page)?.page_uid ?? null,
    [],
  );
  const pageOf = useCallback(
    (uid: string) => pagesRef.current.find((p) => p.page_uid === uid)?.position ?? null,
    [],
  );

  const synced = status === 'synced' || status === 'saving' || status === 'error' || status === 'blocked';
  /** Server-backed from now on: the store stops writing `swm.annotations.<cid>`. */
  const serverBacked = editor.status === 'ready' && status !== 'local' && status !== 'loading';

  const wireOf = useCallback(
    (items: readonly Annotation[]) => {
      const { elements, unplaced } = toWire(items, uidOf, frameAt);
      return unplaced.length ? null : elements;
    },
    [uidOf, frameAt],
  );

  /** What the markup is now, for the server (null while a page has no uid). */
  const currentWire = useCallback(() => wireOf(storeRef.current.items), [wireOf]);
  const changedSinceServer = useCallback(() => {
    const now = currentWire();
    const server = serverWireRef.current;
    return Boolean(now && server && !sameWire(now, server));
  }, [currentWire]);

  const put = useCallback(
    async (elements: MarkupElement[]): Promise<boolean> => {
      if (!contractId) return false;
      setStatus('saving');
      try {
        const out = await documentApi.saveMarkup(contractId, revisionRef.current, elements);
        revisionRef.current = out.revision;
        serverWireRef.current = elements;
        setStatus('synced');
        setError(null);
        if (out.review_reset) {
          setNote('The contract was sent back to review: a change of the annotations affects its content.');
        }
        return true;
      } catch (cause) {
        if (isDocumentError(cause, 'draft_active')) {
          setStatus('blocked');
          setError('Someone is editing this document; your annotations are kept here and saved when they finish.');
          return false;
        }
        if (isDocumentError(cause, 'revision_conflict')) {
          // Someone else saved markup: offer the choice instead of overwriting.
          try {
            const fresh = await documentApi.markup(contractId);
            revisionRef.current = fresh.revision;
            serverWireRef.current = fresh.elements;
            if (!sameWire(fresh.elements, elements)) {
              setPrompt({
                localCount: elements.length,
                serverCount: fresh.elements.length,
                local: elements,
                server: fresh.elements,
                serverRevision: fresh.revision,
              });
            }
            setStatus('synced');
          } catch {
            setStatus('error');
          }
          return false;
        }
        setStatus('error');
        setError(cause instanceof ApiError ? cause.message : 'The annotations could not be saved to the server');
        // Kept in this browser (the legacy key): the next visit offers to import them.
        try {
          if (contractId) localStorage.setItem(annotationsStorageKey(contractId), JSON.stringify(storeRef.current.items));
        } catch {
          /* private mode */
        }
        return false;
      }
    },
    [contractId, setNote],
  );

  // First contact.
  const contactFor = useRef<string | null>(null);
  useEffect(() => {
    if (!contractId) return;
    if (editor.status === 'unavailable') {
      setStatus('local');
      return;
    }
    if (editor.status !== 'ready' || !editor.doc || pages.length === 0) return;
    const key = `${contractId}|${editor.doc.document_id}`;
    if (contactFor.current === key) return;
    contactFor.current = key;
    let live = true;
    // F2: a first contact cut short (the deps moved while it was in flight --
    // e.g. the page order settling) is tried again, not forgotten.
    let finished = false;
    void (async () => {
      try {
        const server = await documentApi.markup(contractId);
        if (!live) return;
        revisionRef.current = server.revision;
        serverWireRef.current = server.elements;
        const localItems = loadAnnotations(contractId);
        const localWire = localItems.length ? wireOf(localItems) : [];
        if (server.revision === 0 && server.elements.length === 0) {
          if (localWire && localWire.length > 0) {
            if (await put(localWire)) renameToMigrated(contractId);
            return;
          }
          setStatus('synced');
          return;
        }
        const { items: serverItems } = fromWire(server.elements, pageOf, frameAt);
        if (!localWire || localWire.length === 0 || sameWire(localWire, server.elements)) {
          storeRef.current.replace(serverItems);
          renameToMigrated(contractId);
          setStatus('synced');
          return;
        }
        // Both have annotations, and they differ: the user decides.
        storeRef.current.replace(serverItems);
        setPrompt({
          localCount: localWire.length,
          serverCount: server.elements.length,
          local: localWire,
          server: server.elements,
          serverRevision: server.revision,
        });
        setStatus('synced');
      } catch (cause) {
        if (!live) return;
        if (cause instanceof ApiError && cause.status === 404) {
          setStatus('local');
          return;
        }
        setStatus('error');
        setError(cause instanceof Error ? cause.message : 'The annotations could not be loaded from the server');
      } finally {
        if (live) finished = true;
      }
    })();
    return () => {
      live = false;
      if (!finished) contactFor.current = null;
    };
  }, [contractId, editor.doc, editor.status, pages.length, pageOf, put, wireOf]);

  const importLocal = useCallback(async () => {
    const current = prompt;
    if (!current || !contractId) return;
    setPrompt(null);
    revisionRef.current = current.serverRevision;
    const { items } = fromWire(current.local, pageOf, frameAt);
    storeRef.current.replace(items);
    if (inSessionRef.current) {
      sessionChangeRef.current();
      renameToMigrated(contractId);
      return;
    }
    if (await put(current.local)) renameToMigrated(contractId);
  }, [contractId, pageOf, prompt, put]);

  const keepServer = useCallback(() => {
    const current = prompt;
    if (!current || !contractId) return;
    setPrompt(null);
    const { items } = fromWire(current.server, pageOf, frameAt);
    storeRef.current.replace(items);
    renameToMigrated(contractId);
  }, [contractId, pageOf, prompt]);

  // Every later change: PUT (debounced) -- or, inside a session, with the draft.
  const lastItems = useRef(store.items);
  useEffect(() => {
    if (store.items === lastItems.current) return;
    lastItems.current = store.items;
    if (!synced || prompt) return;
    if (inSessionRef.current) {
      sessionChangeRef.current();
      return;
    }
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      const send = async () => {
        if (savingRef.current) {
          againRef.current = true;
          return;
        }
        const wire = currentWire();
        if (!wire || (serverWireRef.current && sameWire(wire, serverWireRef.current))) return;
        savingRef.current = true;
        await put(wire);
        savingRef.current = false;
        if (againRef.current) {
          againRef.current = false;
          await send();
        }
      };
      void send();
    }, MARKUP_DEBOUNCE_MS);
  }, [store.items, synced, prompt, currentWire, put]);

  useEffect(() => () => window.clearTimeout(timerRef.current), []);

  /**
   * F2: save what is pending NOW (before finalize / an export: the server
   * draws its own markup then). True when the server has the current list.
   */
  const flush = useCallback(async (): Promise<boolean> => {
    window.clearTimeout(timerRef.current);
    if (status === 'local' || inSessionRef.current) return true;
    for (let wait = 0; savingRef.current && wait < 100; wait += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
    const wire = currentWire();
    if (!wire) return false;
    if (serverWireRef.current && sameWire(wire, serverWireRef.current)) return true;
    return put(wire);
  }, [currentWire, put, status]);

  /**
   * After the session: re-read the server's markup. `adopt` = put it into the
   * store (a Cancel threw the session's markup changes away); otherwise only
   * the revision is refreshed (a commit wrote exactly what is in the store).
   */
  const refresh = useCallback(
    async (adopt: boolean) => {
      if (!contractId || status === 'local') return;
      try {
        const fresh = await documentApi.markup(contractId);
        revisionRef.current = fresh.revision;
        serverWireRef.current = fresh.elements;
        if (adopt) {
          storeRef.current.replace(fromWire(fresh.elements, pageOf, frameAt).items);
        } else {
          // Anything changed after the commit's snapshot is saved now.
          const wire = currentWire();
          if (wire && !sameWire(wire, fresh.elements)) void put(wire);
        }
        setStatus('synced');
        setError(null);
      } catch {
        /* the next change retries */
      }
    },
    [contractId, currentWire, pageOf, put, status],
  );

  return useMemo(
    () => ({
      status,
      error,
      prompt,
      serverBacked,
      importLocal,
      keepServer,
      currentWire,
      changedSinceServer,
      refresh,
      flush,
    }),
    [status, error, prompt, serverBacked, importLocal, keepServer, currentWire, changedSinceServer, refresh, flush],
  );
}

export type MarkupSync = ReturnType<typeof useMarkupSync>;
