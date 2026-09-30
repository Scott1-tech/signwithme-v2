import { useCallback, useEffect, useMemo, useRef } from 'react';
import { ApiError, tokenStore } from '../../api/client';
import {
  documentApi,
  type DocumentOp,
  type DraftPutBody,
  type DraftSavedOut,
  type MarkupElement,
} from '../../api/document';

/**
 * F1b (EDITOR-V2-PLAN §7 «Sessiya»): the draft's autosave.
 *
 *  * ONE `PUT …/draft` in flight at a time; a change made while it flies is
 *    sent right after it (with the revision it answered), never in parallel;
 *  * debounce `DEBOUNCE_MS` after the last change, but never later than
 *    `MAX_WAIT_MS` after the first unsaved one (typing for a minute still
 *    saves every 10 s);
 *  * a network failure (or a 5xx) retries on `BACKOFF_MS` (1 / 2 / 5 / 10 s,
 *    then every 10 s) and says `offline`; a 409 / 422 / 413 is an ANSWER, not
 *    a failure: it stops the loop and is handed to the owner;
 *  * `pagehide` with something unsaved: one `fetch(keepalive)` (≤ 64 KB, the
 *    browser's keepalive budget) -- best effort, the local copy below is the
 *    real safety net;
 *  * every change is first written to `localStorage["swm.draft.<cid>"]`
 *    (`writeLocalDraft`), with `acked` telling whether the server has it.
 */

export const DEBOUNCE_MS = 1500;
export const MAX_WAIT_MS = 10000;
export const BACKOFF_MS = [1000, 2000, 5000, 10000];
export const KEEPALIVE_MAX_BYTES = 64 * 1024;

export interface DraftPayload {
  ops: DocumentOp[];
  markup: MarkupElement[] | null;
}

export type AutosaveFailure =
  | { kind: 'answer'; error: ApiError }
  | { kind: 'offline'; attempt: number; retryInMs: number };

interface Options {
  contractId: string | undefined;
  /** The browser session that owns the lease (`client_session_id`). */
  sessionId: string;
  /** Read at send time: what the draft is now. */
  payload: () => DraftPayload;
  /** The revision the next PUT carries (the last one the server answered). */
  revisionRef: React.MutableRefObject<number>;
  /** False outside an edit session: nothing is sent. */
  active: boolean;
  onSaving(): void;
  onSaved(out: DraftSavedOut, stillDirty: boolean): void;
  onFailure(failure: AutosaveFailure): void;
}

export interface DraftAutosave {
  /** Something changed: save it (debounced). */
  markDirty(): void;
  /** Save NOW and resolve once nothing is unsaved (false: it could not be saved). */
  flush(): Promise<boolean>;
  /** Forget pending work (Cancel / Save / a conflict). */
  stop(): void;
  /** PUTs sent this session (a test / status hook). */
  sentCount(): number;
  /** Anything not yet on the server. */
  isDirty(): boolean;
}

const isNetwork = (cause: unknown) =>
  !(cause instanceof ApiError) || cause.status >= 500 || cause.status === 0;

export function useDraftAutosave(options: Options): DraftAutosave {
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const dirtyRef = useRef(false);
  /** Bumped on every change: a save answered for an older stamp leaves it dirty. */
  const stampRef = useRef(0);
  const firstDirtyAtRef = useRef(0);
  const timerRef = useRef(0);
  const inFlightRef = useRef<Promise<boolean> | null>(null);
  const attemptRef = useRef(0);
  const stoppedRef = useRef(false);
  const sentRef = useRef(0);
  const waitersRef = useRef<Array<(ok: boolean) => void>>([]);

  const clearTimer = () => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = 0;
  };

  const settle = (ok: boolean) => {
    const waiters = waitersRef.current;
    waitersRef.current = [];
    waiters.forEach((resolve) => resolve(ok));
  };

  const sendRef = useRef<() => Promise<boolean>>(() => Promise.resolve(true));

  const schedule = useCallback((delay: number) => {
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      timerRef.current = 0;
      void sendRef.current();
    }, delay);
  }, []);

  const send = useCallback(async (): Promise<boolean> => {
    const current = optionsRef.current;
    if (!current.contractId || !current.active || stoppedRef.current) return !dirtyRef.current;
    if (inFlightRef.current) return inFlightRef.current;
    if (!dirtyRef.current) {
      settle(true);
      return true;
    }
    clearTimer();
    const stamp = stampRef.current;
    const { ops, markup } = current.payload();
    const body: DraftPutBody = {
      revision: current.revisionRef.current,
      client_session_id: current.sessionId,
      ops,
      markup,
    };
    current.onSaving();
    sentRef.current += 1;
    const job = (async (): Promise<boolean> => {
      try {
        const out = await documentApi.saveDraft(current.contractId as string, body);
        if (stoppedRef.current) return false;
        attemptRef.current = 0;
        optionsRef.current.revisionRef.current = out.revision;
        const stillDirty = stampRef.current !== stamp;
        dirtyRef.current = stillDirty;
        if (!stillDirty) firstDirtyAtRef.current = 0;
        optionsRef.current.onSaved(out, stillDirty);
        return true;
      } catch (cause) {
        if (stoppedRef.current) return false;
        if (isNetwork(cause)) {
          const attempt = attemptRef.current;
          attemptRef.current += 1;
          const retryInMs = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
          optionsRef.current.onFailure({ kind: 'offline', attempt: attempt + 1, retryInMs });
          schedule(retryInMs);
          return false;
        }
        optionsRef.current.onFailure({ kind: 'answer', error: cause as ApiError });
        return false;
      } finally {
        inFlightRef.current = null;
      }
    })();
    inFlightRef.current = job;
    const ok = await job;
    if (ok && dirtyRef.current && !stoppedRef.current) {
      // Changed while the PUT was flying: send the rest now.
      return sendRef.current();
    }
    if (ok || !timerRef.current) settle(ok);
    return ok;
  }, [schedule]);
  sendRef.current = send;

  const markDirty = useCallback(() => {
    stoppedRef.current = false;
    stampRef.current += 1;
    dirtyRef.current = true;
    const now = Date.now();
    if (!firstDirtyAtRef.current) firstDirtyAtRef.current = now;
    if (inFlightRef.current) return; // sent right after the one flying
    const waited = now - firstDirtyAtRef.current;
    schedule(Math.max(0, Math.min(DEBOUNCE_MS, MAX_WAIT_MS - waited)));
  }, [schedule]);

  const flush = useCallback((): Promise<boolean> => {
    if (!dirtyRef.current && !inFlightRef.current) return Promise.resolve(true);
    stoppedRef.current = false;
    const done = new Promise<boolean>((resolve) => waitersRef.current.push(resolve));
    if (!inFlightRef.current) {
      attemptRef.current = 0;
      void send();
    }
    return done;
  }, [send]);

  const stop = useCallback(() => {
    stoppedRef.current = true;
    clearTimer();
    dirtyRef.current = false;
    firstDirtyAtRef.current = 0;
    attemptRef.current = 0;
    settle(false);
  }, []);

  // `pagehide`: one keepalive PUT of whatever is unsaved (best effort).
  useEffect(() => {
    const onHide = () => {
      const current = optionsRef.current;
      if (!current.active || !current.contractId || !dirtyRef.current || inFlightRef.current) return;
      const { ops, markup } = current.payload();
      const body = JSON.stringify({
        revision: current.revisionRef.current,
        client_session_id: current.sessionId,
        ops,
        markup,
      });
      if (body.length > KEEPALIVE_MAX_BYTES) return;
      const token = tokenStore.get();
      try {
        void fetch(`/api/contracts/${current.contractId}/document/draft`, {
          method: 'PUT',
          keepalive: true,
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body,
        }).catch(() => undefined);
      } catch {
        /* the local copy is the safety net */
      }
    };
    window.addEventListener('pagehide', onHide);
    return () => window.removeEventListener('pagehide', onHide);
  }, []);

  useEffect(() => () => clearTimer(), []);

  return useMemo(
    () => ({
      markDirty,
      flush,
      stop,
      sentCount: () => sentRef.current,
      isDirty: () => dirtyRef.current || Boolean(inFlightRef.current),
    }),
    [markDirty, flush, stop],
  );
}

/* -- the local copy ------------------------------------------------------------ */

export interface LocalDraft {
  v: 1;
  base_version_no: number;
  /** The last revision the server confirmed. */
  revision: number;
  ops: DocumentOp[];
  markup: MarkupElement[] | null;
  /** When the last change was made (ms). */
  changed_at: number;
  /** The server has exactly this. */
  acked: boolean;
}

export const localDraftKey = (contractId: string) => `swm.draft.${contractId}`;

export function readLocalDraft(contractId: string | undefined): LocalDraft | null {
  if (!contractId) return null;
  try {
    const raw = localStorage.getItem(localDraftKey(contractId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LocalDraft>;
    if (parsed?.v !== 1 || !Array.isArray(parsed.ops) || typeof parsed.base_version_no !== 'number') return null;
    return {
      v: 1,
      base_version_no: parsed.base_version_no,
      revision: typeof parsed.revision === 'number' ? parsed.revision : 0,
      ops: parsed.ops as DocumentOp[],
      markup: Array.isArray(parsed.markup) ? (parsed.markup as MarkupElement[]) : null,
      changed_at: typeof parsed.changed_at === 'number' ? parsed.changed_at : 0,
      acked: parsed.acked === true,
    };
  } catch {
    return null;
  }
}

export function writeLocalDraft(contractId: string | undefined, draft: LocalDraft | null): void {
  if (!contractId) return;
  try {
    if (!draft) localStorage.removeItem(localDraftKey(contractId));
    else localStorage.setItem(localDraftKey(contractId), JSON.stringify(draft));
  } catch {
    /* private mode / quota: the server copy is all there is */
  }
}
