import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { contractsApi, type ContractDuplicate } from '../api/contracts';
import { templatesApi } from '../api/templates';
import { PdfDropZone } from '../components/PdfDropZone';
import {
  MAX_UPLOAD_BYTES,
  describeUploadError,
  formatBytes,
  pageLabel,
  pdfFileProblem,
} from '../lib/contractUpload';
import { readPdfThumbnail, type PdfThumbnail } from '../lib/pdfThumbnail';
import { sha256Hex } from '../lib/sha256';
import { formatServerDateTime } from '../lib/time';
import { readPrefilledWarning } from '../lib/warnings';
import type { Template } from '../types/template';
import '../styles/upload.css';

/** The duplicate check is advisory: past this it is abandoned and the upload goes ahead. */
const DUPLICATE_CHECK_TIMEOUT_MS = 8000;

const MAX_UPLOAD_MB = Math.round(MAX_UPLOAD_BYTES / (1024 * 1024));

const FILE_ERROR_ID = 'upload-file-error';
const DROP_HINT_ID = 'upload-drop-hint';

/**
 * Earlier contracts uploaded from the same bytes, or an empty list when there
 * are none *or the check could not run* (no hash, network error, an older
 * backend without the endpoint). Never throws: a warning must not stop an upload.
 */
async function findDuplicates(file: File): Promise<ContractDuplicate[]> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), DUPLICATE_CHECK_TIMEOUT_MS);
  try {
    const hash = await sha256Hex(await file.arrayBuffer());
    const result = await contractsApi.duplicates(hash, controller.signal);
    return Array.isArray(result?.duplicates) ? result.duplicates : [];
  } catch {
    return [];
  } finally {
    window.clearTimeout(timer);
  }
}

export function ContractUpload() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templateId, setTemplateId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PdfThumbnail | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [contractorName, setContractorName] = useState('');
  const [contractorEmail, setContractorEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'checking' | 'uploading' | null>(null);
  const [duplicates, setDuplicates] = useState<ContractDuplicate[] | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const cancelDuplicateRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    templatesApi
      .list()
      .then((all) => {
        // Only a fully analysed template can be compared against.
        const ready = all.filter((template) => template.status === 'ready');
        setTemplates(ready);
        if (ready.length > 0) setTemplateId(ready[0].id);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Failed to load templates'))
      .finally(() => setTemplatesLoading(false));
  }, []);

  // Page count and first-page thumbnail, so the uploader can see *which*
  // document they picked. Never fatal: `readPdfThumbnail` resolves either way.
  useEffect(() => {
    if (!file) {
      setPreview(null);
      setPreviewBusy(false);
      return;
    }
    let cancelled = false;
    setPreview(null);
    setPreviewBusy(true);
    void readPdfThumbnail(file).then((result) => {
      if (cancelled) return;
      setPreview(result);
      setPreviewBusy(false);
    });
    return () => {
      cancelled = true;
    };
  }, [file]);

  // A PDF dropped anywhere but the zone would otherwise replace this page with
  // the document itself, losing everything typed so far.
  useEffect(() => {
    const swallow = (event: DragEvent) => {
      if (Array.from(event.dataTransfer?.types ?? []).includes('Files')) event.preventDefault();
    };
    window.addEventListener('dragover', swallow);
    window.addEventListener('drop', swallow);
    return () => {
      window.removeEventListener('dragover', swallow);
      window.removeEventListener('drop', swallow);
    };
  }, []);

  useEffect(() => {
    if (duplicates && duplicates.length > 0) cancelDuplicateRef.current?.focus();
  }, [duplicates]);

  const chooseFile = useCallback((picked: File | null) => {
    // A new file invalidates the answer the duplicate check gave for the old one.
    setDuplicates(null);
    setError(null);
    if (!picked) {
      setFile(null);
      setFileError(null);
      return;
    }
    const problem = pdfFileProblem(picked);
    if (problem) {
      setFile(null);
      setFileError(problem);
      return;
    }
    setFileError(null);
    setFile(picked);
  }, []);

  const openPicker = useCallback(() => fileInputRef.current?.click(), []);

  const upload = async () => {
    if (!file || !templateId) return;
    setDuplicates(null);
    setBusy('uploading');
    setError(null);
    try {
      const result = await contractsApi.upload(templateId, file, contractorName, contractorEmail);
      navigate(`/contracts/${result.contract_id}/review`);
    } catch (cause) {
      setError(describeUploadError(cause));
    } finally {
      setBusy(null);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !templateId || busy) return;
    setError(null);
    setBusy('checking');
    const found = await findDuplicates(file);
    if (found.length > 0) {
      setBusy(null);
      setDuplicates(found);
      return;
    }
    await upload();
  };

  const selectedTemplate = templates.find((template) => template.id === templateId);
  const selectedTemplateName = selectedTemplate?.name;
  const differentTemplate = (duplicates ?? []).some((item) => item.template_id !== templateId);

  const pageCount = preview?.pageCount ?? 0;
  const unreadable = Boolean(preview) && !previewBusy && pageCount === 0;
  const ready = Boolean(file && templateId);
  const currentStep = !templateId ? 1 : !file ? 2 : 3;

  const steps: { n: number; label: string; done: boolean }[] = [
    { n: 1, label: 'Template', done: Boolean(templateId) },
    { n: 2, label: 'Contract file', done: Boolean(file) },
    { n: 3, label: 'Details and upload', done: false },
  ];

  const stepClass = (step: { n: number; done: boolean }) =>
    `upload-steps__item${step.done ? ' is-done' : ''}${
      step.n === currentStep ? ' is-current' : ''
    }`;

  return (
    <section className="page upload">
      <header className="page__header upload__header">
        <div>
          <h1>Upload a contract</h1>
          <p className="upload__lede">
            Pick the template the contract was made from, attach the completed PDF, then confirm
            who it belongs to.
          </p>
        </div>
      </header>

      <ol className="upload-steps" aria-label="Upload progress">
        {steps.map((step) => (
          <li
            key={step.n}
            className={stepClass(step)}
            aria-current={step.n === currentStep ? 'step' : undefined}
          >
            <span className="upload-steps__marker" aria-hidden="true">
              {step.done ? '✓' : step.n}
            </span>
            <span className="upload-steps__label">{step.label}</span>
          </li>
        ))}
      </ol>

      <form className="upload__form" onSubmit={submit} noValidate>
        {/* ---------------------------------------------------------- step 1 */}
        <section className="card upload-step" aria-labelledby="upload-step-1">
          <div className="upload-step__head">
            <span className="upload-step__number" aria-hidden="true">
              1
            </span>
            <div>
              <h2 id="upload-step-1">Which template is this contract?</h2>
            </div>
          </div>

          {templatesLoading ? (
            <p className="empty">Loading templates…</p>
          ) : templates.length === 0 ? (
            <p className="empty" data-testid="upload-no-templates">
              No analysed templates available. Upload a blank form on the{' '}
              <Link to="/templates">Templates</Link> page and wait for its analysis to finish.
            </p>
          ) : (
            <fieldset className="upload-fieldset">
              <legend className="visually-hidden">Template</legend>
              <div className="upload-templates">
                {templates.map((template) => {
                  const prefilled = readPrefilledWarning(template.prefilled_summary);
                  const selected = template.id === templateId;
                  return (
                    <label
                      key={template.id}
                      className={`upload-template${selected ? ' is-selected' : ''}`}
                    >
                      <input
                        type="radio"
                        name="template"
                        value={template.id}
                        checked={selected}
                        onChange={() => {
                          setTemplateId(template.id);
                          setError(null);
                        }}
                      />
                      <span className="upload-template__body">
                        <span className="upload-template__name">{template.name}</span>
                        <span className="upload-template__meta">
                          {pageLabel(template.page_count)}
                          {template.description ? ` · ${template.description}` : ''}
                        </span>
                        {prefilled && (
                          <span
                            className="prefilled-warning upload-template__warning"
                            data-testid="prefilled-warning"
                          >
                            <span className="badge badge--warn">Pre-filled template</span>
                            <span className="hint">
                              Looks already signed ({prefilled.signatures.filled}/
                              {prefilled.signatures.total} signatures, {prefilled.dates.filled}/
                              {prefilled.dates.total} dates).
                            </span>
                          </span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          )}
        </section>

        {/* ---------------------------------------------------------- step 2 */}
        <section className="card upload-step" aria-labelledby="upload-step-2">
          <div className="upload-step__head">
            <span className="upload-step__number" aria-hidden="true">
              2
            </span>
            <div>
              <h2 id="upload-step-2">The completed contract</h2>
              <p className="upload-step__hint" id={DROP_HINT_ID}>
                One PDF, up to {MAX_UPLOAD_MB} MB.
              </p>
            </div>
          </div>

          {/* Owned here, so "Replace file" reuses the same picker. */}
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf,.pdf"
            className="visually-hidden"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(event) => {
              chooseFile(event.target.files?.[0] ?? null);
              // Allows picking the same file again after removing it.
              event.target.value = '';
            }}
          />

          {file ? (
            <div className="upload-file" data-testid="upload-file">
              <span className="upload-file__thumb" data-testid="upload-file-thumb">
                {preview?.thumbnail ? (
                  <img src={preview.thumbnail} alt={`First page of ${file.name}`} />
                ) : (
                  <span className="upload-file__thumb-empty">
                    {previewBusy ? 'Reading…' : 'No preview'}
                  </span>
                )}
              </span>

              <div className="upload-file__meta">
                <p className="upload-file__name" title={file.name}>
                  {file.name}
                </p>
                <p className="upload-file__facts">
                  <span data-testid="upload-file-size">{formatBytes(file.size)}</span>
                  {pageCount > 0 && (
                    <>
                      <span aria-hidden="true"> · </span>
                      <span data-testid="upload-file-pages">{pageLabel(pageCount)}</span>
                    </>
                  )}
                  {previewBusy && (
                    <>
                      <span aria-hidden="true"> · </span>
                      <span>reading the PDF…</span>
                    </>
                  )}
                </p>
                {unreadable && (
                  <p className="hint upload-file__unreadable">
                    No preview for this PDF. You can still upload it.
                  </p>
                )}
              </div>

              <div className="upload-file__actions">
                <button type="button" className="button button--small" onClick={openPicker}>
                  Replace file
                </button>
                <button
                  type="button"
                  className="button button--small button--danger"
                  onClick={() => chooseFile(null)}
                >
                  Remove file
                </button>
              </div>
            </div>
          ) : (
            <PdfDropZone
              onFile={chooseFile}
              onBrowse={openPicker}
              describedBy={`${DROP_HINT_ID}${fileError ? ` ${FILE_ERROR_ID}` : ''}`}
            />
          )}

          {fileError && (
            <p className="alert alert--error upload-file__error" id={FILE_ERROR_ID} role="alert" data-testid="upload-file-error">
              {fileError}
            </p>
          )}
        </section>

        {/* ---------------------------------------------------------- step 3 */}
        <section className="card upload-step" aria-labelledby="upload-step-3">
          <div className="upload-step__head">
            <span className="upload-step__number" aria-hidden="true">
              3
            </span>
            <div>
              <h2 id="upload-step-3">Who is it for?</h2>
              <p className="upload-step__hint">Optional.</p>
            </div>
          </div>

          <div className="upload-details">
            <label className="field">
              <span>Contractor name</span>
              <input
                value={contractorName}
                onChange={(event) => setContractorName(event.target.value)}
                data-testid="upload-contractor-name"
                autoComplete="off"
              />
            </label>

            <label className="field">
              <span>Contractor email</span>
              <input
                type="email"
                value={contractorEmail}
                onChange={(event) => setContractorEmail(event.target.value)}
                data-testid="upload-contractor-email"
                autoComplete="off"
              />
            </label>
          </div>

          <dl className="upload-summary" data-testid="upload-summary">
            <div>
              <dt>Template</dt>
              <dd>{selectedTemplateName ?? <span className="upload-summary__todo">not chosen</span>}</dd>
            </div>
            <div>
              <dt>File</dt>
              <dd>
                {file ? (
                  <>
                    {file.name}
                    {pageCount > 0 ? ` · ${pageLabel(pageCount)}` : ''}
                  </>
                ) : (
                  <span className="upload-summary__todo">not attached</span>
                )}
              </dd>
            </div>
          </dl>

          {error && (
            <p className="alert alert--error" role="alert" data-testid="upload-error">
              {error}
            </p>
          )}

          <div className="upload-actions">
            <button
              type="submit"
              className="button button--primary"
              data-testid="upload-submit"
              disabled={busy !== null || !ready}
            >
              {busy === 'checking'
                ? 'Checking…'
                : busy === 'uploading'
                  ? 'Uploading…'
                  : 'Upload and compare'}
            </button>
            <p className="upload-actions__note">
              {busy
                ? 'Keep this page open until it finishes.'
                : ready
                  ? 'The contract opens on its review page as soon as the upload finishes.'
                  : currentStep === 1
                    ? 'Choose a template to continue.'
                    : 'Attach the contract PDF to continue.'}
            </p>
          </div>

          {busy && (
            <div className="upload-progress" role="status" aria-live="polite">
              <span className="upload-progress__track">
                <span className="upload-progress__fill" />
              </span>
              <span className="upload-progress__text">
                {busy === 'checking'
                  ? 'Checking whether this exact file was uploaded before…'
                  : 'Uploading the contract…'}
              </span>
            </div>
          )}
        </section>
      </form>

      {duplicates && duplicates.length > 0 && (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="upload-duplicate-title"
          onKeyDown={(event) => {
            if (event.key === 'Escape') setDuplicates(null);
          }}
        >
          <div className="modal__panel upload-dup">
            <h2 className="modal__title" id="upload-duplicate-title">
              This file was already uploaded
            </h2>
            <p className="modal__hint">
              This exact PDF was already uploaded {duplicates.length} time{duplicates.length === 1 ? '' : 's'}
              {duplicates.length >= 20 ? ' (showing the newest 20)' : ''}. Open the existing contract instead,
              or upload it again anyway.
            </p>

            {differentTemplate && (
              <div className="alert alert--warn">
                <p>
                  An earlier upload used a <strong>different template</strong> than the one selected now
                  {selectedTemplateName ? ` (“${selectedTemplateName}”)` : ''}. Check which template this
                  contract really belongs to before uploading again.
                </p>
              </div>
            )}

            <ul className="duplicate-list">
              {duplicates.map((item) => {
                const mismatch = item.template_id !== templateId;
                return (
                  <li
                    key={item.id}
                    className={`duplicate-list__item${mismatch ? ' duplicate-list__item--mismatch' : ''}`}
                  >
                    <div className="duplicate-list__row">
                      <strong>{formatServerDateTime(item.created_at)}</strong>{' '}
                      <span className="badge badge--neutral">{item.status}</span>{' '}
                      <span className="badge badge--neutral">review: {item.review_status}</span>
                    </div>
                    <div className="duplicate-list__row">
                      Template: {item.template_name ?? 'unknown template'}{' '}
                      {mismatch ? (
                        <span className="badge badge--warn">different template</span>
                      ) : (
                        <span className="badge badge--good">same template</span>
                      )}
                    </div>
                    <Link to={`/contracts/${item.id}/review`} target="_blank" rel="noopener noreferrer">
                      Open existing contract
                    </Link>
                  </li>
                );
              })}
            </ul>

            <div className="modal__actions">
              <button
                ref={cancelDuplicateRef}
                type="button"
                className="button button--primary"
                onClick={() => setDuplicates(null)}
              >
                Cancel
              </button>
              <span className="modal__spacer" />
              <button
                type="button"
                className="button button--ghost upload-dup__anyway"
                onClick={() => void upload()}
              >
                Upload anyway
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
