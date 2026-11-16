import type { ComparisonResult, FmcsaViolation, Inconsistency, MissingField } from '../types/comparison';

export interface OverrideState {
  criticalMissing: MissingField[];
  criticalFmcsa: FmcsaViolation[];
  criticalInconsistencies: Inconsistency[];
  /** "Approve anyway (override)" may be offered. */
  eligible: boolean;
}

/**
 * Override eligibility, decided from the loaded result bucket by bucket. A
 * reviewer may accept empty required fields, cross-field inconsistencies and
 * FMCSA violations, with a written reason. The server applies the same rule.
 */
export function computeOverrideState(result: ComparisonResult | null): OverrideState {
  const criticalMissing =
    result?.missing_required.filter((issue) => issue.severity === 'critical') ?? [];
  const criticalFmcsa =
    result?.fmcsa_violations.filter((issue) => issue.severity === 'critical') ?? [];
  const criticalInconsistencies =
    result?.inconsistencies.filter((issue) => issue.severity === 'critical') ?? [];
  const eligible = Boolean(
    result &&
      !result.can_sign &&
      (criticalMissing.length > 0 || criticalInconsistencies.length > 0 || criticalFmcsa.length > 0),
  );
  return { criticalMissing, criticalFmcsa, criticalInconsistencies, eligible };
}
