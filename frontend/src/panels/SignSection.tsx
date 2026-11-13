import { useState } from 'react';
import { BulkSignPanel } from '../components/BulkSignPanel';
import { DatePicker } from '../components/DatePicker';
import { OverrideBadge } from '../components/OverrideBadge';
import { SlotCardList } from '../components/SlotCardList';
import type { BulkSigning } from '../hooks/useBulkSigning';
import type { DateValuesState } from '../hooks/useDateValues';
import type { SignatureSlots } from '../hooks/useSignatureSlots';
import type { SigningState } from '../hooks/useSigningState';
import type { SlotFocus } from '../hooks/useSlotFocus';
import type { SigningGate } from '../lib/gate';
import {
  CHOOSE_DATE_FIRST,
  datesNeededText,
  focusDateInput,
  slotCardId,
  type SignMode,
} from '../lib/signing';
import { readIdenticalTemplate, similarityPercent } from '../lib/warnings';
import type { ComparisonResult, SignatureSlot } from '../types/comparison';
import type { ContractDetail } from '../types/contract';

interface Props {
  contract: ContractDetail | null;
  result: ComparisonResult | null;
  gate: Pick<SigningGate, 'blocked' | 'notApproved' | 'criticalIssues'>;
  signMode: SignMode;
  onSignModeChange: (mode: SignMode) => void;
  autoFill: boolean;
  onAutoFillChange: (on: boolean) => void;
  autoFillNote: string | null;
  onUndoAutoFill: () => Promise<void>;
  slotSet: SignatureSlots;
  signing: SigningState;
  dates: DateValuesState;
  bulk: BulkSigning;
  focus: SlotFocus;
  /** Opens the chooser for the bulk image. */
  onPickBulkSignature: () => void;
  libraryEmpty: boolean;
  onStartSlot: (slot: SignatureSlot) => void;
  onRemoveSignature: (signatureId: string) => Promise<boolean>;
  busy: boolean;
  /** "Go to review": the workspace opens its review section (no navigation away). */
  onGoToReview: () => void;
}

/**
 * The signing part of the right-hand panel: the mode switch, the automatic
 * fill, the gate's explanation, "One for all", the slot cards, the free
 * placements and the contract date. Markup moved out of `SignaturePage`
 * unchanged (W0); it owns no state of its own.
 */
export function SignSection({
  contract,
  result,
  gate: { blocked, notApproved, criticalIssues },
  signMode,
  onSignModeChange: setSignMode,
  autoFill,
  onAutoFillChange: setAutoFill,
  autoFillNote,
  onUndoAutoFill: undoAutoFill,
  slotSet: { slots, sigSlots, dateSlots, unpairedDateSlots, pairedDateFor },
  signing: { signedFieldIds, signatureForSlot, freeSignatures, emptySigSlots, emptyDateSlots },
  dates,
  bulk,
  focus: { focusedSlotId, collapsedPages, togglePageGroup, jumpToSlot },
  onPickBulkSignature,
  libraryEmpty,
  onStartSlot: startSlot,
  onRemoveSignature: removeSignature,
  busy,
  onGoToReview,
}: Props) {
  const sameAsTemplate = readIdenticalTemplate(result?.template_match);
  const { values: dateValues, set: setDateValue, selectedDate, setSelectedDate } = dates;
  // "Apply to all date fields" pressed with no date chosen. Shown only while
  // the box is still empty; picking a date is the answer, so it goes away.
  const [contractDateRefused, setContractDateRefused] = useState(false);
  const applyDateToAll = () => {
    if (!selectedDate) {
      setContractDateRefused(true);
      focusDateInput('contract-date');
      return;
    }
    setContractDateRefused(false);
    dates.fillAll(
      dateSlots.map((slot) => slot.field_id),
      selectedDate,
    );
  };

  return (
    <>
      <div className="card">
        <div className="card__heading">
          <h2>How do you want to sign?</h2>
          <OverrideBadge contract={contract} result={result} />
          {sameAsTemplate && (
            <span
              className="badge badge--warn"
              data-testid="template-match-badge"
              title={`This contract appears to be the template file itself (page similarity ${similarityPercent(sameAsTemplate)}%). Check that the correct file was uploaded.`}
            >
              Same file as template? ({similarityPercent(sameAsTemplate)}%)
            </span>
          )}
        </div>
        <div className="mode-switch" role="radiogroup" aria-label="Signing mode">
          <button
            type="button"
            role="radio"
            aria-checked={signMode === 'bulk'}
            className={`chip${signMode === 'bulk' ? ' chip--active' : ''}`}
            onClick={() => setSignMode('bulk')}
          >
            One for all
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={signMode === 'slot'}
            className={`chip${signMode === 'slot' ? ' chip--active' : ''}`}
            onClick={() => setSignMode('slot')}
          >
            Each one separately
          </button>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={autoFill}
            onChange={(event) => setAutoFill(event.target.checked)}
          />
          Fill everything automatically when the page opens
        </label>

        {autoFillNote && (
          <div className="alert alert--good" data-testid="auto-fill-note">
            <p>{autoFillNote}</p>
            <div className="alert__actions">
              <button
                type="button"
                className="button button--small"
                disabled={busy}
                onClick={() => void undoAutoFill()}
              >
                Undo
              </button>
            </div>
          </div>
        )}

        {!blocked && emptyDateSlots.length > 0 && (
          <p className="hint" data-testid="dates-needed">
            {datesNeededText(emptyDateSlots.length)}
          </p>
        )}

        {blocked && (
          <div className="alert alert--error">
            {notApproved ? (
              <p>
                This contract has not been approved for signing yet. A reviewer must approve it
                first.
              </p>
            ) : (
              <p>
                Signing is blocked while critical issues remain
                {criticalIssues.length > 0
                  ? ` (${criticalIssues.length} outstanding)`
                  : ''}
                . Correct the fields on the review page, then re-run the comparison.
              </p>
            )}
            <button type="button" className="button button--primary" onClick={onGoToReview}>
              {notApproved ? 'Go to review' : 'Go to review to fix'}
            </button>
          </div>
        )}
      </div>

      {signMode === 'bulk' && (
        <BulkSignPanel
          signature={bulk.bulkSignature}
          onPickSignature={onPickBulkSignature}
          onClearSignature={() => bulk.setBulkSignature(null)}
          date={selectedDate}
          onDateChange={setSelectedDate}
          emptySignatureCount={emptySigSlots.length}
          emptyDateCount={emptyDateSlots.length}
          totalSignatureCount={sigSlots.length}
          totalDateCount={dateSlots.length}
          signedCount={signedFieldIds.size}
          onApply={() => void bulk.runBulkApply(false)}
          onReplaceAll={() => void bulk.runBulkApply(true)}
          progress={bulk.bulkProgress}
          failures={bulk.bulkFailures}
          skipped={bulk.bulkSkipped}
          busy={busy}
          disabled={blocked}
          libraryEmpty={libraryEmpty}
          dateMessage={selectedDate ? null : bulk.dateMessage}
        />
      )}

      <div className="card">
        <div className="card__heading">
          <h2>Signature slots</h2>
        </div>
        {slots.length === 0 && (
          <p className="hint">No signature slots were detected for this contract.</p>
        )}
        <SlotCardList
          mode={signMode}
          sigSlots={sigSlots}
          unpairedDateSlots={unpairedDateSlots}
          pairedDateFor={pairedDateFor}
          signatureForSlot={signatureForSlot}
          dateValues={dateValues}
          focusedSlotId={focusedSlotId}
          collapsedPages={collapsedPages}
          onTogglePage={togglePageGroup}
          onJump={jumpToSlot}
          onSign={startSlot}
          onRemove={(signatureId) => void removeSignature(signatureId)}
          onDateChange={setDateValue}
          busy={busy}
          blocked={blocked}
          cardId={slotCardId}
        />
      </div>

      {freeSignatures.length > 0 && (
        <div className="card">
          <h2>Free placements</h2>
          <ul className="list">
            {freeSignatures.map((signature) => (
              <li key={signature.id} className="list__item">
                <span>
                  Page {signature.page_number} · {signature.field_id ?? 'free placement'}
                </span>
                <button
                  type="button"
                  className="button button--link"
                  disabled={busy}
                  onClick={() => void removeSignature(signature.id)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {signMode === 'slot' && (
        <div className="card">
          <h2>Contract date</h2>
          <DatePicker value={selectedDate} onChange={setSelectedDate} testId="contract-date" />
          {contractDateRefused && !selectedDate && (
            <p className="hint hint--error" role="alert" data-testid="choose-date-message">
              {CHOOSE_DATE_FIRST}: pick the contract date above, then apply it.
            </p>
          )}
          <div className="slot-card__actions">
            {/* Pressable without a date, so it can say what is missing rather
                than sit greyed out (no automatic date -- `lib/signing.ts`). */}
            <button
              type="button"
              className="button"
              disabled={dateSlots.length === 0}
              onClick={applyDateToAll}
            >
              Apply to all date fields
            </button>
          </div>
        </div>
      )}
    </>
  );
}
