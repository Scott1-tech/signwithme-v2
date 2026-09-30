import { useCallback, useEffect, useState } from 'react';
import { signaturesApi } from '../api/signatures';
import type { Signature } from '../types/contract';
import type { BBox } from '../types/field';

export interface PlacementRequest {
  page: number;
  bbox: BBox;
  /** A template slot, or `null` for a free placement at `bbox`. */
  fieldId: string | null;
  imageData: string;
}

/**
 * The signature rows on one contract, as the server holds them.
 *
 * Every row is created, hashed and audited server-side the moment it is made,
 * so nothing here is undoable locally: `place` and `remove` are the requests
 * themselves, and the list only follows what the server answered. `setRows`
 * is for callers that already hold the server's answer (the bulk run).
 *
 * `freeOnly` keeps just the rows with no template slot (the editor's view).
 */
export function useContractSignatures(
  contractId: string | undefined,
  { freeOnly = false }: { freeOnly?: boolean } = {},
) {
  const [rows, setRows] = useState<Signature[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!contractId) return;
    let cancelled = false;
    signaturesApi
      .list(contractId)
      .then((list) => {
        if (!cancelled) setRows(freeOnly ? list.filter((row) => row.field_id === null) : list);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [contractId, freeOnly]);

  /** Create one row. Throws the server's refusal; the list only grows on success. */
  const place = useCallback(
    async (request: PlacementRequest): Promise<Signature> => {
      if (!contractId) throw new Error('No contract');
      // When `field_id` is set the server positions the image from the
      // template, not from `bbox`.
      const created = await signaturesApi.place(contractId, {
        page: request.page,
        bbox: request.bbox,
        field_id: request.fieldId,
        image_data: request.imageData,
      });
      // The server is the authority on where it actually landed.
      setRows((current) => [...current.filter((row) => row.id !== created.id), created]);
      return created;
    },
    [contractId],
  );

  /** Delete one row. Throws the server's refusal; the list only shrinks on success. */
  const remove = useCallback(
    async (signatureId: string): Promise<void> => {
      if (!contractId) return;
      await signaturesApi.remove(contractId, signatureId);
      setRows((current) => current.filter((row) => row.id !== signatureId));
    },
    [contractId],
  );

  return { rows, loaded, setRows, place, remove };
}
