import { DatePicker } from './DatePicker';

/** One slot the bulk run could not fill, named so the user can go fix it. */
export interface BulkFailure {
  fieldId: string;
  label: string;
  message: string;
}

/** One slot the server left as it was, named so the user is not surprised. */
export interface BulkSkip {
  fieldId: string;
  label: string;
}

/** The image chosen once and reused for every slot in the bulk run. */
export interface BulkSignature {
  /** What is uploaded: an uploaded file's original bytes, or the drawn PNG. */
  dataUrl: string;
  label: string;
  /** Display-only cleaned preview of an uploaded file; `dataUrl` when absent. */
  previewUrl?: string;
}

/**
 * State of one "apply to all" run.
 *
 * A run is now a single request (or a handful, if the contract has more slots
 * than the server takes at once), so this is less a per-slot bar than a
 * "working / here is what happened" line: `running` says which of the two is
 * being shown, and `chunk`/`chunks` only matter when the run was split.
 */
export interface BulkProgress {
  done: number;
  total: number;
  chunk: number;
  chunks: number;
  running: boolean;
  placed: number;
  skipped: number;
  failed: number;
}

interface Props {
  signature: BulkSignature | null;
  onPickSignature: () => void;
  onClearSignature: () => void;
  date: string;
  onDateChange: (value: string) => void;
  /** Slots still waiting: what "Apply to all" will touch. */
  emptySignatureCount: number;
  emptyDateCount: number;
  /** Everything the template asks for, so the coverage line has a denominator. */
  totalSignatureCount: number;
  totalDateCount: number;
  /** Already-signed slots, which "Apply to all" skips and "Replace all" redoes. */
  signedCount: number;
  onApply: () => void;
  onReplaceAll: () => void;
  progress: BulkProgress | null;
  failures: BulkFailure[];
  /** Slots the server reported as already signed, so it left them alone. */
  skipped: BulkSkip[];
  busy: boolean;
  /** Signing is blocked (not approved / critical issues outstanding). */
  disabled: boolean;
  /** The user's signature library came back empty. */
  libraryEmpty: boolean;
  /** "Choose the date first" after a run that could not date anything. */
  dateMessage?: string | null;
}

/**
 * The "one signature for everything" panel.
 *
 * It owns no signing logic: it collects a single image and a single date and
 * hands them back. The page sends them to the bulk placement endpoint, which
 * resolves every position from the template -- the server stays the sole
 * authority on where each signature actually lands.
 */
export function BulkSignPanel({
  signature,
  onPickSignature,
  onClearSignature,
  date,
  onDateChange,
  emptySignatureCount,
  emptyDateCount,
  totalSignatureCount,
  totalDateCount,
  signedCount,
  onApply,
  onReplaceAll,
  progress,
  failures,
  skipped,
  busy,
  disabled,
  libraryEmpty,
  dateMessage = null,
}: Props) {
  const pendingTargets = emptySignatureCount + emptyDateCount;
  const totalTargets = totalSignatureCount + totalDateCount;
  const percent =
    progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div className="card">
      <div className="card__heading">
        <h2>One signature for everything</h2>
      </div>
      <p className="hint">
        Choose one signature and one date, then fill every empty slot in a single step.
      </p>

      <div className="bulk-signature">
        <div className="sig-card__preview bulk-signature__preview">
          {signature ? (
            <img src={signature.previewUrl ?? signature.dataUrl} alt="Signature that will be used everywhere" />
          ) : (
            <span className="hint">No signature chosen yet</span>
          )}
        </div>
        <div className="slot-card__row">
          <span className="slot-card__meta">
            {signature ? signature.label : 'Pick one from your library, draw it, or upload an image'}
          </span>
          <div className="slot-card__actions">
            {signature && (
              <button
                type="button"
                className="button button--link"
                disabled={busy}
                onClick={onClearSignature}
              >
                Clear
              </button>
            )}
            <button
              type="button"
              className="button button--primary button--small"
              disabled={busy || disabled}
              onClick={onPickSignature}
            >
              {signature ? 'Change signature' : 'Choose signature'}
            </button>
          </div>
        </div>
        {!signature && libraryEmpty && (
          <p className="hint">
            Choose a signature or draw one first — your saved signature library is empty.
          </p>
        )}
      </div>

      <DatePicker
        value={date}
        onChange={onDateChange}
        label="Date for every date field"
        testId="bulk-date"
      />
      {dateMessage && (
        <p className="hint hint--error" role="alert" data-testid="choose-date-message">
          {dateMessage}
        </p>
      )}

      <p className="hint bulk-signature__coverage">
        Applies to {pendingTargets} empty {pendingTargets === 1 ? 'place' : 'places'} (
        {emptySignatureCount} {emptySignatureCount === 1 ? 'signature' : 'signatures'},{' '}
        {emptyDateCount} {emptyDateCount === 1 ? 'date' : 'dates'}) out of {totalTargets} in this
        contract.
        {signedCount > 0 && ` ${signedCount} already signed — left untouched.`}
      </p>

      <div className="slot-card__actions">
        <button
          type="button"
          className="button button--primary"
          // A signature is only needed when a signature slot is actually
          // waiting: filling the remaining dates must not be gated on one.
          disabled={
            busy ||
            disabled ||
            pendingTargets === 0 ||
            (!signature && emptySignatureCount > 0)
          }
          onClick={onApply}
        >
          {busy && progress?.running ? 'Applying…' : 'Apply to all'}
        </button>
        <button
          type="button"
          className="button"
          disabled={busy || disabled || !signature || signedCount === 0}
          onClick={onReplaceAll}
          title="Removes the existing signatures first, then signs every slot again"
        >
          Replace all
        </button>
      </div>

      {progress && (
        <div className="bulk-progress" aria-live="polite">
          <div className="progress__header">
            <span>
              {progress.running
                ? `Applying to ${progress.total} ${progress.total === 1 ? 'place' : 'places'}…` +
                  (progress.chunks > 1 ? ` (part ${progress.chunk}/${progress.chunks})` : '')
                : `${progress.placed} placed, ${progress.skipped} skipped, ${progress.failed} failed`}
            </span>
            <span>
              {progress.done}/{progress.total}
            </span>
          </div>
          <div className="progress__track">
            <div
              className={`progress__fill progress__fill--${
                !progress.running && progress.failed > 0 ? 'warn' : 'good'
              }`}
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      )}

      {skipped.length > 0 && (
        <p className="hint">
          {skipped.length} {skipped.length === 1 ? 'slot was' : 'slots were'} already signed and{' '}
          {skipped.length === 1 ? 'was' : 'were'} left untouched
          {skipped.length <= 5 ? `: ${skipped.map((slot) => slot.label).join(', ')}` : ''}.
        </p>
      )}

      {failures.length > 0 && (
        <div className="alert alert--error bulk-failures">
          <p>
            {failures.length} {failures.length === 1 ? 'slot' : 'slots'} could not be signed:
          </p>
          <ul className="list">
            {failures.map((failure) => (
              <li key={failure.fieldId} className="list__item">
                <span>{failure.label}</span>
                <span className="slot-card__meta">{failure.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
