import { describeOverrideCounts } from '../lib/override';
import type { ComparisonResult } from '../types/comparison';

interface Props {
  result: ComparisonResult | null;
  processing?: boolean;
  /** The contract was approved by reviewer override although `can_sign` is false. */
  override?: boolean;
}

export function ComplianceBadge({ result, processing, override }: Props) {
  if (processing) return <span className="badge badge--neutral">Analysing…</span>;
  if (!result) return <span className="badge badge--neutral">Not analysed</span>;

  const criticalCount =
    result.missing_required.length +
    result.inconsistencies.filter((issue) => issue.severity === 'critical').length +
    result.fmcsa_violations.length;

  // The rules still say "blocked"; a reviewer decided otherwise and signed for
  // it. Amber, not green: the skipped issues are real and stay listed below.
  // Both waivable buckets count: empty required fields and inconsistencies.
  if (!result.can_sign && override) {
    const skipped = describeOverrideCounts(
      result.missing_required.filter((issue) => issue.severity === 'critical').length,
      result.inconsistencies.filter((issue) => issue.severity === 'critical').length,
    );
    return (
      <span className="badge badge--warn" data-testid="compliance-override">
        Approved with override{skipped ? ` · ${skipped} skipped` : ''}
      </span>
    );
  }

  // Slots are not issues: they are the work the signing step exists to do.
  // Mention the count so the reviewer knows what to expect, but keep it green.
  const slotCount = (result.signature_slots ?? []).length;

  if (result.can_sign && result.warnings.length === 0) {
    return (
      <span className="badge badge--good">
        {slotCount > 0 ? `Ready to sign · ${slotCount} slot${slotCount === 1 ? '' : 's'} to sign` : 'Ready to sign'}
      </span>
    );
  }
  if (result.can_sign) {
    return (
      <span className="badge badge--warn">
        Ready to sign · {result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'}
      </span>
    );
  }
  return (
    <span className="badge badge--bad">
      Blocked · {criticalCount} critical issue{criticalCount === 1 ? '' : 's'}
    </span>
  );
}
