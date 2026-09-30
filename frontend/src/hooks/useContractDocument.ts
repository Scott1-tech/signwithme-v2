import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchPdf } from '../api/client';
import {
  documentApi,
  isEditorUnavailable,
  type DocumentOut,
  type PageOut,
} from '../api/document';

/**
 * The contract's document: the PDF the viewer shows, and -- F1b -- the
 * editor's view of it (EDITOR-V2-PLAN §6, §7 «Viewer»).
 *
 *  1. `GET /document` (created lazily by the server). It answers the CURRENT
 *     version, the draft (if any) and what this user may do (`capabilities`).
 *  2. The pages of that version (`page_uid` per position: the markup wire and
 *     the text model speak uids).
 *  3. The bytes: version 1 IS the upload (`contracts.file_path`, never
 *     copied), so it keeps coming from `/file` -- the same one request as
 *     before; a later version from `/versions/{no}/file`.
 *
 * A server without the editor (404) or a PDF the engine cannot open (422
 * `pdf_encrypted` / `pdf_corrupt`) falls back to the old `/file` path, and
 * `editor.status` says `unavailable`: everything else in the workspace keeps
 * working exactly as before. Any OTHER failure of the editor calls is also
 * not allowed to cost the reader the document: it falls back the same way,
 * with the reason kept in `editor.reason`.
 *
 * `loadError` is only the PDF fetch's own failure; a page that shows one
 * error line for everything copies it into its own state.
 */

export type EditorStatus = 'loading' | 'ready' | 'unavailable';

export interface EditorDocument {
  status: EditorStatus;
  doc: DocumentOut | null;
  /** The current version's pages, by position (1-based). */
  pages: PageOut[];
  /** Why the editor is off (status `unavailable`), in words. */
  reason: string | null;
}

const LOADING: EditorDocument = { status: 'loading', doc: null, pages: [], reason: null };

export interface ContractDocument {
  source: ArrayBuffer | null;
  loadError: string | null;
  editor: EditorDocument;
  /** The version number `source` holds (null: the legacy `/file`). */
  sourceVersion: number | null;
  /**
   * Re-read `GET /document`; when the current version moved on (a commit),
   * the new version's pages and bytes are fetched and `source` is replaced.
   * Resolves once everything is in.
   */
  reload: () => Promise<void>;
}

const describe = (cause: unknown, fallback: string) =>
  cause instanceof Error && cause.message ? cause.message : fallback;

export function useContractDocument(contractId: string | undefined): ContractDocument {
  const [source, setSource] = useState<ArrayBuffer | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorDocument>(LOADING);
  const [sourceVersion, setSourceVersion] = useState<number | null>(null);
  const versionRef = useRef<number | null>(null);
  const liveFor = useRef<string | undefined>(contractId);
  liveFor.current = contractId;

  const bytesFor = useCallback(
    (id: string, versionNo: number | null) =>
      versionNo === null || versionNo === 1
        ? fetchPdf(`/api/contracts/${id}/file`)
        : documentApi.versionFile(id, versionNo),
    [],
  );

  const load = useCallback(
    async (id: string, initial: boolean) => {
      let doc: DocumentOut | null = null;
      let pages: PageOut[] = [];
      let reason: string | null = null;
      try {
        doc = await documentApi.get(id);
        pages = await documentApi.pages(id, doc.current_version.version_no);
      } catch (cause) {
        doc = null;
        pages = [];
        reason = isEditorUnavailable(cause)
          ? 'This server has no document editor for this file'
          : describe(cause, 'The document editor could not be reached');
      }
      if (liveFor.current !== id) return;
      const versionNo = doc ? doc.current_version.version_no : null;
      if (initial || versionNo !== versionRef.current) {
        try {
          const data = await bytesFor(id, versionNo);
          if (liveFor.current !== id) return;
          versionRef.current = versionNo;
          setSourceVersion(versionNo);
          setSource(data);
          setLoadError(null);
        } catch (cause) {
          if (liveFor.current !== id) return;
          if (doc && versionNo !== null && versionNo > 1) {
            // The version file would not come: the reader still gets the upload.
            try {
              const data = await fetchPdf(`/api/contracts/${id}/file`);
              if (liveFor.current !== id) return;
              versionRef.current = null;
              setSourceVersion(null);
              setSource(data);
              doc = null;
              pages = [];
              reason = describe(cause, 'The current version could not be loaded');
            } catch (again) {
              setLoadError(describe(again, 'Failed to load PDF'));
            }
          } else {
            setLoadError(describe(cause, 'Failed to load PDF'));
          }
        }
      }
      if (liveFor.current !== id) return;
      setEditor(
        doc
          ? { status: 'ready', doc, pages, reason: null }
          : { status: 'unavailable', doc: null, pages: [], reason },
      );
    },
    [bytesFor],
  );

  useEffect(() => {
    if (!contractId) return;
    setEditor(LOADING);
    versionRef.current = null;
    void load(contractId, true);
  }, [contractId, load]);

  const reload = useCallback(async () => {
    if (!contractId) return;
    await load(contractId, false);
  }, [contractId, load]);

  return { source, loadError, editor, sourceVersion, reload };
}
