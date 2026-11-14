import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import {
  LEASE_RENEW_SECONDS,
  documentApi,
  isDocumentError,
  type CommitOut,
  type DocumentOp,
  type DraftOut,
  type EngineWarning,
  type MarkupElement,
} from '../../api/document';
import type { EditorDocument } from '../../hooks/useContractDocument';
import type { DocumentHistory } from '../state/useDocumentHistory';
import {
  readLocalDraft,
  useDraftAutosave,
  writeLocalDraft,
  type AutosaveFailure,
  type LocalDraft,
} from './useDraftAutosave';

/**
 * F1b (EDITOR-V2-PLAN §7 «Sessiya»): the edit session of the document.
 *
 *   view --[Edit PDF]--> starting --> edit (saved | dirty | saving | offline)
 *   edit --[Save changes]--> committing --> view (a new version)
 *   edit --[Cancel editing]--> view (the draft is deleted)
 *
 * The ops live HERE (one list, one id per edited block); every change is one
 * `'ops'` step of the workspace's history and one autosave. The server is
 * the owner of the draft: a 409 (someone took it over, the revision moved,
 * a newer version appeared) stops everything and opens the conflict dialog --
 * the user's work is never thrown away without a choice (the local copy
 * `swm.draft.<cid>` stays, and «Download my changes» hands it out as JSON).
 */

export type SessionMode = 'view' | 'edit';
export type SessionStatus =
  | 'idle'
  | 'starting'
  | 'saving'
  | 'saved'
  | 'dirty'
  | 'offline'
  | 'conflict'
  | 'committing'
  | 'error';

export interface RecoveryOffer {
  /** `server`: a draft of mine is on the server; `local`: only this browser has edits. */
  source: 'server' | 'local';
  /** When the edits were last touched (ms), for «Unsaved changes from 14:32». */
  at: number;
  ops: number;
  /** A newer local copy exists next to a server draft: «Restore from this browser». */
  localNewer: boolean;
}

export interface ConflictState {
  code: string;
  message: string;
}

export interface LockState {
  owner: string | null;
  until: string | null;
}

export interface CommitNotice {
  out: CommitOut;
  signaturesRemoved: number;
}

interface Options {
  contractId: string | undefined;
  editor: EditorDocument;
  /** Re-read the document (after a commit / a conflict); `holdView` keeps the reader's place (a new version). */
  reloadDocument: (options?: { holdView?: boolean }) => Promise<void>;
  history: DocumentHistory;
  /** The markup as the server would store it now, or null while it is not loadable. */
  markupWire: () => MarkupElement[] | null;
  /** Whether the markup changed since the session started (then it travels with the draft). */
  markupChanged: () => boolean;
  /** A commit landed (review reset, signatures removed, reparse): the workspace reloads what depends on it. */
  onCommitted: (notice: CommitNotice) => void;
  /** The session ended (saved, cancelled, conflict resolved): markup sync resumes. */
  onEnded?: (reason: 'commit' | 'cancel' | 'conflict') => void;
}

const SESSION_KEY = (cid: string) => `swm.editSession.${cid}`;

function randomId(length = 24): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join('');
}

/** One id per browser tab and contract (kept across a reload of the tab). */
function tabSessionId(contractId: string | undefined): string {
  if (!contractId) return randomId();
  try {
    const stored = sessionStorage.getItem(SESSION_KEY(contractId));
    if (stored && /^[A-Za-z0-9_-]{8,64}$/.test(stored)) return stored;
    const fresh = randomId();
    sessionStorage.setItem(SESSION_KEY(contractId), fresh);
    return fresh;
  } catch {
    return randomId();
  }
}

export const newOpId = () => `op_${randomId(12)}`;

function describeError(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) return cause.message || fallback;
  return cause instanceof Error ? cause.message : fallback;
}

/** Like `isDocumentError` but not a type guard (several checks in a row must not narrow to `never`). */
const hasCode = (cause: unknown, code: string): boolean => cause instanceof ApiError && cause.code === code;

const sameOps = (a: readonly DocumentOp[], b: readonly DocumentOp[]) =>
  a.length === b.length && JSON.stringify(a) === JSON.stringify(b);

export function useEditSession({
  contractId,
  editor,
  reloadDocument,
  history,
  markupWire,
  markupChanged,
  onCommitted,
  onEnded,
}: Options) {
  const sessionId = useMemo(() => tabSessionId(contractId), [contractId]);
  const [mode, setMode] = useState<SessionMode>('view');
  const [status, setStatus] = useState<SessionStatus>('idle');
  const [ops, setOpsState] = useState<DocumentOp[]>([]);
  const opsRef = useRef<DocumentOp[]>([]);
  const revisionRef = useRef(0);
  const baseVersionRef = useRef(0);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  const [lock, setLock] = useState<LockState | null>(null);
  const [signaturesPrompt, setSignaturesPrompt] = useState<{ count: number } | null>(null);
  const [recovery, setRecovery] = useState<RecoveryOffer | null>(null);
  const [warnings, setWarnings] = useState<EngineWarning[]>([]);
  /** 422 answers per op id (`invalid_ops` from a PUT, an engine refusal at commit). */
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [revision, setRevision] = useState(0);
  const commitIdRef = useRef<string | null>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const markupWireRef = useRef(markupWire);
  markupWireRef.current = markupWire;
  const markupChangedRef = useRef(markupChanged);
  markupChangedRef.current = markupChanged;

  const doc = editor.doc;
  const currentVersion = doc?.current_version.version_no ?? 0;
  const capabilities = doc?.capabilities ?? null;

  const payload = useCallback(
    () => ({
      ops: opsRef.current,
      markup: markupChangedRef.current() ? markupWireRef.current() : null,
    }),
    [],
  );

  const saveLocal = useCallback(
    (acked: boolean) => {
      const { ops: list, markup } = payload();
      if (list.length === 0 && markup === null) {
        writeLocalDraft(contractId, null);
        return;
      }
      const draft: LocalDraft = {
        v: 1,
        base_version_no: baseVersionRef.current,
        revision: revisionRef.current,
        ops: list,
        markup,
        changed_at: Date.now(),
        acked,
      };
      writeLocalDraft(contractId, draft);
    },
    [contractId, payload],
  );

  const onFailure = useCallback((failure: AutosaveFailure) => {
    if (failure.kind === 'offline') {
      setStatus('offline');
      return;
    }
    const cause = failure.error;
    if (
      hasCode(cause, 'revision_conflict') ||
      hasCode(cause, 'draft_taken_over') ||
      hasCode(cause, 'base_outdated') ||
      hasCode(cause, 'draft_not_found')
    ) {
      setStatus('conflict');
      setConflict({
        code: cause.code,
        message: hasCode(cause, 'draft_taken_over')
          ? 'This document is now being edited in another window or by another user.'
          : hasCode(cause, 'base_outdated')
            ? 'A newer version of this document was saved while you were editing.'
            : hasCode(cause, 'draft_not_found')
              ? 'Your draft is no longer on the server (it was cancelled elsewhere).'
              : 'Your draft was changed somewhere else while you were editing.',
      });
      return;
    }
    if (hasCode(cause, 'invalid_ops')) {
      const list = (cause.details.ops as Array<{ id: string; reason: string }> | undefined) ?? [];
      const map: Record<string, string> = {};
      list.forEach((item) => {
        if (item && typeof item.id === 'string') map[item.id] = String(item.reason);
      });
      setProblems(map);
      setStatus('error');
      setError(`${list.length || 'Some'} edit${list.length === 1 ? '' : 's'} cannot be applied — fix or undo ${list.length === 1 ? 'it' : 'them'}.`);
      return;
    }
    setStatus('error');
    setError(describeError(cause, 'The draft could not be saved'));
  }, []);

  const autosave = useDraftAutosave({
    contractId,
    sessionId,
    payload,
    revisionRef,
    active: mode === 'edit',
    onSaving: () => setStatus('saving'),
    onSaved: (out, stillDirty) => {
      setRevision(out.revision);
      setSavedAt(Date.now());
      setWarnings(out.warnings ?? []);
      setProblems({});
      setError(null);
      setStatus(stillDirty ? 'dirty' : 'saved');
      if (!stillDirty) saveLocal(true);
    },
    onFailure,
  });

  /* -- ops ------------------------------------------------------------------ */

  const applyOps = useCallback((next: DocumentOp[]) => {
    opsRef.current = next;
    setOpsState(next);
  }, []);

  /** A user change of the op list: ONE `'ops'` history step + autosave. */
  const changeOps = useCallback(
    (label: string, update: (current: DocumentOp[]) => DocumentOp[]) => {
      if (modeRef.current !== 'edit') return;
      const before = opsRef.current;
      const after = update(before);
      if (after === before || sameOps(after, before)) return;
      applyOps(after);
      history.record({ channel: 'ops', label, before, after });
      setStatus('dirty');
      saveLocal(false);
      autosave.markDirty();
    },
    [applyOps, autosave, history, saveLocal],
  );

  /** The markup changed during the session: it travels with the next PUT. */
  const markupTouched = useCallback(() => {
    if (modeRef.current !== 'edit') return;
    setStatus('dirty');
    saveLocal(false);
    autosave.markDirty();
  }, [autosave, saveLocal]);

  // Undo / redo of an op step.
  const { register, dropChannel } = history;
  useEffect(
    () =>
      register('ops', (value) => {
        if (modeRef.current !== 'edit') return;
        applyOps(value as DocumentOp[]);
        setStatus('dirty');
        saveLocal(false);
        autosave.markDirty();
      }),
    [register, applyOps, autosave, saveLocal],
  );

  /* -- start / resume ------------------------------------------------------- */

  const adopt = useCallback(
    (draft: DraftOut) => {
      revisionRef.current = draft.revision;
      baseVersionRef.current = draft.base_version_no;
      setRevision(draft.revision);
      applyOps(draft.ops ?? []);
      // Read by the callers that continue right after `start()` resolves.
      modeRef.current = 'edit';
      setMode('edit');
      setStatus('saved');
      setSavedAt(Date.now());
      setError(null);
      setConflict(null);
      setLock(null);
      setRecovery(null);
      setProblems({});
      setWarnings([]);
    },
    [applyOps],
  );

  const begin = useCallback(
    async (options: { takeOver?: boolean; restore?: LocalDraft | null } = {}) => {
      if (!contractId || !doc) return false;
      setStatus('starting');
      setError(null);
      try {
        const draft = await documentApi.startDraft(contractId, {
          base_version_no: currentVersion,
          client_session_id: sessionId,
          ...(options.takeOver ? { take_over: true } : {}),
        });
        adopt(draft);
        const restore = options.restore;
        if (restore && (restore.ops.length > 0 || restore.markup)) {
          // «Restore from this browser»: the local list replaces the server's, one step.
          const before = opsRef.current;
          applyOps(restore.ops);
          history.record({ channel: 'ops', label: 'Restore edits from this browser', before, after: restore.ops });
          setStatus('dirty');
          autosave.markDirty();
        }
        return true;
      } catch (cause) {
        if (isDocumentError(cause, 'draft_locked')) {
          setStatus('idle');
          setLock({
            owner: (cause.details.owner_name as string | undefined) ?? null,
            until: (cause.details.lease_expires_at as string | undefined) ?? null,
          });
          return false;
        }
        if (isDocumentError(cause, 'base_outdated')) {
          setStatus('idle');
          setError('A newer version was saved meanwhile — the document was reloaded; press Edit PDF again.');
          await reloadDocument();
          return false;
        }
        setStatus('error');
        setError(describeError(cause, 'Could not start editing'));
        return false;
      }
    },
    [adopt, applyOps, autosave, contractId, currentVersion, doc, history, reloadDocument, sessionId],
  );

  const start = useCallback(() => begin({}), [begin]);
  const takeOver = useCallback(() => begin({ takeOver: true }), [begin]);

  // What was left behind: a server draft of mine, and/or a local copy.
  const offeredFor = useRef<string | null>(null);
  useEffect(() => {
    if (!contractId || editor.status !== 'ready' || !doc) return;
    const key = `${contractId}|${currentVersion}`;
    if (offeredFor.current === key || modeRef.current === 'edit') return;
    offeredFor.current = key;
    const local = readLocalDraft(contractId);
    const localUsable = local && local.base_version_no === currentVersion && (local.ops.length > 0 || local.markup);
    const server = doc.draft;
    if (server && server.is_mine) {
      const at = new Date(server.lease_expires_at).getTime() - 120_000;
      setRecovery({
        source: 'server',
        at: Number.isFinite(at) ? at : Date.now(),
        ops: -1,
        localNewer: Boolean(localUsable && local && !local.acked),
      });
      return;
    }
    if (server && !server.is_mine) {
      setLock({ owner: server.owner_name, until: server.lease_expires_at });
    }
    if (!server && localUsable && local) {
      setRecovery({ source: 'local', at: local.changed_at, ops: local.ops.length, localNewer: true });
    }
  }, [contractId, currentVersion, doc, editor.status]);

  const resume = useCallback(() => begin({ takeOver: true }), [begin]);
  const restoreLocal = useCallback(() => {
    const local = readLocalDraft(contractId);
    return begin({ takeOver: true, restore: local });
  }, [begin, contractId]);

  /** «Discard» on the recovery banner: the server draft (if mine) and the local copy go. */
  const discardRecovered = useCallback(async () => {
    if (!contractId) return;
    const server = doc?.draft;
    setRecovery(null);
    writeLocalDraft(contractId, null);
    if (server && server.is_mine) {
      try {
        await documentApi.discardDraft(contractId, server.revision);
      } catch (cause) {
        setError(describeError(cause, 'Could not discard the draft'));
      }
      await reloadDocument();
    }
  }, [contractId, doc?.draft, reloadDocument]);

  /* -- lease ------------------------------------------------------------------ */

  useEffect(() => {
    if (mode !== 'edit' || !contractId) return undefined;
    const timer = window.setInterval(() => {
      if (autosave.isDirty()) return; // the PUT renews the lease itself
      documentApi.renewLease(contractId, revisionRef.current, sessionId).catch((cause) => {
        if (isDocumentError(cause, 'draft_taken_over') || isDocumentError(cause, 'draft_not_found')) {
          onFailure({ kind: 'answer', error: cause as ApiError });
        }
      });
    }, LEASE_RENEW_SECONDS * 1000);
    return () => window.clearInterval(timer);
  }, [autosave, contractId, mode, onFailure, sessionId]);

  /* -- end: save / cancel ------------------------------------------------------- */

  const finish = useCallback((reason: 'commit' | 'cancel' | 'conflict') => {
    autosave.stop();
    applyOps([]);
    dropChannel('ops');
    writeLocalDraft(contractId, null);
    modeRef.current = 'view';
    setMode('view');
    setStatus('idle');
    setProblems({});
    setWarnings([]);
    setSignaturesPrompt(null);
    commitIdRef.current = null;
    onEnded?.(reason);
  }, [applyOps, autosave, contractId, dropChannel, onEnded]);

  const commit = useCallback(
    async (discardSignatures: boolean) => {
      if (!contractId) return;
      setStatus('committing');
      setError(null);
      const flushed = await autosave.flush();
      if (!flushed) {
        setStatus((current) => (current === 'committing' ? 'error' : current));
        setError((current) => current ?? 'The last changes could not be saved; nothing was committed.');
        return;
      }
      if (!commitIdRef.current) commitIdRef.current = `c_${randomId(20)}`;
      const body = {
        revision: revisionRef.current,
        commit_request_id: commitIdRef.current,
        ...(discardSignatures ? { discard_signatures: true } : {}),
      };
      let out: CommitOut | null = null;
      for (let attempt = 0; attempt < 3 && !out; attempt += 1) {
        try {
          out = await documentApi.commit(contractId, body);
        } catch (cause) {
          if (cause instanceof ApiError && cause.status < 500 && cause.status !== 0) {
            if (hasCode(cause, 'signatures_present')) {
              setSignaturesPrompt({ count: Number(cause.details.count) || 0 });
              setStatus('saved');
              return;
            }
            if (hasCode(cause, 'text_overflow') || hasCode(cause, 'stale_target') ||
                hasCode(cause, 'undrawable_character') || hasCode(cause, 'unsupported_span') ||
                hasCode(cause, 'duplicate_target') || hasCode(cause, 'target_not_found') ||
                hasCode(cause, 'text_move_not_applied') || hasCode(cause, 'old_text_not_removed') ||
                hasCode(cause, 'op_outside_page')) {
              const opId = cause.details.op_id as string | undefined;
              if (opId) setProblems({ [opId]: cause.code });
              if (hasCode(cause, 'text_overflow') && opId) {
                setWarnings([{ code: 'text_overflow', op_id: opId, ...cause.details }]);
              }
              commitIdRef.current = null;
              setStatus('error');
              setError(
                hasCode(cause, 'text_overflow')
                  ? 'An edited text does not fit its box — choose how to fit it, then save again.'
                  : hasCode(cause, 'stale_target')
                    ? 'The text of one edit is no longer in the document; undo that edit and try again.'
                    : hasCode(cause, 'text_move_not_applied')
                      ? 'A moved text block could not be placed at its new position; move it again or undo the move.'
                      : hasCode(cause, 'old_text_not_removed')
                        ? 'Part of the old text could not be removed from the page, so nothing was saved; ' +
                          'undo that edit, or replace the whole line.'
                        : describeError(cause, 'An edit cannot be applied'),
              );
              return;
            }
            if (hasCode(cause, 'revision_conflict') || hasCode(cause, 'base_outdated') ||
                hasCode(cause, 'draft_taken_over') || hasCode(cause, 'draft_not_found')) {
              onFailure({ kind: 'answer', error: cause });
              return;
            }
            commitIdRef.current = null;
            setStatus('error');
            setError(describeError(cause, 'The changes could not be saved'));
            return;
          }
          if (attempt === 2) {
            setStatus('offline');
            setError('The server did not answer; press Save changes again (the same save is retried, never doubled).');
            return;
          }
          await new Promise((resolve) => window.setTimeout(resolve, 1000 * (attempt + 1)));
        }
      }
      if (!out) return;
      const notice: CommitNotice = { out, signaturesRemoved: out.remapped.signatures_removed };
      finish('commit');
      await reloadDocument({ holdView: true });
      onCommitted(notice);
    },
    [autosave, contractId, finish, onCommitted, onFailure, reloadDocument],
  );

  const save = useCallback(() => commit(false), [commit]);
  const confirmDiscardSignatures = useCallback(() => {
    setSignaturesPrompt(null);
    return commit(true);
  }, [commit]);
  const dismissSignatures = useCallback(() => setSignaturesPrompt(null), []);

  const cancel = useCallback(async () => {
    if (!contractId || modeRef.current !== 'edit') return;
    const pending = opsRef.current.length + (markupChangedRef.current() ? 1 : 0);
    if (
      pending > 0 &&
      !window.confirm(
        `Discard ${opsRef.current.length} unsaved edit${opsRef.current.length === 1 ? '' : 's'} to the document? This cannot be undone.`,
      )
    ) {
      return;
    }
    autosave.stop();
    try {
      await documentApi.discardDraft(contractId, revisionRef.current);
    } catch (cause) {
      if (isDocumentError(cause, 'revision_conflict')) {
        const current = Number(cause.details.current_revision);
        if (Number.isFinite(current)) {
          try {
            await documentApi.discardDraft(contractId, current);
          } catch {
            /* reported below by the reload */
          }
        }
      } else if (!isDocumentError(cause, 'draft_not_found')) {
        setError(describeError(cause, 'Could not cancel the draft'));
      }
    }
    finish('cancel');
    await reloadDocument();
  }, [autosave, contractId, finish, reloadDocument]);

  /* -- conflict dialog -------------------------------------------------------- */

  const reloadAfterConflict = useCallback(async () => {
    autosave.stop();
    applyOps([]);
    dropChannel('ops');
    setMode('view');
    setStatus('idle');
    setConflict(null);
    // The local copy stays: «Restore from this browser» can bring it back.
    offeredFor.current = null;
    onEnded?.('conflict');
    await reloadDocument();
  }, [applyOps, autosave, dropChannel, onEnded, reloadDocument]);

  const downloadChanges = useCallback(() => {
    const blob = new Blob(
      [JSON.stringify({ contract_id: contractId, base_version_no: baseVersionRef.current, ops: opsRef.current, markup: payload().markup }, null, 2)],
      { type: 'application/json' },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `contract-${contractId}-edits.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 2000);
  }, [contractId, payload]);

  // A different contract: nothing carries over.
  useEffect(() => {
    return () => {
      offeredFor.current = null;
    };
  }, [contractId]);

  return {
    sessionId,
    mode,
    status,
    ops,
    opsRef,
    revision,
    revisionRef,
    savedAt,
    error,
    conflict,
    lock,
    recovery,
    warnings,
    problems,
    signaturesPrompt,
    capabilities,
    available: editor.status === 'ready' && Boolean(doc),
    start,
    takeOver,
    resume,
    restoreLocal,
    discardRecovered,
    changeOps,
    markupTouched,
    flush: autosave.flush,
    putCount: autosave.sentCount,
    save,
    cancel,
    confirmDiscardSignatures,
    dismissSignatures,
    reloadAfterConflict,
    downloadChanges,
    clearError: () => setError(null),
    dismissRecovery: () => setRecovery(null),
  };
}

export type EditSession = ReturnType<typeof useEditSession>;
