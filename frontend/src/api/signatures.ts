import type { FinalizeLayoutRequest } from '../lib/layout';
import type { BBox } from '../types/field';
import type { Signature } from '../types/contract';
import { ApiError, api, tokenStore } from './client';

/**
 * One date value for one template date field. No bbox on purpose: the server
 * takes the coordinates from the template, so the client cannot steer where a
 * date lands. `page` is informational only.
 */
export interface DatePlacementInput {
  field_id: string;
  value: string;
  page?: number;
}

/** Shape of `ApiError.details` on a 409 from finalize. */
export interface MissingSlotRef {
  field_id: string;
  label: string;
  page: number | null;
}

export interface FinalizeConflictDetails {
  missing_signature_slots?: MissingSlotRef[];
  missing_date_fields?: MissingSlotRef[];
}

/** A slot that now holds a signature. Position comes from the template. */
export interface BulkPlaced {
  field_id: string;
  signature_id: string;
  page_number: number;
  bbox: BBox;
}

/** A slot the server deliberately left alone. Today the only reason is
 * `already_signed`: an existing signature is never overwritten. */
export interface BulkSkipped {
  field_id: string;
  reason: 'already_signed' | string;
}

/** A slot that could not take the signature at all. */
export interface BulkFailed {
  field_id: string;
  reason: 'unknown_field' | 'not_a_signature_field' | 'page_out_of_range' | string;
}

/**
 * Always a 200, even when nothing was placed: partial success is the normal
 * outcome. Every requested field id comes back in exactly one of the lists.
 */
export interface BulkResult {
  contract_id: string;
  placed: BulkPlaced[];
  skipped: BulkSkipped[];
  failed: BulkFailed[];
}

/** The server's per-request cap on `field_ids`; longer runs must be chunked. */
export const MAX_BULK_SLOTS = 200;

/** Statuses that mean "this server has no bulk endpoint", i.e. fall back to
 * the one-request-per-slot loop. Anything else is a real refusal. */
export function isBulkUnsupported(cause: unknown): boolean {
  return cause instanceof ApiError && (cause.status === 404 || cause.status === 405);
}

/**
 * Turn a refused bulk request into something a signer can act on.
 *
 * The bulk endpoint rejects the *whole* request only for reasons that apply to
 * the whole request -- the image, the contract's state, or access -- so each
 * status maps to one concrete sentence rather than a generic failure.
 */
export function describeBulkError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 413) {
      return 'That signature image is too large (the limit is 2 MB). Choose or draw a smaller one.';
    }
    if (cause.status === 409) {
      return 'This contract is not in a state where it can be signed right now.';
    }
    if (cause.status === 403) {
      return 'You are not allowed to sign this contract.';
    }
    if (cause.status === 422) {
      return `The signature could not be applied: ${cause.message}`;
    }
    return cause.message;
  }
  return cause instanceof Error ? cause.message : 'Could not apply the signature';
}

/**
 * The signing gate refused an overridden contract: a critical issue appeared
 * after approval that the override did not cover. Recognised by an explicit
 * reason if the server sends one, else by the gate's own message.
 */
export function isNewIssuesConflict(cause: unknown): boolean {
  if (!(cause instanceof ApiError) || cause.status !== 409) return false;
  const reason = cause.details.reason;
  if (typeof reason === 'string' && /new_(critical_)?issues|override_stale|not_covered/i.test(reason)) {
    return true;
  }
  return /unresolved critical issues|new (critical )?issues/i.test(cause.message);
}

export const NEW_ISSUES_MESSAGE =
  'New issues appeared after approval — the contract must be reviewed again.';

export const signaturesApi = {
  list: (contractId: string) => api.get<Signature[]>(`/api/contracts/${contractId}/signatures`),

  place: (
    contractId: string,
    placement: {
      page: number;
      bbox: BBox;
      field_id?: string | null;
      signer_name?: string;
      signer_role?: 'contractor' | 'company_rep';
      image_data: string;
    },
  ) => api.post<Signature>(`/api/contracts/${contractId}/signatures`, placement),

  /**
   * One captured image onto many template signature slots in a single request.
   *
   * Deliberately narrower than `place`: no `page` and no `bbox` travel at all,
   * so every position is resolved from the template server-side. The image is
   * decoded once instead of once per slot, which is the whole point.
   */
  placeBulk: (
    contractId: string,
    input: {
      imageData: string;
      fieldIds: string[];
      signerRole?: 'contractor' | 'company_rep';
    },
  ) =>
    api.post<BulkResult>(`/api/contracts/${contractId}/signatures/bulk`, {
      image_data: input.imageData,
      field_ids: input.fieldIds,
      signer_name: null,
      signer_email: null,
      signer_role: input.signerRole ?? 'contractor',
    }),

  remove: (contractId: string, signatureId: string) =>
    api.del<{ removed: boolean }>(`/api/contracts/${contractId}/signatures/${signatureId}`),

  /**
   * The placed signature's image as the server will print it (background
   * removed, cropped to ink). Streams PNG bytes, so an authorised `fetch` in
   * the style of `fetchPdf` rather than the JSON client.
   */
  imageBlob: async (contractId: string, signatureId: string): Promise<Blob> => {
    const token = tokenStore.get();
    const response = await fetch(
      `/api/contracts/${contractId}/signatures/${signatureId}/image`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    );
    if (!response.ok) {
      throw new ApiError(
        response.status,
        'signature_image_failed',
        `Could not load the signature image (${response.status})`,
      );
    }
    return response.blob();
  },

  finalize: (
    contractId: string,
    payload: {
      contract_date?: string;
      dates?: DatePlacementInput[];
      apply_paired_dates?: boolean;
      /** Position/appearance tuning; out-of-range values are refused with 422. */
      layout?: FinalizeLayoutRequest;
    },
  ) =>
    api.post<{ contract_id: string; status: string; file_hash: string; download_url: string }>(
      `/api/contracts/${contractId}/finalize`,
      payload,
    ),
};
