import type { ComparisonResult } from '../types/comparison';
import type { Contract, ContractDetail, ContractField, DateField } from '../types/contract';
import { ApiError, api } from './client';

/** Minimum trimmed length of the review notes when approving with an override. */
export const OVERRIDE_NOTES_MIN = 10;

export type OverrideBlockingCategory = 'fmcsa_violations' | 'inconsistencies';

/** `details` of the 409 the server returns when an override was asked for but is not allowed. */
export interface OverrideNotAllowedDetails {
  reason: 'override_not_allowed';
  blocking_categories: OverrideBlockingCategory[];
}

export interface SubmitReviewOptions {
  /**
   * Approve although critical `missing_required` fields and/or cross-field
   * `inconsistencies` remain (never FMCSA violations). Requires notes.
   */
  overrideMissing?: boolean;
}

/**
 * Turns a failed `submitReview` into a sentence for the reviewer. The override
 * path has two server answers of its own: 422 (notes too short) and 409 with
 * `reason: "override_not_allowed"` naming which buckets still block.
 */
export function describeReviewError(cause: unknown, options: SubmitReviewOptions = {}): string {
  if (cause instanceof ApiError) {
    if (options.overrideMissing && cause.status === 422) {
      return `Review notes must be at least ${OVERRIDE_NOTES_MIN} characters to approve with an override.`;
    }
    // Only FMCSA violations are beyond an override now; inconsistencies can be waived.
    if (cause.status === 409 && cause.details.reason === 'override_not_allowed') {
      return 'Override is not allowed: FMCSA violations must be corrected first. Fix the fields, re-run the comparison and try again.';
    }
  }
  return cause instanceof Error ? cause.message : 'Review failed';
}

/** Turns a refused contract deletion into a sentence an admin can act on. */
export function describeContractDeleteError(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 409) {
      return 'This contract is still being processed. Wait until processing finishes, then delete it.';
    }
    if (cause.status === 403) return 'Only an admin can delete contracts.';
    if (cause.status === 404) return 'This contract no longer exists; it was already deleted.';
    return cause.message;
  }
  return cause instanceof Error ? cause.message : 'Could not delete the contract';
}

/** An earlier contract uploaded from the same PDF bytes (no contractor details). */
export interface ContractDuplicate {
  id: string;
  status: string;
  review_status: string;
  template_id: string;
  template_name: string | null;
  created_at: string;
}

export const contractsApi = {
  /** `sha256`: lowercase hex SHA-256 of the raw PDF bytes (see lib/sha256). */
  duplicates: (sha256: string, signal?: AbortSignal) =>
    api.get<{ duplicates: ContractDuplicate[] }>(
      `/api/contracts/duplicates?sha256=${encodeURIComponent(sha256)}`,
      signal,
    ),

  list: (params: { status?: string; templateId?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.templateId) query.set('template_id', params.templateId);
    const suffix = query.toString() ? `?${query}` : '';
    return api.get<Contract[]>(`/api/contracts${suffix}`);
  },

  get: (id: string) => api.get<ContractDetail>(`/api/contracts/${id}`),

  /** Admin only. Permanent: the contract, its signatures and any final PDF. */
  remove: (id: string) => api.del<{ id: string; deleted: boolean }>(`/api/contracts/${id}`),

  upload: (templateId: string, file: File, contractorName?: string, contractorEmail?: string) => {
    const form = new FormData();
    form.append('template_id', templateId);
    form.append('file', file);
    if (contractorName) form.append('contractor_name', contractorName);
    if (contractorEmail) form.append('contractor_email', contractorEmail);
    return api.upload<{ contract_id: string; task_id: string | null; status: string }>(
      '/api/contracts',
      form,
    );
  },

  comparison: (id: string, signal?: AbortSignal) =>
    api.get<ComparisonResult>(`/api/contracts/${id}/compare`, signal),

  rerunComparison: (id: string) => api.post<ComparisonResult>(`/api/contracts/${id}/compare`),

  fields: (id: string, page?: number) =>
    api.get<ContractField[]>(`/api/contracts/${id}/fields${page ? `?page=${page}` : ''}`),

  correctField: (
    id: string,
    fieldId: string,
    correction: { value?: string | null; selected?: string[]; checked?: boolean },
  ) =>
    api.patch<ContractField>(
      `/api/contracts/${id}/fields/${encodeURIComponent(fieldId)}`,
      correction,
    ),

  dateFields: (id: string) => api.get<DateField[]>(`/api/contracts/${id}/date-fields`),

  submitReview: (
    id: string,
    decision: 'approved' | 'rejected',
    notes?: string,
    options: SubmitReviewOptions = {},
  ) =>
    api.post<Contract>(`/api/review/${id}`, {
      decision,
      notes,
      // Only sent when asked for, so the request stays byte-identical to the
      // pre-override shape for ordinary approvals and rejections.
      ...(options.overrideMissing ? { override_missing: true } : {}),
    }),

  reviewQueue: () => api.get<Contract[]>('/api/review/queue'),

  auditTrail: (id: string) =>
    api.get<
      {
        action: string;
        actor_email: string | null;
        actor_role: string | null;
        details: Record<string, unknown>;
        timestamp: string;
      }[]
    >(`/api/review/${id}/audit`),

  verifyFinal: (id: string) =>
    api.get<{ recorded_hash: string; current_hash: string; intact: boolean }>(
      `/api/download/${id}/verify`,
    ),
};
