import { datesNeededText } from '../lib/signing';
import type { SignatureSlot } from '../types/comparison';

interface Props {
  blocked: boolean;
  /** Why it is blocked, in words (`null` while signing is open). */
  blockedReason: string | null;
  signatureCount: number;
  /** Every required slot signed and every required date chosen (local check). */
  slotsComplete: boolean;
  /** Required signature slots still empty / required date slots still without a date. */
  missingSignatures: SignatureSlot[];
  missingDates: SignatureSlot[];
  /** Point the page (and the panel) at the first of what is missing. */
  onShowMissing: (fieldId: string) => void;
}

/**
 * What stands between the contract and «Generate final contract» (W1), in one
 * line, always on screen: the bar sticks to the bottom of the panel whichever
 * sections are open.
 *
 * F1a: the button itself (`editor-finalize`) moved to the editor's top bar
 * (`workspace/EditorTopBar.tsx`, EDITOR-V2-PLAN §7), gated by the same rule
 * (`finalizeDisabled`). This bar keeps the readiness line
 * (`finalize-readiness`, with «Show»), the reason while signing is blocked
 * (`finalize-blocked-reason`) and says when everything is ready.
 */
export function FinalizeBar({
  blocked,
  blockedReason,
  signatureCount,
  slotsComplete,
  missingSignatures,
  missingDates,
  onShowMissing,
}: Props) {
  const firstMissing = missingSignatures[0] ?? missingDates[0] ?? null;
  const parts: string[] = [];
  if (missingSignatures.length > 0) {
    parts.push(`${missingSignatures.length} signature${missingSignatures.length === 1 ? '' : 's'}`);
  }
  if (missingDates.length > 0) {
    parts.push(`${missingDates.length} date${missingDates.length === 1 ? '' : 's'}`);
  }
  return (
    <div className="card finalize-bar" data-testid="finalize-bar">
      {blocked && blockedReason && (
        <p className="hint hint--error" data-testid="finalize-blocked-reason">
          {blockedReason}
        </p>
      )}
      {!blocked && !slotsComplete && (
        <p className="hint" data-testid="finalize-readiness">
          Sign every required slot and choose each required date first.
          {parts.length > 0 && (
            <>
              {' '}
              Still missing: {parts.join(', ')}.{' '}
              {firstMissing && (
                <button
                  type="button"
                  className="button button--link"
                  onClick={() => onShowMissing(firstMissing.field_id)}
                >
                  Show
                </button>
              )}
            </>
          )}
          {/* Dates are never filled in on their own, so an empty one is always
              the user's to choose; say so rather than just counting it. */}
          {missingDates.length > 0 && (
            <>
              {' '}
              <span data-testid="finalize-date-needed">{datesNeededText(missingDates.length)}</span>
            </>
          )}
        </p>
      )}
      {!blocked && slotsComplete && signatureCount === 0 && (
        <p className="hint" data-testid="finalize-no-signature">
          Place at least one signature first.
        </p>
      )}
      {!blocked && slotsComplete && signatureCount > 0 && (
        <p className="hint" data-testid="finalize-ready">
          Ready: «Generate final contract» is in the bar above the document.
        </p>
      )}
    </div>
  );
}
