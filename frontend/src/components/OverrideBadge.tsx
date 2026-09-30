import { describeOverrideCounts, describeOverriddenFields } from '../lib/override';
import type { ComparisonResult } from '../types/comparison';
import { readOverride } from '../types/contract';
import type { ContractDetail } from '../types/contract';

interface Props {
  contract: ContractDetail | null | undefined;
  /** Used to put the skipped items in words (field labels, inconsistency text). */
  result?: ComparisonResult | null;
  /** Print the reason and the skipped items next to the badge. */
  detailed?: boolean;
}

/**
 * "Approved with override" marker for a contract that a reviewer approved while
 * required fields were empty and/or cross-field inconsistencies remained.
 * Renders nothing for ordinary approvals, so it can sit next to any heading.
 */
export function OverrideBadge({ contract, result, detailed = true }: Props) {
  const override = readOverride(contract);
  if (!override.active) return null;

  const items = describeOverriddenFields(override.fields, result);
  const missing = items.filter((item) => item.kind === 'missing').length;
  const inconsistencies = items.length - missing;
  const counts = describeOverrideCounts(missing, inconsistencies);
  const skipped = counts ? `${counts} skipped` : 'Nothing listed as skipped';
  const list = items.map((item) => item.text).join('; ');
  const title = [skipped, list ? `Skipped: ${list}` : '', override.reason ? `Reason: ${override.reason}` : '']
    .filter(Boolean)
    .join(' · ');

  return (
    <span className="override" title={title} data-testid="override-badge">
      <span className="badge badge--warn">Approved with override</span>
      {detailed && (
        <span className="override__meta">
          {skipped}
          {override.reason ? ` · ${override.reason}` : ''}
          {items.length > 0 && (
            <span className="override__items" data-testid="override-items">
              {items.map((item) => (
                <span key={item.id} className="override__item" data-override-id={item.id}>
                  {item.kind === 'inconsistency' ? 'Inconsistency: ' : 'Empty: '}
                  {item.text}
                </span>
              ))}
            </span>
          )}
        </span>
      )}
    </span>
  );
}
