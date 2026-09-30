import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { BulkFailure, BulkProgress, BulkSignature, BulkSkip } from '../components/BulkSignPanel';
import {
  NEW_ISSUES_MESSAGE,
  describeBulkError,
  isNewIssuesConflict,
  signaturesApi,
} from '../api/signatures';
import { userSignaturesApi } from '../api/userSignatures';
import { BULK_CONCURRENCY, type BulkOutcome } from '../lib/signing';
import { runPool } from '../lib/pool';
import type { DateValues } from './useDateValues';
import type { ComparisonResult } from '../types/comparison';
import type { Signature, UserSignature } from '../types/contract';

/**
 * What the automatic first-load fill put on the contract, kept so "Undo" can
 * take back exactly that and nothing the user did afterwards. Its own hook,
 * declared before `useBulkSigning`, because a manual bulk run clears it.
 */
export function useAutoFillRecord() {
  const [autoFilled, setAutoFilled] = useState<BulkOutcome | null>(null);
  const [autoFillNote, setAutoFillNote] = useState<string | null>(null);
  return { autoFilled, setAutoFilled, autoFillNote, setAutoFillNote };
}

interface Options {
  record: ReturnType<typeof useAutoFillRecord>;
  autoFill: boolean;
  contractId: string | undefined;
  result: ComparisonResult | null;
  signaturesLoaded: boolean;
  libraryLoaded: boolean;
  blocked: boolean;
  defaultLibrarySignature: UserSignature | null;
  emptySigSlots: unknown[];
  emptyDateSlots: unknown[];
  applyToAll: (options: { dataUrl: string; date: string; replace: boolean }) => Promise<BulkOutcome>;
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  setBulkSignature: (signature: BulkSignature | null) => void;
  setSignatures: Dispatch<SetStateAction<Signature[]>>;
  setDateValues: (next: DateValues | ((current: DateValues) => DateValues)) => void;
  setBulkProgress: (progress: BulkProgress | null) => void;
  setBulkFailures: (failures: BulkFailure[]) => void;
  setBulkSkipped: (skipped: BulkSkip[]) => void;
}

/**
 * The first-load automatic fill and its "Undo".
 *
 * Moved out of `SignaturePage` word for word (W0): the effect keeps exactly
 * its dependency list, and `autoFillRan` still lives for the whole visit, so
 * the fill runs at most once -- a StrictMode double-mount included.
 */
export function useAutoFill({
  record,
  autoFill,
  contractId,
  result,
  signaturesLoaded,
  libraryLoaded,
  blocked,
  defaultLibrarySignature,
  emptySigSlots,
  emptyDateSlots,
  applyToAll,
  setBusy,
  setError,
  setBulkSignature,
  setSignatures,
  setDateValues,
  setBulkProgress,
  setBulkFailures,
  setBulkSkipped,
}: Options) {
  const { autoFilled, setAutoFilled, autoFillNote, setAutoFillNote } = record;
  const autoFillRan = useRef(false);

  /**
   * First-load automatic fill.
   *
   * Runs at most once per visit (`autoFillRan`), only when the user has left
   * the preference on, only when there is a saved signature to use, and only
   * when something is actually still empty. Everything it did is remembered so
   * a single "Undo" takes it all back.
   */
  useEffect(() => {
    if (!autoFill || autoFillRan.current) return;
    if (!contractId || !result || !signaturesLoaded || !libraryLoaded) return;
    if (blocked) return;
    if (!defaultLibrarySignature) return;
    if (emptySigSlots.length === 0 && emptyDateSlots.length === 0) return;

    autoFillRan.current = true;
    void (async () => {
      setBusy(true);
      setError(null);
      try {
        const dataUrl = await userSignaturesApi.imageAsDataUrl(defaultLibrarySignature.id);
        const label = defaultLibrarySignature.label ?? 'Saved signature';
        setBulkSignature({ dataUrl, label });
        // Signatures only. NO automatic date (user's decision, 2026-09-25):
        // the run gets an empty date, so `applyToAll` fills no date slot and
        // `filledDateFieldIds` stays empty -- Undo therefore only ever takes
        // back signatures. The panel's "Choose the date for N date fields"
        // line (`dates-needed`) asks for the rest.
        const outcome = await applyToAll({ dataUrl, date: '', replace: false });
        const placed = outcome.placedSignatureIds.length;
        if (placed > 0) {
          setAutoFilled(outcome);
          setAutoFillNote(
            `${placed} ${placed === 1 ? 'signature was' : 'signatures were'} placed automatically. ` +
              'Dates are never filled in automatically — choose them yourself. ' +
              'Check everything before generating the final contract.',
          );
        }
        // Whatever it managed to place stays; the refusal is still shown.
        if (outcome.error) setError(`Automatic fill failed: ${outcome.error}`);
      } catch (cause) {
        setError(
          isNewIssuesConflict(cause)
            ? NEW_ISSUES_MESSAGE
            : `Automatic fill failed: ${describeBulkError(cause)}`,
        );
      } finally {
        setBusy(false);
      }
    })();
  }, [
    autoFill,
    contractId,
    result,
    signaturesLoaded,
    libraryLoaded,
    blocked,
    defaultLibrarySignature,
    emptySigSlots.length,
    emptyDateSlots.length,
    applyToAll,
  ]);

  /** Take back exactly what the automatic fill put there, nothing else. */
  const undoAutoFill = useCallback(async () => {
    if (!contractId || !autoFilled) return;
    setBusy(true);
    setError(null);
    const stuck: string[] = [];
    await runPool(autoFilled.placedSignatureIds, BULK_CONCURRENCY, async (signatureId) => {
      try {
        await signaturesApi.remove(contractId, signatureId);
      } catch {
        stuck.push(signatureId);
      }
    });
    setSignatures((current) =>
      current.filter(
        (signature) =>
          !autoFilled.placedSignatureIds.includes(signature.id) || stuck.includes(signature.id),
      ),
    );
    setDateValues((current) => {
      const next = { ...current };
      autoFilled.filledDateFieldIds.forEach((fieldId) => {
        delete next[fieldId];
      });
      return next;
    });
    setAutoFilled(null);
    setAutoFillNote(null);
    setBulkProgress(null);
    setBulkFailures([]);
    setBulkSkipped([]);
    if (stuck.length > 0) {
      setError(`${stuck.length} automatically placed signature(s) could not be removed.`);
    }
    setBusy(false);
  }, [contractId, autoFilled]);

  return { autoFilled, autoFillNote, undoAutoFill };
}
