import { ComparisonSidebar } from '../components/ComparisonSidebar';
import { OverrideBadge } from '../components/OverrideBadge';
import { OVERRIDE_NOTES_MIN } from '../api/contracts';
import { describeOverrideCounts, pagesSuffix } from '../lib/override';
import type { OverrideState } from '../lib/reviewRules';
import type { ComparisonResult, PageIssue } from '../types/comparison';
import type { ContractDetail } from '../types/contract';

const NOTES_EXAMPLE = "e.g. CDL verified against the driver's license copy; the letter on page 14 is a typo";

interface Props {
  contract: ContractDetail | null;
  result: ComparisonResult | null;
  issues: PageIssue[];
  currentPage: number;
  processing: boolean;
  activeFieldId: string | null;
  onNavigateToIssue: (issue: PageIssue) => void;
  onCorrectField: (issue: PageIssue, value: string) => Promise<void>;
  canCorrect: boolean;
  correcting: boolean;
  /** The signed-in user may decide (reviewer or above). */
  isReviewer: boolean;
  overrideActive: boolean;
  overrideState: OverrideState;
  notes: string;
  onNotesChange: (notes: string) => void;
  busy: boolean;
  onDecide: (decision: 'approved' | 'rejected') => void;
  onApproveWithOverride: () => Promise<void>;
  /** "Continue to signing": the workspace switches to its signing panel in place. */
  onContinueToSigning: () => void;
}

/**
 * The review part of the right-hand panel: the comparison sidebar, the
 * reviewer's decision (with the override path) and "Continue to signing".
 * Markup moved out of `ReviewPage` unchanged (W0); the decisions themselves
 * stay with the page.
 */
export function ReviewSection({
  contract,
  result,
  issues,
  currentPage,
  processing,
  activeFieldId,
  onNavigateToIssue: goToIssue,
  onCorrectField: correctField,
  canCorrect,
  correcting,
  isReviewer,
  overrideActive,
  overrideState: {
    criticalMissing,
    criticalInconsistencies,
    eligible: overrideEligible,
    unavailable: overrideUnavailable,
    blockers: overrideBlockers,
  },
  notes,
  onNotesChange: setNotes,
  busy,
  onDecide: decide,
  onApproveWithOverride: approveWithOverride,
  onContinueToSigning,
}: Props) {
  const notesLength = notes.trim().length;
  const notesSufficient = notesLength >= OVERRIDE_NOTES_MIN;

  return (
    <>
    <ComparisonSidebar
      result={result}
      issues={issues}
      currentPage={currentPage}
      processing={processing}
      onNavigateToIssue={goToIssue}
      activeFieldId={activeFieldId}
      onCorrectField={correctField}
      canCorrect={canCorrect}
      correcting={correcting}
      override={overrideActive}
    />

    {isReviewer && contract && contract.review_status === 'pending' && (
      <div className="review__decision card">
        <h3>Decision</h3>

        {overrideEligible && (
          <div className="alert alert--warn" data-testid="override-eligible">
            <p>
              Approval is blocked by{' '}
              {describeOverrideCounts(criticalMissing.length, criticalInconsistencies.length)}.
            </p>
            {criticalMissing.length > 0 && (
              <>
                <p>
                  {criticalMissing.length} required field
                  {criticalMissing.length === 1 ? ' is' : 's are'} empty:
                </p>
                <ul className="override-list" data-testid="override-missing">
                  {criticalMissing.map((field) => (
                    <li key={field.field_id}>
                      {field.label}
                      {field.page ? ` · Page ${field.page}` : ''}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {criticalInconsistencies.length > 0 && (
              <>
                <p>Cross-field inconsistencies:</p>
                <ul className="override-list" data-testid="override-inconsistencies">
                  {criticalInconsistencies.map((issue, index) => (
                    <li key={`${issue.type}:${index}`}>
                      {issue.issue}
                      {pagesSuffix(issue.pages ?? [])}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p>
              Correct the fields and re-run the comparison, or approve anyway with a written
              reason.
            </p>
          </div>
        )}

        <textarea
          className="field__textarea"
          placeholder={
            overrideEligible
              ? `Review notes (required for override, at least ${OVERRIDE_NOTES_MIN} characters) — ${NOTES_EXAMPLE}`
              : 'Review notes (optional)'
          }
          value={notes}
          aria-required={overrideEligible}
          onChange={(event) => setNotes(event.target.value)}
        />
        {overrideEligible && (
          <p className={notesSufficient ? 'hint' : 'hint hint--error'} data-testid="notes-hint">
            {notesLength}/{OVERRIDE_NOTES_MIN} characters
            {notesSufficient
              ? ' · reason recorded in the audit trail'
              : ` · at least ${OVERRIDE_NOTES_MIN} required to approve with override`}
            <br />
            <span className="hint" data-testid="notes-example">
              Say why it is safe, {NOTES_EXAMPLE}.
            </span>
          </p>
        )}

        {result && !result.can_sign && !overrideEligible && (
          <p className="hint hint--error" data-testid="blocked-hint">
            Approval is blocked while critical issues remain. Correct the fields and re-run the
            comparison.
            {overrideUnavailable && overrideBlockers.length > 0 && (
              <>
                {overrideBlockers.map((line) => (
                  <span key={line} className="blocker-line" data-testid="override-blocker">
                    <br />
                    {line}
                  </span>
                ))}
              </>
            )}
          </p>
        )}

        <div className="review__actions">
          <button
            type="button"
            className="button button--ghost"
            disabled={busy}
            onClick={() => decide('rejected')}
          >
            Reject
          </button>
          {overrideEligible ? (
            // Replaces the ordinary approve button: two "approve" buttons,
            // one of them permanently disabled, would only invite confusion.
            <button
              type="button"
              className="button button--warning"
              disabled={busy || !notesSufficient}
              onClick={() => void approveWithOverride()}
            >
              Approve anyway (override)
            </button>
          ) : (
            <button
              type="button"
              className="button button--primary"
              disabled={busy || !result?.can_sign}
              onClick={() => decide('approved')}
            >
              Approve for signing
            </button>
          )}
        </div>
      </div>
    )}

    {contract && contract.review_status === 'approved' && contract.status !== 'completed' && (
      <div className="card">
        <p>Approved for signing.</p>
        <OverrideBadge contract={contract} result={result} />
        <button type="button" className="button button--primary" onClick={onContinueToSigning}>
          Continue to signing
        </button>
      </div>
    )}
    </>
  );
}
