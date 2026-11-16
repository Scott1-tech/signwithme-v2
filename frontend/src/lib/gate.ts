import type { ComparisonResult, FmcsaViolation, Inconsistency, MissingField } from '../types/comparison';
import { readOverride, type ContractDetail, type ReviewOverride } from '../types/contract';

export type CriticalIssue = MissingField | Inconsistency | FmcsaViolation;

/** Whether signing may start here, and if not, why. */
export interface SigningGate {
  override: ReviewOverride;
  /** The reviewer's override is on record and covers the critical issues it was given for. */
  overrideCovers: boolean;
  blocked: boolean;
  /** Blocked because no reviewer approved it yet (as opposed to open issues). */
  notApproved: boolean;
  criticalIssues: CriticalIssue[];
  /** Short machine reason for `blocked`, `null` when signing is open. */
  reason: 'not_approved' | 'critical_issues' | null;
}

/**
 * The signing page's gate, as a pure function of what the page has loaded.
 *
 * A reviewer override waives `can_sign=false` here: the open critical issues
 * (empty required fields, inconsistencies, FMCSA violations) were accepted on
 * record, so the page must not re-block on them. The server remains the judge
 * at placement and finalize; it re-checks the override against the current
 * comparison and answers 409 when something new has appeared.
 */
export function computeSigningGate(
  contract: ContractDetail | null,
  result: ComparisonResult | null,
): SigningGate {
  const override = readOverride(contract);
  const overrideCovers = override.active;
  // Approval is no longer required (owner decision). Only open critical issues
  // still block signing, and an override is what lets them through.
  const blocked = result?.can_sign === false && !overrideCovers;
  const notApproved: boolean = false;
  const criticalIssues: CriticalIssue[] = result
    ? [...result.missing_required, ...result.inconsistencies, ...result.fmcsa_violations].filter(
        (issue) => issue.severity === 'critical',
      )
    : [];
  return {
    override,
    overrideCovers,
    blocked,
    notApproved,
    criticalIssues,
    reason: !blocked ? null : notApproved ? 'not_approved' : 'critical_issues',
  };
}
