import { useCallback, useMemo, useRef } from 'react';
import type { DocumentOp, Rect } from '../../api/document';
import type { RectPt } from '../tools/types';
import { newOpId, type EditSession } from './useEditSession';

/**
 * F2: the structure / content ops that are not text edits (EDITOR-V2-PLAN
 * §3): `redact`, `image.remove`, `link.*` and `page.*`. They live in the edit
 * session's ONE op list (`session.changeOps`: one `'ops'` history step and
 * one autosave per change) next to the text edits.
 *
 * A change made while the document is only being VIEWED starts the session
 * first (like a click on a text block does): the op then lands in the new
 * draft. Nothing here writes to the server itself.
 */

export const toRect = (box: RectPt): Rect => ({
  x_pt: Math.round(box.x * 100) / 100,
  y_pt: Math.round(box.y * 100) / 100,
  w_pt: Math.round(box.w * 100) / 100,
  h_pt: Math.round(box.h * 100) / 100,
});

interface Options {
  session: EditSession;
  /** The page uids as the viewer shows them now (index = position − 1), read at call time. */
  shownUids: () => readonly string[];
  setNote: (note: string | null) => void;
}

export function useDocumentOps({ session, shownUids, setNote }: Options) {
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const shownRef = useRef(shownUids);
  shownRef.current = shownUids;

  const uidOf = useCallback((page: number): string | null => shownRef.current()[page - 1] ?? null, []);
  const pageOf = useCallback((uid: string): number | null => {
    const at = shownRef.current().indexOf(uid);
    return at < 0 ? null : at + 1;
  }, []);

  /** The session, started if need be. False when it could not start (the reason is on screen). */
  const ensureSession = useCallback(async (): Promise<boolean> => {
    const current = sessionRef.current;
    if (current.mode === 'edit') return true;
    if (!current.available || !current.capabilities?.can_edit) {
      setNote(
        current.capabilities?.reason === 'not_permitted'
          ? 'Only the uploader, a reviewer or an admin can change the document itself.'
          : 'The document cannot be edited right now.',
      );
      return false;
    }
    const started = await current.start();
    return Boolean(started);
  }, [setNote]);

  /** One user change of the op list (starts the session if needed). */
  const change = useCallback(
    async (label: string, update: (list: DocumentOp[]) => DocumentOp[]) => {
      if (!(await ensureSession())) return false;
      sessionRef.current.changeOps(label, update);
      return true;
    },
    [ensureSession],
  );

  const redact = useCallback(
    (page: number, box: RectPt) => {
      const uid = uidOf(page);
      if (!uid) return;
      void change('Redact', (list) => [
        ...list,
        { id: newOpId(), kind: 'redact', page_uid: uid, rects: [toRect(box)], fill: 'black' },
      ]).then((done) => {
        if (done) setNote('Redacted: the content under the black box is removed from the file when you save.');
      });
    },
    [change, setNote, uidOf],
  );

  const removeImage = useCallback(
    (uid: string, xref: number, bbox: Rect) =>
      change('Delete original image', (list) =>
        list.some((op) => op.kind === 'image.remove' && op.page_uid === uid && op.xref === xref)
          ? list
          : [...list, { id: newOpId(), kind: 'image.remove', page_uid: uid, xref, bbox }],
      ),
    [change],
  );

  const dropOp = useCallback(
    (opId: string, label: string) => change(label, (list) => list.filter((op) => op.id !== opId)),
    [change],
  );

  const ops = session.ops;
  const byKind = useMemo(() => {
    const map = new Map<string, DocumentOp[]>();
    ops.forEach((op) => {
      const list = map.get(op.kind);
      if (list) list.push(op);
      else map.set(op.kind, [op]);
    });
    return map;
  }, [ops]);

  return { uidOf, pageOf, ensureSession, change, redact, removeImage, dropOp, byKind, ops };
}

export type DocumentOps = ReturnType<typeof useDocumentOps>;
