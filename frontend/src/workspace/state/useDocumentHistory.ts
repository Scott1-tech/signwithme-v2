import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/**
 * The workspace's ONE undo history (plan §2.3, W2).
 *
 * Before W2 the editor had its own snapshot history inside `useAnnotations`
 * and a slot drag was not undoable at all. Now every local, user-made change
 * of the document is one `Step` on one stack, whatever it changed:
 *
 *   * `annotations` -- a whole annotation list (place, move, resize, retype,
 *     recolour, delete), exactly the snapshots the old history kept;
 *   * `layout`      -- a whole `FinalizeLayout` (a slot dragged or resized, a
 *     stepper, a slider, "Move together", Reset).
 *
 * WHY WHOLE VALUES AND NOT INVERSE COMMANDS
 * -----------------------------------------
 * Same argument as the old annotation history: a list of ≤ 200 small objects
 * and a layout of a few dozen numbers are cheaper to keep than an inverse per
 * operation is to write and test, and a snapshot cannot drift -- undo restores
 * a state that was really on the screen once. Unchanged entries share their
 * references, so a step costs one array or one object, not a deep copy.
 *
 * WHAT IS NOT IN HERE (on purpose)
 * --------------------------------
 *  * Server writes: a placed signature, a bulk run, the automatic fill. They are
 *    saved (and hashed, and audited) the moment they happen; undoing one would
 *    be a hidden DELETE and redoing it a new row with a new id. `SIGNATURE_LOCK_NOTE`
 *    and `test_editor.py` pin this.
 *  * Corrections the app makes by itself: the page-edge clamp of the layout
 *    and the one before finalize are `replace`s -- they change the present
 *    without adding a step (plan §2.3, W2b).
 *
 * THE CHANNELS OWN THEIR STATE
 * ----------------------------
 * The history never holds "the" layout or "the" list; it holds before/after
 * pairs and, to undo, hands the right value back to whoever registered for
 * that channel (`register`). The owners write synchronously through refs, so
 * a step is recorded in the event handler that made it -- never inside a
 * `setState` updater, which StrictMode runs twice.
 */

/**
 * F1b: `ops` -- the edit session's content operations (EDITOR-V2-PLAN §7:
 * «har blok commit = `'ops'` kanalida bitta qadam»), a whole op list per
 * step like the annotations. Its steps only mean something while that
 * session is open: Save / Cancel drop them (`dropChannel('ops')`).
 */
export type HistoryChannel = 'annotations' | 'layout' | 'ops';

export interface HistoryStep {
  channel: HistoryChannel;
  /** What the step did, in words ("Move slot", "Place text"). Shown as the button's title. */
  label: string;
  before: unknown;
  after: unknown;
  /**
   * Steps with the same key, on the same channel, recorded within
   * `COALESCE_MS` of each other with nothing undone in between, merge into one
   * (a slider dragged across its track is one step, not forty).
   */
  coalesce?: string;
}

interface StoredStep extends HistoryStep {
  at: number;
}

export interface DocumentHistory {
  record(step: HistoryStep): void;
  undo(): void;
  redo(): void;
  canUndo: boolean;
  canRedo: boolean;
  undoDepth: number;
  redoDepth: number;
  /** The label of the step Undo / Redo would take, for the buttons' titles. */
  undoLabel: string | null;
  redoLabel: string | null;
  /** The owner of a channel says how to put a value back. Returns the unregister. */
  register(channel: HistoryChannel, apply: (value: unknown) => void): () => void;
  /** Forget everything (a different contract was loaded). */
  reset(): void;
  /** F1b: forget one channel's steps (the edit session ended); the others stay. */
  dropChannel(channel: HistoryChannel): void;
}

/** Steps kept, all channels together. The oldest falls off the back. */
export const HISTORY_LIMIT = 100;
/** How close two coalescing steps must be, in milliseconds. */
export const COALESCE_MS = 1200;

export function useDocumentHistory(contractId: string | undefined): DocumentHistory {
  const past = useRef<StoredStep[]>([]);
  const future = useRef<StoredStep[]>([]);
  const appliers = useRef(new Map<HistoryChannel, (value: unknown) => void>());
  // The stacks live in refs (they are written from event handlers, several
  // times per batch); this counter is only what makes the buttons re-render.
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((value) => value + 1), []);

  const reset = useCallback(() => {
    if (past.current.length === 0 && future.current.length === 0) return;
    past.current = [];
    future.current = [];
    bump();
  }, [bump]);

  // A different contract is a different document: nothing is carried across.
  const loadedFor = useRef(contractId);
  useEffect(() => {
    if (loadedFor.current === contractId) return;
    loadedFor.current = contractId;
    reset();
  }, [contractId, reset]);

  const record = useCallback(
    (step: HistoryStep) => {
      const now = Date.now();
      const top = past.current[past.current.length - 1];
      if (
        step.coalesce &&
        top &&
        future.current.length === 0 &&
        top.channel === step.channel &&
        top.coalesce === step.coalesce &&
        now - top.at <= COALESCE_MS
      ) {
        past.current = [...past.current.slice(0, -1), { ...top, after: step.after, at: now }];
        bump();
        return;
      }
      const next = [...past.current, { ...step, at: now }];
      if (next.length > HISTORY_LIMIT) next.splice(0, next.length - HISTORY_LIMIT);
      past.current = next;
      future.current = [];
      bump();
    },
    [bump],
  );

  const undo = useCallback(() => {
    const step = past.current[past.current.length - 1];
    if (!step) return;
    past.current = past.current.slice(0, -1);
    future.current = [step, ...future.current].slice(0, HISTORY_LIMIT);
    appliers.current.get(step.channel)?.(step.before);
    bump();
  }, [bump]);

  const redo = useCallback(() => {
    const [step, ...rest] = future.current;
    if (!step) return;
    future.current = rest;
    const next = [...past.current, { ...step, at: 0 }];
    if (next.length > HISTORY_LIMIT) next.splice(0, next.length - HISTORY_LIMIT);
    past.current = next;
    appliers.current.get(step.channel)?.(step.after);
    bump();
  }, [bump]);

  const dropChannel = useCallback(
    (channel: HistoryChannel) => {
      const keepPast = past.current.filter((step) => step.channel !== channel);
      const keepFuture = future.current.filter((step) => step.channel !== channel);
      if (keepPast.length === past.current.length && keepFuture.length === future.current.length) return;
      past.current = keepPast;
      future.current = keepFuture;
      bump();
    },
    [bump],
  );

  const register = useCallback((channel: HistoryChannel, apply: (value: unknown) => void) => {
    appliers.current.set(channel, apply);
    return () => {
      if (appliers.current.get(channel) === apply) appliers.current.delete(channel);
    };
  }, []);

  return useMemo(() => {
    const top = past.current[past.current.length - 1];
    const ahead = future.current[0];
    return {
      record,
      undo,
      redo,
      register,
      reset,
      dropChannel,
      canUndo: past.current.length > 0,
      canRedo: future.current.length > 0,
      undoDepth: past.current.length,
      redoDepth: future.current.length,
      undoLabel: top?.label ?? null,
      redoLabel: ahead?.label ?? null,
    };
    // `version` is what says the refs moved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, record, undo, redo, register, reset, dropChannel]);
}
