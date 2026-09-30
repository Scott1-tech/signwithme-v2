import { useCallback, useMemo } from 'react';
import { groupPositionedByPage } from '../lib/signing';
import type { ComparisonResult, SignatureSlot } from '../types/comparison';

/**
 * The template's signature and date slots, as the comparison reported them.
 *
 * Slots come from the comparison result, which already carries the pairing
 * between each signature and its date, so there is no separate template or
 * date-field fetch. Older comparisons may lack the bucket entirely.
 */
export function useSignatureSlots(result: ComparisonResult | null) {
  const slots = useMemo<SignatureSlot[]>(() => result?.signature_slots ?? [], [result]);
  const sigSlots = useMemo(() => slots.filter((slot) => slot.kind === 'signature'), [slots]);
  const dateSlots = useMemo(() => slots.filter((slot) => slot.kind === 'date'), [slots]);

  const dateSlotById = useMemo(
    () => new Map(dateSlots.map((slot) => [slot.field_id, slot])),
    [dateSlots],
  );
  const slotById = useMemo(() => new Map(slots.map((slot) => [slot.field_id, slot])), [slots]);
  const sigSlotIds = useMemo(() => new Set(sigSlots.map((slot) => slot.field_id)), [sigSlots]);

  // Pairing is resolved from either side, since the backend states it on both.
  const pairedDateFor = useCallback(
    (sig: SignatureSlot): SignatureSlot | null =>
      (sig.paired_date_field_id ? dateSlotById.get(sig.paired_date_field_id) : undefined) ??
      dateSlots.find((date) => date.paired_signature_field_id === sig.field_id) ??
      null,
    [dateSlotById, dateSlots],
  );

  const unpairedDateSlots = useMemo(() => {
    const taken = new Set(
      sigSlots.map((sig) => pairedDateFor(sig)?.field_id).filter(Boolean) as string[],
    );
    return dateSlots.filter((date) => !taken.has(date.field_id));
  }, [sigSlots, dateSlots, pairedDateFor]);

  // The viewer shows every page at once, so the overlay is built page by page
  // rather than filtered down to one "current" page. Grouping once keeps each
  // page's render a map lookup instead of a scan over every slot.
  const sigByPage = useMemo(() => groupPositionedByPage(sigSlots), [sigSlots]);
  const dateByPage = useMemo(() => groupPositionedByPage(dateSlots), [dateSlots]);

  return {
    slots,
    sigSlots,
    dateSlots,
    slotById,
    sigSlotIds,
    pairedDateFor,
    unpairedDateSlots,
    sigByPage,
    dateByPage,
  };
}

export type SignatureSlots = ReturnType<typeof useSignatureSlots>;
