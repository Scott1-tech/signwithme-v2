import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { ApiError } from '../api/client';
import {
  firstNonWinAnsi,
  readShareToken,
  sharePublicApi,
  type FieldValue,
  type SharedFieldOut,
  type SharedPageOut,
  type ShareSessionOut,
} from '../api/share';
import '../styles/share.css';

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

/**
 * F3-UI (EDITOR-V2-PLAN §8, §11 Q4): the page a share link opens -- `/s#t=<token>`.
 *
 * No account, no app navigation. The token is read from the URL FRAGMENT
 * (never sent to a server, never in a Referer) and the fragment is removed
 * from the address bar at once (`history.replaceState`), before anything is
 * fetched; after that it lives only in memory and travels only in the
 * `X-Share-Token` header (`sharePublicApi`). It is never written to storage.
 *
 * The page: the pinned version's PDF (pdf.js, `GET /api/share/file`) with an
 * input over every field the link allows (positions in pt from
 * `GET /api/share/session`, pages sized from its `pages`), an optional name
 * and e-mail, Submit. Every refusal of the link -- unknown, expired, revoked,
 * used up -- is the server's same 404, and this page says the same neutral
 * thing for all of them.
 */

type Phase = 'loading' | 'ready' | 'invalid' | 'throttled' | 'error' | 'thanks';

const INVALID_TEXT = 'This link is invalid or has expired.';
const THROTTLED_TEXT = 'Too many attempts from this computer. Please try again later.';

const REASON_TEXT: Record<string, string> = {
  required: 'This field is required.',
  invalid_field_value: 'This value is not accepted here.',
  undrawable_character: 'Contains characters this form cannot print — use Latin letters.',
  field_readonly: 'This field cannot be changed.',
  field_not_fillable: 'This field cannot be filled.',
  unknown_field: 'This field is no longer in the document.',
};

// The token of THIS page load. Module-level on purpose: React's development
// double render must not read the (already cleared) fragment a second time.
let capturedToken: string | null | undefined;

function takeToken(): string | null {
  const hash = window.location.hash;
  if (hash) {
    capturedToken = readShareToken(hash);
    // Out of the address bar (and the history entry) before any request.
    window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
  }
  return capturedToken ?? null;
}

/** An unrotated top-left rect (pt) on a page turned `rotation` degrees clockwise, in display pt. */
function displayRect(
  rect: { x_pt: number; y_pt: number; w_pt: number; h_pt: number },
  width: number,
  height: number,
  rotation: number,
) {
  const { x_pt: x, y_pt: y, w_pt: w, h_pt: h } = rect;
  switch (((rotation % 360) + 360) % 360) {
    case 90:
      return { left: height - (y + h), top: x, width: h, height: w };
    case 180:
      return { left: width - (x + w), top: height - (y + h), width: w, height: h };
    case 270:
      return { left: y, top: width - (x + w), width: h, height: w };
    default:
      return { left: x, top: y, width: w, height: h };
  }
}

function initialValues(fields: SharedFieldOut[]): Record<string, FieldValue> {
  const values: Record<string, FieldValue> = {};
  fields.forEach((field) => {
    if (field.name in values) {
      // a radio group: the chosen button's export value, else ""
      if (field.type === 'radio' && field.value === true && field.export_value) values[field.name] = field.export_value;
      return;
    }
    if (field.type === 'checkbox') values[field.name] = field.value === true;
    else if (field.type === 'radio') {
      values[field.name] =
        typeof field.value === 'string' ? field.value : field.value === true && field.export_value ? field.export_value : '';
    } else values[field.name] = typeof field.value === 'string' ? field.value : '';
  });
  return values;
}

const fieldKey = (field: SharedFieldOut) =>
  field.type === 'radio' ? `${field.name}-${field.export_value ?? ''}` : field.name;

const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]{1,189}$/;

function PdfPageCanvas({
  pdf,
  position,
  scale,
  width,
  height,
}: {
  pdf: PDFDocumentProxy;
  position: number;
  scale: number;
  width: number;
  height: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let cancelled = false;
    let task: { cancel: () => void; promise: Promise<unknown> } | null = null;
    void pdf.getPage(position).then((page) => {
      if (cancelled || !canvasRef.current) return;
      const ratio = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: scale * ratio });
      const canvas = canvasRef.current;
      canvas.width = Math.round(viewport.width);
      canvas.height = Math.round(viewport.height);
      const context = canvas.getContext('2d');
      if (!context) return;
      task = page.render({ canvasContext: context, viewport });
      task.promise.catch(() => undefined);
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, position, scale]);
  return (
    <canvas
      ref={canvasRef}
      className="sf-page__canvas"
      style={{ width: `${width * scale}px`, height: `${height * scale}px` }}
      aria-hidden="true"
    />
  );
}

export function SharedForm() {
  const [token, setToken] = useState<string | null>(() => takeToken());
  const [phase, setPhase] = useState<Phase>(token ? 'loading' : 'invalid');
  const [info, setInfo] = useState<ShareSessionOut | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [values, setValues] = useState<Record<string, FieldValue>>({});
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [left, setLeft] = useState<number | null>(null);
  const [width, setWidth] = useState(() => Math.min(window.innerWidth - 32, 900));
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    document.title = 'Fill in the form';
    const onResize = () => setWidth(Math.min(window.innerWidth - 32, 900));
    // Another link pasted into this same tab only changes the fragment.
    const onHash = () => {
      if (!window.location.hash) return;
      const next = takeToken();
      setToken(next);
      if (!next) setPhase('invalid');
      else setAttempt((n) => n + 1);
    };
    window.addEventListener('resize', onResize);
    window.addEventListener('hashchange', onHash);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('hashchange', onHash);
    };
  }, []);

  const refuse = (cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 404) setPhase('invalid');
    else if (cause instanceof ApiError && cause.status === 429) setPhase('throttled');
    else setPhase('error');
  };

  useEffect(() => {
    if (!token) return undefined;
    let live = true;
    let loaded: PDFDocumentProxy | null = null;
    setPhase('loading');
    (async () => {
      try {
        const session = await sharePublicApi.session(token);
        const bytes = await sharePublicApi.file(token);
        const doc = await pdfjs.getDocument({ data: new Uint8Array(bytes) }).promise;
        loaded = doc;
        if (!live) return;
        setInfo(session);
        setValues(initialValues(session.fields));
        setLeft(session.submissions_left);
        setPdf(doc);
        setPhase('ready');
      } catch (cause) {
        if (live) refuse(cause);
      }
    })();
    return () => {
      live = false;
      void loaded?.destroy();
    };
  }, [token, attempt]);

  const byPage = useMemo(() => {
    const map = new Map<string, SharedFieldOut[]>();
    (info?.fields ?? []).forEach((field) => {
      const list = map.get(field.page_uid);
      if (list) list.push(field);
      else map.set(field.page_uid, [field]);
    });
    return map;
  }, [info]);

  const groups = useMemo(() => {
    const seen = new Map<string, SharedFieldOut>();
    (info?.fields ?? []).forEach((field) => {
      if (!seen.has(field.name)) seen.set(field.name, field);
    });
    return [...seen.values()];
  }, [info]);

  const setValue = (fieldName: string, value: FieldValue) => {
    setValues((current) => ({ ...current, [fieldName]: value }));
    setFieldErrors((current) => {
      if (!(fieldName in current)) return current;
      const next = { ...current };
      delete next[fieldName];
      return next;
    });
  };

  /** What the server would refuse, said before anything is sent. */
  const checkLocally = (): Record<string, string> => {
    const problems: Record<string, string> = {};
    groups.forEach((field) => {
      const value = values[field.name];
      if (typeof value === 'string') {
        const bad = firstNonWinAnsi(value);
        if (bad) {
          problems[field.name] = `“${bad}” cannot be printed in this form — use Latin letters (Windows-1252).`;
          return;
        }
      }
      const empty = value === undefined || value === false || (typeof value === 'string' && !value.trim());
      if (field.required && empty) problems[field.name] = REASON_TEXT.required;
    });
    return problems;
  };

  const submit = async () => {
    if (!token || !info || submitting) return;
    setFormError(null);
    const local = checkLocally();
    if (email.trim() && !EMAIL_RE.test(email.trim())) {
      setFieldErrors(local);
      setFormError('Check the e-mail address (or leave it empty).');
      return;
    }
    if (Object.keys(local).length > 0) {
      setFieldErrors(local);
      setFormError('Please correct the marked fields.');
      return;
    }
    setFieldErrors({});
    setSubmitting(true);
    try {
      const out = await sharePublicApi.submit(token, {
        values,
        submitter_name: name.trim() || null,
        submitter_email: email.trim() || null,
      });
      setLeft(out.submissions_left);
      setPhase('thanks');
      // The document is not needed any more.
      void pdf?.destroy();
      setPdf(null);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'invalid_submission') {
        const list = (cause.details.fields as Array<{ name: string; reason: string }> | undefined) ?? [];
        const next: Record<string, string> = {};
        list.forEach((item) => {
          if (item.name) next[item.name] = REASON_TEXT[item.reason] ?? `Not accepted (${item.reason}).`;
        });
        setFieldErrors(next);
        const notAllowed = Number(cause.details.not_allowed) || 0;
        setFormError(
          notAllowed > 0
            ? 'Some values were not part of this form. Reload the page and try again.'
            : 'Please correct the marked fields.',
        );
      } else if (cause instanceof ApiError && cause.code === 'empty_submission') {
        setFormError('Nothing was filled in.');
      } else if (cause instanceof ApiError && cause.status === 422) {
        setFormError('Check your name and e-mail address.');
      } else if (cause instanceof ApiError && (cause.status === 404 || cause.status === 429)) {
        refuse(cause);
      } else {
        setFormError('The form could not be sent. Check the connection and try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (phase !== 'ready' || !info || !pdf) {
    return (
      <main className="sf" data-testid="shared-form" data-state={phase}>
        <div className="sf-card" role={phase === 'loading' ? 'status' : 'alert'} data-testid={`shared-${phase}`}>
          {phase === 'loading' && <p>Loading the form…</p>}
          {phase === 'invalid' && <p>{INVALID_TEXT}</p>}
          {phase === 'throttled' && <p>{THROTTLED_TEXT}</p>}
          {phase === 'error' && (
            <>
              <p>The form could not be loaded.</p>
              <button type="button" className="sf-btn" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            </>
          )}
          {phase === 'thanks' && (
            <>
              <h1 className="sf-title">Thank you</h1>
              <p>Your answers were sent. You can close this page.</p>
              {left !== null && left > 0 && (
                <p className="sf-muted" data-testid="shared-left">
                  This link can be used {left} more time{left === 1 ? '' : 's'}.
                </p>
              )}
            </>
          )}
        </div>
      </main>
    );
  }

  const errorNames = Object.keys(fieldErrors);
  const pages: SharedPageOut[] = [...info.pages].sort((a, b) => a.position - b.position);

  return (
    <main className="sf" data-testid="shared-form" data-state="ready">
      <header className="sf-head">
        <h1 className="sf-title" data-testid="shared-title">
          {info.document_title}
        </h1>
        <p className="sf-muted">
          Fill in the highlighted fields, then press «Submit». The link works until{' '}
          {new Date(info.expires_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}.
        </p>
      </header>

      <div className="sf-pages">
        {pages.map((page) => {
          const turned = page.rotation % 180 !== 0;
          const shownW = turned ? page.height_pt : page.width_pt;
          const shownH = turned ? page.width_pt : page.height_pt;
          const scale = width / shownW;
          const fields = byPage.get(page.page_uid) ?? [];
          return (
            <section
              key={page.page_uid}
              className="sf-page"
              data-testid={`shared-page-${page.position}`}
              data-scale={scale}
              style={{ width: `${shownW * scale}px`, height: `${shownH * scale}px` }}
              aria-label={`Page ${page.position}`}
            >
              <PdfPageCanvas pdf={pdf} position={page.position} scale={scale} width={shownW} height={shownH} />
              {/* pdf.js paints the PDF's own widget appearance (its value in this
                  version) into the canvas; an opaque cover under each input keeps
                  that from showing through next to what the recipient types. */}
              {fields.map((field) => {
                const box = displayRect(field.rect, page.width_pt, page.height_pt, page.rotation);
                return (
                  <span
                    key={`cover-${fieldKey(field)}`}
                    className="sf-cover"
                    aria-hidden="true"
                    style={{
                      left: `${box.left * scale}px`,
                      top: `${box.top * scale}px`,
                      width: `${box.width * scale}px`,
                      height: `${box.height * scale}px`,
                    }}
                  />
                );
              })}
              {fields.map((field) => {
                const box = displayRect(field.rect, page.width_pt, page.height_pt, page.rotation);
                const style: CSSProperties = {
                  left: `${box.left * scale}px`,
                  top: `${box.top * scale}px`,
                  width: `${box.width * scale}px`,
                  height: `${box.height * scale}px`,
                  fontSize: `${Math.max(8, Math.min((field.size_pt ?? Math.min(box.height * 0.7, 12)) * scale, 24))}px`,
                };
                const key = fieldKey(field);
                const problem = fieldErrors[field.name];
                const label = field.tooltip || field.name;
                const common = {
                  'data-testid': `shared-field-${key}`,
                  'data-field': field.name,
                  'aria-label': `${label}${field.required ? ' (required)' : ''}`,
                  'aria-invalid': problem ? true : undefined,
                  'aria-describedby': problem ? `sf-err-${field.name}` : undefined,
                  title: problem ?? label,
                  className: `sf-input${problem ? ' sf-input--bad' : ''}${field.required ? ' sf-input--req' : ''}`,
                  style,
                };
                const value = values[field.name];
                if (field.type === 'checkbox') {
                  return (
                    <input
                      key={key}
                      type="checkbox"
                      {...common}
                      checked={value === true}
                      onChange={(event) => setValue(field.name, event.target.checked)}
                    />
                  );
                }
                if (field.type === 'radio') {
                  return (
                    <input
                      key={key}
                      type="radio"
                      name={`sf-radio-${field.name}`}
                      {...common}
                      aria-label={`${label}: ${field.export_value ?? ''}`}
                      checked={value === field.export_value}
                      onChange={() => setValue(field.name, field.export_value ?? '')}
                    />
                  );
                }
                if (field.type === 'dropdown') {
                  return (
                    <select
                      key={key}
                      {...common}
                      value={typeof value === 'string' ? value : ''}
                      onChange={(event) => setValue(field.name, event.target.value)}
                    >
                      <option value="">—</option>
                      {(field.options ?? []).map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  );
                }
                if (field.type === 'multiline') {
                  return (
                    <textarea
                      key={key}
                      {...common}
                      maxLength={field.max_len ?? undefined}
                      value={typeof value === 'string' ? value : ''}
                      onChange={(event) => setValue(field.name, event.target.value)}
                    />
                  );
                }
                return (
                  <input
                    key={key}
                    type="text"
                    {...common}
                    maxLength={field.max_len ?? undefined}
                    value={typeof value === 'string' ? value : ''}
                    onChange={(event) => setValue(field.name, event.target.value)}
                  />
                );
              })}
            </section>
          );
        })}
      </div>

      <form
        className="sf-card sf-submit"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        aria-label="Send the form"
      >
        {(formError || errorNames.length > 0) && (
          <div className="sf-errors" role="alert" data-testid="shared-errors">
            {formError && <p>{formError}</p>}
            {errorNames.length > 0 && (
              <ul>
                {errorNames.map((fieldName) => (
                  <li key={fieldName} id={`sf-err-${fieldName}`} data-testid={`shared-field-error-${fieldName}`}>
                    <strong>{fieldName}</strong>: {fieldErrors[fieldName]}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="sf-who">
          <label>
            <span>Your name (optional)</span>
            <input
              data-testid="shared-name"
              maxLength={120}
              autoComplete="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            <span>Your e-mail (optional)</span>
            <input
              data-testid="shared-email"
              type="email"
              maxLength={254}
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
        </div>
        <p className="sf-muted">
          Only the fields above are sent. The person who shared this form checks your answers before they go into the
          document.
        </p>
        <button type="submit" className="sf-btn sf-btn--primary" data-testid="shared-submit" disabled={submitting}>
          {submitting ? 'Sending…' : 'Submit'}
        </button>
      </form>
    </main>
  );
}
