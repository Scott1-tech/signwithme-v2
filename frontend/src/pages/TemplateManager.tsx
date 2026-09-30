import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { templatesApi } from '../api/templates';
import { readPrefilledWarning } from '../lib/warnings';
import type { Template, TemplateDetail } from '../types/template';

/** A delete the server refused because contracts still use the template. */
interface BlockedDelete {
  template: Template;
  contractCount: number | null;
}

export function TemplateManager() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<TemplateDetail | null>(null);
  const [name, setName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<BlockedDelete | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    setTemplates(await templatesApi.list());
  }, []);

  useEffect(() => {
    void refresh().catch((cause) =>
      setError(cause instanceof Error ? cause.message : 'Failed to load templates'),
    );
  }, [refresh]);

  // Detection runs in a worker, so the list is polled while anything is
  // still processing rather than leaving the admin to refresh by hand.
  useEffect(() => {
    const processing = templates.some((template) => template.status === 'processing');
    if (!processing) {
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    pollRef.current = window.setInterval(() => void refresh(), 3000);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [templates, refresh]);

  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await templatesApi.upload(file, name || file.name);
      setName('');
      setFile(null);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Upload failed');
    } finally {
      setBusy(false);
    }
  };

  // Permanent delete. While contracts still reference the template the server
  // answers 409 with their count; archiving stays available as the way out.
  const removeTemplate = async (template: Template) => {
    if (!window.confirm(`Delete the template “${template.name}” permanently? This cannot be undone.`)) {
      return;
    }
    setDeletingId(template.id);
    setListError(null);
    setBlocked(null);
    try {
      await templatesApi.purge(template.id);
      setTemplates((current) => current.filter((item) => item.id !== template.id));
      setSelected((current) => (current?.id === template.id ? null : current));
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        const count = Number(cause.details.contract_count);
        setBlocked({ template, contractCount: Number.isFinite(count) ? count : null });
      } else if (cause instanceof ApiError && cause.status === 403) {
        setListError('Only an admin can delete templates.');
      } else {
        setListError(cause instanceof Error ? cause.message : 'Could not delete the template');
      }
    } finally {
      setDeletingId(null);
    }
  };

  const archiveInstead = async () => {
    if (!blocked) return;
    setDeletingId(blocked.template.id);
    setListError(null);
    try {
      await templatesApi.archive(blocked.template.id);
      setBlocked(null);
      await refresh();
    } catch (cause) {
      setListError(cause instanceof Error ? cause.message : 'Could not archive the template');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="page">
      <header className="page__header">
        <h1>Templates</h1>
      </header>

      <form className="card" onSubmit={upload}>
        <h2>Upload a blank template</h2>
        <p className="hint">
          Field detection runs in the background; the template becomes available for contracts once
          it reports <strong>ready</strong>.
        </p>
        <label className="field">
          <span>Template name</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Driver contract" />
        </label>
        <label className="field">
          <span>PDF file</span>
          <input
            type="file"
            accept="application/pdf"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            required
          />
        </label>
        {error && <p className="alert alert--error">{error}</p>}
        <button type="submit" className="button button--primary" disabled={busy || !file}>
          {busy ? 'Uploading…' : 'Upload and analyse'}
        </button>
      </form>

      {listError && (
        <p className="alert alert--error" role="alert">
          {listError}
        </p>
      )}
      {blocked && (
        <div className="alert alert--warn" role="alert">
          <p>
            {blocked.contractCount === null
              ? 'Contracts still use this template'
              : `${blocked.contractCount} ${blocked.contractCount === 1 ? 'contract uses' : 'contracts use'} this template`}{' '}
            (“{blocked.template.name}”) — delete them first.
          </p>
          <div className="alert__actions">
            <button
              type="button"
              className="button button--small"
              disabled={deletingId !== null}
              onClick={() => void archiveInstead()}
            >
              Archive instead
            </button>
            <button
              type="button"
              className="button button--link"
              onClick={() => setBlocked(null)}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Pages</th>
            <th>Fields</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {templates.map((template) => {
            const prefilled = readPrefilledWarning(template.prefilled_summary);
            return (
            <tr key={template.id}>
              <td>
                {template.name}
                {prefilled && (
                  <div className="prefilled-warning" data-testid="prefilled-warning">
                    <span className="badge badge--warn">Pre-filled template</span>
                    <div className="hint">
                      Already contains {prefilled.signatures.filled}/{prefilled.signatures.total}{' '}
                      signatures and {prefilled.dates.filled}/{prefilled.dates.total} dates — this
                      looks like a signed document, not a blank form. Signatures and dates present in
                      contracts will be treated as filled.
                    </div>
                  </div>
                )}
              </td>
              <td>
                <span
                  className={`badge badge--${
                    template.status === 'ready' ? 'good' : template.status === 'failed' ? 'bad' : 'neutral'
                  }`}
                >
                  {template.status}
                </span>
                {template.error_message && (
                  <span className="hint hint--error"> {template.error_message}</span>
                )}
              </td>
              <td>{template.page_count}</td>
              <td>
                <button
                  type="button"
                  className="button button--link"
                  onClick={() =>
                    templatesApi.get(template.id).then(setSelected).catch(() => setSelected(null))
                  }
                >
                  Inspect
                </button>
              </td>
              <td>
                <div className="table__actions">
                  <button
                    type="button"
                    className="button button--link"
                    onClick={() => templatesApi.reanalyze(template.id).then(() => refresh())}
                  >
                    Re-analyse
                  </button>
                  <button
                    type="button"
                    className="button button--link button--danger"
                    disabled={deletingId !== null}
                    onClick={() => void removeTemplate(template)}
                  >
                    {deletingId === template.id ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>

      {selected && (
        <div className="card">
          <h2>
            {selected.name} · {selected.field_schema.field_count} detected fields
          </h2>
          <p className="hint">
            Detection is good, not perfect. A misplaced signature box is the difference between a
            signature landing on the line or beside it — correct coordinates here.
          </p>
          {selected.field_schema.pages.map((page) => (
            <details key={page.page_number}>
              <summary>
                Page {page.page_number} — {page.fields.length} fields
              </summary>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Type</th>
                    <th>Required</th>
                    <th>x, y</th>
                    <th>w × h</th>
                  </tr>
                </thead>
                <tbody>
                  {page.fields.map((field) => (
                    <tr key={field.field_id}>
                      <td>
                        <code>{field.field_id}</code>
                        <div className="hint">{field.label}</div>
                      </td>
                      <td>{field.field_type}</td>
                      <td>{field.required ? 'yes' : 'no'}</td>
                      <td>
                        {field.bbox.x.toFixed(0)}, {field.bbox.y.toFixed(0)}
                      </td>
                      <td>
                        {field.bbox.width.toFixed(0)} × {field.bbox.height.toFixed(0)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
