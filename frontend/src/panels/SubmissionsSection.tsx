import { useState } from 'react';
import { ApiError } from '../api/client';
import {
  MAX_REJECT_REASON,
  isSensitiveName,
  shareApi,
  type FieldValue,
  type SubmissionOut,
  type SubmissionStatus,
} from '../api/share';
import type { EditSession } from '../workspace/session/useEditSession';
import type { ShareState } from '../workspace/share/useShare';
import '../styles/share.css';

/**
 * F3-UI (EDITOR-V2-PLAN §8): «Submissions» in the right-hand panel -- what
 * people filled in through a share link.
 *
 * A section of its own (same look as `PanelSection`, own open state), drawn
 * only for a user who may edit the document and only once there is a link
 * or a submission: nothing to show, nothing drawn.
 *
 *  * Accept -> `POST .../accept {client_session_id}` with THIS tab's edit
 *    session id: the server puts the values into a new draft of that session;
 *    the workspace then opens it (`session.start()` -> 200, the ops inside).
 *    The user looks at the document and presses «Save changes» -- the usual
 *    commit, with the usual «signatures will be removed» dialog. Nothing is
 *    written into someone else's draft: another draft -> 409 `draft_locked`,
 *    said in words.
 *  * Reject needs a reason (kept on the submission).
 *  * Times are local; the server masks sensitive fields (SSN, date of birth,
 *    bank …) and the row says so.
 */

const STATUS_TEXT: Record<SubmissionStatus, string> = {
  received: 'To review',
  accepted: 'Accepted',
  rejected: 'Rejected',
};

const REASON_TEXT: Record<string, string> = {
  invalid_field_value: 'the value does not fit the field',
  field_readonly: 'the field is read-only now',
  field_not_fillable: 'the field cannot be filled',
  unknown_field: 'the field is no longer in the document',
  undrawable_character: 'a character cannot be printed',
};

const localTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '';

const shownValue = (value: FieldValue) =>
  typeof value === 'boolean' ? (value ? 'Yes (ticked)' : 'No') : Array.isArray(value) ? value.join(', ') : value;

function describeAcceptError(cause: unknown): { code: string; text: string } {
  if (cause instanceof ApiError) {
    const details = cause.details;
    switch (cause.code) {
      case 'draft_locked': {
        if (details.is_mine) {
          return {
            code: cause.code,
            text: 'You have edits open on this document. Save or cancel them first — the submitted values go into a new draft.',
          };
        }
        const owner = typeof details.owner_name === 'string' ? details.owner_name : 'Someone else';
        const until =
          typeof details.lease_expires_at === 'string'
            ? ` (until ${new Date(details.lease_expires_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })})`
            : '';
        return {
          code: cause.code,
          text: `${owner} is editing this document${until}. Ask them to save or cancel, then accept again.`,
        };
      }
      case 'submission_not_pending':
        return { code: cause.code, text: 'This submission was already reviewed.' };
      case 'submission_unreadable':
        return { code: cause.code, text: 'The submitted values can no longer be read (the server key changed).' };
      case 'contract_completed':
        return { code: cause.code, text: 'The contract is finalised; its document cannot change.' };
      case 'contract_busy':
        return { code: cause.code, text: 'The contract is being analysed — try again when it has finished.' };
      case 'invalid_ops': {
        const list = (details.fields as Array<{ name: string; reason: string }> | undefined) ?? [];
        const parts = list.map((item) => `${item.name || 'a field'}: ${REASON_TEXT[item.reason] ?? item.reason}`);
        return {
          code: cause.code,
          text: `The values no longer fit the current version${parts.length ? ` — ${parts.join('; ')}` : ''}.`,
        };
      }
      default:
        return { code: cause.code, text: cause.message || 'Could not accept the submission' };
    }
  }
  return { code: 'error', text: cause instanceof Error ? cause.message : 'Could not accept the submission' };
}

interface Props {
  share: ShareState;
  session: EditSession;
}

export function SubmissionsSection({ share, session }: Props) {
  const [open, setOpen] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string; code: string; text: string } | null>(null);
  const [accepted, setAccepted] = useState<{ id: string; fields: number } | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState('');

  if (!share.enabled || (share.links.length === 0 && share.submissions.length === 0)) return null;
  const pending = share.submissions.filter((row) => row.status === 'received').length;
  const bodyId = 'workspace-section-submissions';

  const accept = async (row: SubmissionOut) => {
    if (!share.contractId) return;
    setBusyId(row.id);
    setError(null);
    setAccepted(null);
    try {
      const out = await shareApi.accept(share.contractId, row.id, session.sessionId);
      setAccepted({ id: row.id, fields: out.draft.ops.length });
      // The draft is this tab's (same session id): open it for review + Save.
      await session.start();
    } catch (cause) {
      setError({ id: row.id, ...describeAcceptError(cause) });
    } finally {
      setBusyId(null);
      void share.reloadLists();
    }
  };

  const reject = async (row: SubmissionOut) => {
    if (!share.contractId || !reason.trim()) return;
    setBusyId(row.id);
    setError(null);
    try {
      await shareApi.reject(share.contractId, row.id, reason.trim());
      setRejecting(null);
      setReason('');
    } catch (cause) {
      const code = cause instanceof ApiError ? cause.code : 'error';
      setError({
        id: row.id,
        code,
        text: code === 'submission_not_pending' ? 'This submission was already reviewed.' : cause instanceof Error ? cause.message : 'Could not reject',
      });
    } finally {
      setBusyId(null);
      void share.reloadLists();
    }
  };

  return (
    <section
      className={`workspace-section${open ? ' is-open' : ''}`}
      data-testid="section-submissions"
      data-open={open ? 'true' : 'false'}
    >
      <h2 className="workspace-section__heading">
        <button
          type="button"
          className="workspace-section__toggle"
          data-testid="section-toggle-submissions"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((value) => !value)}
        >
          <span className="workspace-section__chevron" aria-hidden="true">
            {open ? '▾' : '▸'}
          </span>
          <span className="workspace-section__title">Submissions</span>
          <span className="workspace-section__badge" data-testid="section-submissions-badge">
            {pending > 0 ? `${pending} to review` : share.submissions.length}
          </span>
        </button>
      </h2>
      {open && (
        <div className="workspace-section__body sw-subs" id={bodyId}>
          <div className="sw-subs__bar">
            <span className="sw-subs__hint">From «Share for others to fill» links.</span>
            <button
              type="button"
              className="sw-subs__btn"
              data-testid="submissions-refresh"
              aria-label="Check for new submissions"
              title="Check for new submissions"
              onClick={() => void share.reloadLists()}
            >
              Refresh
            </button>
          </div>
          {share.loadError && (
            <p className="alert alert--error" role="alert" data-testid="submissions-load-error">
              {share.loadError}
            </p>
          )}
          {accepted && (
            <p className="alert alert--info" role="status" data-testid="submissions-accepted">
              The values ({accepted.fields} field{accepted.fields === 1 ? '' : 's'}) are in a draft. Check the document,
              then press «Save changes» to make them a new version — or «Cancel editing» to drop them.
            </p>
          )}
          {share.submissions.length === 0 ? (
            <p className="sw-subs__empty" data-testid="submissions-empty">
              Nothing submitted yet.
            </p>
          ) : (
            <ul className="sw-subs__list">
              {share.submissions.map((row) => (
                <li
                  key={row.id}
                  className={`sw-sub sw-sub--${row.status}`}
                  data-testid={`submission-${row.id}`}
                  data-status={row.status}
                >
                  <div className="sw-sub__head">
                    <span className={`sw-chip sw-chip--${row.status}`}>{STATUS_TEXT[row.status]}</span>
                    <span className="sw-sub__time" title={row.created_at}>
                      {localTime(row.created_at)}
                    </span>
                  </div>
                  <div className="sw-sub__who">
                    {row.submitter_name || row.submitter_email ? (
                      <>
                        {row.submitter_name && <span data-testid="submission-name">{row.submitter_name}</span>}
                        {row.submitter_email && <span className="sw-sub__email"> &lt;{row.submitter_email}&gt;</span>}
                      </>
                    ) : (
                      <span className="sw-sub__muted">No name given</span>
                    )}
                    {row.recipient_label && <span className="sw-sub__muted"> · link: {row.recipient_label}</span>}
                    {row.version_no !== null && <span className="sw-sub__muted"> · v{row.version_no}</span>}
                  </div>
                  {row.values_readable && row.values ? (
                    <dl className="sw-sub__values" data-testid="submission-values">
                      {Object.entries(row.values).map(([name, value]) => (
                        <div key={name} className="sw-sub__pair" data-field={name}>
                          <dt>{name}</dt>
                          <dd>
                            {shownValue(value) || <span className="sw-sub__muted">(empty)</span>}
                            {isSensitiveName(name) && (
                              <span
                                className="sw-sub__masked"
                                data-testid="submission-masked"
                                title="Sensitive field: the value is masked here; the full value goes into the document when accepted"
                              >
                                masked
                              </span>
                            )}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="sw-sub__muted" data-testid="submission-unreadable">
                      The values cannot be read ({row.field_count} field{row.field_count === 1 ? '' : 's'}).
                    </p>
                  )}
                  {row.status === 'rejected' && row.reject_reason && (
                    <p className="sw-sub__muted" data-testid="submission-reject-reason">
                      Rejected{row.reviewed_by_name ? ` by ${row.reviewed_by_name}` : ''}: {row.reject_reason}
                    </p>
                  )}
                  {row.status === 'accepted' && (
                    <p className="sw-sub__muted">
                      Accepted{row.reviewed_by_name ? ` by ${row.reviewed_by_name}` : ''} {localTime(row.reviewed_at)}
                    </p>
                  )}
                  {error && error.id === row.id && (
                    <p className="alert alert--error" role="alert" data-testid="submission-error" data-code={error.code}>
                      {error.text}
                    </p>
                  )}
                  {row.status === 'received' && rejecting !== row.id && (
                    <div className="sw-sub__actions">
                      <button
                        type="button"
                        className="sw-subs__btn sw-subs__btn--primary"
                        data-testid={`submission-accept-${row.id}`}
                        aria-label={`Accept the submission from ${row.submitter_name ?? 'this sender'}: load its values into a draft`}
                        title="Load the values into a draft of the document; you check them and press Save changes"
                        disabled={busyId !== null || !row.values_readable}
                        onClick={() => void accept(row)}
                      >
                        {busyId === row.id ? 'Accepting…' : 'Accept'}
                      </button>
                      <button
                        type="button"
                        className="sw-subs__btn"
                        data-testid={`submission-reject-${row.id}`}
                        aria-label={`Reject the submission from ${row.submitter_name ?? 'this sender'}`}
                        title="Reject with a reason (nothing changes in the document)"
                        disabled={busyId !== null}
                        onClick={() => {
                          setRejecting(row.id);
                          setReason('');
                          setError(null);
                        }}
                      >
                        Reject…
                      </button>
                    </div>
                  )}
                  {row.status === 'received' && rejecting === row.id && (
                    <div className="sw-sub__reject" data-testid="submission-reject-form">
                      <label htmlFor={`reject-${row.id}`} className="sw-sub__label">
                        Reason (required)
                      </label>
                      <textarea
                        id={`reject-${row.id}`}
                        data-testid="submission-reject-reason-input"
                        rows={2}
                        maxLength={MAX_REJECT_REASON}
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                      />
                      <div className="sw-sub__actions">
                        <button type="button" className="sw-subs__btn" onClick={() => setRejecting(null)}>
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="sw-subs__btn sw-subs__btn--danger"
                          data-testid="submission-reject-confirm"
                          disabled={!reason.trim() || busyId !== null}
                          title={reason.trim() ? 'Reject this submission' : 'Write a reason first'}
                          onClick={() => void reject(row)}
                        >
                          {busyId === row.id ? 'Rejecting…' : 'Reject'}
                        </button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
