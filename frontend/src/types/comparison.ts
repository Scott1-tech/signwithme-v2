import type { TemplateMatch } from '../lib/warnings';
import type { BBox, FieldType } from './field';

/** `info` never blocks approval; it marks work left for the signing step. */
export type Severity = 'critical' | 'warning' | 'info';

export interface MissingField {
  field_id: string;
  label: string;
  page: number | null;
  type: FieldType | null;
  /** Present so the highlighter can draw the box. A label alone is not enough. */
  bbox: BBox | null;
  severity: Severity;
}

/**
 * One shape for both cross-field mismatches and per-field format problems, so
 * the sidebar renders them with a single component.
 */
export interface Inconsistency {
  type: string;
  issue: string;
  values: Record<string, unknown>;
  /** Pages stated explicitly; the UI never parses them out of field-id prefixes. */
  pages: number[];
  field_id?: string | null;
  /** Set for single-field format problems so the highlighter can draw the box;
   *  absent/null for cross-field mismatches, which span no single region. */
  bbox?: BBox | null;
  severity: Severity;
}

export interface FmcsaViolation {
  rule: string;
  issue: string;
  page: number | null;
  field_ids: string[];
  severity: Severity;
}

/**
 * A signature or date slot the template expects to be filled at signing time.
 * Reported by the comparison so the review page can show what is still to be
 * signed, but it is not an "issue": it never blocks approval, and the server
 * re-checks every required slot at finalize regardless of what the UI sends.
 */
export interface SignatureSlot {
  field_id: string;
  label: string;
  page: number | null;
  bbox: BBox | null;
  /**
   * Top edge of the rule's ink under the slot, PDF points, top-left origin
   * (same space as `bbox`). `null` -- or absent from an older server -- means
   * no rule was found and `bbox.y + bbox.height` stands in for it.
   */
  rule_y?: number | null;
  kind: 'signature' | 'date';
  required: boolean;
  /** Set when kind=signature: the date field printed alongside it. */
  paired_date_field_id: string | null;
  /** Set when kind=date: the signature this date belongs to. */
  paired_signature_field_id: string | null;
  severity: 'info';
}

export interface ComparisonResult {
  contract_id: string | null;
  completion_percentage: number;
  missing_required: MissingField[];
  inconsistencies: Inconsistency[];
  fmcsa_violations: FmcsaViolation[];
  warnings: Inconsistency[];
  /** Absent on comparisons stored before slots were reported; read as `?? []`. */
  signature_slots?: SignatureSlot[];
  /** Additive: absent/null on older servers. Read through `readIdenticalTemplate`. */
  template_match?: TemplateMatch | null;
  can_sign: boolean;
  field_count: number;
  filled_count: number;
}

export type IssueKind = 'missing' | 'inconsistency' | 'fmcsa' | 'warning' | 'signature_slot';

/** A page-anchored issue, normalised from all five buckets for rendering. */
export interface PageIssue {
  kind: IssueKind;
  key: string;
  label: string;
  detail: string;
  severity: Severity;
  page: number | null;
  fieldId: string | null;
  bbox: BBox | null;
}
