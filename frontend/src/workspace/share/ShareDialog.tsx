import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import {
  MAX_SUBMISSIONS_PER_LINK,
  SHARE_DEFAULT_TTL_HOURS,
  SHARE_MAX_TTL_HOURS,
  SHARE_PROBLEM_TEXT,
  shareApi,
  type ShareLinkCreatedOut,
  type ShareLinkOut,
  type ShareStatus,
} from '../../api/share';
import type { ShareState } from './useShare';
import '../../styles/share.css';

/**
 * F3-UI (EDITOR-V2-PLAN §8, §11 Q4 «faqat LAN, faqat to'ldirish»):
 * «Share for others to fill».
 *
 *  * `ShareButton` -- the top bar's entry; drawn only for a user who may edit
 *    the document and only when there is a field to share (or a link to
 *    manage): never a dead button.
 *  * `ShareDialog` -- which fields, a label for the recipient, how long, how
 *    many submissions; the plain warning that anyone on the office network
 *    with the link can fill them. Nothing is created until the user ticks
 *    that they understand AND presses «Create link».
 *  * The link's URL is in the create answer ONLY: it is shown once, with
 *    «Copy link», and never again (the server keeps a hash). The list below
 *    shows every link's state and «Revoke».
 */

const WARNING =
  'Anyone on the office network with this link can fill these fields. The link works only on the LAN.';

const STATUS_TEXT: Record<ShareStatus, string> = {
  active: 'Active',
  expired: 'Expired',
  revoked: 'Revoked',
  used_up: 'Used up',
};

const TYPE_TEXT: Record<string, string> = {
  text: 'Text',
  multiline: 'Multi-line text',
  dropdown: 'Dropdown',
  radio: 'Radio group',
  checkbox: 'Checkbox',
  signature: 'Signature box',
  other: 'Button / list',
};

const localTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '';

export function ShareButton({ share }: { share: ShareState }) {
  if (!share.available) return null;
  const active = share.links.filter((link) => link.status === 'active').length;
  return (
    <button
      type="button"
      className="sj-btn"
      data-testid="share-open"
      aria-haspopup="dialog"
      aria-label="Share for others to fill"
      title="Share chosen form fields for someone on the office network to fill (LAN only)"
      onClick={share.openDialog}
    >
      <span aria-hidden="true">⇪</span>
      <span className="sj-btn__label">Share for others to fill</span>
      {active > 0 && (
        <span className="sw-share-count" data-testid="share-active-count" title={`${active} active link(s)`}>
          {active}
        </span>
      )}
    </button>
  );
}

type Unit = 'hours' | 'days';

function describeCreateError(cause: unknown, names: string[]): string {
  if (cause instanceof ApiError) {
    if (cause.code === 'fields_not_shareable') {
      const list = (cause.details.fields as Array<{ index: number; reason: string }> | undefined) ?? [];
      const parts = list.map(
        (item) => `“${names[item.index] ?? `#${item.index + 1}`}” — ${SHARE_PROBLEM_TEXT[item.reason] ?? item.reason}`,
      );
      return `These fields cannot be shared: ${parts.join('; ') || 'see the list'}.`;
    }
    if (cause.code === 'expiry_too_long') return 'A share link lasts at most 14 days.';
    if (cause.code === 'contract_completed') return 'The contract is finalised; its fields can no longer be shared.';
    return cause.message || 'The link could not be created';
  }
  return cause instanceof Error ? cause.message : 'The link could not be created';
}

export function ShareDialog({ share }: { share: ShareState }) {
  if (!share.dialogOpen) return null;
  return <ShareDialogBody share={share} />;
}

function ShareDialogBody({ share }: { share: ShareState }) {
  const shareable = useMemo(() => share.groups.filter((group) => group.problem === null), [share.groups]);
  const blocked = useMemo(() => share.groups.filter((group) => group.problem !== null), [share.groups]);
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(shareable.map((group) => group.name)));
  const [label, setLabel] = useState('');
  const [amount, setAmount] = useState(String(SHARE_DEFAULT_TTL_HOURS));
  const [unit, setUnit] = useState<Unit>('hours');
  const [maxSubmissions, setMaxSubmissions] = useState('1');
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ShareLinkCreatedOut | null>(null);
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [revoking, setRevoking] = useState<string | null>(null);
  const urlRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const hours = Number(amount) * (unit === 'days' ? 24 : 1);
  const hoursValid = Number.isFinite(hours) && Number.isInteger(hours) && hours >= 1;
  const tooLong = hoursValid && hours > SHARE_MAX_TTL_HOURS;
  const max = Number(maxSubmissions);
  const maxValid = Number.isInteger(max) && max >= 1 && max <= MAX_SUBMISSIONS_PER_LINK;
  const problem = !chosen.size
    ? 'Choose at least one field'
    : !hoursValid
      ? 'Enter how long the link works (whole hours or days)'
      : tooLong
        ? 'A share link lasts at most 14 days'
        : !maxValid
          ? `Submissions: 1 to ${MAX_SUBMISSIONS_PER_LINK}`
          : !understood
            ? 'Confirm that you understand who can use the link'
            : null;

  // Escape closes (not while a request is in flight); focus goes into the dialog.
  useEffect(() => {
    dialogRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) {
        event.stopPropagation();
        share.closeDialog();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [busy, share]);

  const toggle = (name: string) =>
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const create = async () => {
    if (problem || !share.contractId) return;
    const names = shareable.map((group) => group.name).filter((name) => chosen.has(name));
    setBusy(true);
    setError(null);
    try {
      const out = await shareApi.create(share.contractId, {
        allowed_fields: names,
        recipient_label: label.trim() || null,
        expires_in_hours: hours,
        max_submissions: max,
      });
      setCreated(out);
      setCopied('idle');
      void share.reloadLists();
    } catch (cause) {
      setError(describeCreateError(cause, names));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.url);
      setCopied('copied');
      return;
    } catch {
      /* no clipboard permission: fall back to a selection copy */
    }
    const input = urlRef.current;
    if (input) {
      input.focus();
      input.select();
      // eslint-disable-next-line @typescript-eslint/no-deprecated -- the only fallback without the async API
      setCopied(document.execCommand('copy') ? 'copied' : 'failed');
    } else {
      setCopied('failed');
    }
  };

  const revoke = async (link: ShareLinkOut) => {
    if (!share.contractId) return;
    const who = link.recipient_label ? ` for “${link.recipient_label}”` : '';
    if (!window.confirm(`Revoke this link${who}? It stops working at once; received submissions stay.`)) return;
    setRevoking(link.id);
    setError(null);
    try {
      await shareApi.revoke(share.contractId, link.id);
      await share.reloadLists();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not revoke the link');
    } finally {
      setRevoking(null);
    }
  };

  const finishCreated = () => {
    // The URL goes with this state: it cannot be shown again.
    setCreated(null);
    setUnderstood(false);
    setCopied('idle');
  };

  return (
    <div className="modal-backdrop sj-modal-backdrop" data-testid="share-dialog-backdrop">
      <div
        className="sj-modal sw-share"
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-dialog-title"
        data-testid="share-dialog"
        tabIndex={-1}
        ref={dialogRef}
      >
        <div className="sw-share__head">
          <h2 className="sj-modal__title" id="share-dialog-title">
            Share for others to fill
          </h2>
          <button
            type="button"
            className="sj-btn sw-share__close"
            data-testid="share-close"
            aria-label="Close"
            title="Close"
            disabled={busy}
            onClick={share.closeDialog}
          >
            ×
          </button>
        </div>

        {created ? (
          <section className="sw-share__created" data-testid="share-created" aria-live="polite">
            <p className="sw-share__ok">Link created. Send it to the person who should fill the form.</p>
            <label className="sw-share__label" htmlFor="share-url">
              Link (shown only this once)
            </label>
            <div className="sw-share__url-row">
              <input
                id="share-url"
                ref={urlRef}
                className="sw-share__url"
                data-testid="share-url"
                readOnly
                value={created.url}
                aria-label="The share link"
                onFocus={(event) => event.currentTarget.select()}
              />
              <button type="button" className="sj-btn sj-btn--primary" data-testid="share-copy" onClick={() => void copy()}>
                Copy link
              </button>
            </div>
            <p className="sw-share__status" role="status" data-testid="share-copy-status" data-state={copied}>
              {copied === 'copied' ? 'Copied.' : copied === 'failed' ? 'Copy failed — select the link and copy it by hand.' : ''}
            </p>
            <p className="sj-modal__hint" data-testid="share-once-note">
              This link cannot be shown again: SignWithMe keeps only a fingerprint of it. If it is lost, revoke it and
              create a new one.
            </p>
            <p className="sj-modal__hint">
              Expires {localTime(created.expires_at)} · {created.max_submissions} submission
              {created.max_submissions === 1 ? '' : 's'} · version {created.version_no}
            </p>
            <div className="sj-modal__actions">
              <button type="button" className="sj-btn sj-btn--primary" data-testid="share-done" onClick={finishCreated}>
                Done
              </button>
            </div>
          </section>
        ) : (
          <section className="sw-share__form" data-testid="share-form">
            {shareable.length === 0 ? (
              <p className="alert alert--warn" data-testid="share-no-fields">
                This version has no form field that can be filled from a link. Add text, dropdown, checkbox or radio
                fields first.
              </p>
            ) : (
              <fieldset className="sw-share__fields" data-testid="share-fields">
                <legend>Fields to share ({chosen.size} of {shareable.length})</legend>
                <div className="sw-share__bulk">
                  <button type="button" className="sj-btn" data-testid="share-all" onClick={() => setChosen(new Set(shareable.map((g) => g.name)))}>
                    All
                  </button>
                  <button type="button" className="sj-btn" data-testid="share-none" onClick={() => setChosen(new Set())}>
                    None
                  </button>
                </div>
                <ul className="sw-share__list">
                  {shareable.map((group) => (
                    <li key={group.name}>
                      <label className="sw-share__check">
                        <input
                          type="checkbox"
                          data-testid={`share-field-${group.name}`}
                          checked={chosen.has(group.name)}
                          onChange={() => toggle(group.name)}
                        />
                        <span className="sw-share__fname">{group.name}</span>
                        <span className="sw-share__ftype">
                          {TYPE_TEXT[group.type] ?? group.type}
                          {group.required ? ' · required' : ''}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
                {blocked.length > 0 && (
                  <p className="sj-modal__hint" data-testid="share-blocked-fields">
                    Not shareable:{' '}
                    {blocked.map((group) => `${group.name} (${SHARE_PROBLEM_TEXT[group.problem ?? ''] ?? group.problem})`).join(', ')}
                  </p>
                )}
              </fieldset>
            )}

            {shareable.length > 0 && (
              <div className="sw-share__grid">
                <label className="sw-share__label" htmlFor="share-label">
                  Recipient label <span className="sw-share__opt">(optional, for your list)</span>
                </label>
                <input
                  id="share-label"
                  className="sw-share__input"
                  data-testid="share-label"
                  maxLength={120}
                  value={label}
                  placeholder="e.g. Driver — J. Smith"
                  onChange={(event) => setLabel(event.target.value)}
                />
                <label className="sw-share__label" htmlFor="share-expiry">
                  Expires after
                </label>
                <span className="sw-share__inline">
                  <input
                    id="share-expiry"
                    className="sw-share__input sw-share__input--num"
                    data-testid="share-expiry"
                    type="number"
                    min={1}
                    step={1}
                    value={amount}
                    aria-invalid={!hoursValid || tooLong}
                    onChange={(event) => setAmount(event.target.value)}
                  />
                  <select
                    className="sw-share__input"
                    data-testid="share-expiry-unit"
                    aria-label="Unit"
                    value={unit}
                    onChange={(event) => setUnit(event.target.value as Unit)}
                  >
                    <option value="hours">hours</option>
                    <option value="days">days</option>
                  </select>
                  <span className="sw-share__opt">max 14 days</span>
                </span>
                <label className="sw-share__label" htmlFor="share-max">
                  Submissions allowed
                </label>
                <input
                  id="share-max"
                  className="sw-share__input sw-share__input--num"
                  data-testid="share-max"
                  type="number"
                  min={1}
                  max={MAX_SUBMISSIONS_PER_LINK}
                  step={1}
                  value={maxSubmissions}
                  aria-invalid={!maxValid}
                  onChange={(event) => setMaxSubmissions(event.target.value)}
                />
              </div>
            )}

            {shareable.length > 0 && (
              <div className="alert alert--warn sw-share__warning" role="note" data-testid="share-warning">
                <p>{WARNING}</p>
                <label className="sw-share__check">
                  <input
                    type="checkbox"
                    data-testid="share-ack"
                    checked={understood}
                    onChange={(event) => setUnderstood(event.target.checked)}
                  />
                  <span>I understand — create a link anyone on the office network can use.</span>
                </label>
              </div>
            )}

            {error && (
              <p className="alert alert--error" role="alert" data-testid="share-error">
                {error}
              </p>
            )}

            {shareable.length > 0 && (
              <div className="sj-modal__actions">
                {problem && (
                  <span className="sw-share__why" data-testid="share-create-reason">
                    {problem}
                  </span>
                )}
                <button type="button" className="sj-btn" onClick={share.closeDialog} disabled={busy}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="sj-btn sj-btn--primary"
                  data-testid="share-create"
                  disabled={Boolean(problem) || busy}
                  title={problem ?? 'Create the link (it is shown once)'}
                  onClick={() => void create()}
                >
                  {busy ? 'Creating…' : 'Create link'}
                </button>
              </div>
            )}
          </section>
        )}

        <section className="sw-share__links" aria-label="Links of this contract" data-testid="share-links">
          <h3 className="sw-share__subtitle">Links of this contract</h3>
          {share.links.length === 0 ? (
            <p className="sj-modal__hint" data-testid="share-links-empty">
              No links yet.
            </p>
          ) : (
            <table className="sw-share__table">
              <thead>
                <tr>
                  <th scope="col">Recipient</th>
                  <th scope="col">Status</th>
                  <th scope="col">Expires</th>
                  <th scope="col">Submissions</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {share.links.map((link) => (
                  <tr key={link.id} data-testid={`share-link-${link.id}`} data-status={link.status}>
                    <td title={`${link.allowed_fields.length} field(s), version ${link.version_no}`}>
                      {link.recipient_label ?? <span className="sw-share__opt">—</span>}
                    </td>
                    <td>
                      <span className={`sw-chip sw-chip--${link.status}`}>{STATUS_TEXT[link.status]}</span>
                    </td>
                    <td>{localTime(link.expires_at)}</td>
                    <td>
                      {link.submission_count}/{link.max_submissions}
                      {link.submissions_pending > 0 && (
                        <span className="sw-share__opt"> · {link.submissions_pending} to review</span>
                      )}
                    </td>
                    <td>
                      {link.status !== 'revoked' && (
                        <button
                          type="button"
                          className="sj-btn"
                          data-testid={`share-revoke-${link.id}`}
                          aria-label={`Revoke the link${link.recipient_label ? ` for ${link.recipient_label}` : ''}`}
                          title="Stop this link working now"
                          disabled={revoking === link.id}
                          onClick={() => void revoke(link)}
                        >
                          {revoking === link.id ? 'Revoking…' : 'Revoke'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
