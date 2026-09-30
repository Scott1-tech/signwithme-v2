import { pagesSuffix } from './override';
import type { ComparisonResult, FmcsaViolation, Inconsistency, MissingField } from '../types/comparison';

/**
 * What blocks an override: only critical FMCSA violations (empty fields and
 * cross-field inconsistencies can be waived). One line each, with its page.
 */
export function describeOverrideBlockers(result: ComparisonResult | null): string[] {
  if (!result) return [];
  const lines = result.fmcsa_violations
    .filter((violation) => violation.severity === 'critical')
    .map(
      (violation) =>
        `Override is not available: FMCSA violation must be corrected first: ${violation.issue || violation.rule}${pagesSuffix([violation.page])}`,
    );
  return [...new Set(lines)];
}

export interface OverrideState {
  criticalMissing: MissingField[];
  criticalFmcsa: FmcsaViolation[];
  criticalInconsistencies: Inconsistency[];
  /** "Approve anyway (override)" may be offered. */
  eligible: boolean;
  /** Blocked, and an override cannot help: a critical FMCSA violation is open. */
  unavailable: boolean;
  blockers: string[];
}

/**
 * Override eligibility, decided from the loaded result bucket by bucket.
 * Empty required fields and cross-field inconsistencies may be waived with a
 * written reason; a critical FMCSA violation never can, so the button is not
 * offered while one exists. The server applies the same rule.
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
      criticalFmcsa.length === 0 &&
      (criticalMissing.length > 0 || criticalInconsistencies.length > 0),
  );
  const unavailable = Boolean(result && !result.can_sign && criticalFmcsa.length > 0);
  return {
    criticalMissing,
    criticalFmcsa,
    criticalInconsistencies,
    eligible,
    unavailable,
    blockers: describeOverrideBlockers(result),
  };
}
