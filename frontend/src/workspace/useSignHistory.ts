import { useCallback, useEffect, useRef, useState } from 'react';
import type { PlacementRequest } from '../hooks/useContractSignatures';
import type { SignatureImageEntry } from '../hooks/useSignatureImages';
import type { Signature } from '../types/contract';

/**
 * Undo / redo for what signing puts on the pages: a placed signature (each one
 * the automatic fill places too) and a chosen date. The PDF editor's Undo and
 * Redo buttons drive it (see `EmbeddedEditor`), so one button takes back the
 * last change, whether it was a text edit in the editor or a signature.
 *
 * Signatures are server rows: undo deletes the row, redo places the same image
 * at the same spot again (a new row). Dates are client state only.
 */
type Entry =
  | { kind: 'signature'; signature: Signature; at: number; imageData?: string }
  | { kind: 'date'; fieldId: string; before: string; after: string; at: number };

interface Options {
  signatures: Signature[];
  signaturesLoaded: boolean;
  dateValues: Record<string, string>;
  signatureImages: ReadonlyMap<string, SignatureImageEntry>;
  place: (request: PlacementRequest) => Promise<Signature>;
  remove: (signatureId: string) => Promise<void>;
  setDateValue: (fieldId: string, value: string) => void;
  onError: (message: string) => void;
}

export interface SignHistory {
  /** When the newest undoable signing change was made (`Date.now()`), or -1. */
  lastAt: number;
  canUndo: boolean;
  canRedo: boolean;
  busy: boolean;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  /** A newer change was made in the editor: what was undone here stays undone. */
  clearRedo: () => void;
}

async function blobUrlToDataUrl(url: string): Promise<string> {
  const blob = await (await fetch(url)).blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the signature image'));
    reader.readAsDataURL(blob);
  });
}

export function useSignHistory(options: Options): SignHistory {
  const opts = useRef(options);
  opts.current = options;

  const undoStack = useRef<Entry[]>([]);
  const redoStack = useRef<Entry[]>([]);
  const knownIds = useRef<Set<string> | null>(null);
  const knownDates = useRef<Record<string, string> | null>(null);
  // Changes this hook makes itself, so they are not recorded as new actions.
  const expectedIds = useRef(new Set<string>());
  const expectedDates = useRef(new Map<string, string>());
  const replaying = useRef(0);
  const busyRef = useRef(false);
  const [busy, setBusyState] = useState(false);
  const [, setVersion] = useState(0);
  const bump = () => setVersion((value) => value + 1);
  const setBusy = (value: boolean) => {
    busyRef.current = value;
    setBusyState(value);
  };

  const { signatures, signaturesLoaded, dateValues } = options;

  // A signature that appears is a new step; one that goes away by other means
  // (the panel's Remove, a new version) leaves the history.
  useEffect(() => {
    if (!signaturesLoaded) return;
    const ids = new Set(signatures.map((row) => row.id));
    const previous = knownIds.current;
    knownIds.current = ids;
    if (!previous) return;
    let changed = false;
    if ([...previous].some((id) => !ids.has(id))) {
      undoStack.current = undoStack.current.filter(
        (entry) => entry.kind !== 'signature' || ids.has(entry.signature.id),
      );
      changed = true;
    }
    const now = Date.now();
    let added = false;
    signatures.forEach((row) => {
      if (previous.has(row.id)) return;
      if (expectedIds.current.delete(row.id) || replaying.current > 0) return;
      undoStack.current.push({ kind: 'signature', signature: row, at: now });
      added = true;
    });
    if (added) redoStack.current = [];
    if (changed || added) bump();
  }, [signatures, signaturesLoaded]);

  useEffect(() => {
    const previous = knownDates.current;
    knownDates.current = { ...dateValues };
    if (!previous) return;
    const keys = new Set([...Object.keys(previous), ...Object.keys(dateValues)]);
    let added = false;
    keys.forEach((fieldId) => {
      const before = previous[fieldId] ?? '';
      const after = dateValues[fieldId] ?? '';
      if (before === after) return;
      if (expectedDates.current.get(fieldId) === after) {
        expectedDates.current.delete(fieldId);
        return;
      }
      undoStack.current.push({ kind: 'date', fieldId, before, after, at: Date.now() });
      added = true;
    });
    if (added) {
      redoStack.current = [];
      bump();
    }
  }, [dateValues]);

  const undo = useCallback(async () => {
    if (busyRef.current) return;
    const entry = undoStack.current.pop();
    if (!entry) return;
    if (entry.kind === 'date') {
      expectedDates.current.set(entry.fieldId, entry.before);
      opts.current.setDateValue(entry.fieldId, entry.before);
      redoStack.current.push(entry);
      bump();
      return;
    }
    setBusy(true);
    replaying.current += 1;
    try {
      if (!entry.imageData) {
        const image = opts.current.signatureImages.get(entry.signature.id);
        if (image && typeof image === 'object') {
          entry.imageData = await blobUrlToDataUrl(image.url).catch(() => undefined);
        }
      }
      await opts.current.remove(entry.signature.id);
      redoStack.current.push(entry);
    } catch (cause) {
      undoStack.current.push(entry);
      opts.current.onError(cause instanceof Error ? cause.message : 'Could not undo the signature');
    } finally {
      replaying.current -= 1;
      setBusy(false);
      bump();
    }
  }, []);

  const redo = useCallback(async () => {
    if (busyRef.current) return;
    const entry = redoStack.current.pop();
    if (!entry) return;
    if (entry.kind === 'date') {
      expectedDates.current.set(entry.fieldId, entry.after);
      opts.current.setDateValue(entry.fieldId, entry.after);
      undoStack.current.push({ ...entry, at: Date.now() });
      bump();
      return;
    }
    setBusy(true);
    replaying.current += 1;
    try {
      if (!entry.imageData) throw new Error('This signature cannot be placed again: its image is not available.');
      const { signature } = entry;
      const created = await opts.current.place({
        page: signature.page_number,
        bbox: signature.bbox,
        fieldId: signature.field_id,
        imageData: entry.imageData,
      });
      expectedIds.current.add(created.id);
      undoStack.current.push({ ...entry, signature: created, at: Date.now() });
    } catch (cause) {
      redoStack.current.push(entry);
      opts.current.onError(cause instanceof Error ? cause.message : 'Could not redo the signature');
    } finally {
      replaying.current -= 1;
      setBusy(false);
      bump();
    }
  }, []);

  const clearRedo = useCallback(() => {
    if (redoStack.current.length === 0) return;
    redoStack.current = [];
    bump();
  }, []);

  const top = undoStack.current[undoStack.current.length - 1];
  return {
    lastAt: top ? top.at : -1,
    canUndo: undoStack.current.length > 0,
    canRedo: redoStack.current.length > 0,
    busy,
    undo,
    redo,
    clearRedo,
  };
}
