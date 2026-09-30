import type { ReactNode } from 'react';
import type { CommitNotice, EditSession, SessionStatus } from './useEditSession';
import type { MarkupSync } from './useMarkupSync';

/**
 * F1b (EDITOR-V2-PLAN §7 «Top bar», «Sessiya»): what the user sees of the
 * edit session -- the top bar's `[Edit PDF]` / `[Save changes] [Cancel
 * editing]` + save status, the banners (recovery, lock, errors, «review
 * reopened») and the three dialogs (signatures will be removed, conflict,
 * annotations from this browser).
 */

export const STATUS_TEXT: Record<SessionStatus, string> = {
  idle: '',
  starting: 'Opening…',
  saving: 'Saving…',
  saved: 'All changes saved',
  dirty: 'Unsaved changes',
  offline: 'Offline — retrying…',
  conflict: 'Conflict — not saved',
  committing: 'Saving a new version…',
  error: 'Not saved',
};

export const CAPABILITY_REASON: Record<string, string> = {
  not_permitted: 'Only an admin, a reviewer or the person who uploaded the contract can edit the document',
  contract_completed: 'The contract is finalised; its document cannot change',
  contract_busy: 'The contract is being analysed — try again when it has finished',
};

export const FINALIZE_WHILE_EDITING = 'Save or cancel your edits first';

const time = (ms: number) =>
  new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });

const timeIso = (iso: string | null) => (iso ? time(new Date(iso).getTime()) : '');

/** The top bar's session group. Null: nothing to show (no editor, or not allowed). */
export function sessionControls(session: EditSession): ReactNode {
  if (!session.available) return null;
  const caps = session.capabilities;
  // F2b (§11 Q2): a fill-only user (`can_fill`) gets Save / Cancel once filling a field started the session.
  const filling = session.mode === 'edit' && Boolean((caps as { can_fill?: boolean } | null)?.can_fill);
  if (caps && !caps.can_edit && caps.reason === 'not_permitted' && !filling) return null;
  if (session.mode === 'view') {
    const reason =
      caps && !caps.can_edit
        ? (CAPABILITY_REASON[caps.reason ?? ''] ?? 'Editing is not possible now')
        : session.lock
          ? `Being edited by ${session.lock.owner ?? 'someone else'}${session.lock.until ? ` (until ${timeIso(session.lock.until)})` : ''}`
          : null;
    return (
      <>
        <button
          type="button"
          className="sj-btn"
          data-testid="editor-edit-pdf"
          title={reason ?? 'Edit the document’s own text (a draft; nothing changes until Save changes)'}
          disabled={Boolean(reason) || session.status === 'starting'}
          onClick={() => void session.start()}
        >
          <span aria-hidden="true">✎</span>
          <span className="sj-btn__label">{session.status === 'starting' ? 'Opening…' : 'Edit PDF'}</span>
        </button>
        {reason && (
          <span className="sj-topbar__reason" data-testid="editor-edit-pdf-reason">
            {reason}
          </span>
        )}
      </>
    );
  }
  const busy = session.status === 'committing' || session.status === 'starting';
  return (
    <>
      <span
        className={`sj-save-status sj-save-status--${session.status}`}
        data-testid="editor-save-status"
        data-status={session.status}
        role="status"
        title={session.savedAt ? `Last saved ${time(session.savedAt)}` : undefined}
      >
        {STATUS_TEXT[session.status]}
      </span>
      <button
        type="button"
        className="sj-btn sj-btn--primary"
        data-testid="editor-save"
        disabled={busy || session.status === 'conflict'}
        title="Save the edits as a new version of the document"
        onClick={() => void session.save()}
      >
        <span className="sj-btn__label">{session.status === 'committing' ? 'Saving…' : 'Save changes'}</span>
      </button>
      <button
        type="button"
        className="sj-btn"
        data-testid="editor-cancel"
        disabled={busy}
        title="Throw the draft away; the document stays as it was"
        onClick={() => void session.cancel()}
      >
        <span className="sj-btn__label">Cancel editing</span>
      </button>
    </>
  );
}

interface BannerProps {
  session: EditSession;
  markup: MarkupSync;
  committed: CommitNotice | null;
  onDismissCommitted: () => void;
}

/** The banners that stay until something answers them. */
export function SessionBanners({ session, markup, committed, onDismissCommitted }: BannerProps) {
  const recovery = session.recovery;
  return (
    <>
      {recovery && session.mode === 'view' && (
        <div className="alert alert--warn sj-banner" role="status" data-testid="session-recovery" data-source={recovery.source}>
          <span>
            Unsaved changes from {time(recovery.at)}
            {recovery.source === 'local' ? ' (kept in this browser)' : ''}.
          </span>
          <span className="sj-banner__actions">
            {recovery.source === 'server' && (
              <button type="button" className="sj-btn sj-btn--primary" data-testid="session-resume" onClick={() => void session.resume()}>
                Resume
              </button>
            )}
            {recovery.localNewer && (
              <button type="button" className="sj-btn" data-testid="session-restore-local" onClick={() => void session.restoreLocal()}>
                Restore from this browser
              </button>
            )}
            <button
              type="button"
              className="sj-btn"
              data-testid="session-discard"
              onClick={() => {
                if (window.confirm('Discard the unsaved edits? This cannot be undone.')) void session.discardRecovered();
              }}
            >
              Discard
            </button>
          </span>
        </div>
      )}
      {session.lock && session.mode === 'view' && !recovery && (
        <div className="alert alert--warn sj-banner" role="status" data-testid="session-lock">
          <span>
            {session.lock.owner ?? 'Someone'} is editing this document
            {session.lock.until ? ` (lease until ${timeIso(session.lock.until)})` : ''}.
          </span>
          <span className="sj-banner__actions">
            <button
              type="button"
              className="sj-btn"
              data-testid="session-take-over"
              onClick={() => {
                if (window.confirm('Take over the edit session? The other window stops being able to save.')) {
                  void session.takeOver();
                }
              }}
            >
              Take over
            </button>
          </span>
        </div>
      )}
      {session.error && (
        <p className="alert alert--error" role="alert" data-testid="session-error">
          {session.error}
        </p>
      )}
      {committed && (
        <div className="alert alert--info sj-banner" role="status" data-testid="session-committed">
          <span>
            {committed.out.content_changed
              ? `Saved as version ${committed.out.version.version_no}.`
              : 'Saved (the document’s text did not change).'}
            {committed.out.review_reset && (
              <strong data-testid="session-review-reopened"> Review reopened: the contract must be reviewed and approved again.</strong>
            )}
            {committed.signaturesRemoved > 0 && (
              <span data-testid="session-signatures-removed">
                {' '}
                {committed.signaturesRemoved} signature{committed.signaturesRemoved === 1 ? ' was' : 's were'} removed — the contract must be signed again.
              </span>
            )}
          </span>
          <button type="button" className="sj-btn" aria-label="Dismiss" onClick={onDismissCommitted}>
            ×
          </button>
        </div>
      )}
      {markup.error && (
        <p className="alert alert--warn" role="status" data-testid="markup-sync-error">
          {markup.error}
        </p>
      )}
    </>
  );
}

function Modal({ testId, title, children }: { testId: string; title: string; children: ReactNode }) {
  return (
    <div className="modal-backdrop sj-modal-backdrop" data-testid={`${testId}-backdrop`}>
      <div className="modal sj-modal" role="dialog" aria-modal="true" aria-label={title} data-testid={testId}>
        <h2 className="sj-modal__title">{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function SessionDialogs({ session, markup }: { session: EditSession; markup: MarkupSync }) {
  const prompt = session.signaturesPrompt;
  const conflict = session.conflict;
  const migration = markup.prompt;
  return (
    <>
      {prompt && (
        <Modal testId="signatures-dialog" title="Signatures will be removed">
          <p data-testid="signatures-dialog-text">
            {prompt.count} signature{prompt.count === 1 ? '' : 's'} will be removed and the contract must be signed again.
          </p>
          <p className="sj-modal__hint">
            The document’s text changes, so what was signed is no longer what the contract says. The removal is recorded in
            the audit trail.
          </p>
          <div className="sj-modal__actions">
            <button type="button" className="sj-btn" data-testid="signatures-dialog-cancel" onClick={session.dismissSignatures}>
              Keep editing
            </button>
            <button
              type="button"
              className="sj-btn sj-btn--danger"
              data-testid="signatures-dialog-confirm"
              onClick={() => void session.confirmDiscardSignatures()}
            >
              Remove signatures and save
            </button>
          </div>
        </Modal>
      )}
      {conflict && (
        <Modal testId="conflict-dialog" title="Your edits could not be saved">
          <p data-testid="conflict-dialog-text">{conflict.message}</p>
          <p className="sj-modal__hint">
            Nothing of yours is lost yet: download your changes, or reload to see the document as it is now (this browser
            keeps a copy you can restore).
          </p>
          <div className="sj-modal__actions">
            <button type="button" className="sj-btn" data-testid="conflict-download" onClick={session.downloadChanges}>
              Download my changes (JSON)
            </button>
            <button
              type="button"
              className="sj-btn sj-btn--primary"
              data-testid="conflict-reload"
              onClick={() => void session.reloadAfterConflict()}
            >
              Reload
            </button>
          </div>
        </Modal>
      )}
      {migration && (
        <Modal testId="markup-migration-dialog" title="Annotations in this browser">
          <p data-testid="markup-migration-text">
            This browser has {migration.localCount} annotation{migration.localCount === 1 ? '' : 's'} for this contract that
            differ from the {migration.serverCount} saved on the server.
          </p>
          <p className="sj-modal__hint">
            Import replaces the server’s annotations with this browser’s. Keep uses the server’s; this browser’s copy is set
            aside (not deleted).
          </p>
          <div className="sj-modal__actions">
            <button type="button" className="sj-btn" data-testid="markup-keep-server" onClick={markup.keepServer}>
              Keep the server’s
            </button>
            <button type="button" className="sj-btn sj-btn--primary" data-testid="markup-import" onClick={() => void markup.importLocal()}>
              Import from this browser
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
