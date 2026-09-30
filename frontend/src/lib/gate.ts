import type { ComparisonResult, FmcsaViolation, Inconsistency, MissingField } from '../types/comparison';
import { readOverride, type ContractDetail, type ReviewOverride } from '../types/contract';

export type CriticalIssue = MissingField | Inconsistency | FmcsaViolation;

/** Whether signing may start here, and if not, why. */
export interface SigningGate {
  override: ReviewOverride;
  /** The override is on record and nothing it cannot waive (FMCSA) is open. */
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
 * A reviewer override waives `can_sign=false` here: the empty required fields
 * were accepted on record, so the page must not re-block on them. The server
 * remains the judge at placement and finalize; this only opens the UI.
 *
 * An override covers empty fields and cross-field inconsistencies, never an
 * FMCSA violation. If a new issue appears after approval the server answers
 * 409 at placement/finalize and the page says the contract needs review again.
 */
export function computeSigningGate(
  contract: ContractDetail | null,
  result: ComparisonResult | null,
): SigningGate {
  const override = readOverride(contract);
  const criticalFmcsaCount =
    result?.fmcsa_violations.filter((violation) => violation.severity === 'critical').length ?? 0;
  const overrideCovers = override.active && criticalFmcsaCount === 0;
  const blocked =
    contract?.review_status !== 'approved' || (result?.can_sign === false && !overrideCovers);
  // Why it is blocked, so the page can say so and point somewhere instead of
  // swallowing every click in silence. "Not yet approved" and "critical issues
  // outstanding" are different problems with different next steps.
  const notApproved = Boolean(contract) && contract?.review_status !== 'approved';
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
