import { useCallback, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type {
  BulkFailure,
  BulkProgress,
  BulkSignature,
  BulkSkip,
} from '../components/BulkSignPanel';
import {
  MAX_BULK_SLOTS,
  NEW_ISSUES_MESSAGE,
  describeBulkError,
  isBulkUnsupported,
  isNewIssuesConflict,
  signaturesApi,
} from '../api/signatures';
import type { BulkResult } from '../api/signatures';
import {
  BULK_CONCURRENCY,
  CHOOSE_DATE_FIRST,
  describeSlotFailure,
  focusDateInput,
  signingErrorText,
  type BulkOutcome,
} from '../lib/signing';
import { runPool } from '../lib/pool';
import type { DateValues } from './useDateValues';
import type { Signature } from '../types/contract';
import type { SignatureSlot } from '../types/comparison';
import type { BBox } from '../types/field';

interface Options {
  contractId: string | undefined;
  signatures: Signature[];
  setSignatures: Dispatch<SetStateAction<Signature[]>>;
  sigSlots: SignatureSlot[];
  dateSlots: SignatureSlot[];
  dateValues: DateValues;
  setDateValues: (next: DateValues | ((current: DateValues) => DateValues)) => void;
  selectedDate: string;
  emptySigSlots: SignatureSlot[];
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  /** The automatic fill's record; a manual run supersedes it (see `useAutoFill`). */
  setAutoFilled: (outcome: BulkOutcome | null) => void;
  setAutoFillNote: (note: string | null) => void;
}

/**
 * "One for all": one image and one date, applied across every empty slot.
 *
 * Moved out of `SignaturePage` word for word (W0). Only the names the page
 * used to own now arrive as options; every `useCallback` keeps exactly the
 * dependency list it had, because the automatic fill's effect depends on
 * `applyToAll` and a wider list would re-arm it.
 */
export function useBulkSigning({
  contractId,
  signatures,
  setSignatures,
  sigSlots,
  dateSlots,
  dateValues,
  setDateValues,
  selectedDate,
  emptySigSlots,
  setBusy,
  setError,
  setAutoFilled,
  setAutoFillNote,
}: Options) {
  const [bulkSignature, setBulkSignature] = useState<BulkSignature | null>(null);
  const [bulkProgress, setBulkProgress] = useState<BulkProgress | null>(null);
  const [bulkFailures, setBulkFailures] = useState<BulkFailure[]>([]);
  const [bulkSkipped, setBulkSkipped] = useState<BulkSkip[]>([]);
  // "Choose the date first", after a run that had no date to place.
  const [dateMessage, setDateMessage] = useState<string | null>(null);

  /**
   * Fill every slot the run is allowed to touch with one image and one date.
   *
   * All the signatures go out in **one** request to the bulk endpoint, which
   * still resolves every position from the template -- a bulk run has exactly
   * the same authority as eleven individual clicks, it just stops re-uploading
   * and re-decoding the same image once per slot. Per-slot problems come back
   * inside a 200 (`skipped` / `failed`), so one refused slot never strands the
   * others; only a whole-request refusal (image, signing gate, access) stops
   * the run, and that is reported rather than thrown. Dates never leave the
   * browser here; they travel in the finalize payload, as before.
   */
  const applyToAll = useCallback(
    async (options: { dataUrl: string; date: string; replace: boolean }): Promise<BulkOutcome> => {
      const outcome: BulkOutcome = {
        placedSignatureIds: [],
        filledDateFieldIds: [],
        failures: [],
        skipped: [],
        error: null,
      };
      if (!contractId) return outcome;

      const placedBySlot = new Map(
        signatures
          .filter((signature) => signature.field_id)
          .map((signature) => [signature.field_id as string, signature]),
      );
      // Without `replace`, an already-signed slot is left out rather than
      // retried: the server would only answer `already_signed`, and that is
      // not worth a line in the UI. With no image at all the run is dates-only.
      const chosen = options.dataUrl
        ? sigSlots.filter(
            (slot) => slot.bbox && (options.replace || !placedBySlot.has(slot.field_id)),
          )
        : [];
      // A repeated field id makes the server refuse the *entire* request (422),
      // so the duplicate dies here rather than costing every other slot.
      const seen = new Set<string>();
      const targets = chosen.filter((slot) => {
        if (seen.has(slot.field_id)) return false;
        seen.add(slot.field_id);
        return true;
      });
      const slotById = new Map(targets.map((slot) => [slot.field_id, slot]));
      const labelOf = (fieldId: string) => slotById.get(fieldId)?.label ?? fieldId;

      const dateTargets = dateSlots.filter(
        (slot) => options.replace || !dateValues[slot.field_id],
      );

      setBulkFailures([]);
      setBulkSkipped([]);

      let requestIds = targets.map((slot) => slot.field_id);

      // "Replace all": the endpoint never overwrites, so the old signature has
      // to go first. A removal that fails takes its slot out of the request --
      // otherwise it would only come back as `already_signed` and read like a
      // different problem than it is.
      if (options.replace && requestIds.length > 0) {
        const stuck = new Set<string>();
        const existing = targets
          .map((slot) => ({ slot, signature: placedBySlot.get(slot.field_id) }))
          .filter((entry): entry is { slot: SignatureSlot; signature: Signature } =>
            Boolean(entry.signature),
          );
        await runPool(existing, BULK_CONCURRENCY, async ({ slot, signature }) => {
          try {
            await signaturesApi.remove(contractId, signature.id);
            setSignatures((current) => current.filter((item) => item.id !== signature.id));
          } catch {
            stuck.add(slot.field_id);
            outcome.failures.push({
              fieldId: slot.field_id,
              label: slot.label,
              message: 'The signature already there could not be removed, so it was left alone.',
            });
          }
        });
        requestIds = requestIds.filter((fieldId) => !stuck.has(fieldId));
      }

      // One request unless the contract has more slots than the server accepts
      // at once; then the run is split and the parts go out in order.
      const chunks: string[][] = [];
      for (let index = 0; index < requestIds.length; index += MAX_BULK_SLOTS) {
        chunks.push(requestIds.slice(index, index + MAX_BULK_SLOTS));
      }

      const publishProgress = (running: boolean, chunk: number) =>
        setBulkProgress({
          done:
            outcome.placedSignatureIds.length + outcome.skipped.length + outcome.failures.length,
          total: targets.length,
          chunk,
          chunks: Math.max(1, chunks.length),
          running,
          placed: outcome.placedSignatureIds.length,
          skipped: outcome.skipped.length,
          failed: outcome.failures.length,
        });

      const absorb = (result: BulkResult) => {
        const now = new Date().toISOString();
        // `bbox` and `page_number` come back with the response, so the page can
        // draw the new signatures without re-listing them.
        const created: Signature[] = result.placed.map((entry) => ({
          id: entry.signature_id,
          contract_id: result.contract_id,
          signer_name: null,
          signer_email: null,
          signer_role: 'contractor',
          page_number: entry.page_number,
          bbox: entry.bbox,
          field_id: entry.field_id,
          signed_at: now,
        }));
        outcome.placedSignatureIds.push(...created.map((signature) => signature.id));
        if (created.length > 0) setSignatures((current) => [...current, ...created]);
        outcome.skipped.push(
          ...result.skipped.map((entry) => ({
            fieldId: entry.field_id,
            label: labelOf(entry.field_id),
          })),
        );
        outcome.failures.push(
          ...result.failed.map((entry) => ({
            fieldId: entry.field_id,
            label: labelOf(entry.field_id),
            message: describeSlotFailure(entry.reason),
          })),
        );
      };

      if (targets.length > 0) publishProgress(true, 1);

      // Set when the server turns out not to know the bulk route at all: from
      // that chunk on, the old one-request-per-slot loop finishes the job.
      let fallbackFrom: number | null = null;
      for (let index = 0; index < chunks.length; index += 1) {
        publishProgress(true, index + 1);
        try {
          absorb(
            await signaturesApi.placeBulk(contractId, {
              imageData: options.dataUrl,
              fieldIds: chunks[index],
            }),
          );
        } catch (cause) {
          if (isBulkUnsupported(cause)) {
            fallbackFrom = index;
            break;
          }
          // 413 / 422 / 409 / 403 are real answers about this request; retrying
          // it slot by slot would only repeat the same refusal N times.
          outcome.error = isNewIssuesConflict(cause) ? NEW_ISSUES_MESSAGE : describeBulkError(cause);
          break;
        }
      }

      if (fallbackFrom !== null) {
        const remaining = chunks
          .slice(fallbackFrom)
          .flat()
          .map((fieldId) => slotById.get(fieldId))
          .filter((slot): slot is SignatureSlot => Boolean(slot));
        await runPool(remaining, BULK_CONCURRENCY, async (slot) => {
          try {
            const created = await signaturesApi.place(contractId, {
              page: slot.page ?? 1,
              bbox: slot.bbox as BBox,
              field_id: slot.field_id,
              image_data: options.dataUrl,
            });
            outcome.placedSignatureIds.push(created.id);
            setSignatures((current) => [...current, created]);
          } catch (cause) {
            outcome.failures.push({
              fieldId: slot.field_id,
              label: slot.label,
              message: signingErrorText(cause, 'Placement failed'),
            });
          } finally {
            publishProgress(true, chunks.length);
          }
        });
      }

      if (targets.length > 0) publishProgress(false, Math.max(1, chunks.length));

      if (options.date && dateTargets.length > 0) {
        setDateValues((current) => {
          const next = { ...current };
          dateTargets.forEach((slot) => {
            next[slot.field_id] = options.date;
          });
          return next;
        });
        outcome.filledDateFieldIds = dateTargets.map((slot) => slot.field_id);
      }

      setBulkFailures(outcome.failures);
      setBulkSkipped(outcome.skipped);
      return outcome;
    },
    [contractId, signatures, sigSlots, dateSlots, dateValues],
  );

  const runBulkApply = useCallback(
    async (replace: boolean) => {
      // Only signature slots need an image. A run that has nothing left but
      // dates to fill is allowed to proceed without one.
      if (!bulkSignature && (replace || emptySigSlots.length > 0)) {
        setError('Choose a signature first, then apply it to every slot.');
        return;
      }
      // No automatic date: without a chosen date the run dates nothing. It
      // still signs, but the user is sent to the date box -- and a run that
      // had only dates left to fill does nothing at all.
      const emptyDates = dateSlots.filter((slot) => !dateValues[slot.field_id]).length;
      const missingDate = !selectedDate && emptyDates > 0;
      const willSign = Boolean(bulkSignature) && (replace || emptySigSlots.length > 0);
      if (missingDate) {
        setDateMessage(
          `${CHOOSE_DATE_FIRST}: pick it in “Date for every date field”. ` +
            `${emptyDates} date ${emptyDates === 1 ? 'field is' : 'fields are'} still empty.`,
        );
        focusDateInput('bulk-date');
        if (!willSign) return;
      } else {
        setDateMessage(null);
      }
      setBusy(true);
      setError(null);
      // A manual run supersedes whatever the automatic fill did, so its undo
      // banner goes away rather than offering to revert a stale set.
      setAutoFilled(null);
      setAutoFillNote(null);
      try {
        const outcome = await applyToAll({
          dataUrl: bulkSignature?.dataUrl ?? '',
          date: selectedDate,
          replace,
        });
        if (outcome.error) {
          setError(outcome.error);
        } else if (outcome.failures.length > 0) {
          setError(
            `${outcome.failures.length} of ${
              outcome.failures.length +
              outcome.placedSignatureIds.length +
              outcome.skipped.length
            } slots could not be signed. The rest were filled.`,
          );
        }
      } catch (cause) {
        setError(isNewIssuesConflict(cause) ? NEW_ISSUES_MESSAGE : describeBulkError(cause));
      } finally {
        setBusy(false);
        // The busy flag disabled the box while the run went out; send the
        // user back to it now that it can take a date again.
        if (missingDate) setTimeout(() => focusDateInput('bulk-date'), 0);
      }
    },
    [bulkSignature, applyToAll, selectedDate, emptySigSlots.length, dateSlots, dateValues],
  );

  return {
    bulkSignature,
    setBulkSignature,
    bulkProgress,
    setBulkProgress,
    bulkFailures,
    setBulkFailures,
    bulkSkipped,
    setBulkSkipped,
    applyToAll,
    runBulkApply,
    dateMessage,
  };
}

export type BulkSigning = ReturnType<typeof useBulkSigning>;
