import type { BBox } from './field';

export type ContractStatus =
  | 'uploaded'
  | 'parsing'
  | 'analyzed'
  | 'reviewing'
  | 'signing'
  | 'completed'
  | 'failed';

export type ReviewStatus = 'pending' | 'approved' | 'rejected';

export interface Contract {
  id: string;
  template_id: string;
  contractor_name: string | null;
  contractor_email: string | null;
  status: ContractStatus;
  review_status: ReviewStatus;
  error_message: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * One row of `GET /api/contracts` (`ContractListOut`): the plain contract plus
 * what the dashboard needs in order to show a list without a second request per
 * row -- the joined template name, and how much the comparison engine found
 * wrong, as counts only.
 *
 * Every added field is OPTIONAL on purpose. They are additive on the server, so
 * a build talking to an older backend simply gets the previous `ContractOut`
 * shape back and every marker below disappears; nothing may throw on that.
 *
 * `null` on a derived field means "this contract has no stored comparison yet"
 * (uploaded, parsing, or a failed parse). It does NOT mean "zero problems", so
 * a renderer must distinguish `0` from `null`/absent.
 */
export interface ContractListItem extends Contract {
  /** From the list query's LEFT JOIN; `null` when the template went missing. */
  template_name?: string | null;
  /** Critical entries per category, by the same severity rule the review and
   *  signing gates use (`app/services/review_override.py critical_counts`). */
  critical_missing_count?: number | null;
  critical_inconsistency_count?: number | null;
  critical_fmcsa_count?: number | null;
  /** The engine's stored verdict. Signing additionally honours a recorded
   *  override, so `can_sign === false` alone does not mean "cannot be signed". */
  can_sign?: boolean | null;
  review_override?: boolean;
  /**
   * Signature/date slots in the STORED comparison -- deliberately NOT the
   * number the signing screen shows. `GET /{id}/compare` drops slots on pages
   * the uploaded PDF does not have, and that decision needs the PDF itself,
   * which a list endpoint must not read. So this is the UNFILTERED count and
   * may be higher than the number of slots a signer will ever see; the
   * dashboard therefore does not display it (see `pages/Dashboard.tsx`).
   */
  stored_signature_slot_count?: number | null;
}

export interface ContractDetail extends Contract {
  extracted_data: Record<string, ExtractedValue>;
  comparison_result: Record<string, unknown>;
  final_available: boolean;
  /**
   * Reviewer override: the contract was approved although required fields were
   * still empty. Additive on the server; responses from before the feature may
   * omit all three, so always read them through `readOverride()`.
   */
  review_override?: boolean;
  review_override_reason?: string | null;
  /** Template field ids the reviewer chose to skip. */
  review_override_fields?: string[];
}

export interface ReviewOverride {
  active: boolean;
  reason: string | null;
  fields: string[];
}

/** Normalises the optional override trio to `false / null / []`. */
export function readOverride(contract: ContractDetail | null | undefined): ReviewOverride {
  return {
    active: contract?.review_override ?? false,
    reason: contract?.review_override_reason ?? null,
    fields: contract?.review_override_fields ?? [],
  };
}

export interface ExtractedValue {
  value: string | null;
  normalized?: string | null;
  confidence: number;
  detected: boolean;
  status?: string;
  selected?: string[];
  checked?: boolean;
  has_signature?: boolean;
  rows?: string[][];
}

export interface ContractField {
  template_field_id: string;
  field_type: string;
  status: 'empty' | 'filled' | 'invalid' | 'verified';
  raw_value: string | null;
  normalized_value: string | null;
  confidence_score: number | null;
  is_required: boolean;
  is_valid: boolean | null;
  validation_error: string | null;
  bbox: BBox | null;
  page_number: number | null;
  extra: Record<string, unknown>;
}

export interface DateField {
  field_id: string;
  label: string | null;
  page: number | null;
  bbox: BBox;
  required: boolean;
  paired_signature_field_id: string | null;
  current_value: string | null;
}

export interface Signature {
  id: string;
  contract_id: string;
  signer_name: string | null;
  signer_email: string | null;
  signer_role: 'contractor' | 'company_rep';
  page_number: number;
  bbox: BBox;
  field_id: string | null;
  signed_at: string;
}

// A reusable signature in the current user's private library. The raw image
// bytes are never part of this projection -- they are fetched on demand from
// the ownership-checked `/api/signatures/{id}/image` endpoint.
export interface UserSignature {
  id: string;
  label: string | null;
  is_default: boolean;
  signature_hash: string;
  created_at: string;
}
