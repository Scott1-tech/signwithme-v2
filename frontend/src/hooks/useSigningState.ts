import { useCallback, useMemo } from 'react';
import type { SignatureSlots } from './useSignatureSlots';
import type { DateValues } from './useDateValues';
import type { Signature } from '../types/contract';

/**
 * What is signed and what is still empty, derived from the server's signature
 * rows, the template's slots and the chosen dates. Nothing here is state of
 * its own; it is the one place these questions are answered.
 */
export function useSigningState(
  signatures: Signature[],
  { sigSlots, dateSlots, sigSlotIds }: Pick<SignatureSlots, 'sigSlots' | 'dateSlots' | 'sigSlotIds'>,
  dateValues: DateValues,
) {
  const signaturesByPage = useMemo(() => {
    const byPage = new Map<number, Signature[]>();
    signatures.forEach((signature) => {
      const page = signature.page_number;
      if (!page) return;
      const list = byPage.get(page);
      if (list) list.push(signature);
      else byPage.set(page, [signature]);
    });
    return byPage;
  }, [signatures]);

  const signedFieldIds = useMemo(
    () => new Set(signatures.map((signature) => signature.field_id).filter(Boolean) as string[]),
    [signatures],
  );
  const signatureForSlot = useCallback(
    (fieldId: string) => signatures.find((signature) => signature.field_id === fieldId) ?? null,
    [signatures],
  );

  // Anything not tied to a known signature slot. A free placement is a real
  // signature on the page, but the server does not count it towards any slot.
  const freeSignatures = useMemo(
    () => signatures.filter((signature) => !signature.field_id || !sigSlotIds.has(signature.field_id)),
    [signatures, sigSlotIds],
  );

  // Local readiness check so the button can explain itself. The server runs
  // the same check at finalize and is the one that actually decides.
  const slotsComplete = useMemo(
    () =>
      sigSlots.filter((slot) => slot.required).every((slot) => signedFieldIds.has(slot.field_id)) &&
      dateSlots.filter((slot) => slot.required).every((slot) => Boolean(dateValues[slot.field_id])),
    [sigSlots, dateSlots, signedFieldIds, dateValues],
  );

  // What a bulk run would still have to do. A signature slot with no bbox is
  // not reachable from here at all, so it is never counted as a target.
  const emptySigSlots = useMemo(
    () => sigSlots.filter((slot) => slot.bbox && !signedFieldIds.has(slot.field_id)),
    [sigSlots, signedFieldIds],
  );
  const emptyDateSlots = useMemo(
    () => dateSlots.filter((slot) => !dateValues[slot.field_id]),
    [dateSlots, dateValues],
  );

  return {
    signaturesByPage,
    signedFieldIds,
    signatureForSlot,
    freeSignatures,
    slotsComplete,
    emptySigSlots,
    emptyDateSlots,
  };
}

export type SigningState = ReturnType<typeof useSigningState>;
