import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../api/client';
import { editorApi, isAnnotationsUnsupported, type EditorFinalizeBody } from '../api/editor';
import type { DatePlacementInput, FinalizeConflictDetails } from '../api/signatures';
import type { AnnotationRequest } from '../lib/annotations';
import { layoutForRequest, type FinalizeLayout } from '../lib/layout';
import type { FinalizeMissing } from '../lib/signing';

export const ANNOTATIONS_UNSUPPORTED_NOTE =
  'This server does not accept annotations yet (finalize contract v4 is not deployed). ' +
  'Nothing was lost: the annotations are still saved in this browser.';

/** `POST /finalize`, whichever screen sends it: one endpoint, one body shape. */
export type FinalizeBody = EditorFinalizeBody & { dates?: DatePlacementInput[] };

export interface FinalizeInputs {
  /**
   * One ISO value per date slot, keyed by template field id. The server looks
   * up the coordinates itself; nothing here decides where a date prints.
   * `undefined` leaves the key out altogether (the editor never sent it).
   */
  dates?: Record<string, string>;
  layout: FinalizeLayout;
  /** Every slot this contract really has: the layout may only name these (422 otherwise). */
  slotIds: string[];
  /** The signature slots among them: `slot_scales` is signature-only. */
  signatureSlotIds: string[];
  /**
   * Free annotations. `undefined`: the caller does not deal in annotations at
   * all. An empty list: none this time -- the key is still left out, so a
   * server that predates contract v4 never has to see it.
   */
  annotations?: AnnotationRequest[];
}

/** The one place the finalize body is put together. */
export function buildFinalizeBody({
  dates,
  layout,
  slotIds,
  signatureSlotIds,
  annotations,
}: FinalizeInputs): FinalizeBody {
  const body: FinalizeBody = {};
  if (dates) {
    body.dates = Object.entries(dates)
      .filter(([, value]) => value)
      .map(([field_id, value]) => ({ field_id, value }));
  }
  body.apply_paired_dates = false;
  // `slot_scales` is a signature-only map (a date field id is a 422) and only
  // the sizes that differ from the global slider travel.
  body.layout = layoutForRequest(layout, slotIds, signatureSlotIds);
  // Free signatures are NOT in here: they are already rows on the contract
  // and finalize reads them itself.
  if (annotations && annotations.length > 0) body.annotations = annotations;
  return body;
}

interface Options {
  contractId: string | undefined;
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  /**
   * A refusal in words, for this screen. Called for everything except the
   * "no annotations on this server" 422, which is answered here. May act too
   * (the signing page drops layout keys the server did not recognise).
   */
  describeError: (cause: unknown) => string;
}

/**
 * Generate the final contract and go to it.
 *
 * Whatever the screen, a 409 that names what is still missing is kept in
 * `missing` so the page can list it, and success navigates to
 * `/contracts/:id/final` with the new file hash. How the button is gated and
 * how other refusals read stay with each screen until W1 merges them.
 */
export function useFinalize({ contractId, setBusy, setError, describeError }: Options) {
  const navigate = useNavigate();
  const [missing, setMissing] = useState<FinalizeMissing | null>(null);

  const finalize = useCallback(
    async (inputs: FinalizeInputs) => {
      if (!contractId) return;
      setBusy(true);
      setError(null);
      setMissing(null);
      try {
        const done = await editorApi.finalize(contractId, buildFinalizeBody(inputs));
        navigate(`/contracts/${contractId}/final`, { state: { fileHash: done.file_hash } });
      } catch (cause) {
        if (inputs.annotations !== undefined && isAnnotationsUnsupported(cause)) {
          setError(ANNOTATIONS_UNSUPPORTED_NOTE);
          return;
        }
        // A 409 names exactly which slots the server still wants; show them
        // rather than the generic message so the user knows what to fill in.
        if (cause instanceof ApiError && cause.status === 409) {
          const details = cause.details as FinalizeConflictDetails;
          const missingSignatures = details.missing_signature_slots ?? [];
          const missingDates = details.missing_date_fields ?? [];
          if (missingSignatures.length > 0 || missingDates.length > 0) {
            setMissing({ signatures: missingSignatures, dates: missingDates });
          }
        }
        setError(describeError(cause));
      } finally {
        setBusy(false);
      }
    },
    [contractId, setBusy, setError, describeError, navigate],
  );

  return { finalize, missing };
}
